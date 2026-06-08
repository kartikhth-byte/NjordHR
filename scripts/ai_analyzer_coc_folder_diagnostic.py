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


def _coc_snippets(text: str):
    patterns = [
        r"\bc\s*\.?\s*o\s*\.?\s*c\b",
        r"\bcertificate\s+of\s+competency\b",
        r"\bhighest\s+license\s+held\b",
        r"\bchief\s+engineer\b",
        r"\b2nd\s+engineer\b",
        r"\b3rd\s+engineer\b",
        r"\b4th\s+engineer\b",
        r"\bmeo\s+class\s+i\b",
        r"\bmeo\s+class\s+ii\b",
        r"\bmeo\s+class\s+iv\b",
        r"\bmaster\b",
        r"\bchief\s+officer\b",
        r"\b2nd\s+officer\b",
        r"\b3rd\s+officer\b",
    ]
    snippets = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 240)
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
    coc_fact = analyzer._extract_coc_fact_from_text(text)
    return {
        "filename": pdf_path.name,
        "source_type": _source_type(pdf_path.name),
        "text_chars": len(text),
        "text_words": len(text.split()),
        "coc_fact": coc_fact,
        "label_snippets": _coc_snippets(text),
    }


def _summarize(rows):
    by_source = defaultdict(lambda: {
        "count": 0,
        "status_counts": Counter(),
        "expiry_status_counts": Counter(),
        "grade_counts": Counter(),
    })
    for row in rows:
        bucket = by_source[row["source_type"]]
        bucket["count"] += 1
        coc_fact = row.get("coc_fact") or {}
        bucket["status_counts"][str(coc_fact.get("status") or "MISSING")] += 1
        bucket["expiry_status_counts"][str(coc_fact.get("expiry_status") or "MISSING")] += 1
        bucket["grade_counts"][str(coc_fact.get("grade") or "None")] += 1

    return {
        source: {
            "count": payload["count"],
            "status_counts": dict(payload["status_counts"]),
            "expiry_status_counts": dict(payload["expiry_status_counts"]),
            "grade_counts": dict(payload["grade_counts"]),
        }
        for source, payload in by_source.items()
    }


def main():
    parser = argparse.ArgumentParser(description="Folder-level COC diagnostic for AI analyzer real PDFs.")
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
