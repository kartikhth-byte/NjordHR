import unittest

from candidate_facts import CANDIDATE_FACTS_SCHEMA_VERSION, normalize_candidate_facts_v1, validate_candidate_facts_v1


def _valid_candidate_facts():
        return {
            "schema_version": CANDIDATE_FACTS_SCHEMA_VERSION,
            "source": {
            "resume_id": "resume-1",
            "candidate_id": "candidate-1",
            "source_origin": "seajobs_download",
            "detected_layout": "seajobs",
            "file_name": "resume.pdf",
            "content_hash": "abc123",
        },
        "identity": {
            "candidate_name": {
                "value": "Jane Doe",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
            }
        },
        "documents": [
            {
                "fact_id": "doc-1",
                "fact_type": "document",
                "canonical_value": "passport",
                "display_value": "Passport",
                "presence": "observed_true",
                "confidence": "high",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "seajobs",
                    "parser_version": "1",
                    "method": "table_parser",
                },
                "document_type": "passport",
                "document_number_present": True,
                "issue_date": None,
                "expiry_date": "2028-01-01",
                "country": "IN",
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev-1",
                "source_kind": "pdf_page",
                "source_id": "resume-1/page-1",
                "page_number": 1,
            }
        ],
        "extraction": {
            "parser_version": "1",
            "status": "complete",
            "minimums_satisfied": [],
            "minimums_missing": [],
            "provenance": {
                "mode": "persisted",
                "raw_text_version": "v1",
                "chunk_index_version": "v1",
                "fallback_reason": None,
            },
            "warnings": [],
        },
        "rank": {},
        "certificates": [],
        "endorsements": [],
        "courses": [],
        "contracts": [],
        "rank_experience": [],
        "engine_experience": [],
        "vessel_experience": [],
        "application": {},
        "derived": {},
    }


class CandidateFactsSchemaTests(unittest.TestCase):
    def test_valid_candidate_facts_row_passes_validation(self):
        payload = _valid_candidate_facts()
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.errors, [])
        normalized = normalize_candidate_facts_v1(payload)
        self.assertEqual(normalized["validation"]["status"], "valid")
        self.assertEqual(normalized["schema_version"], CANDIDATE_FACTS_SCHEMA_VERSION)

    def test_unknown_source_requires_partial_or_failed_extraction(self):
        payload = _valid_candidate_facts()
        payload["source"]["source_origin"] = "unknown"
        payload["source"]["detected_layout"] = "unknown"
        payload["extraction"]["status"] = "complete"
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(error["code"] == "invalid_value" for error in result.errors))

    def test_missing_required_source_field_fails_validation(self):
        payload = _valid_candidate_facts()
        del payload["source"]["content_hash"]
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(error["path"] == "source.content_hash" for error in result.errors))

    def test_fact_item_requires_common_contract_fields(self):
        payload = _valid_candidate_facts()
        del payload["documents"][0]["presence"]
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(error["path"] == "documents[0].presence" for error in result.errors))

    def test_contract_vessel_tonnage_entries_are_validated(self):
        payload = _valid_candidate_facts()
        payload["contracts"] = [
            {
                "fact_id": "contract-1",
                "fact_type": "contract",
                "canonical_value": "MT Example",
                "display_value": "MT Example",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "seajobs",
                    "parser_version": "1",
                    "method": "table_parser",
                },
                "rank": "2nd_engineer",
                "vessel_name": "MT Example",
                "vessel_type": "oil_tanker",
                "ship_family": "tanker",
                "vessel_tonnage": [
                    {
                        "value": "58000",
                        "unit": "tons",
                        "source_label": "Tonnage",
                        "confidence": 1.5,
                        "evidence_text": "Tonnage: 58000",
                    }
                ],
            }
        ]
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(error["path"] == "contracts[0].vessel_tonnage[0].value" for error in result.errors))
        self.assertTrue(any(error["path"] == "contracts[0].vessel_tonnage[0].unit" for error in result.errors))
        self.assertTrue(any(error["path"] == "contracts[0].vessel_tonnage[0].confidence" for error in result.errors))

    def test_contract_vessel_tonnage_requires_source_label_and_evidence_text(self):
        payload = _valid_candidate_facts()
        payload["contracts"] = [
            {
                "fact_id": "contract-1",
                "fact_type": "contract",
                "canonical_value": "MT Example",
                "display_value": "MT Example",
                "presence": "observed_true",
                "confidence": "medium",
                "evidence_ids": ["ev-1"],
                "extraction": {
                    "extractor": "seajobs",
                    "parser_version": "1",
                    "method": "table_parser",
                },
                "rank": "2nd_engineer",
                "vessel_name": "MT Example",
                "vessel_type": "oil_tanker",
                "ship_family": "tanker",
                "vessel_tonnage": [
                    {
                        "value": 58000,
                        "unit": "unspecified",
                        "confidence": 0.9,
                    }
                ],
            }
        ]
        result = validate_candidate_facts_v1(payload)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(error["path"] == "contracts[0].vessel_tonnage[0].source_label" for error in result.errors))
        self.assertTrue(any(error["path"] == "contracts[0].vessel_tonnage[0].evidence_text" for error in result.errors))


if __name__ == "__main__":
    unittest.main()
