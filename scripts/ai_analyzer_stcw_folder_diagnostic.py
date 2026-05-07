import argparse
import configparser
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_analyzer import AIResumeAnalyzer, AdvancedPDFProcessor, ConfigManager


def _build_analyzer():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "config.ini")
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    analyzer.config = ConfigManager(parser)
    analyzer.pdf_processor = AdvancedPDFProcessor()
    return analyzer


def _source_type(name: str) -> str:
    return "email" if name.startswith("EMAIL_") else "non_email"


def _stcw_snippets(text: str):
    patterns = [
        r"\bpst\b",
        r"\bpersonal survival techniques\b",
        r"\bfpff\b",
        r"\bfire prevention and fire fighting\b",
        r"\befa\b",
        r"\belementary first aid\b",
        r"\bpssr\b",
        r"\bpersonal safety(?:\s*&|\s+and)?\s+social responsibilities?\b",
    ]
    snippets = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 220)
            snippets.append(text[start:end].replace("\n", " ").strip())
    deduped = []
    seen = set()
    for snippet in snippets:
        if snippet not in seen:
            deduped.append(snippet)
            seen.add(snippet)
    return deduped[:12]


def _build_row(analyzer, pdf_path: Path):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    stcw_fact = analyzer._extract_stcw_fact_from_text(text)
    return {
        "filename": pdf_path.name,
        "source_type": _source_type(pdf_path.name),
        "text_chars": len(text),
        "text_words": len(text.split()),
        "stcw_fact": stcw_fact,
        "label_snippets": _stcw_snippets(text),
    }


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


def main():
    parser = argparse.ArgumentParser(description="Folder-level STCW diagnostic for AI analyzer real PDFs.")
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
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
