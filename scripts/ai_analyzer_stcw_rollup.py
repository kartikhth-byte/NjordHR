import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_analyzer_stcw_folder_diagnostic import _build_analyzer, _build_row


DEFAULT_RESUME_ROOT = Path("/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes")
DEFAULT_OUTPUT = REPO_ROOT / "AI_Search_Results" / "ai_analyzer_stcw_rollup_2026-05-07.json"


def _summarize(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "status_counts": Counter(),
        "outcome_counts": Counter(),
        "certificate_state_counts": {
            "pst": Counter(),
            "fpff": Counter(),
            "efa": Counter(),
            "pssr": Counter(),
        },
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        stcw_fact = row.get("stcw_fact") or {}
        bucket["status_counts"][str(stcw_fact.get("status") or "MISSING")] += 1
        bucket["outcome_counts"][str(stcw_fact.get("stcw_basic_all_valid"))] += 1
        certs = stcw_fact.get("certificates") or {}
        for cert_id in ["pst", "fpff", "efa", "pssr"]:
            bucket["certificate_state_counts"][cert_id][str(certs.get(cert_id) or "unknown")] += 1

    return {
        source: {
            "count": payload["count"],
            "status_counts": dict(payload["status_counts"]),
            "outcome_counts": dict(payload["outcome_counts"]),
            "certificate_state_counts": {
                cert_id: dict(counter)
                for cert_id, counter in payload["certificate_state_counts"].items()
            },
        }
        for source, payload in by_source.items()
    }


def _compact_examples(rows, limit=5):
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            0 if ((row.get("stcw_fact") or {}).get("stcw_basic_all_valid") is None) else 1,
            row["filename"],
        ),
    )
    examples = []
    for row in sorted_rows[:limit]:
        stcw_fact = row.get("stcw_fact") or {}
        examples.append({
            "filename": row["filename"],
            "status": stcw_fact.get("status"),
            "stcw_basic_all_valid": stcw_fact.get("stcw_basic_all_valid"),
            "certificates": stcw_fact.get("certificates") or {},
            "label_snippets": row.get("label_snippets") or [],
        })
    return examples


def main():
    parser = argparse.ArgumentParser(description="Horizontal STCW rollup across live rank folders.")
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
        "scope_note": "Horizontal STCW validation pass across live rank folders.",
        "overall_summary_by_source_type": _summarize(all_rows),
        "folders": folders,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
