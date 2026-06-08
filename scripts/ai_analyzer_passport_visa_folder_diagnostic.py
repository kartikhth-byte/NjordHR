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
        r"passport",
        r"\bvisa\b",
        r"\bc1\s*/\s*d\b",
        r"\bb1\s*/\s*b2\b",
        r"\bschengen\b",
        r"\bmcv\b",
    ]
    snippets = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 150)
            snippets.append(text[start:end].replace("\n", " ").strip())
    deduped = []
    seen = set()
    for snippet in snippets:
        if snippet not in seen:
            deduped.append(snippet)
            seen.add(snippet)
    return deduped[:10]


def _build_row(analyzer, pdf_path: Path):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    logistics = analyzer._extract_logistics_from_text(text)
    passport_fact = logistics.get("passport_fact") or {}
    visa_fact = logistics.get("visa_fact") or {}
    return {
        "filename": pdf_path.name,
        "source_type": _source_type(pdf_path.name),
        "text_chars": len(text),
        "text_words": len(text.split()),
        "passport_fact": passport_fact,
        "visa_fact": {
            "status": visa_fact.get("status"),
            "visa_type": visa_fact.get("visa_type"),
            "expiry_date": visa_fact.get("expiry_date"),
            "expiry_status": visa_fact.get("expiry_status"),
            "visa_records": visa_fact.get("visa_records") or [],
        },
        "resolved_logistics": {
            "passport_expiry_date": logistics.get("passport_expiry_date"),
            "passport_expiry_status": logistics.get("passport_expiry_status"),
            "passport_valid": logistics.get("passport_valid"),
            "us_visa_valid": logistics.get("us_visa_valid"),
            "us_visa_status": logistics.get("us_visa_status"),
            "us_visa_expiry_date": logistics.get("us_visa_expiry_date"),
        },
        "label_snippets": _snippet_candidates(text),
    }


def _summarize(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "passport_status_counts": Counter(),
        "visa_status_counts": Counter(),
        "passport_present_count": 0,
        "visa_record_count": 0,
        "valid_visa_count": 0,
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        passport_status = str((row.get("resolved_logistics") or {}).get("passport_expiry_status") or "MISSING")
        visa_status = str((row.get("visa_fact") or {}).get("status") or "MISSING")
        bucket["passport_status_counts"][passport_status] += 1
        bucket["visa_status_counts"][visa_status] += 1
        if (row.get("resolved_logistics") or {}).get("passport_expiry_date") is not None:
            bucket["passport_present_count"] += 1
        if (row.get("visa_fact") or {}).get("visa_records"):
            bucket["visa_record_count"] += 1
        if (row.get("resolved_logistics") or {}).get("us_visa_valid") is True:
            bucket["valid_visa_count"] += 1
    return {
        source: {
            "count": payload["count"],
            "passport_status_counts": dict(payload["passport_status_counts"]),
            "visa_status_counts": dict(payload["visa_status_counts"]),
            "passport_present_count": payload["passport_present_count"],
            "visa_record_count": payload["visa_record_count"],
            "valid_visa_count": payload["valid_visa_count"],
        }
        for source, payload in by_source.items()
    }


def main():
    parser = argparse.ArgumentParser(description="Folder-level passport/visa diagnostic for AI analyzer real PDFs.")
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
