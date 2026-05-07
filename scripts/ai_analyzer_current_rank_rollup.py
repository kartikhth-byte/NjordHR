import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_analyzer_current_rank_folder_diagnostic import _build_analyzer, _build_row


DEFAULT_RESUME_ROOT = Path("/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes")
DEFAULT_OUTPUT = REPO_ROOT / "AI_Search_Results" / "ai_analyzer_current_rank_rollup_2026-05-07.json"


def _summarize(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "rank_status_counts": Counter(),
        "source_label_counts": Counter(),
        "canonical_rank_counts": Counter(),
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        rank_fact = row.get("rank_fact") or {}
        bucket["rank_status_counts"][str(rank_fact.get("status") or "MISSING")] += 1
        if rank_fact.get("source_label"):
            bucket["source_label_counts"][str(rank_fact["source_label"])] += 1
        if rank_fact.get("canonical_id"):
            bucket["canonical_rank_counts"][str(rank_fact["canonical_id"])] += 1
    return {
        source: {
            "count": payload["count"],
            "rank_status_counts": dict(payload["rank_status_counts"]),
            "source_label_counts": dict(payload["source_label_counts"]),
            "canonical_rank_counts": dict(payload["canonical_rank_counts"]),
        }
        for source, payload in by_source.items()
    }


def _compact_examples(rows, limit=5):
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            0 if (row.get("rank_fact") or {}).get("status") != "PARSED" else 1,
            row["filename"],
        ),
    )
    examples = []
    for row in sorted_rows[:limit]:
        rank_fact = row.get("rank_fact") or {}
        examples.append({
            "filename": row["filename"],
            "rank_status": rank_fact.get("status"),
            "rank_raw": rank_fact.get("raw_rank"),
            "canonical_id": rank_fact.get("canonical_id"),
            "source_label": rank_fact.get("source_label"),
            "label_snippets": row.get("label_snippets") or [],
        })
    return examples


def main():
    parser = argparse.ArgumentParser(description="Horizontal current-rank rollup across live rank folders.")
    parser.add_argument("--resume-root", default=str(DEFAULT_RESUME_ROOT), help="Root resume directory")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    args = parser.parse_args()

    resume_root = Path(args.resume_root)
    analyzer = _build_analyzer()

    folders = []
    all_rows = []
    for rank_folder in sorted(p for p in resume_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        pdf_paths = sorted(rank_folder.glob("*.pdf"))
        if not pdf_paths:
            continue
        rows = [_build_row(analyzer, pdf_path) for pdf_path in pdf_paths]
        folders.append({
            "rank_folder": rank_folder.name,
            "pdf_count": len(rows),
            "summary_by_source_type": _summarize(rows),
            "email_examples": _compact_examples([row for row in rows if row["source_type"] == "email"]),
        })
        all_rows.extend(rows)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "resume_root": str(resume_root),
        "scope_note": "Horizontal current-rank validation pass across live rank folders.",
        "overall_summary_by_source_type": _summarize(all_rows),
        "folders": folders,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
