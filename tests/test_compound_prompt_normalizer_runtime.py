import unittest
from unittest.mock import Mock, patch

from candidate_facts.aliases.filter_capability_catalog import PROMOTED_FAMILIES
from query_understanding.compound_prompt_normalizer_provider import AvailabilityNormalizerProviderResult
from query_understanding.compound_prompt_normalizer_runtime import (
    promoted_constraints_from_prompt,
    promoted_availability_constraint_from_prompt,
)


def _provider_result(payload):
    return AvailabilityNormalizerProviderResult(
        model_id="fake-model",
        prompt_template_version="test-template",
        raw_llm_output="{}",
        parsed_payload=payload,
    )


def _provider_result_with_helpers(payload):
    return AvailabilityNormalizerProviderResult(
        model_id="fake-model",
        prompt_template_version="test-template",
        raw_llm_output="{}",
        parsed_payload=payload,
        helper_tool_version="1.0.0",
        helper_tool_calls=(
            {
                "tool_id": "locate_prompt_span.v1",
                "input_hash": "input-hash",
                "accepted": True,
                "result_hash": "result-hash",
                "errors": [],
            },
        ),
    )


def _availability_payload(prompt, phrase="available immediately"):
    start = prompt.index(phrase)
    return {
        "version": "v1",
        "constraints": [
            {
                "filter_family": "availability",
                "parameters": {
                    "version": "v1",
                    "value_type": "status",
                    "status": "immediate",
                    "available_by_date": None,
                    "available_from_date": None,
                    "available_until_date": None,
                    "relative_days": None,
                    "resolved_reference_date": "2026-06-30",
                    "display_value": phrase,
                },
                "source_span": {
                    "text": phrase,
                    "start": start,
                    "end": start + len(phrase),
                },
            }
        ],
        "soft_signals": [],
        "unapplied": [],
        "needs_review": [],
    }


def _vessel_tonnage_constraint(prompt, phrase="vessels above 50000 GT"):
    start = prompt.index(phrase)
    return {
        "filter_family": "vessel_tonnage",
        "parameters": {
            "version": "v1",
            "value_type": "minimum",
            "min_value": 50000,
            "max_value": None,
            "unit": "gt_grt",
            "years_back": None,
            "display_value": phrase,
        },
        "source_span": {
            "text": phrase,
            "start": start,
            "end": start + len(phrase),
        },
    }


def _coc_country_constraint(prompt, phrase="Indian CoC"):
    start = prompt.index(phrase)
    return {
        "filter_family": "coc_country_match",
        "parameters": {
            "version": "v1",
            "type": "coc_country_match",
            "countries": ["india"],
            "operator": "contains_any",
            "display_value": phrase,
        },
        "source_span": {
            "text": phrase,
            "start": start,
            "end": start + len(phrase),
        },
    }


def _availability_constraint(prompt, phrase="available immediately"):
    return _availability_payload(prompt, phrase=phrase)["constraints"][0]


def _combined_payload(prompt):
    return {
        "version": "v1",
        "constraints": [
            _availability_constraint(prompt),
            _vessel_tonnage_constraint(prompt),
        ],
        "soft_signals": [],
        "unapplied": [],
        "needs_review": [],
    }


def _combined_three_family_payload(prompt):
    return {
        "version": "v1",
        "constraints": [
            _availability_constraint(prompt),
            _vessel_tonnage_constraint(prompt),
            _coc_country_constraint(prompt),
        ],
        "soft_signals": [],
        "unapplied": [],
        "needs_review": [],
    }


