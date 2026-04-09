# csv_manager.py - Event Log CSV Manager

import os
import threading
from datetime import UTC, datetime
import pandas as pd


class CSVManager:
    """Manages a single master CSV as an event log."""

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
    AI_SEARCH_AUDIT_COLUMNS = [
        'Timestamp',
        'Search_Session_ID',
        'Candidate_ID',
        'Filename',
        'Facts_Version',
        'Rank_Applied_For',
        'AI_Search_Prompt',
        'Applied_Ship_Type_Filter',
        'Experienced_Ship_Type_Filter',
        'Hard_Filter_Decision',
        'Reason_Codes',
        'Reason_Messages',
        'LLM_Reached',
        'Result_Bucket',
    ]

    def __init__(self, base_folder='Verified_Resumes', server_url='http://127.0.0.1:5000'):
        self.base_folder = base_folder
        self.server_url = server_url
        self.master_csv = os.path.join(base_folder, 'verified_resumes.csv')
        self.ai_search_audit_csv = os.path.join(base_folder, 'ai_search_audit.csv')
        self._lock = threading.RLock()
        os.makedirs(base_folder, exist_ok=True)

    def _load_master_df(self):
        if os.path.exists(self.master_csv):
            df = pd.read_csv(self.master_csv, keep_default_na=False)
            for col in self.COLUMNS:
                if col not in df.columns:
                    df[col] = ''
            return df[self.COLUMNS]
        return pd.DataFrame(columns=self.COLUMNS)

    def _save_master_df(self, df):
        temp_path = f"{self.master_csv}.tmp"
        df.to_csv(temp_path, index=False, lineterminator='\n')
        os.replace(temp_path, self.master_csv)

    def _load_ai_search_audit_df(self):
        if os.path.exists(self.ai_search_audit_csv):
            df = pd.read_csv(self.ai_search_audit_csv, keep_default_na=False, dtype=str)
            for col in self.AI_SEARCH_AUDIT_COLUMNS:
                if col not in df.columns:
                    df[col] = ''
            return df[self.AI_SEARCH_AUDIT_COLUMNS]
        return pd.DataFrame(columns=self.AI_SEARCH_AUDIT_COLUMNS)

    def _save_ai_search_audit_df(self, df):
        temp_path = f"{self.ai_search_audit_csv}.tmp"
        df.to_csv(temp_path, index=False, lineterminator='\n')
        os.replace(temp_path, self.ai_search_audit_csv)

    def log_event(self, candidate_id, filename, event_type, status='New', notes='',
                  rank_applied_for='', search_ship_type='', ai_prompt='',
                  ai_reason='', extracted_data=None, resume_url='', admin_override=False):
        """Append one event row to the single master CSV."""
        extracted_data = extracted_data or {}
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        resolved_resume_url = str(resume_url or '').strip() or f"{self.server_url}/get_resume/{rank_applied_for}/{filename}"

        new_row = {
            'Candidate_ID': str(candidate_id),
            'Filename': filename,
            'Resume_URL': resolved_resume_url,
            'Date_Added': timestamp,
            'Event_Type': event_type,
            'Status': status,
            'Notes': notes,
            'Rank_Applied_For': rank_applied_for,
            'Search_Ship_Type': search_ship_type,
            'AI_Search_Prompt': ai_prompt,
            'AI_Match_Reason': ai_reason,
            'Name': extracted_data.get('name', ''),
            'Present_Rank': extracted_data.get('present_rank', ''),
            'Email': extracted_data.get('email', ''),
            'Country': extracted_data.get('country', ''),
            'Mobile_No': extracted_data.get('mobile_no', '')
        }

        try:
            with self._lock:
                df = self._load_master_df()
                df = pd.concat([df, pd.DataFrame([new_row], columns=self.COLUMNS)], ignore_index=True)
                self._save_master_df(df)
            return True
        except Exception as e:
            print(f"[CSV ERROR] Failed to append event row: {e}")
            return False

    def log_ai_search_audit(
        self,
        search_session_id,
        candidate_id,
        filename,
        facts_version='',
        rank_applied_for='',
        ai_prompt='',
        applied_ship_type_filter='',
        experienced_ship_type_filter='',
        hard_filter_decision='',
        reason_codes='',
        reason_messages='',
        llm_reached=False,
        result_bucket='',
    ):
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        new_row = {
            'Timestamp': timestamp,
            'Search_Session_ID': str(search_session_id or ''),
            'Candidate_ID': str(candidate_id or ''),
            'Filename': str(filename or ''),
            'Facts_Version': str(facts_version or ''),
            'Rank_Applied_For': str(rank_applied_for or ''),
            'AI_Search_Prompt': str(ai_prompt or ''),
            'Applied_Ship_Type_Filter': str(applied_ship_type_filter or ''),
            'Experienced_Ship_Type_Filter': str(experienced_ship_type_filter or ''),
            'Hard_Filter_Decision': str(hard_filter_decision or ''),
            'Reason_Codes': str(reason_codes or ''),
            'Reason_Messages': str(reason_messages or ''),
            'LLM_Reached': 'true' if llm_reached else 'false',
            'Result_Bucket': str(result_bucket or ''),
        }
        try:
            with self._lock:
                df = self._load_ai_search_audit_df()
                df = pd.concat([df, pd.DataFrame([new_row], columns=self.AI_SEARCH_AUDIT_COLUMNS)], ignore_index=True)
                self._save_ai_search_audit_df(df)
            return True
        except Exception as e:
            print(f"[CSV ERROR] Failed to append AI search audit row: {e}")
            return False

    def get_ai_search_audit_rows(self):
        with self._lock:
            df = self._load_ai_search_audit_df()
        if df.empty:
            return []
        return df.sort_values('Timestamp').to_dict(orient='records')

    def get_latest_status_per_candidate(self, rank_name=''):
        """Return latest event row per candidate, optionally filtered by rank."""
        with self._lock:
            df = self._load_master_df()
        if df.empty:
            return df

        df_sorted = df.sort_values('Date_Added')
        latest = df_sorted.groupby('Candidate_ID', as_index=False).tail(1)
        if rank_name:
            latest = latest[latest['Rank_Applied_For'] == rank_name]
            if latest.empty:
                return pd.DataFrame(columns=self.COLUMNS)
        return latest.sort_values('Date_Added', ascending=False).reset_index(drop=True)

    def get_candidate_history(self, candidate_id):
        with self._lock:
            df = self._load_master_df()
        if df.empty:
            return []
        history = df[df['Candidate_ID'].astype(str) == str(candidate_id)].sort_values('Date_Added')
        return history.to_dict(orient='records')

    def get_latest_candidate_row(self, candidate_id):
        """Get latest event row for a candidate as dict."""
        with self._lock:
            df = self._load_master_df()
        if df.empty:
            return None
        candidate_rows = df[df['Candidate_ID'].astype(str) == str(candidate_id)]
        if candidate_rows.empty:
            return None
        latest = candidate_rows.sort_values('Date_Added').tail(1)
        return latest.iloc[0].to_dict()

    def log_status_change(self, candidate_id, status, admin_override=False):
        """Log a status_change event using latest known candidate fields."""
        latest = self.get_latest_candidate_row(candidate_id)
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
            },
            resume_url=latest.get('Resume_URL', '')
        )

    def log_note_added(self, candidate_id, notes):
        """Log a note_added event using latest known candidate fields."""
        latest = self.get_latest_candidate_row(candidate_id)
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
            },
            resume_url=latest.get('Resume_URL', '')
        )

    def update_last_row_notes(self, candidate_id, new_notes):
        """Update notes on the most recent event row for a candidate."""
        with self._lock:
            df = self._load_master_df()
            if df.empty:
                return False

            candidate_rows = df[df['Candidate_ID'].astype(str) == str(candidate_id)]
            if candidate_rows.empty:
                return False

            last_idx = candidate_rows.sort_values('Date_Added').index[-1]
            df.at[last_idx, 'Notes'] = new_notes
            self._save_master_df(df)
            return True

    def get_rank_counts(self):
        """Return counts of latest candidate rows grouped by rank."""
        latest = self.get_latest_status_per_candidate()
        if latest.empty:
            return []

        counts = latest.groupby('Rank_Applied_For').size().reset_index(name='count')
        rows = counts.to_dict(orient='records')
        rows.sort(key=lambda r: r['Rank_Applied_For'])
        return rows

    def get_csv_stats(self):
        with self._lock:
            full_df = self._load_master_df()
            latest = self.get_latest_status_per_candidate()
            return {
                'master_csv_exists': os.path.exists(self.master_csv),
                'master_csv_rows': len(full_df),
                'latest_candidates': len(latest),
                'rank_breakdown': self.get_rank_counts()
            }
