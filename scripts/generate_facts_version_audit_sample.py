import json
from pathlib import Path

from csv_manager import CSVManager
from scripts.ai_search_facts_version_report import build_report


def main() -> None:
    sample_root = Path("AI_Search_Results") / "facts_version_sample_run"
    sample_root.mkdir(parents=True, exist_ok=True)

    manager = CSVManager(base_folder=str(sample_root))
    manager.log_ai_search_audit(
        search_session_id="sample-search-1",
        candidate_id="sample-1001",
        filename="Chief_Officer_1001.pdf",
        facts_version="1.1",
        rank_applied_for="Chief_Officer",
        ai_prompt="having valid US visa",
        applied_ship_type_filter="Bulk Carrier",
        experienced_ship_type_filter="Bulk Carrier",
        hard_filter_decision="UNKNOWN",
        reason_codes="VERSION_MISMATCH_UNKNOWN",
        reason_messages="Candidate facts remain on v1.1 during sample migration evidence run.",
        llm_reached=False,
        result_bucket="needs_review",
    )
    manager.log_ai_search_audit(
        search_session_id="sample-search-1",
        candidate_id="sample-1002",
        filename="Chief_Officer_1002.pdf",
        facts_version="2.0",
        rank_applied_for="Chief_Officer",
        ai_prompt="having valid US visa",
        applied_ship_type_filter="Bulk Carrier",
        experienced_ship_type_filter="Bulk Carrier",
        hard_filter_decision="PASS",
        reason_codes="US_VISA_VALID",
        reason_messages="Candidate facts are on v2.0 during sample migration evidence run.",
        llm_reached=True,
        result_bucket="verified_match",
    )

    audit_csv = sample_root / "ai_search_audit.csv"
    report = build_report(audit_csv)
    report["sample_run"] = {
        "purpose": "Demonstrate that post-change audit rows persist explicit Facts_Version values.",
        "rows_written": 2,
    }

    report_path = Path("AI_Search_Results") / "facts_version_audit_sample_current.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
