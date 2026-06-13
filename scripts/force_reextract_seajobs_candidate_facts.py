"""Force-refresh stale Seajobs-looking candidate facts rows.

This repair tool is intentionally narrow: it targets PDFs whose text clearly
looks like the Seajobs layout, but whose current persisted candidate facts were
previously routed as generic/manual/unknown. By default it only reports what it
would refresh. Pass ``--apply`` to persist refreshed local rows.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from candidate_facts.orchestrator import looks_like_seajobs_layout  # noqa: E402
from candidate_facts.persistence import select_current_candidate_resume_facts_row  # noqa: E402
from candidate_facts.repository import CandidateFactsRepository  # noqa: E402
from candidate_facts.validation_cache import candidate_facts_validation_cache_base_dir  # noqa: E402
from rank_folders import rank_folder_slug  # noqa: E402


STALE_SOURCE_ORIGINS = {"", "manual_upload", "unknown"}
STALE_DETECTED_LAYOUTS = {"", "manual", "unknown"}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


def _facts_from_row(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        return {}
    facts = row.get("facts_json")
    return facts if isinstance(facts, Mapping) else {}


def _source_state(candidate_facts: Mapping[str, Any] | None) -> tuple[str, str, str]:
    facts = candidate_facts if isinstance(candidate_facts, Mapping) else {}
    source = facts.get("source") if isinstance(facts.get("source"), Mapping) else {}
    extraction = facts.get("extraction") if isinstance(facts.get("extraction"), Mapping) else {}
    return (
        str(source.get("source_origin") or "").strip(),
        str(source.get("detected_layout") or "").strip(),
        str(extraction.get("parser_version") or "").strip(),
    )


def is_stale_generic_or_unknown_seajobs_row(
    current_row: Mapping[str, Any] | None,
    source_text: str,
    *,
    include_missing: bool = False,
    force: bool = False,
) -> tuple[bool, str]:
    """Return whether a row should be force-refreshed and a concise reason."""

    if not looks_like_seajobs_layout(source_text):
        return False, "not_seajobs_layout"
    if force:
        return True, "forced_seajobs_layout"
    if current_row is None:
        return (True, "missing_current_row") if include_missing else (False, "missing_current_row")

    source_origin, detected_layout, parser_version = _source_state(_facts_from_row(current_row))
    if source_origin == "seajobs_download" and detected_layout == "seajobs":
        return False, "already_seajobs"
    if source_origin in STALE_SOURCE_ORIGINS or detected_layout in STALE_DETECTED_LAYOUTS:
        return True, "stale_generic_or_unknown_source"
    if parser_version.startswith("generic_pdf."):
        return True, "stale_generic_parser"
    return False, "current_row_not_stale"


def build_chunks_for_pdf(analyzer: Any, pdf_path: Path, rank_name: str, source_text: str) -> list[dict[str, Any]]:
    resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
    return [
        {
            "id": f"force-reextract-{resume_id}",
            "score": 1.0,
            "text": source_text,
            "metadata": {
                "resume_id": resume_id,
                "rank": rank_name,
                "filename": pdf_path.name,
                "source_path": str(pdf_path),
                "raw_text": source_text[:12000],
            },
        }
    ]


def _iter_rank_pdf_paths(download_root: Path, rank: str | None) -> Iterable[tuple[str, Path]]:
    if rank:
        folder = download_root / rank_folder_slug(rank)
        if not folder.exists():
            folder = download_root / rank
        if not folder.exists():
            raise FileNotFoundError(f"Rank folder not found: {rank}")
        rank_name = folder.name.replace("_", " ").replace("-", "/")
        for pdf_path in sorted(folder.glob("*.pdf")):
            yield rank_name, pdf_path
        return

    for folder in sorted(path for path in download_root.iterdir() if path.is_dir()):
        rank_name = folder.name.replace("_", " ").replace("-", "/")
        for pdf_path in sorted(folder.glob("*.pdf")):
            yield rank_name, pdf_path


def _summarize_tonnage(candidate_facts: Mapping[str, Any]) -> dict[str, Any]:
    experience = candidate_facts.get("experience") if isinstance(candidate_facts.get("experience"), Mapping) else {}
    values = list(experience.get("vessel_tonnage_values") or [])
    contracts = candidate_facts.get("contracts") if isinstance(candidate_facts.get("contracts"), list) else []
    contract_entries = 0
    for contract in contracts:
        if isinstance(contract, Mapping):
            contract_entries += len(contract.get("vessel_tonnage") or [])
    return {
        "values": values,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "contract_entry_count": contract_entries,
    }


def scan_and_refresh(
    *,
    analyzer: Any,
    repo: CandidateFactsRepository,
    download_root: Path,
    rank: str | None = None,
    apply: bool = False,
    include_missing: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scanned = 0
    selected = 0
    refreshed = 0
    skipped = 0
    errors = 0
    text_cache: dict[str, str] = {}
    folder_metadata_cache: dict[str, Mapping[str, Any]] = {}

    for rank_name, pdf_path in _iter_rank_pdf_paths(download_root, rank):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        candidate_resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
        current_row = select_current_candidate_resume_facts_row(
            repo.rows,
            candidate_resume_id=candidate_resume_id,
            schema_version="candidate_facts.v1",
        )
        try:
            source_text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
        except Exception as exc:  # pragma: no cover - exercised only by real PDFs
            errors += 1
            rows.append({
                "filename": pdf_path.name,
                "rank": rank_name,
                "candidate_resume_id": candidate_resume_id,
                "status": "error",
                "reason": "extract_text_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        should_refresh, reason = is_stale_generic_or_unknown_seajobs_row(
            current_row,
            source_text,
            include_missing=include_missing,
            force=force,
        )
        if not should_refresh:
            skipped += 1
            rows.append({
                "filename": pdf_path.name,
                "rank": rank_name,
                "candidate_resume_id": candidate_resume_id,
                "status": "skipped",
                "reason": reason,
            })
            continue

        selected += 1
        chunks = build_chunks_for_pdf(analyzer, pdf_path, rank_name, source_text)
        text_cache[str(pdf_path)] = source_text
        folder_key = str(pdf_path.parent)
        if folder_key not in folder_metadata_cache:
            folder_metadata_cache[folder_key] = analyzer._rank_manifest_metadata(pdf_path.parent)
        folder_metadata = folder_metadata_cache[folder_key]
        candidate_facts = analyzer._synchronous_reextract_candidate_facts(
            pdf_path.name,
            rank_name,
            chunks,
            original_path=pdf_path,
            text_cache=text_cache,
            folder_metadata=folder_metadata,
        )
        source_origin, detected_layout, parser_version = _source_state(candidate_facts)
        status = "would_refresh"
        persist_row_id = ""
        if apply:
            persist_result = repo.persist_candidate_facts(
                candidate_resume_id=candidate_resume_id,
                resume_blob_id=candidate_resume_id,
                candidate_facts=candidate_facts,
                parser_version=parser_version or "legacy_bridge.v1",
                facts_revision=str(candidate_facts.get("facts_version") or candidate_facts.get("schema_version") or "candidate_facts.v1"),
                extraction_warnings=list((candidate_facts.get("extraction") or {}).get("warnings") or []),
            )
            refreshed += 1 if persist_result.get("committed") else 0
            persist_row_id = str((persist_result.get("row") or {}).get("id") or "")
            status = "refreshed" if persist_result.get("committed") else "persisted_non_current"

        rows.append({
            "filename": pdf_path.name,
            "rank": rank_name,
            "candidate_resume_id": candidate_resume_id,
            "status": status,
            "reason": reason,
            "source_origin": source_origin,
            "detected_layout": detected_layout,
            "parser_version": parser_version,
            "tonnage": _summarize_tonnage(candidate_facts),
            "persistence_row_id": persist_row_id,
        })

    return {
        "schema_version": "force_reextract_seajobs_candidate_facts.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "apply" if apply else "dry_run",
        "download_root": str(download_root),
        "rank": rank or "",
        "include_missing": include_missing,
        "force": force,
        "scanned_count": scanned,
        "selected_count": selected,
        "refreshed_count": refreshed,
        "skipped_count": skipped,
        "error_count": errors,
        "rows": rows,
    }


def _load_runtime_config(config_path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(config_path)
    if not parser.has_section("Settings"):
        raise RuntimeError(f"Missing [Settings] in config file: {config_path}")
    return parser


def _resolve_download_root(config: configparser.ConfigParser, override: str | None) -> Path:
    raw = str(override or "").strip() or str(config.get("Settings", "Default_Download_Folder", fallback="")).strip()
    if not raw:
        raw = str(config.get("Settings", "Additional_Local_Folder", fallback="Verified_Resumes")).strip()
    return Path(raw or "Verified_Resumes").expanduser().resolve()


def _build_analyzer(config: configparser.ConfigParser) -> Any:
    from ai_analyzer import AIResumeAnalyzer

    return AIResumeAnalyzer(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh stale generic/unknown candidate facts for PDFs that clearly use the Seajobs layout."
    )
    parser.add_argument("--config", default=os.getenv("NJORDHR_CONFIG_PATH", "config.ini"))
    parser.add_argument("--download-root", default="")
    parser.add_argument("--cache-dir", default=os.getenv("NJORDHR_CANDIDATE_FACTS_CACHE_DIR", candidate_facts_validation_cache_base_dir()))
    parser.add_argument("--rank", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-missing", action="store_true", help="Also persist Seajobs-looking PDFs with no current row.")
    parser.add_argument("--force", action="store_true", help="Refresh all Seajobs-looking PDFs, even if the current row is already Seajobs.")
    parser.add_argument("--apply", action="store_true", help="Persist refreshed rows. Omit for dry-run.")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    config = _load_runtime_config(args.config)
    download_root = _resolve_download_root(config, args.download_root)
    if not download_root.exists():
        raise SystemExit(f"Download root not found: {download_root}")

    analyzer = _build_analyzer(config)
    repo = CandidateFactsRepository(validation_cache_dir=args.cache_dir)
    report = scan_and_refresh(
        analyzer=analyzer,
        repo=repo,
        download_root=download_root,
        rank=args.rank.strip() or None,
        apply=bool(args.apply),
        include_missing=bool(args.include_missing),
        force=bool(args.force),
        limit=args.limit,
    )

    output_path = Path(args.output or f"AI_Search_Results/force_reextract_seajobs_candidate_facts_{_utc_timestamp()}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{report['mode']}: scanned={report['scanned_count']} selected={report['selected_count']} "
        f"refreshed={report['refreshed_count']} skipped={report['skipped_count']} errors={report['error_count']}"
    )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
