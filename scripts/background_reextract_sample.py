import argparse
import configparser
import hashlib
import json
from pathlib import Path
from datetime import date, datetime

from ai_analyzer import AIResumeAnalyzer


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _digest(payload) -> str:
    rendered = json.dumps(payload, sort_keys=True, default=_json_default)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _build_chunks(analyzer, pdf_path: Path, rank_name: str, text: str):
    resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
    return [
        {
            "id": f"sample-{resume_id}",
            "score": 1.0,
            "metadata": {
                "resume_id": resume_id,
                "rank": rank_name,
                "filename": pdf_path.name,
                "source_path": str(pdf_path),
                "raw_text": text[:12000],
            },
        }
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline v2.0 re-extraction sample and idempotence check on real PDFs.")
    parser.add_argument("--rank-folder", default="Verified_Resumes/Chief_Officer")
    parser.add_argument("--limit", type=int, default=11)
    parser.add_argument("--output", default="AI_Search_Results/background_reextract_sample_current.json")
    args = parser.parse_args()

    rank_folder = Path(args.rank_folder)
    if not rank_folder.exists():
        raise SystemExit(f"Rank folder not found: {rank_folder}")

    config = configparser.ConfigParser()
    config.read("config.ini")
    analyzer = AIResumeAnalyzer(config)
    text_cache = {}
    folder_metadata = analyzer._rank_manifest_metadata(rank_folder)
    rank_name = rank_folder.name.replace("_", " ").replace("-", "/")

    rows = []
    processed = 0
    skipped = 0

    for pdf_path in sorted(rank_folder.glob("*.pdf"))[: args.limit]:
        try:
            text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
        except Exception as exc:
            rows.append(
                {
                    "filename": pdf_path.name,
                    "status": "error",
                    "error": str(exc),
                }
            )
            skipped += 1
            continue

        if not text.strip():
            rows.append(
                {
                    "filename": pdf_path.name,
                    "status": "skipped",
                    "reason": "no_text",
                }
            )
            skipped += 1
            continue

        chunks = _build_chunks(analyzer, pdf_path, rank_name, text)
        facts_one = analyzer._synchronous_reextract_candidate_facts(
            pdf_path.name,
            rank_name,
            chunks,
            original_path=pdf_path,
            text_cache=text_cache,
            folder_metadata=folder_metadata,
        )
        facts_two = analyzer._synchronous_reextract_candidate_facts(
            pdf_path.name,
            rank_name,
            chunks,
            original_path=pdf_path,
            text_cache=text_cache,
            folder_metadata=folder_metadata,
        )

        digest_one = _digest(facts_one)
        digest_two = _digest(facts_two)
        rows.append(
            {
                "filename": pdf_path.name,
                "status": "processed",
                "facts_version_first": str(facts_one.get("facts_version") or ""),
                "facts_version_second": str(facts_two.get("facts_version") or ""),
                "idempotent": digest_one == digest_two,
                "digest_first": digest_one,
                "digest_second": digest_two,
            }
        )
        processed += 1

    processed_rows = [row for row in rows if row.get("status") == "processed"]
    all_v2 = all(row.get("facts_version_first") == "2.0" and row.get("facts_version_second") == "2.0" for row in processed_rows)
    all_idempotent = all(bool(row.get("idempotent")) for row in processed_rows)

    report = {
        "rank_folder": str(rank_folder),
        "sample_limit": args.limit,
        "processed_count": processed,
        "skipped_count": skipped,
        "all_processed_rows_v2_0": all_v2,
        "all_processed_rows_idempotent": all_idempotent,
        "note": (
            "This is an offline evidence harness around the same v2.0 fact-building path used by synchronous re-extraction. "
            "It does not prove a separate background job scheduler or Pinecone/Gemini ingestion path."
        ),
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
