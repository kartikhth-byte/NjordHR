#!/usr/bin/env python3
"""
Migrate legacy NjordHR CSV layouts into single master event-log CSV.

Legacy inputs supported:
- Verified_Resumes/<rank>/<rank>_verified.csv
- Verified_Resumes/verified_resumes.csv (legacy column layout)
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from csv_manager import CSVManager


LEGACY_COLUMNS = {
    'Filename', 'Resume_URL', 'Date_Added', 'Name', 'Present_Rank',
    'Email', 'Country', 'Mobile_No', 'AI_Match_Reason'
}


def _extract_candidate_id(filename):
    name = str(filename)
    match = re.search(r'_(\d+)\.pdf$', name, re.IGNORECASE)
    if match:
        return match.group(1)

    stem = os.path.splitext(os.path.basename(name))[0]
    parts = stem.split('_')
    for part in parts:
        if part.isdigit() and len(part) >= 3:
            return part

    fallback = re.search(r'(\d{3,})', stem)
    return fallback.group(1) if fallback else ''


def _normalize_row(row, rank_name, server_url):
    filename = str(row.get('Filename', '')).strip()
    candidate_id = str(row.get('Candidate_ID', '')).strip() or _extract_candidate_id(filename)
    if not candidate_id or not filename:
        return None

    rank_applied_for = str(row.get('Rank_Applied_For', '')).strip() or rank_name
    date_added = str(row.get('Date_Added', '')).strip() or (datetime.utcnow().isoformat() + 'Z')
    resume_url = str(row.get('Resume_URL', '')).strip() or f"{server_url}/get_resume/{rank_applied_for}/{filename}"

    return {
        'Candidate_ID': candidate_id,
        'Filename': filename,
        'Resume_URL': resume_url,
        'Date_Added': date_added,
        'Event_Type': str(row.get('Event_Type', '')).strip() or 'initial_verification',
        'Status': str(row.get('Status', '')).strip() or 'New',
        'Notes': str(row.get('Notes', '')).strip(),
        'Rank_Applied_For': rank_applied_for,
        'Search_Ship_Type': str(row.get('Search_Ship_Type', '')).strip(),
        'AI_Search_Prompt': str(row.get('AI_Search_Prompt', '')).strip(),
        'AI_Match_Reason': str(row.get('AI_Match_Reason', '')).strip(),
        'Name': str(row.get('Name', '')).strip(),
        'Present_Rank': str(row.get('Present_Rank', '')).strip(),
        'Email': str(row.get('Email', '')).strip(),
        'Country': str(row.get('Country', '')).strip(),
        'Mobile_No': str(row.get('Mobile_No', '')).strip(),
    }


def _is_new_schema(df):
    return set(CSVManager.COLUMNS).issubset(set(df.columns))


def _is_legacy_schema(df):
    return len(LEGACY_COLUMNS.intersection(set(df.columns))) >= 5


def migrate_legacy_csvs(base_folder='Verified_Resumes', server_url='http://127.0.0.1:5000',
                        dry_run=False, create_backup=True):
    os.makedirs(base_folder, exist_ok=True)
    master_path = os.path.join(base_folder, 'verified_resumes.csv')

    existing_master = pd.DataFrame(columns=CSVManager.COLUMNS)
    source_files = []
    source_rows = 0

    if os.path.exists(master_path):
        df_master = pd.read_csv(master_path, keep_default_na=False)
        if _is_new_schema(df_master):
            existing_master = df_master[CSVManager.COLUMNS].copy()
        elif _is_legacy_schema(df_master):
            source_files.append((master_path, ''))
        else:
            raise ValueError(f"Master CSV exists but schema is unknown: {master_path}")

    for entry in os.listdir(base_folder):
        rank_dir = os.path.join(base_folder, entry)
        if not os.path.isdir(rank_dir):
            continue
        rank_csv = os.path.join(rank_dir, f"{entry}_verified.csv")
        if os.path.isfile(rank_csv):
            source_files.append((rank_csv, entry))

    migrated = []
    for path, rank_name in source_files:
        df = pd.read_csv(path, keep_default_na=False)
        source_rows += len(df)
        for _, row in df.iterrows():
            normalized = _normalize_row(row, rank_name, server_url)
            if normalized:
                migrated.append(normalized)

    if not migrated:
        return {
            'success': True,
            'dry_run': dry_run,
            'message': 'No legacy rows found to migrate.',
            'source_files': len(source_files),
            'source_rows': source_rows,
            'migrated_rows': 0,
            'added_rows': 0,
            'skipped_duplicates': 0,
            'master_path': master_path,
        }

    df_new = pd.DataFrame(migrated, columns=CSVManager.COLUMNS)

    combined = pd.concat([existing_master, df_new], ignore_index=True)
    dedupe_cols = ['Candidate_ID', 'Filename', 'Date_Added', 'Event_Type', 'Status', 'Notes']
    for col in dedupe_cols:
        combined[col] = combined[col].astype(str)
    before = len(combined)
    combined = combined.drop_duplicates(subset=dedupe_cols, keep='first')
    after = len(combined)
    skipped_duplicates = before - after
    added_rows = after - len(existing_master)

    if not dry_run:
        if create_backup and os.path.exists(master_path):
            backup_name = f"verified_resumes.pre_migration_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            backup_path = os.path.join(base_folder, backup_name)
            existing_master.to_csv(backup_path, index=False)
        combined.to_csv(master_path, index=False)

    return {
        'success': True,
        'dry_run': dry_run,
        'source_files': len(source_files),
        'source_rows': source_rows,
        'migrated_rows': len(df_new),
        'added_rows': added_rows,
        'skipped_duplicates': skipped_duplicates,
        'master_path': master_path,
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy NjordHR CSVs to event-log master CSV")
    parser.add_argument('--base-folder', default='Verified_Resumes', help='Path to Verified_Resumes folder')
    parser.add_argument('--server-url', default='http://127.0.0.1:5000', help='Server URL for resume links')
    parser.add_argument('--dry-run', action='store_true', help='Analyze migration without writing')
    parser.add_argument('--no-backup', action='store_true', help='Disable pre-migration master CSV backup')
    args = parser.parse_args()

    result = migrate_legacy_csvs(
        base_folder=args.base_folder,
        server_url=args.server_url,
        dry_run=args.dry_run,
        create_backup=not args.no_backup
    )
    print(result)


if __name__ == '__main__':
    main()
