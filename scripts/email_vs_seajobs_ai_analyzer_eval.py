import argparse
import configparser
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_analyzer import AIResumeAnalyzer, AdvancedPDFProcessor, ConfigManager


DEFAULT_RESUME_ROOT = Path("/Users/kartikraghavan/Library/Application Support/NjordHR/Resumes")
DEFAULT_OUTPUT = REPO_ROOT / "AI_Search_Results" / "email_vs_seajobs_ai_analyzer_eval_2026-05-07.json"

CORE_FACT_FIELDS = [
    "personal.dob",
    "role.current_rank_normalized",
    "certifications.coc",
    "certifications.stcw_basic_all_valid",
    "logistics.passport_expiry_date",
    "travel.visa_records",
    "experience.vessel_types",
]

PROMPT_FAMILIES = [
    ("rank_match", "{rank_label}"),
    ("age_range", "{rank_label} age between 30 and 50 years old"),
    ("us_visa", "{rank_label} with valid us visa"),
    ("coc_document_gate", "{rank_label} with valid coc"),
    ("stcw_basic", "{rank_label} with valid stcw basic"),
]


class _RegistryStub:
    def generate_resume_id(self, file_path):
        return hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _build_analyzer():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "config.ini")
    analyzer = AIResumeAnalyzer.__new__(AIResumeAnalyzer)
    analyzer.config = ConfigManager(parser)
    analyzer.registry = _RegistryStub()
    analyzer.pdf_processor = AdvancedPDFProcessor()
    analyzer._configured_ship_type_labels_cache = None
    return analyzer


def _source_type(pdf_path: Path) -> str:
    return "email" if pdf_path.name.startswith("EMAIL_") else "non_email"


def _rank_label(folder_name: str) -> str:
    return folder_name.replace("_", " ").replace("-", "/")


def _build_chunks(analyzer, pdf_path: Path, rank_label: str, text: str):
    resume_id = analyzer.registry.generate_resume_id(str(pdf_path))
    return [
        {
            "id": f"eval-{resume_id}",
            "score": 1.0,
            "metadata": {
                "resume_id": resume_id,
                "rank": rank_label,
                "filename": pdf_path.name,
                "source_path": str(pdf_path),
                "raw_text": text[:12000],
            },
        }
    ]


def _nested_get(payload, dotted_key):
    value = payload
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _select_samples(folder: Path):
    pdfs = sorted(folder.glob("*.pdf"))
    email = [path for path in pdfs if _source_type(path) == "email"]
    non_email = [path for path in pdfs if _source_type(path) == "non_email"]
    if not email or not non_email:
        return []
    return email + non_email[: len(email)]


def _evaluate_file(analyzer, pdf_path: Path, folder_name: str, folder_metadata: dict):
    text = analyzer.pdf_processor.extract_text(str(pdf_path)) or ""
    if not text.strip():
        return {
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "rank_folder": folder_name,
            "source_type": _source_type(pdf_path),
            "status": "skipped",
            "skip_reason": "no_text",
            "text_chars": 0,
            "text_words": 0,
        }

    rank_label = _rank_label(folder_name)
    chunks = _build_chunks(analyzer, pdf_path, rank_label, text)
    facts = analyzer._build_candidate_facts(
        pdf_path.name,
        rank_label,
        chunks,
        original_path=pdf_path,
        text_cache={str(pdf_path): text},
        folder_metadata=folder_metadata,
    )

    field_rows = {}
    for field in CORE_FACT_FIELDS:
        meta = ((facts.get("fact_meta") or {}).get(field) or {})
        value = _nested_get(facts, field)
        field_rows[field] = {
            "status": meta.get("status") or "MISSING",
            "value_present": _has_value(value),
            "confidence": meta.get("confidence"),
            "source_label": meta.get("source_label"),
            "value": value,
        }

    hard_filter_results = {}
    for family, template in PROMPT_FAMILIES:
        prompt = template.format(rank_label=rank_label)
        constraints = analyzer._extract_job_constraints(prompt, rank=rank_label)
        result = analyzer._evaluate_hard_filters(facts, constraints)
        hard_filter_results[family] = {
            "decision": result.get("decision"),
            "reason_codes": [row.get("reason_code") for row in (result.get("results") or [])],
        }

    usable_count = sum(1 for row in field_rows.values() if row["value_present"])
    return {
        "filename": pdf_path.name,
        "path": str(pdf_path),
        "rank_folder": folder_name,
        "source_type": _source_type(pdf_path),
        "status": "processed",
        "text_chars": len(text),
        "text_words": len(text.split()),
        "usable_core_field_count": usable_count,
        "fields": field_rows,
        "hard_filters": hard_filter_results,
    }


