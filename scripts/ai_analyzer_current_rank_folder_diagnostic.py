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


def _rank_snippets(text: str):
    patterns = [
        r"present\s+rank",
        r"applied\s+for\s+rank",
        r"post\s+applied\s+for",
        r"applying\s+for",
        r"application\s+for\s+position\s+as",
        r"\brank\s*[:\-]",
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
    return deduped[:10]


def _build_row(analyzer, pdf_path: Path):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    rank_fact = analyzer._extract_rank_fact_from_text(text)
    return {
        "filename": pdf_path.name,
        "source_type": _source_type(pdf_path.name),
        "text_chars": len(text),
        "text_words": len(text.split()),
        "rank_fact": rank_fact,
        "label_snippets": _rank_snippets(text),
    }


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


def main():
    parser = argparse.ArgumentParser(description="Folder-level current-rank diagnostic for AI analyzer real PDFs.")
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
