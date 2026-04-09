import argparse
import json
import os
from pathlib import Path

from ai_analyzer import Analyzer, FileRegistry
from csv_manager import CSVManager
from scripts.ai_search_facts_version_report import build_report


def _log_audit_rows(csv_manager: CSVManager, audit_rows, rank_folder, prompt, search_session_id):
    for row in audit_rows or []:
        reasons = row.get("hard_filter_reasons") or []
        reason_codes = ";".join(
            str(reason.get("reason_code", "")).strip()
            for reason in reasons
            if str(reason.get("reason_code", "")).strip()
        )
        reason_messages = "; ".join(
            str(reason.get("message", "")).strip()
            for reason in reasons
            if str(reason.get("message", "")).strip()
        )
        csv_manager.log_ai_search_audit(
            search_session_id=search_session_id,
            candidate_id=str(row.get("candidate_id", "")).strip(),
            filename=str(row.get("filename", "")).strip(),
            facts_version=str(row.get("facts_version", "")).strip(),
            rank_applied_for=rank_folder,
            ai_prompt=prompt,
            hard_filter_decision=str(row.get("hard_filter_decision", "")).strip(),
            reason_codes=reason_codes,
            reason_messages=reason_messages,
            llm_reached=bool(row.get("llm_reached", False)),
            result_bucket=str(row.get("result_bucket", "")).strip(),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one real hard-constraint search and refresh facts-version audit evidence.")
    parser.add_argument("--rank-folder", default="Verified_Resumes/Chief_Officer")
    parser.add_argument("--prompt", default="having valid US visa")
    parser.add_argument("--output", default="AI_Search_Results/facts_version_audit_progress_current.json")
    args = parser.parse_args()

    rank_folder = Path(args.rank_folder)
    if not rank_folder.exists():
        raise SystemExit(f"Rank folder not found: {rank_folder}")

    previous_config_path = os.environ.get("NJORDHR_CONFIG_PATH")
    temp_config_path = Path("AI_Search_Results") / "facts_version_real_search_config.ini"
    temp_registry_path = Path("AI_Search_Results") / "facts_version_real_search_registry.db"

    try:
        import configparser

        config = configparser.ConfigParser()
        source_path = previous_config_path or "config.ini"
        config.read(source_path)
        if not config.has_section("Settings"):
            config.add_section("Settings")
        if not config.has_section("Advanced"):
            config.add_section("Advanced")
        config.set("Settings", "Default_Download_Folder", str(rank_folder.parent.resolve()))
        config.set("Advanced", "registry_db_path", str(temp_registry_path.resolve()))
        with temp_config_path.open("w", encoding="utf-8") as handle:
            config.write(handle)

        registry = FileRegistry(str(temp_registry_path.resolve()))
        for pdf_path in sorted(rank_folder.glob("*.pdf")):
            abs_pdf_path = str(pdf_path.resolve())
            registry.upsert_file_record(
                abs_pdf_path,
                pdf_path.stat().st_mtime,
                registry.generate_resume_id(abs_pdf_path),
            )

        os.environ["NJORDHR_CONFIG_PATH"] = str(temp_config_path)

        analyzer = Analyzer("")
        Analyzer._instance._ingest_folder = lambda *_args, **_kwargs: iter([])
        Analyzer._instance._reason_with_llm = lambda *_args, **_kwargs: {
            "is_match": False,
            "reason": "LLM reasoning disabled for migration audit evidence run.",
            "confidence": 0.0,
        }
        Analyzer._instance.LLM_RATE_LIMIT_SLEEP_SECONDS = 0
        complete_event = None
        for event in analyzer.run_analysis_stream(str(rank_folder), args.prompt):
            if event.get("type") == "error":
                raise SystemExit(event.get("message") or "Real search failed.")
            if event.get("type") == "complete":
                complete_event = event

        if not complete_event:
            raise SystemExit("Analyzer did not emit a complete event.")

        csv_manager = CSVManager(base_folder="Verified_Resumes")
        search_session_id = "real-search-facts-version-sample"
        _log_audit_rows(
            csv_manager,
            complete_event.get("hard_filter_audit", []),
            rank_folder.name,
            args.prompt,
            search_session_id,
        )

        report = build_report(Path("Verified_Resumes") / "ai_search_audit.csv")
        report["latest_real_search_sample"] = {
            "rank_folder": rank_folder.name,
            "prompt": args.prompt,
            "search_session_id": search_session_id,
            "scanned": ((complete_event.get("hard_filter_summary") or {}).get("scanned")),
            "passed": ((complete_event.get("hard_filter_summary") or {}).get("passed")),
            "failed": ((complete_event.get("hard_filter_summary") or {}).get("failed")),
            "unknown": ((complete_event.get("hard_filter_summary") or {}).get("unknown")),
        }
        output_path = Path(args.output)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {output_path}")
    finally:
        if previous_config_path is None:
            os.environ.pop("NJORDHR_CONFIG_PATH", None)
        else:
            os.environ["NJORDHR_CONFIG_PATH"] = previous_config_path


if __name__ == "__main__":
    main()