def _summarize_rows(rows):
    by_source = defaultdict(lambda: {
        "processed": 0,
        "skipped": 0,
        "text_chars": [],
        "text_words": [],
        "usable_core_field_count": [],
        "field_status_counts": {field: Counter() for field in CORE_FACT_FIELDS},
        "field_value_present_counts": {field: Counter() for field in CORE_FACT_FIELDS},
        "hard_filter_decisions": {family: Counter() for family, _ in PROMPT_FAMILIES},
    })

    for row in rows:
        bucket = by_source[row["source_type"]]
        if row["status"] != "processed":
            bucket["skipped"] += 1
            continue
        bucket["processed"] += 1
        bucket["text_chars"].append(row["text_chars"])
        bucket["text_words"].append(row["text_words"])
        bucket["usable_core_field_count"].append(row["usable_core_field_count"])
        for field, field_row in row["fields"].items():
            bucket["field_status_counts"][field][field_row["status"]] += 1
            bucket["field_value_present_counts"][field]["present" if field_row["value_present"] else "missing"] += 1
        for family, family_row in row["hard_filters"].items():
            bucket["hard_filter_decisions"][family][family_row["decision"] or "UNKNOWN"] += 1

    summary = {}
    for source_type, payload in by_source.items():
        summary[source_type] = {
            "processed": payload["processed"],
            "skipped": payload["skipped"],
            "avg_text_chars": statistics.mean(payload["text_chars"]) if payload["text_chars"] else 0,
            "median_text_chars": statistics.median(payload["text_chars"]) if payload["text_chars"] else 0,
            "avg_text_words": statistics.mean(payload["text_words"]) if payload["text_words"] else 0,
            "median_text_words": statistics.median(payload["text_words"]) if payload["text_words"] else 0,
            "avg_usable_core_field_count": (
                statistics.mean(payload["usable_core_field_count"]) if payload["usable_core_field_count"] else 0
            ),
            "field_status_counts": {
                field: dict(counter) for field, counter in payload["field_status_counts"].items()
            },
            "field_value_present_counts": {
                field: dict(counter) for field, counter in payload["field_value_present_counts"].items()
            },
            "hard_filter_decisions": {
                family: dict(counter) for family, counter in payload["hard_filter_decisions"].items()
            },
        }
    return summary


def _mixed_folder_inventory(resume_root: Path):
    rows = []
    for folder in sorted(resume_root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        pdfs = sorted(folder.glob("*.pdf"))
        email_count = sum(1 for path in pdfs if _source_type(path) == "email")
        non_email_count = len(pdfs) - email_count
        if email_count and non_email_count:
            rows.append(
                {
                    "rank_folder": folder.name,
                    "email_count": email_count,
                    "non_email_count": non_email_count,
                    "sampled_email_count": email_count,
                    "sampled_non_email_count": min(email_count, non_email_count),
                }
            )
    return rows


def _find_low_coverage_examples(rows, source_type, limit=8):
    filtered = [
        row for row in rows
        if row["status"] == "processed" and row["source_type"] == source_type
    ]
    filtered.sort(
        key=lambda row: (
            row["usable_core_field_count"],
            row["text_chars"],
            row["filename"].lower(),
        )
    )
    examples = []
    for row in filtered[:limit]:
        missing_fields = [
            field for field, field_row in row["fields"].items() if not field_row["value_present"]
        ]
        examples.append(
            {
                "rank_folder": row["rank_folder"],
                "filename": row["filename"],
                "usable_core_field_count": row["usable_core_field_count"],
                "text_chars": row["text_chars"],
                "missing_fields": missing_fields,
                "hard_filter_decisions": {
                    family: family_row["decision"] for family, family_row in row["hard_filters"].items()
                },
            }
        )
    return examples


def main():
    parser = argparse.ArgumentParser(
        description="Compare Outlook email-downloaded resumes against non-email live resumes through the same AI analyzer fact pipeline."
    )
    parser.add_argument("--resume-root", default=str(DEFAULT_RESUME_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    resume_root = Path(args.resume_root)
    if not resume_root.exists():
        raise SystemExit(f"Resume root not found: {resume_root}")

    analyzer = _build_analyzer()
    rows = []

    for folder_row in _mixed_folder_inventory(resume_root):
        folder = resume_root / folder_row["rank_folder"]
        folder_metadata = analyzer._rank_manifest_metadata(folder)
        for pdf_path in _select_samples(folder):
            rows.append(_evaluate_file(analyzer, pdf_path, folder.name, folder_metadata))

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "resume_root": str(resume_root),
        "scope_note": (
            "This is a Phase 1 offline comparison of the shared candidate-facts and hard-filter path. "
            "It compares live rank-folder PDFs whose filenames start with EMAIL_ against matched non-email PDFs "
            "from the same mixed folders. Non-email files are mostly SeaJobs-style corpus files, but this harness "
            "classifies by filename/source pattern rather than asserting upstream provenance from metadata."
        ),
        "mixed_folder_inventory": _mixed_folder_inventory(resume_root),
        "sample_policy": {
            "folders_included": "live rank folders with at least one EMAIL_ PDF and at least one non-email PDF",
            "email_sampling": "all EMAIL_ PDFs in each mixed folder",
            "non_email_sampling": "first N sorted non-email PDFs in the same folder, where N equals sampled email count",
            "core_fact_fields": CORE_FACT_FIELDS,
            "hard_filter_prompt_families": [family for family, _ in PROMPT_FAMILIES],
        },
        "summary_by_source_type": _summarize_rows(rows),
        "low_coverage_examples": {
            "email": _find_low_coverage_examples(rows, "email"),
            "non_email": _find_low_coverage_examples(rows, "non_email"),
        },
        "rows": rows,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
