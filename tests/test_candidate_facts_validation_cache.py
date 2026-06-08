import tempfile
import unittest
from datetime import date
from pathlib import Path

from candidate_facts.validation_cache import (
    CandidateFactsValidationCache,
    build_candidate_facts_content_hash,
    build_candidate_facts_review_id,
    candidate_facts_validation_cache_base_dir,
)


def _candidate_facts_payload():
    return {
        "schema_version": "candidate_facts.v1",
        "source": {
            "resume_id": "candidate-resume-1",
            "candidate_id": "candidate-1",
            "source_origin": "manual_upload",
            "detected_layout": "unknown",
            "file_name": "resume.pdf",
            "content_hash": "abc123",
        },
        "identity": {
            "candidate_name": {
                "value": "Jane Doe",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "snippet": "Jane Doe",
            },
            "dob": {
                "value": "1990-05-04",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "snippet": "Date of Birth: 04-May-1990",
            },
        },
        "rank": {
            "value": "2nd_engineer",
            "presence": "observed_true",
            "confidence": "high",
            "evidence_ids": ["ev-1"],
            "snippet": "Present Rank: 2nd Engineer",
        },
        "documents": [
            {
                "fact_id": "passport",
                "fact_type": "document",
                "canonical_value": "passport",
                "display_value": "Passport",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "document_type": "passport",
                "document_number_present": True,
                "issue_date": None,
                "expiry_date": "2029-01-01",
                "country": "IN",
                "snippet": "Passport Expiry Date 01-Jan-2029",
            }
        ],
        "certificates": [
            {
                "fact_id": "coc",
                "fact_type": "certificate",
                "canonical_value": "ii/2",
                "display_value": "CoC II/2",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "certificate_type": "coc",
                "certificate_number_present": False,
                "issue_date": None,
                "expiry_date": "2028-01-01",
                "grade": "II/2",
                "status": "active",
                "snippet": "CoC Grade II/2 Expiry Date 01-Jan-2028",
            }
        ],
        "endorsements": [],
        "courses": [],
        "contracts": [
            {
                "fact_id": "contract-1",
                "fact_type": "contract",
                "canonical_value": "abc-shipping",
                "display_value": "ABC Shipping",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "contract_order": 1,
                "rank": "2nd_engineer",
                "vessel_name": "Ocean Star",
                "vessel_type": "bulk carrier",
                "ship_family": "bulk carrier",
                "engine_family": "diesel",
                "company": "ABC Shipping",
                "start_date": "2023-01-01",
                "end_date": "2023-06-01",
                "duration_months": 5,
                "is_current_contract": False,
                "snippet": "1 2nd Engineer Ocean Star Bulk Carrier ABC Shipping 01-Jan-2023 01-Jun-2023",
            },
            {
                "fact_id": "contract-2",
                "fact_type": "contract",
                "canonical_value": "abc-shipping-2",
                "display_value": "ABC Shipping",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "contract_order": 2,
                "rank": "2nd_engineer",
                "vessel_name": "Sea Breeze",
                "vessel_type": "tanker",
                "ship_family": "tanker",
                "engine_family": "diesel",
                "company": "ABC Shipping",
                "start_date": "2024-03-01",
                "end_date": "2024-09-01",
                "duration_months": 6,
                "is_current_contract": False,
                "snippet": "2 2nd Engineer Sea Breeze Tanker ABC Shipping 01-Mar-2024 01-Sep-2024",
            },
        ],
        "rank_experience": [
            {
                "fact_id": "rank-exp-1",
                "fact_type": "rank_experience",
                "canonical_value": "2nd_engineer",
                "display_value": "2nd engineer",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "rank": "2nd_engineer",
                "duration_months": 60,
                "source": "contracts",
                "snippet": "2nd engineer sea service 60 months",
            }
        ],
        "engine_experience": [],
        "vessel_experience": [
            {
                "fact_id": "vessel-exp-1",
                "fact_type": "vessel_experience",
                "canonical_value": "bulk carrier",
                "display_value": "bulk carrier",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "generic_pdf",
                    "parser_version": "generic_pdf.v1",
                    "method": "fallback",
                },
                "ship_family": "bulk carrier",
                "duration_months": 24,
                "contract_ids": ["contract-1"],
                "snippet": "bulk carrier service record",
            }
        ],
        "application": {"applied_ship_types": ["bulk carrier", "tanker"]},
        "derived": {},
        "evidence": [
            {
                "evidence_id": "ev-1",
                "source_kind": "raw_text_chunk",
                "source_id": "resume-1/chunk-1",
            }
        ],
        "extraction": {
            "parser_version": "generic_pdf.v1",
            "status": "partial",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "semantic_chunk",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": "generic_fallback",
            },
            "warnings": ["generic_candidate_facts_fallback"],
        },
    }


