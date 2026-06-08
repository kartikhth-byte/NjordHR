#!/usr/bin/env python3
"""
Run a guarded v2.0 CandidateFacts migration sample.

This runner is intentionally sample-first. By default it does not call external
embedding/Pinecone services and does not mark the normal ingest registry. It
does persist a migration-state file so a second invocation can prove
orchestration-level idempotence for the same sample.

Use --upsert-index only in a network-enabled environment when intentionally
exercising the full indexing write path.
"""

import argparse
import configparser
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_analyzer import AIResumeAnalyzer


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _digest(payload) -> str:
    rendered = json.dumps(payload, sort_keys=True, default=_json_default)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"records": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_chunks(analyzer, pdf_path: Path, rank_name: str, text: str):
    resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
    return analyzer.prepper.chunk_text(
        text,
        resume_id,
        rank_name,
        filename=pdf_path.name,
        source_path=str(pdf_path),
    )


def _select_pdfs(rank_folder: Path, limit: int):
    pdfs = sorted(rank_folder.glob("*.pdf"))
    if limit > 0:
        return pdfs[:limit]
    return pdfs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a guarded v2.0 background migration sample.")
    parser.add_argument("--rank-folder", default="Verified_Resumes/Chief_Officer")
    parser.add_argument("--limit", type=int, default=11, help="Max PDFs to process; use 0 for all files.")
    parser.add_argument("--output", default="AI_Search_Results/background_migration_runner_current.json")
    parser.add_argument("--state", default="AI_Search_Results/background_migration_runner_state.json")
    parser.add_argument(
        "--upsert-index",
        action="store_true",
        help="Generate embeddings and upsert chunks to the configured vector index.",
    )
    parser.add_argument(
        "--mark-ingest-registry",
        action="store_true",
        help="Mark processed files in the normal ingest registry. Usually leave off for evidence runs.",
    )
    args = parser.parse_args()

    rank_folder = Path(args.rank_folder)
    if not rank_folder.exists():
        raise SystemExit(f"Rank folder not found: {rank_folder}")

    config = configparser.ConfigParser()
    config.read("config.ini")
    analyzer = AIResumeAnalyzer(config)
    folder_metadata = analyzer._rank_manifest_metadata(rank_folder)
    rank_name = rank_folder.name.replace("_", " ").replace("-", "/")
    state_path = Path(args.state)
    state = _load_state(state_path)
    state_records = state.setdefault("records", {})

    rows = []
    counts = {
        "attempted": 0,
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "idempotent_matches": 0,
        "idempotent_mismatches": 0,
        "indexed": 0,
        "registry_marked": 0,
    }

    text_cache = {}
    for pdf_path in _select_pdfs(rank_folder, args.limit):
        counts["attempted"] += 1
        row = {
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "status": "started",
        }
        try:
            text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
            if len(text.strip()) < 100:
                row.update({"status": "skipped", "reason": "insufficient_text"})
                counts["skipped"] += 1
                rows.append(row)
                continue

            chunks = _build_chunks(analyzer, pdf_path, rank_name, text)
            facts = analyzer._build_candidate_facts(
                pdf_path.name,
                rank_name,
                chunks,
                original_path=pdf_path,
                text_cache=text_cache,
                folder_metadata=folder_metadata,
            )
            facts_version = str(facts.get("facts_version") or "")
            digest = _digest(facts)
            record_key = str(pdf_path.resolve())
            previous = state_records.get(record_key) or {}
            previous_digest = previous.get("digest")
            idempotent_match = previous_digest == digest if previous_digest else None
            if idempotent_match is True:
                counts["idempotent_matches"] += 1
            elif idempotent_match is False:
                counts["idempotent_mismatches"] += 1

            indexed = False
            if args.upsert_index:
                embeddings = analyzer.prepper.get_embeddings([chunk["text"] for chunk in chunks])
                if not embeddings:
                    raise RuntimeError(analyzer.prepper.last_error or "Embedding generation failed.")
                analyzer.vector_db.upsert_chunks(chunks, embeddings, rank_name)
                indexed = True
                counts["indexed"] += 1

            registry_marked = False
            if args.mark_ingest_registry:
                resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
                analyzer._ingest_mark_processed(str(pdf_path), pdf_path.stat().st_mtime, resume_id)
                registry_marked = True
                counts["registry_marked"] += 1

            state_records[record_key] = {
                "filename": pdf_path.name,
                "last_modified": pdf_path.stat().st_mtime,
                "facts_version": facts_version,
                "digest": digest,
                "rank_folder": str(rank_folder),
            }
            row.update(
                {
                    "status": "processed",
                    "facts_version": facts_version,
                    "digest": digest,
                    "previous_digest": previous_digest,
                    "idempotent_match": idempotent_match,
                    "indexed": indexed,
                    "registry_marked": registry_marked,
                }
            )
            counts["processed"] += 1
        except Exception as exc:
            row.update({"status": "failed", "error": str(exc)})
            counts["failed"] += 1
        rows.append(row)

    _write_state(state_path, state)
    processed_rows = [row for row in rows if row.get("status") == "processed"]
    report = {
        "rank_folder": str(rank_folder),
        "limit": args.limit,
        "state_path": str(state_path),
        "mode": {
            "upsert_index": bool(args.upsert_index),
            "mark_ingest_registry": bool(args.mark_ingest_registry),
        },
        "counts": counts,
        "all_processed_rows_v2_0": all(row.get("facts_version") == AIResumeAnalyzer.FACTS_VERSION for row in processed_rows),
        "all_comparable_rows_idempotent": all(
            row.get("idempotent_match") is not False
            for row in processed_rows
        ),
        "note": (
            "Default mode validates the orchestrated v2.0 fact-building and migration-state path without external upserts. "
            "--upsert-index additionally exercises the configured embedding/vector-index write path."
        ),
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