class CompoundPromptNormalizerRuntimeTests(unittest.TestCase):
    def test_promoted_families_include_availability_vessel_tonnage_and_coc_country(self):
        self.assertEqual(PROMOTED_FAMILIES, {"availability", "coc_country_match", "vessel_tonnage"})

    def test_deterministic_mode_does_not_invoke_provider_or_dispatch(self):
        provider = Mock()
        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "deterministic"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                "Need crew available immediately",
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_not_called()
        self.assertIsNone(constraint)
        self.assertFalse(diagnostics["provider_invoked"])
        self.assertFalse(diagnostics["dispatched"])

    def test_deterministic_mode_does_not_dispatch_any_promoted_family(self):
        provider = Mock()
        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "deterministic"}, clear=False):
            constraints, diagnostics = promoted_constraints_from_prompt(
                "Need crew available immediately with vessels above 50000 GT",
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_not_called()
        self.assertEqual(constraints, {})
        self.assertEqual(set(diagnostics), {"availability", "coc_country_match", "vessel_tonnage"})
        self.assertFalse(diagnostics["availability"]["dispatched"])
        self.assertFalse(diagnostics["coc_country_match"]["dispatched"])
        self.assertFalse(diagnostics["vessel_tonnage"]["dispatched"])

    def test_unknown_mode_falls_back_to_deterministic_without_provider_call(self):
        provider = Mock()
        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "surprise"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                "Need crew available immediately",
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_not_called()
        self.assertIsNone(constraint)
        self.assertEqual(diagnostics["mode"], "deterministic")
        self.assertFalse(diagnostics["provider_invoked"])
        self.assertFalse(diagnostics["dispatched"])

    def test_shadow_mode_invokes_provider_but_does_not_dispatch(self):
        prompt = "Need crew available immediately"
        provider = Mock(return_value=_provider_result(_availability_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "shadow"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertIsNone(constraint)
        self.assertTrue(diagnostics["provider_invoked"])
        self.assertFalse(diagnostics["dispatched"])
        self.assertEqual(diagnostics["validator_result"], "accepted")

    def test_shadow_mode_invokes_provider_but_dispatches_no_promoted_families(self):
        prompt = "Need crew available immediately with vessels above 50000 GT"
        provider = Mock(return_value=_provider_result(_combined_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "shadow"}, clear=False):
            constraints, diagnostics = promoted_constraints_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertEqual(constraints, {})
        self.assertFalse(diagnostics["availability"]["dispatched"])
        self.assertFalse(diagnostics["coc_country_match"]["dispatched"])
        self.assertFalse(diagnostics["vessel_tonnage"]["dispatched"])
        self.assertEqual(diagnostics["availability"]["validator_result"], "accepted")
        self.assertEqual(diagnostics["coc_country_match"]["validator_result"], "accepted")
        self.assertEqual(diagnostics["vessel_tonnage"]["validator_result"], "accepted")

    def test_live_mode_dispatches_valid_promoted_availability_constraint(self):
        prompt = "Need crew available immediately"
        provider = Mock(return_value=_provider_result(_availability_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertEqual(
            constraint,
            {
                "value_type": "status",
                "display_value": "available immediately",
                "resolved_reference_date": "2026-06-30",
                "status": "immediately",
            },
        )
        self.assertTrue(diagnostics["provider_invoked"])
        self.assertTrue(diagnostics["dispatched"])
        self.assertEqual(diagnostics["validator_result"], "accepted")

    def test_live_mode_dispatches_availability_and_vessel_tonnage_independently(self):
        prompt = "Need crew available immediately with vessels above 50000 GT"
        provider = Mock(return_value=_provider_result(_combined_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraints, diagnostics = promoted_constraints_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertEqual(
            constraints["availability"],
            {
                "value_type": "status",
                "display_value": "available immediately",
                "resolved_reference_date": "2026-06-30",
                "status": "immediately",
            },
        )
        self.assertEqual(
            constraints["vessel_tonnage"],
            {
                "min_value": 50000,
                "max_value": None,
                "unit": "gt_grt",
            },
        )
        self.assertTrue(diagnostics["availability"]["dispatched"])
        self.assertTrue(diagnostics["vessel_tonnage"]["dispatched"])
        self.assertEqual(diagnostics["availability"]["validator_result"], "accepted")
        self.assertEqual(diagnostics["vessel_tonnage"]["validator_result"], "accepted")

    def test_live_mode_dispatches_three_promoted_families_independently(self):
        prompt = "Need crew available immediately with vessels above 50000 GT and Indian CoC"
        provider = Mock(return_value=_provider_result(_combined_three_family_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraints, diagnostics = promoted_constraints_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertEqual(set(constraints), {"availability", "coc_country_match", "vessel_tonnage"})
        self.assertEqual(
            constraints["coc_country_match"],
            {
                "countries": ["india"],
                "operator": "contains_any",
                "display_value": "Indian CoC",
            },
        )
        self.assertTrue(diagnostics["availability"]["dispatched"])
        self.assertTrue(diagnostics["coc_country_match"]["dispatched"])
        self.assertTrue(diagnostics["vessel_tonnage"]["dispatched"])

    def test_live_mode_marks_only_families_seen_in_provider_payload(self):
        prompt = "Need crew available immediately with vessels above 50000 GT"
        provider = Mock(return_value=_provider_result(_availability_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraints, diagnostics = promoted_constraints_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertEqual(set(constraints), {"availability"})
        self.assertTrue(diagnostics["availability"]["family_seen"])
        self.assertFalse(diagnostics["coc_country_match"]["family_seen"])
        self.assertFalse(diagnostics["vessel_tonnage"]["family_seen"])
        self.assertFalse(diagnostics["coc_country_match"]["dispatched"])
        self.assertFalse(diagnostics["vessel_tonnage"]["dispatched"])
        self.assertEqual(diagnostics["coc_country_match"]["validator_result"], "accepted")
        self.assertEqual(diagnostics["vessel_tonnage"]["validator_result"], "accepted")

    def test_live_mode_preserves_helper_tool_diagnostics(self):
        prompt = "Need crew available immediately"
        provider = Mock(return_value=_provider_result_with_helpers(_availability_payload(prompt)))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        self.assertIsNotNone(constraint)
        self.assertTrue(diagnostics["dispatched"])
        self.assertEqual(diagnostics["helper_tool_version"], "1.0.0")
        self.assertEqual(diagnostics["helper_tool_call_count"], 1)
        self.assertEqual(diagnostics["helper_tool_calls"][0]["tool_id"], "locate_prompt_span.v1")

    def test_live_mode_rejects_invalid_payload_before_dispatch(self):
        prompt = "Need crew available immediately"
        payload = _availability_payload(prompt)
        payload["constraints"][0]["parameters"]["value_type"] = "unsupported"
        provider = Mock(return_value=_provider_result(payload))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                prompt,
                reference_date="2026-06-30",
                provider=provider,
            )

        self.assertIsNone(constraint)
        self.assertFalse(diagnostics["dispatched"])
        self.assertEqual(diagnostics["validator_result"], "rejected")
        self.assertTrue(diagnostics["validator_errors"])

    def test_live_mode_without_credentials_falls_back_without_dispatch(self):
        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=True):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                "Need crew available immediately",
                reference_date="2026-06-30",
            )

        self.assertIsNone(constraint)
        self.assertTrue(diagnostics["provider_invoked"])
        self.assertFalse(diagnostics["dispatched"])
        self.assertEqual(diagnostics["transport_error"], "missing_api_credentials")

    def test_live_mode_provider_transport_error_does_not_dispatch(self):
        provider = Mock(return_value=AvailabilityNormalizerProviderResult(
            model_id="fake-model",
            prompt_template_version="test-template",
            raw_llm_output=None,
            parsed_payload=None,
            transport_error="Timeout: request timed out",
        ))

        with patch.dict("os.environ", {"NJORDHR_LLM_NORMALIZER_MODE": "live"}, clear=False):
            constraint, diagnostics = promoted_availability_constraint_from_prompt(
                "Need crew available immediately",
                reference_date="2026-06-30",
                provider=provider,
            )

        provider.assert_called_once()
        self.assertIsNone(constraint)
        self.assertTrue(diagnostics["provider_invoked"])
        self.assertFalse(diagnostics["dispatched"])
        self.assertEqual(diagnostics["transport_error"], "Timeout: request timed out")
        self.assertEqual(diagnostics["validator_result"], "rejected")
        self.assertEqual(diagnostics["validator_errors"], ["provider returned no parsed payload"])


if __name__ == "__main__":
    unittest.main()
