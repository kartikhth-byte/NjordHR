import os
from datetime import datetime

import pandas as pd
import requests

from repositories.candidate_event_repo import CandidateEventRepo


def resolve_supabase_api_key():
    """Prefer modern Supabase secret key, fallback to legacy service role key."""
    return (
        os.getenv("SUPABASE_SECRET_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


class SupabaseCandidateEventRepo(CandidateEventRepo):
    """Supabase REST-backed candidate event repository."""

    COLUMNS = [
        'Candidate_ID',
        'Filename',
        'Resume_URL',
        'Date_Added',
        'Event_Type',
        'Status',
        'Notes',
        'Rank_Applied_For',
        'Search_Ship_Type',
        'AI_Search_Prompt',
        'AI_Match_Reason',
        'Name',
        'Present_Rank',
        'Email',
        'Country',
        'Mobile_No'
    ]

    def __init__(self, supabase_url, service_role_key, server_url='http://127.0.0.1:5000', timeout_seconds=20):
        self.supabase_url = supabase_url.rstrip('/')
        self.server_url = server_url
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, params=None, json_body=None, headers=None):
        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)
        url = f"{self.supabase_url}{path}"
        resp = requests.request(
            method=method,
            url=url,
            headers=req_headers,
            params=params,
            json=json_body,
            timeout=self.timeout_seconds,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Supabase request failed {resp.status_code}: {resp.text}")
        if resp.text:
            try:
                return resp.json()
            except Exception:
                return []
        return []

    def _event_to_csv_row(self, row):
        rank = row.get("rank_applied_for", "") or ""
        filename = row.get("filename", "") or ""
        resume_url = row.get("resume_url") or f"{self.server_url}/get_resume/{rank}/{filename}"
        return {
            "Candidate_ID": str(row.get("candidate_external_id", "") or ""),
            "Filename": filename,
            "Resume_URL": resume_url,
            "Date_Added": row.get("created_at", "") or "",
            "Event_Type": row.get("event_type", "") or "",
            "Status": row.get("status", "") or "",
            "Notes": row.get("notes", "") or "",
            "Rank_Applied_For": rank,
            "Search_Ship_Type": row.get("search_ship_type", "") or "",
            "AI_Search_Prompt": row.get("ai_search_prompt", "") or "",
            "AI_Match_Reason": row.get("ai_match_reason", "") or "",
            "Name": row.get("name", "") or "",
            "Present_Rank": row.get("present_rank", "") or "",
            "Email": row.get("email", "") or "",
            "Country": row.get("country", "") or "",
            "Mobile_No": row.get("mobile_no", "") or "",
        }

    def _upsert_candidate(self, candidate_external_id, payload):
        body = [{
            "candidate_external_id": str(candidate_external_id),
            **payload,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }]
        self._request(
            "POST",
            "/rest/v1/candidates",
            params={"on_conflict": "candidate_external_id"},
            json_body=body,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def _insert_event(self, event):
        self._request(
            "POST",
            "/rest/v1/candidate_events",
            json_body=[event],
            headers={"Prefer": "return=minimal"},
        )

    def _fetch_events(self, filters=None, order_desc=True):
        params = {
            "select": "candidate_external_id,filename,resume_url,event_type,status,notes,rank_applied_for,"
                      "search_ship_type,ai_search_prompt,ai_match_reason,name,present_rank,email,country,mobile_no,created_at",
            "order": f"created_at.{ 'desc' if order_desc else 'asc' }",
        }
        if filters:
            params.update(filters)
        return self._request("GET", "/rest/v1/candidate_events", params=params)

    def log_event(self, candidate_id, filename, event_type, status='New', notes='',
                  rank_applied_for='', search_ship_type='', ai_prompt='',
                  ai_reason='', extracted_data=None):
        try:
            extracted_data = extracted_data or {}
            candidate_external_id = str(candidate_id)
            resume_url = f"{self.server_url}/get_resume/{rank_applied_for}/{filename}"

            self._upsert_candidate(
                candidate_external_id,
                {
                    "latest_filename": filename,
                    "rank_applied_for": rank_applied_for,
                    "name": extracted_data.get("name", ""),
                    "present_rank": extracted_data.get("present_rank", ""),
                    "email": extracted_data.get("email", ""),
                    "country": extracted_data.get("country", ""),
                    "mobile_no": extracted_data.get("mobile_no", ""),
                },
            )

            self._insert_event({
                "candidate_external_id": candidate_external_id,
                "filename": filename,
                "resume_url": resume_url,
                "event_type": event_type,
                "status": status,
                "notes": notes,
                "rank_applied_for": rank_applied_for,
                "search_ship_type": search_ship_type,
                "ai_search_prompt": ai_prompt,
                "ai_match_reason": ai_reason,
                "name": extracted_data.get("name", ""),
                "present_rank": extracted_data.get("present_rank", ""),
                "email": extracted_data.get("email", ""),
                "country": extracted_data.get("country", ""),
                "mobile_no": extracted_data.get("mobile_no", ""),
            })
            return True
        except Exception as exc:
            print(f"[SUPABASE ERROR] Failed to log event: {exc}")
            return False

    def get_latest_status_per_candidate(self, rank_name=''):
        try:
            events = self._fetch_events(filters={}, order_desc=True)
            latest_by_candidate = {}
            for row in events:
                cid = str(row.get("candidate_external_id", ""))
                if not cid:
                    continue
                if cid not in latest_by_candidate:
                    latest_by_candidate[cid] = row
            rows = [self._event_to_csv_row(v) for v in latest_by_candidate.values()]
            df = pd.DataFrame(rows, columns=self.COLUMNS)
            if df.empty:
                return pd.DataFrame(columns=self.COLUMNS)
            if rank_name:
                df = df[df["Rank_Applied_For"] == rank_name]
                if df.empty:
                    return pd.DataFrame(columns=self.COLUMNS)
            return df.sort_values("Date_Added", ascending=False).reset_index(drop=True)
        except Exception as exc:
            print(f"[SUPABASE ERROR] Failed to fetch latest status: {exc}")
            return pd.DataFrame(columns=self.COLUMNS)

    def get_candidate_history(self, candidate_id):
        try:
            events = self._fetch_events(
                filters={"candidate_external_id": f"eq.{str(candidate_id)}"},
                order_desc=False
            )
            return [self._event_to_csv_row(row) for row in events]
        except Exception as exc:
            print(f"[SUPABASE ERROR] Failed to fetch candidate history: {exc}")
            return []

    def _get_latest_candidate_row(self, candidate_id):
        df = self.get_latest_status_per_candidate()
        if df.empty:
            return None
        rows = df[df["Candidate_ID"].astype(str) == str(candidate_id)]
        if rows.empty:
            return None
        return rows.iloc[0].to_dict()

    def log_status_change(self, candidate_id, status):
        latest = self._get_latest_candidate_row(candidate_id)
        if not latest:
            return False
        return self.log_event(
            candidate_id=candidate_id,
            filename=latest.get('Filename', ''),
            event_type='status_change',
            status=status,
            notes=latest.get('Notes', ''),
            rank_applied_for=latest.get('Rank_Applied_For', ''),
            search_ship_type=latest.get('Search_Ship_Type', ''),
            ai_prompt=latest.get('AI_Search_Prompt', ''),
            ai_reason=latest.get('AI_Match_Reason', ''),
            extracted_data={
                'name': latest.get('Name', ''),
                'present_rank': latest.get('Present_Rank', ''),
                'email': latest.get('Email', ''),
                'country': latest.get('Country', ''),
                'mobile_no': latest.get('Mobile_No', '')
            }
        )

    def log_note_added(self, candidate_id, notes):
        latest = self._get_latest_candidate_row(candidate_id)
        if not latest:
            return False
        return self.log_event(
            candidate_id=candidate_id,
            filename=latest.get('Filename', ''),
            event_type='note_added',
            status=latest.get('Status', 'New'),
            notes=notes,
            rank_applied_for=latest.get('Rank_Applied_For', ''),
            search_ship_type=latest.get('Search_Ship_Type', ''),
            ai_prompt=latest.get('AI_Search_Prompt', ''),
            ai_reason=latest.get('AI_Match_Reason', ''),
            extracted_data={
                'name': latest.get('Name', ''),
                'present_rank': latest.get('Present_Rank', ''),
                'email': latest.get('Email', ''),
                'country': latest.get('Country', ''),
                'mobile_no': latest.get('Mobile_No', '')
            }
        )

    def get_rank_counts(self):
        latest = self.get_latest_status_per_candidate()
        if latest.empty:
            return []
        grouped = latest.groupby("Rank_Applied_For").size().reset_index(name="count")
        rows = grouped.to_dict(orient="records")
        rows.sort(key=lambda r: r.get("Rank_Applied_For", ""))
        return rows

    def get_csv_stats(self):
        latest = self.get_latest_status_per_candidate()
        total_rows = 0
        try:
            rows = self._request(
                "GET",
                "/rest/v1/candidate_events",
                params={"select": "id"},
            )
            total_rows = len(rows)
        except Exception:
            total_rows = 0
        return {
            "master_csv_exists": False,
            "master_csv_rows": total_rows,
            "latest_candidates": len(latest),
            "rank_breakdown": self.get_rank_counts(),
        }


def can_enable_supabase_repo():
    return bool(os.getenv("SUPABASE_URL") and resolve_supabase_api_key())