class CandidateFactsValidationCacheTests(unittest.TestCase):
    def test_cache_dir_is_os_portable(self):
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="darwin").endswith("Library/Application Support/NjordHR/candidate_facts"))
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="linux").endswith(".config/njordhr/candidate_facts"))
        self.assertTrue(candidate_facts_validation_cache_base_dir(home="/Users/kartik", system="windows").endswith("NjordHR/candidate_facts"))

    def test_capture_approve_and_promote_review_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                review_alignment_report={
                    "status": "match",
                    "compared_field_count": 2,
                    "mismatch_count": 0,
                    "mismatches": [],
                },
                review_alignment_status="match",
                review_alignment_mismatch_count=0,
                review_alignment_mismatches=[],
            )
            self.assertEqual(record["review_status"], "pending_review")
            self.assertEqual(len(cache.list_review_items(review_status="pending_review")), 1)

            approved = cache.approve_review_item(record["id"], reviewed_by="reviewer", review_notes="looks good")
            self.assertEqual(approved["review_status"], "approved")

            rows = []
            result = cache.promote_review_item_to_persisted(rows, record["id"])
            self.assertTrue(result["persist"]["committed"])
            self.assertEqual(result["review_item"]["persistence_status"], "persisted")
            self.assertTrue(Path(cache.path).exists())
            self.assertEqual(cache.get_review_item(record["id"])["persistence_row_id"], result["persist"]["row"]["id"])
            self.assertEqual(result["persist"]["row"]["candidate_facts_hash"], record["candidate_facts_hash"])

    def test_capture_without_alignment_report_is_not_checked_and_blocks_promotion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            self.assertEqual(record["review_alignment_status"], "not_checked")
            self.assertFalse(record["review_alignment_checked"])

            cache.approve_review_item(record["id"], reviewed_by="reviewer", review_notes="looks good")
            with self.assertRaises(ValueError) as ctx:
                cache.promote_review_item_to_persisted([], record["id"])
            self.assertIn("explicit alignment report", str(ctx.exception))

    def test_capture_serializes_live_extractor_date_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            payload = _candidate_facts_payload()
            payload["derived"]["availability_date"] = date(2026, 5, 25)

            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=payload,
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )

            self.assertEqual(record["candidate_facts"]["derived"]["availability_date"], "2026-05-25")
            restarted = CandidateFactsValidationCache(base_dir=tmpdir)
            self.assertEqual(
                restarted.get_review_item(record["id"])["candidate_facts"]["derived"]["availability_date"],
                "2026-05-25",
            )

    def test_reject_review_item_keeps_item_out_of_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            rejected = cache.reject_review_item(record["id"], reviewed_by="reviewer", review_notes="bad evidence")
            self.assertEqual(rejected["review_status"], "rejected")
            with self.assertRaises(ValueError):
                cache.promote_review_item_to_persisted([], record["id"])

    def test_review_id_is_deterministic(self):
        candidate_facts_hash = build_candidate_facts_content_hash(_candidate_facts_payload())
        review_id = build_candidate_facts_review_id(
            candidate_resume_id="candidate-resume-1",
            resume_blob_id="blob-1",
            schema_version="candidate_facts.v1",
            parser_version="generic_pdf.v1",
            facts_revision="rev-1",
            candidate_facts_hash=candidate_facts_hash,
        )
        self.assertEqual(
            review_id,
            build_candidate_facts_review_id(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                schema_version="candidate_facts.v1",
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                candidate_facts_hash=candidate_facts_hash,
            ),
        )

    def test_recapture_with_changed_content_creates_new_review_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            first = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            second_payload = _candidate_facts_payload()
            second_payload["identity"]["candidate_name"]["value"] = "Jane Smith"
            second = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=second_payload,
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(cache.list_review_items()), 2)
            self.assertEqual(cache.get_review_item(first["id"])["review_status"], "superseded")
            self.assertEqual(cache.get_review_item(second["id"])["review_status"], "pending_review")
            self.assertEqual(len(cache.list_review_items(review_status="pending_review")), 1)

    def test_superseded_review_items_cannot_be_approved_or_promoted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            first = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                review_alignment_report={
                    "status": "match",
                    "compared_field_count": 2,
                    "mismatch_count": 0,
                    "mismatches": [],
                },
                review_alignment_status="match",
                review_alignment_mismatch_count=0,
                review_alignment_mismatches=[],
            )
            second_payload = _candidate_facts_payload()
            second_payload["identity"]["candidate_name"]["value"] = "Jane Smith"
            cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=second_payload,
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                review_alignment_report={
                    "status": "match",
                    "compared_field_count": 2,
                    "mismatch_count": 0,
                    "mismatches": [],
                },
                review_alignment_status="match",
                review_alignment_mismatch_count=0,
                review_alignment_mismatches=[],
            )
            self.assertEqual(cache.get_review_item(first["id"])["review_status"], "superseded")

            with self.assertRaises(ValueError):
                cache.approve_review_item(first["id"], reviewed_by="reviewer")
            with self.assertRaises(ValueError):
                cache.reject_review_item(first["id"], reviewed_by="reviewer")

    def test_only_pending_review_items_can_be_approved_or_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
                review_alignment_report={
                    "status": "match",
                    "compared_field_count": 2,
                    "mismatch_count": 0,
                    "mismatches": [],
                },
                review_alignment_status="match",
                review_alignment_mismatch_count=0,
                review_alignment_mismatches=[],
            )
            cache.approve_review_item(record["id"], reviewed_by="reviewer")
            with self.assertRaises(ValueError):
                cache.approve_review_item(record["id"], reviewed_by="reviewer")
            with self.assertRaises(ValueError):
                cache.reject_review_item(record["id"], reviewed_by="reviewer")

    def test_review_summary_includes_key_facts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            record = cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )

            summaries = cache.list_review_item_summaries()
            self.assertEqual(len(summaries), 1)
            summary = summaries[0]["candidate_facts_review_summary"]
            self.assertEqual(summary["key_fact_count"], len(summary["key_facts"]))

            field_paths = {fact["field_path"] for fact in summary["key_facts"]}
            self.assertIn("identity.candidate_name", field_paths)
            self.assertIn("personal.dob", field_paths)
            self.assertIn("derived.age_years", field_paths)
            self.assertIn("role.applied_rank_normalized", field_paths)
            self.assertIn("logistics.passport_expiry_date", field_paths)
            self.assertIn("logistics.passport_valid", field_paths)
            self.assertIn("certifications.coc.grade", field_paths)
            self.assertIn("experience.vessel_types", field_paths)
            self.assertIn("derived.current_rank_months_total", field_paths)
            self.assertIn("derived.has_contract_gap_over_6_months", field_paths)
            self.assertIn("derived.same_company_contract_count_max", field_paths)

            self.assertTrue(all(fact["affects_match"] for fact in summary["key_facts"]))

            review_item = cache.get_review_item(record["id"])
            self.assertIn("candidate_facts_review_summary", review_item)
            self.assertEqual(review_item["candidate_facts_review_summary"]["key_fact_count"], summary["key_fact_count"])

    def test_missing_fields_are_marked_unobserved_and_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            payload = _candidate_facts_payload()
            payload["identity"].pop("dob", None)
            payload["documents"] = []
            payload["certificates"] = []
            payload["application"] = {"applied_ship_types": []}
            payload["contracts"] = []
            payload["rank_experience"] = []
            payload["vessel_experience"] = []
            payload["derived"]["same_company_contract_count_max"] = 2

            cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=payload,
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )

            summary = cache.list_review_item_summaries()[0]["candidate_facts_review_summary"]
            dob_row = next(fact for fact in summary["key_facts"] if fact["field_path"] == "personal.dob")
            passport_row = next(fact for fact in summary["key_facts"] if fact["field_path"] == "logistics.passport_expiry_date")
            coc_row = next(fact for fact in summary["key_facts"] if fact["field_path"] == "certifications.coc.grade")
            same_company_row = next(fact for fact in summary["key_facts"] if fact["field_path"] == "derived.same_company_contract_count_max")
            self.assertEqual(dob_row["presence"], "unobserved_unknown")
            self.assertEqual(dob_row["confidence_level"], "low")
            self.assertEqual(dob_row["warning_level"], "missing")
            self.assertEqual(dob_row["evidence_ids"], [])
            self.assertEqual(passport_row["presence"], "unobserved_unknown")
            self.assertEqual(passport_row["confidence_level"], "low")
            self.assertEqual(passport_row["warning_level"], "missing")
            self.assertEqual(passport_row["evidence_ids"], [])
            self.assertEqual(coc_row["presence"], "unobserved_unknown")
            self.assertEqual(coc_row["warning_level"], "missing")
            self.assertEqual(coc_row["evidence_ids"], [])
            self.assertEqual(same_company_row["display_value"], 2)
            self.assertEqual(same_company_row["warning_level"], "ok")

    def test_high_impact_fields_are_flagged_as_matching_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CandidateFactsValidationCache(base_dir=tmpdir)
            cache.capture_candidate_facts_for_review(
                candidate_resume_id="candidate-resume-1",
                resume_blob_id="blob-1",
                candidate_facts=_candidate_facts_payload(),
                parser_version="generic_pdf.v1",
                facts_revision="rev-1",
            )

            summary = cache.list_review_item_summaries()[0]["candidate_facts_review_summary"]
            matching_fields = {
                fact["field_path"]
                for fact in summary["key_facts"]
                if fact["affects_match"]
            }
            self.assertIn("identity.candidate_name", matching_fields)
            self.assertIn("personal.dob", matching_fields)
            self.assertIn("role.applied_rank_normalized", matching_fields)
            self.assertIn("logistics.passport_expiry_date", matching_fields)
            self.assertIn("logistics.passport_valid", matching_fields)
            self.assertIn("certifications.coc.grade", matching_fields)
            self.assertIn("experience.vessel_types", matching_fields)
            self.assertIn("derived.current_rank_months_total", matching_fields)
            self.assertIn("derived.has_contract_gap_over_6_months", matching_fields)
            self.assertIn("derived.same_company_contract_count_max", matching_fields)


if __name__ == "__main__":
    unittest.main()
