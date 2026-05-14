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


def _serialize_row(row):
    return {
        "row_index": row.get("row_index"),
        "rank_normalized": row.get("rank_normalized"),
        "sign_in_date": row.get("sign_in_date"),
        "sign_out_date": row.get("sign_out_date"),
        "vessel_types": row.get("vessel_types") or [],
        "snippet": row.get("snippet"),
    }


def _build_row(analyzer, pdf_path: Path, vessel_type: str, months: int, contracts: int):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    fact = analyzer._extract_seajobs_experience_rows(text, original_path=pdf_path)
    candidate_facts = {
        "experience": {"service_rows": fact.get("rows") or []},
        "fact_meta": {
            "experience.service_rows": {
                "status": fact.get("status", "MISSING"),
                "confidence": fact.get("confidence"),
            }
        },
    }
    rule_result = analyzer._evaluate_recent_contract_vessel_experience_rule(
        candidate_facts,
        {
            "vessel_type": vessel_type,
            "min_months": months,
            "lookback_contracts": contracts,
        },
    )
    return {
        "filename": pdf_path.name,
        "status": fact.get("status"),
        "row_count": len(fact.get("rows") or []),
        "rule_decision": rule_result.get("decision"),
        "rule_reason_code": rule_result.get("reason_code"),
        "rule_actual_value": rule_result.get("actual_value"),
        "rows": [_serialize_row(row) for row in (fact.get("rows") or [])[:8]],
    }


def _summary(rows):
    return {
        "status_counts": dict(Counter(row.get("status") or "MISSING" for row in rows)),
        "rule_decision_counts": dict(Counter(row.get("rule_decision") or "MISSING" for row in rows)),
        "rule_reason_counts": dict(Counter(row.get("rule_reason_code") or "MISSING" for row in rows)),
    }


def main():
    parser = argparse.ArgumentParser(description="Folder diagnostic for recent-contract vessel experience.")
    parser.add_argument("--folder", required=True, help="Absolute path to a rank folder")
    parser.add_argument("--output", required=True, help="Path to output JSON")
    parser.add_argument("--vessel-type", default="container", help="Canonical vessel type to evaluate")
    parser.add_argument("--months", type=int, default=12, help="Minimum qualifying months")
    parser.add_argument("--contracts", type=int, default=3, help="Recent contract lookback window")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise SystemExit(f"Folder not found: {folder}")

    analyzer = _build_analyzer()
    rows = [
        _build_row(analyzer, pdf_path, args.vessel_type, args.months, args.contracts)
        for pdf_path in sorted(folder.glob("*.pdf"))
    ]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "folder": str(folder),
        "constraint": {
            "vessel_type": args.vessel_type,
            "min_months": args.months,
            "lookback_contracts": args.contracts,
        },
        "summary": _summary(rows),
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
