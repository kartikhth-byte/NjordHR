import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from ai_analyzer_dob_folder_diagnostic import _build_analyzer, _build_row


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESUME_ROOT = Path("/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes")
DEFAULT_OUTPUT = REPO_ROOT / "AI_Search_Results" / "ai_analyzer_dob_rollup_2026-05-07.json"


def _live_rank_folders(resume_root: Path):
    return [
        folder
        for folder in sorted(resume_root.iterdir())
        if folder.is_dir() and not folder.name.startswith("_")
    ]


def _folder_summary(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "dob_status_counts": Counter(),
        "dob_present_count": 0,
        "age_present_count": 0,
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        status = str((row.get("dob_fact") or {}).get("status") or "MISSING")
        bucket["dob_status_counts"][status] += 1
        if (row.get("dob_fact") or {}).get("dob") is not None:
            bucket["dob_present_count"] += 1
        if (row.get("resolved_age") or {}).get("age") is not None:
            bucket["age_present_count"] += 1
    return {
        source: {
            "count": payload["count"],
            "dob_status_counts": dict(payload["dob_status_counts"]),
            "dob_present_count": payload["dob_present_count"],
            "age_present_count": payload["age_present_count"],
        }
        for source, payload in by_source.items()
    }


def _collect_email_examples(rows, limit=5):
    email_rows = [row for row in rows if row["source_type"] == "email"]
    email_rows.sort(
        key=lambda row: (
            0 if (row.get("dob_fact") or {}).get("dob") is None else 1,
            str((row.get("dob_fact") or {}).get("status") or ""),
            row["filename"].lower(),
        )
    )
    return [
        {
            "filename": row["filename"],
            "dob_status": (row.get("dob_fact") or {}).get("status"),
            "dob": (row.get("dob_fact") or {}).get("dob"),
            "label_snippets": row.get("label_snippets", [])[:2],
        }
        for row in email_rows[:limit]
    ]


def main():
    parser = argparse.ArgumentParser(description="Horizontal DOB/age rollup across live rank folders.")
    parser.add_argument("--resume-root", default=str(DEFAULT_RESUME_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    resume_root = Path(args.resume_root)
    if not resume_root.exists():
        raise SystemExit(f"Resume root not found: {resume_root}")

    analyzer = _build_analyzer()
    folder_rows = []
    overall_rows = []

    for folder in _live_rank_folders(resume_root):
        pdfs = sorted(folder.glob("*.pdf"))
        if not pdfs:
            continue
        rows = [_build_row(analyzer, pdf_path) for pdf_path in pdfs]
        overall_rows.extend(rows)
        folder_rows.append(
            {
                "rank_folder": folder.name,
                "pdf_count": len(rows),
                "summary_by_source_type": _folder_summary(rows),
                "email_examples": _collect_email_examples(rows),
            }
        )

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "resume_root": str(resume_root),
        "scope_note": (
            "Horizontal DOB/age validation pass after the targeted 2nd_Officer DOB parser fix. "
            "This covers live rank folders only and splits counts by EMAIL_ filename pattern versus non-email PDFs."
        ),
        "overall_summary_by_source_type": _folder_summary(overall_rows),
        "folders": folder_rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
