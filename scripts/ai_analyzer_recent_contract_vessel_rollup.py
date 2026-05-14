import argparse
import configparser
import json
import sys
from collections import Counter
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


def _evaluate_pdf(analyzer, pdf_path: Path, vessel_type: str, months: int, contracts: int):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    fact = analyzer._extract_seajobs_experience_rows(text, original_path=pdf_path)
    result = analyzer._evaluate_recent_contract_vessel_experience_rule(
        {
            "experience": {"service_rows": fact.get("rows") or []},
            "fact_meta": {
                "experience.service_rows": {
                    "status": fact.get("status", "MISSING"),
                    "confidence": fact.get("confidence"),
                }
            },
        },
        {
            "vessel_type": vessel_type,
            "min_months": months,
            "lookback_contracts": contracts,
        },
    )
    return fact.get("status", "MISSING"), result.get("decision", "MISSING"), result.get("reason_code", "MISSING")


def main():
    parser = argparse.ArgumentParser(description="Roll up recent-contract vessel diagnostics across rank folders.")
    parser.add_argument("--root", required=True, help="Root folder containing rank directories")
    parser.add_argument("--output", required=True, help="Path to output JSON")
    parser.add_argument("--vessel-type", default="container", help="Canonical vessel type to evaluate")
    parser.add_argument("--months", type=int, default=12, help="Minimum qualifying months")
    parser.add_argument("--contracts", type=int, default=3, help="Recent contract lookback window")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Folder not found: {root}")

    analyzer = _build_analyzer()
    folders = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        pdf_paths = sorted(folder.glob("*.pdf"))
        if not pdf_paths:
            continue
        status_counts = Counter()
        decision_counts = Counter()
        reason_counts = Counter()
        for pdf_path in pdf_paths:
            status, decision, reason = _evaluate_pdf(
                analyzer,
                pdf_path,
                args.vessel_type,
                args.months,
                args.contracts,
            )
            status_counts[status] += 1
            decision_counts[decision] += 1
            reason_counts[reason] += 1
        folders.append({
            "folder": folder.name,
            "pdf_count": len(pdf_paths),
            "status_counts": dict(status_counts),
            "rule_decision_counts": dict(decision_counts),
            "rule_reason_counts": dict(reason_counts),
        })

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "constraint": {
            "vessel_type": args.vessel_type,
            "min_months": args.months,
            "lookback_contracts": args.contracts,
        },
        "folders": folders,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
