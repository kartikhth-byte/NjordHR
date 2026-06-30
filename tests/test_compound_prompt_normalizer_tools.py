import unittest

from query_understanding.compound_prompt_normalizer_tools import (
    CHECK_AVAILABILITY_PARAMETERS_TOOL_ID,
    CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID,
    HELPER_TOOL_VERSION,
    LOCATE_PROMPT_SPAN_TOOL_ID,
    PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID,
    availability_helper_tool_context,
    check_availability_parameters,
    classify_availability_conflict,
    locate_prompt_span,
    parse_availability_date_phrase,
)


class CompoundPromptNormalizerHelperToolTests(unittest.TestCase):
    def test_locate_prompt_span_accepts_unique_span_and_rejects_repeated_text(self):
        accepted = locate_prompt_span("Need crew available immediately", "available immediately")
        self.assertEqual(set(accepted), {"tool_id", "accepted", "result", "errors"})
        self.assertEqual(accepted["tool_id"], LOCATE_PROMPT_SPAN_TOOL_ID)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["errors"], [])
        self.assertEqual(
            accepted["result"]["span"],
            {"text": "available immediately", "start": 10, "end": 31},
        )

        rejected = locate_prompt_span("available now and available later", "available")
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["result"], {})
        self.assertIn("text appears more than once", rejected["errors"])

    def test_parse_availability_date_phrase_accepts_supported_dates_and_rejects_ambiguous_numeric(self):
        self.assertEqual(
            parse_availability_date_phrase("13/04/2026")["result"]["date"],
            "2026-04-13",
        )
        self.assertEqual(
            parse_availability_date_phrase("Apr 13 2026")["result"]["date"],
            "2026-04-13",
        )
        relative = parse_availability_date_phrase("within 30 days", reference_date="2026-06-29")
        self.assertEqual(relative["tool_id"], PARSE_AVAILABILITY_DATE_PHRASE_TOOL_ID)
        self.assertEqual(relative["result"]["date"], "2026-07-29")
        self.assertEqual(relative["result"]["relative_days"], 30)

        ambiguous = parse_availability_date_phrase("04/05/2026")
        self.assertFalse(ambiguous["accepted"])
        self.assertIn("ambiguous numeric date", ambiguous["errors"])

        invalid_iso = parse_availability_date_phrase("2026-02-30")
        self.assertFalse(invalid_iso["accepted"])
        self.assertIn("invalid calendar date", invalid_iso["errors"])

    def test_check_availability_parameters_uses_catalog_validation(self):
        parameters = {
            "version": "v1",
            "value_type": "relative_days",
            "status": None,
            "available_by_date": None,
            "available_from_date": None,
            "available_until_date": None,
            "relative_days": 30,
            "resolved_reference_date": "2026-06-29",
            "display_value": "within 30 days",
        }
        accepted = check_availability_parameters(parameters)
        self.assertEqual(accepted["tool_id"], CHECK_AVAILABILITY_PARAMETERS_TOOL_ID)
        self.assertTrue(accepted["accepted"])

        rejected = check_availability_parameters({**parameters, "relative_days": 366})
        self.assertFalse(rejected["accepted"])
        self.assertTrue(any("outside plausibility bounds" in error for error in rejected["errors"]))

    def test_classify_availability_conflict_routes_contradictions_and_day_of_week(self):
        conflict = classify_availability_conflict("Available immediately but not available until 15 Apr 2026")
        self.assertEqual(conflict["tool_id"], CLASSIFY_AVAILABILITY_CONFLICT_TOOL_ID)
        self.assertEqual(conflict["result"]["route"], "needs_review")

        out_of_scope = classify_availability_conflict("available only on Tuesdays")
        self.assertEqual(out_of_scope["result"]["route"], "unapplied")

    def test_helper_tool_context_returns_llm_outputs_and_hash_only_audit_records(self):
        outputs, audit = availability_helper_tool_context(
            "Need crew available within 30 days",
            reference_date="2026-06-29",
        )

        self.assertGreaterEqual(len(outputs), 4)
        self.assertEqual(len(outputs), len(audit))
        self.assertTrue(any(item["accepted"] for item in outputs))
        self.assertTrue(any(call["accepted"] for call in audit))
        self.assertTrue(all(set(call) == {"tool_id", "input_hash", "accepted", "result_hash", "errors"} for call in audit))
        self.assertTrue(all("available within 30 days" not in str(call) for call in audit))
        self.assertEqual(HELPER_TOOL_VERSION, "1.0.0")


if __name__ == "__main__":
    unittest.main()
