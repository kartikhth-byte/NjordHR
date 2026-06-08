import argparse
import configparser
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_analyzer import AIResumeAnalyzer, AdvancedPDFProcessor, ConfigManager


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _build_analyzer():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "config.ini")
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    analyzer.config = ConfigManager(parser)
    analyzer.pdf_processor = AdvancedPDFProcessor()
    return analyzer


def _source_type(name: str) -> str:
    return "email" if name.startswith("EMAIL_") else "non_email"


def _snippet_candidates(text: str):
    patterns = [
        r"(?:date\s*/?\s*place\s*of\s*birth|place\s+date\s+of\s+birth|date\s+of\s+birth|dob|d\.o\.b\.?)",
        r"(?:^|\b)(age)\s*[:\-]?\s*(\d{1,3})\b",
        r"(?:^|\b)(aged)\s+(\d{1,3})\b",
    ]
    snippets = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 120)
            snippets.append(text[start:end].replace("\n", " ").strip())
    deduped = []
    seen = set()
    for snippet in snippets:
        if snippet not in seen:
            deduped.append(snippet)
            seen.add(snippet)
    return deduped[:8]


def _build_row(analyzer, pdf_path: Path):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    dob_fact = analyzer._extract_dob_fact_from_text(text)
    age_info = analyzer._resolve_candidate_age(
        [{"metadata": {"raw_text": text}}],
        original_path=None,
        text_cache={},
    )
    return {
        "filename": pdf_path.name,
        "source_type": _source_type(pdf_path.name),
        "text_chars": len(text),
        "text_words": len(text.split()),
        "dob_fact": dob_fact,
        "resolved_age": {
            "dob": age_info.get("dob"),
            "age": age_info.get("age"),
            "dob_parse_status": age_info.get("dob_parse_status"),
            "stated_age": age_info.get("stated_age"),
            "stated_age_status": age_info.get("stated_age_status"),
        },
        "label_snippets": _snippet_candidates(text),
    }


def _summarize(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "dob_status_counts": Counter(),
        "stated_age_status_counts": Counter(),
        "dob_present_count": 0,
        "age_present_count": 0,
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        dob_status = str((row.get("dob_fact") or {}).get("status") or "MISSING")
        stated_age_status = str((row.get("resolved_age") or {}).get("stated_age_status") or "MISSING")
        bucket["dob_status_counts"][dob_status] += 1
        bucket["stated_age_status_counts"][stated_age_status] += 1
        if (row.get("dob_fact") or {}).get("dob") is not None:
            bucket["dob_present_count"] += 1
        if (row.get("resolved_age") or {}).get("age") is not None:
            bucket["age_present_count"] += 1
    return {
        source: {
            "count": payload["count"],
            "dob_status_counts": dict(payload["dob_status_counts"]),
            "stated_age_status_counts": dict(payload["stated_age_status_counts"]),
            "dob_present_count": payload["dob_present_count"],
            "age_present_count": payload["age_present_count"],
        }
        for source, payload in by_source.items()
    }


def main():
    parser = argparse.ArgumentParser(description="Folder-level DOB/age diagnostic for AI analyzer real PDFs.")
    parser.add_argument("--folder", required=True, help="Absolute path to a rank folder")
    parser.add_argument("--output", required=True, help="Path to output JSON")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise SystemExit(f"Folder not found: {folder}")

    analyzer = _build_analyzer()
    rows = [_build_row(analyzer, pdf_path) for pdf_path in sorted(folder.glob("*.pdf"))]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "folder": str(folder),
        "summary_by_source_type": _summarize(rows),
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
