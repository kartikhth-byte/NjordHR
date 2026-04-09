import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def build_report(audit_csv_path: Path) -> dict:
    counts = Counter()
    session_counts = Counter()
    total_rows = 0

    if not audit_csv_path.exists():
        return {
            "audit_csv": str(audit_csv_path),
            "exists": False,
            "total_rows": 0,
            "facts_version_counts": {},
            "sessions_with_version_counts": {},
            "note": "AI search audit CSV not found.",
        }

    with audit_csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total_rows += 1
            version = str(row.get("Facts_Version") or "").strip() or "<missing>"
            counts[version] += 1

            session_id = str(row.get("Search_Session_ID") or "").strip()
            if session_id:
                session_counts[(session_id, version)] += 1

    versioned_sessions = Counter()
    for (session_id, version), _count in session_counts.items():
        versioned_sessions[version] += 1

    note = (
        "Historical rows created before Facts_Version audit persistence will appear as <missing>. "
        "New searches after the audit-storage change should populate explicit facts_version values."
    )

    return {
        "audit_csv": str(audit_csv_path),
        "exists": True,
        "total_rows": total_rows,
        "facts_version_counts": dict(sorted(counts.items())),
        "sessions_with_version_counts": dict(sorted(versioned_sessions.items())),
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize AI search audit facts_version coverage.")
    parser.add_argument(
        "--audit-csv",
        default="Verified_Resumes/ai_search_audit.csv",
        help="Path to the AI search audit CSV.",
    )
    parser.add_argument(
        "--output",
        default="AI_Search_Results/facts_version_audit_progress_current.json",
        help="Path to write the JSON report.",
    )
    args = parser.parse_args()

    audit_csv_path = Path(args.audit_csv)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = build_report(audit_csv_path)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
