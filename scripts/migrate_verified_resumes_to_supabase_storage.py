#!/usr/bin/env python3
import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests


def env(name, default=""):
    return os.getenv(name, default).strip()


def safe_segment(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def extract_candidate_id(filename):
    match = re.search(r"_(\d+)(?:_|\.)", filename)
    return match.group(1) if match else ""


def upload_pdf(supabase_url, api_key, bucket, object_path, file_path):
    endpoint = f"{supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
    with open(file_path, "rb") as fh:
        content = fh.read()
    resp = requests.post(
        endpoint,
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/pdf",
            "x-upsert": "true",
        },
        data=content,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"upload {file_path.name} failed ({resp.status_code}): {resp.text[:300]}")
    return f"storage://{bucket}/{object_path}"


def update_candidate_events_resume_url(supabase_url, api_key, filename, rank, storage_url):
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/candidate_events"
    resp = requests.patch(
        endpoint,
        params={"filename": f"eq.{filename}", "rank_applied_for": f"eq.{rank}"},
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json={"resume_url": storage_url},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"update candidate_events failed ({resp.status_code}): {resp.text[:300]}")


def update_master_csv(master_csv_path, filename, rank, storage_url):
    if not master_csv_path.exists():
        return 0
    df = pd.read_csv(master_csv_path, keep_default_na=False)
    if "Filename" not in df.columns or "Rank_Applied_For" not in df.columns or "Resume_URL" not in df.columns:
        return 0
    mask = (df["Filename"].astype(str) == filename) & (df["Rank_Applied_For"].astype(str) == rank)
    updated = int(mask.sum())
    if updated > 0:
        df.loc[mask, "Resume_URL"] = storage_url
        df.to_csv(master_csv_path, index=False)
    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill Verified_Resumes PDFs into Supabase Storage and update resume URLs.")
    parser.add_argument("--verified-folder", required=True, help="Path to Verified_Resumes root")
    parser.add_argument("--bucket", default=env("SUPABASE_RESUME_BUCKET", "resumes"), help="Supabase Storage bucket name")
    parser.add_argument("--apply", action="store_true", help="Perform writes (default is dry-run)")
    args = parser.parse_args()

    supabase_url = env("SUPABASE_URL")
    api_key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not api_key:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY) are required.", file=sys.stderr)
        return 1

    root = Path(args.verified_folder).expanduser().resolve()
    if not root.exists():
        print(f"Verified folder not found: {root}", file=sys.stderr)
        return 1

    master_csv = root / "verified_resumes.csv"
    planned = []
    for rank_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        rank = rank_dir.name
        for pdf in sorted(rank_dir.glob("*.pdf")):
            candidate_id = extract_candidate_id(pdf.name) or "unknown"
            object_path = f"{safe_segment(rank)}/{safe_segment(candidate_id)}/{safe_segment(pdf.name)}"
            storage_url = f"storage://{args.bucket}/{object_path}"
            planned.append((rank, pdf, object_path, storage_url))

    if not planned:
        print("No PDFs found under rank subfolders.")
        return 0

    print(f"Found {len(planned)} PDF(s) under {root}")
    print("Mode:", "APPLY" if args.apply else "DRY-RUN")

    uploaded = 0
    db_updated = 0
    csv_updated = 0
    errors = []
    for rank, pdf, object_path, storage_url in planned:
        try:
            if args.apply:
                uploaded_url = upload_pdf(supabase_url, api_key, args.bucket, object_path, pdf)
                update_candidate_events_resume_url(supabase_url, api_key, pdf.name, rank, uploaded_url)
                csv_updated += update_master_csv(master_csv, pdf.name, rank, uploaded_url)
                uploaded += 1
                db_updated += 1
            else:
                print(f"[PLAN] {pdf.name} -> {storage_url}")
        except Exception as exc:
            errors.append(f"{pdf.name}: {exc}")

    print({
        "success": len(errors) == 0,
        "mode": "apply" if args.apply else "dry_run",
        "files_found": len(planned),
        "uploaded": uploaded,
        "db_updated": db_updated,
        "csv_rows_updated": csv_updated,
        "errors": errors[:20],
    })
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

