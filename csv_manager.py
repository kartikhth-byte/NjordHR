# csv_manager.py - Event Log CSV Manager

import os
from datetime import datetime
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

    def __init__(self, base_folder='Verified_Resumes', server_url='http://127.0.0.1:5000'):
        self.base_folder = base_folder
        self.server_url = server_url
        self.master_csv = os.path.join(base_folder, 'verified_resumes.csv')
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
        df.to_csv(self.master_csv, index=False)

    def log_event(self, candidate_id, filename, event_type, status='New', notes='',
                  rank_applied_for='', search_ship_type='', ai_prompt='',
                  ai_reason='', extracted_data=None):
        """Append one event row to the single master CSV."""
        extracted_data = extracted_data or {}
        timestamp = datetime.utcnow().isoformat() + 'Z'
        resume_url = f"{self.server_url}/get_resume/{rank_applied_for}/{filename}"

        new_row = {
            'Candidate_ID': str(candidate_id),
            'Filename': filename,
            'Resume_URL': resume_url,
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
            df = self._load_master_df()
            df = pd.concat([df, pd.DataFrame([new_row], columns=self.COLUMNS)], ignore_index=True)
            self._save_master_df(df)
            return True
        except Exception as e:
            print(f"[CSV ERROR] Failed to append event row: {e}")
            return False

    def get_latest_status_per_candidate(self, rank_name=''):
        """Return latest event row per candidate, optionally filtered by rank."""
        df = self._load_master_df()
        if df.empty:
            return df

        if rank_name:
            df = df[df['Rank_Applied_For'] == rank_name]
            if df.empty:
                return df

        df_sorted = df.sort_values('Date_Added')
        latest = df_sorted.groupby('Candidate_ID', as_index=False).tail(1)
        return latest.sort_values('Date_Added', ascending=False).reset_index(drop=True)

    def get_candidate_history(self, candidate_id):
        df = self._load_master_df()
        if df.empty:
            return []
        history = df[df['Candidate_ID'].astype(str) == str(candidate_id)].sort_values('Date_Added')
        return history.to_dict(orient='records')

    def get_latest_candidate_row(self, candidate_id):
        """Get latest event row for a candidate as dict."""
        df = self._load_master_df()
        if df.empty:
            return None
        candidate_rows = df[df['Candidate_ID'].astype(str) == str(candidate_id)]
        if candidate_rows.empty:
            return None
        latest = candidate_rows.sort_values('Date_Added').tail(1)
        return latest.iloc[0].to_dict()

    def log_status_change(self, candidate_id, status):
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
            }
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
            }
        )

    def update_last_row_notes(self, candidate_id, new_notes):
        """Update notes on the most recent event row for a candidate."""
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
        latest = self.get_latest_status_per_candidate()
        return {
            'master_csv_exists': os.path.exists(self.master_csv),
            'master_csv_rows': len(self._load_master_df()),
            'latest_candidates': len(latest),
            'rank_breakdown': self.get_rank_counts()
        }
