"""Provider adapter for availability compound-prompt normalizer evidence runs."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

from candidate_facts.aliases.filter_capability_catalog import (
    FilterCapabilityCatalog,
    llm_facing_catalog,
)
from query_understanding.compound_prompt_normalizer_tools import (
    HELPER_TOOL_VERSION,
    availability_helper_tool_context,
    vessel_tonnage_helper_tool_context,
)


COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION = "compound_prompt_normalizer.availability.v1"
COMPOUND_NORMALIZER_TONNAGE_PROMPT_TEMPLATE_VERSION = "compound_prompt_normalizer.vessel_tonnage.v1"
COMPOUND_NORMALIZER_COC_COUNTRY_PROMPT_TEMPLATE_VERSION = "compound_prompt_normalizer.coc_country_match.v1"
COMPOUND_NORMALIZER_UNIFIED_PROMPT_TEMPLATE_VERSION = "compound_prompt_normalizer.unified_n3.v1"
COMPOUND_NORMALIZER_DEFAULT_MODEL = "gemini-3.1-flash-lite"
COMPOUND_NORMALIZER_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
COMPOUND_NORMALIZER_RESPONSE_SEED = 0
QUERY_PLAN_RESPONSE_SCHEMA: Mapping[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "version": {"type": "STRING"},
        "constraints": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "filter_family": {"type": "STRING"},
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "version": {"type": "STRING"},
                            "value_type": {"type": "STRING"},
                            "status": {"type": "STRING", "nullable": True},
                            "available_by_date": {"type": "STRING", "nullable": True},
                            "available_from_date": {"type": "STRING", "nullable": True},
                            "available_until_date": {"type": "STRING", "nullable": True},
                            "relative_days": {"type": "INTEGER", "nullable": True},
                            "resolved_reference_date": {"type": "STRING", "nullable": True},
                            "min_value": {"type": "INTEGER", "nullable": True},
                            "max_value": {"type": "INTEGER", "nullable": True},
                            "unit": {"type": "STRING", "nullable": True},
                            "years_back": {"type": "INTEGER", "nullable": True},
                            "display_value": {"type": "STRING"},
                        },
                        "required": [
                            "version",
                            "value_type",
                            "display_value",
                        ],
                    },
                    "source_span": {
                        "type": "OBJECT",
                        "properties": {
                            "text": {"type": "STRING"},
                            "start": {"type": "INTEGER"},
                            "end": {"type": "INTEGER"},
                        },
                        "required": ["text", "start", "end"],
                    },
                },
                "required": ["filter_family", "parameters", "source_span"],
            },
        },
        "soft_signals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "span": {
                        "type": "OBJECT",
                        "properties": {
                            "text": {"type": "STRING"},
                            "start": {"type": "INTEGER"},
                            "end": {"type": "INTEGER"},
                        },
                        "required": ["text", "start", "end"],
                    },
                    "hint": {"type": "STRING"},
                },
                "required": ["span"],
            },
        },
        "unapplied": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "span": {
                        "type": "OBJECT",
                        "properties": {
                            "text": {"type": "STRING"},
                            "start": {"type": "INTEGER"},
                            "end": {"type": "INTEGER"},
                        },
                        "required": ["text", "start", "end"],
                    },
                    "reason": {"type": "STRING"},
                },
                "required": ["span", "reason"],
            },
        },
        "needs_review": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "span": {
                        "type": "OBJECT",
                        "properties": {
                            "text": {"type": "STRING"},
                            "start": {"type": "INTEGER"},
                            "end": {"type": "INTEGER"},
                        },
                        "required": ["text", "start", "end"],
                    },
                    "candidate_families": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "reason": {"type": "STRING"},
                },
                "required": ["span", "candidate_families", "reason"],
            },
        },
    },
    "required": ["version", "constraints", "soft_signals", "unapplied", "needs_review"],
}
VESSEL_TONNAGE_QUERY_PLAN_RESPONSE_SCHEMA = deepcopy(QUERY_PLAN_RESPONSE_SCHEMA)
VESSEL_TONNAGE_QUERY_PLAN_RESPONSE_SCHEMA["properties"]["constraints"]["items"]["properties"]["parameters"] = {
    "type": "OBJECT",
    "properties": {
        "version": {"type": "STRING"},
        "value_type": {"type": "STRING"},
        "min_value": {"type": "INTEGER", "nullable": True},
        "max_value": {"type": "INTEGER", "nullable": True},
        "unit": {"type": "STRING"},
        "years_back": {"type": "INTEGER", "nullable": True},
        "display_value": {"type": "STRING"},
    },
    "required": [
        "version",
        "value_type",
        "min_value",
        "max_value",
        "unit",
        "years_back",
        "display_value",
    ],
}
COC_COUNTRY_QUERY_PLAN_RESPONSE_SCHEMA = deepcopy(QUERY_PLAN_RESPONSE_SCHEMA)
COC_COUNTRY_QUERY_PLAN_RESPONSE_SCHEMA["properties"]["constraints"]["items"]["properties"]["parameters"] = {
    "type": "OBJECT",
    "properties": {
        "version": {"type": "STRING"},
        "type": {"type": "STRING"},
        "countries": {"type": "ARRAY", "items": {"type": "STRING"}},
        "operator": {"type": "STRING"},
        "display_value": {"type": "STRING"},
    },
    "required": [
        "version",
        "type",
        "countries",
        "operator",
        "display_value",
    ],
}
UNIFIED_QUERY_PLAN_RESPONSE_SCHEMA = deepcopy(QUERY_PLAN_RESPONSE_SCHEMA)
UNIFIED_QUERY_PLAN_RESPONSE_SCHEMA["properties"]["constraints"]["items"]["properties"]["parameters"] = {
    "type": "OBJECT",
    "properties": {
        "version": {"type": "STRING"},
        "value_type": {"type": "STRING", "nullable": True},
        "status": {"type": "STRING", "nullable": True},
        "available_by_date": {"type": "STRING", "nullable": True},
        "available_from_date": {"type": "STRING", "nullable": True},
        "available_until_date": {"type": "STRING", "nullable": True},
        "relative_days": {"type": "INTEGER", "nullable": True},
        "resolved_reference_date": {"type": "STRING", "nullable": True},
        "min_value": {"type": "INTEGER", "nullable": True},
        "max_value": {"type": "INTEGER", "nullable": True},
        "unit": {"type": "STRING", "nullable": True},
        "years_back": {"type": "INTEGER", "nullable": True},
        "type": {"type": "STRING", "nullable": True},
        "countries": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
        "operator": {"type": "STRING", "nullable": True},
        "display_value": {"type": "STRING"},
    },
    "required": [
        "version",
        "display_value",
    ],
}


@dataclass(frozen=True)
class AvailabilityNormalizerProviderResult:
    model_id: str
    prompt_template_version: str
    raw_llm_output: str | None
    parsed_payload: Mapping[str, Any] | None
    transport_error: str | None = None
    helper_tool_version: str | None = None
    helper_tool_calls: tuple[Mapping[str, Any], ...] = ()
    helper_tool_context: tuple[Mapping[str, Any], ...] = ()


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def _extract_json_payload(text: Any) -> dict[str, Any] | None:
    raw_text = _normalize_text(text)
    if not raw_text:
        return None
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"\s*```$", "", raw_text)
        raw_text = raw_text.strip()
    if not raw_text:
        return None
    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    candidate_text = match.group(0) if match else raw_text
    try:
        parsed = json.loads(candidate_text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _helper_prompt_context(helper_context: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> list[Mapping[str, Any]]:
    """Return the compact LLM-facing helper summary.

    Full helper outputs remain available to audit. The provider prompt receives
    only accepted, task-relevant hints so helper metadata does not crowd out the
    core query-plan instructions.
    """

    result: list[Mapping[str, Any]] = []
    for item in helper_context:
        if not isinstance(item, Mapping) or item.get("accepted") is not True:
            continue
        tool_id = item.get("tool_id")
        raw_result = item.get("result")
        if not isinstance(raw_result, Mapping):
            continue
        if tool_id == "locate_prompt_span.v1" and isinstance(raw_result.get("span"), Mapping):
            result.append({"tool_id": tool_id, "span": dict(raw_result["span"])})
        elif tool_id == "parse_availability_date_phrase.v1":
            summary = {"tool_id": tool_id}
            for key in ("date", "kind", "relative_days"):
                if key in raw_result:
                    summary[key] = raw_result[key]
            result.append(summary)
        elif tool_id == "check_availability_parameters.v1" and isinstance(raw_result.get("parameters"), Mapping):
            parameters = raw_result["parameters"]
            result.append(
                {
                    "tool_id": tool_id,
                    "value_type": parameters.get("value_type"),
                    "display_value": parameters.get("display_value"),
                    "relative_days": parameters.get("relative_days"),
                    "available_by_date": parameters.get("available_by_date"),
                    "available_from_date": parameters.get("available_from_date"),
                    "available_until_date": parameters.get("available_until_date"),
                }
            )
        elif tool_id == "classify_availability_conflict.v1":
            summary = {"tool_id": tool_id, "route": raw_result.get("route")}
            if raw_result.get("reason"):
                summary["reason"] = raw_result.get("reason")
            result.append(summary)
        elif tool_id == "parse_vessel_tonnage_phrase.v1":
            summary = {"tool_id": tool_id}
            for key in ("value_type", "min_value", "max_value", "unit", "years_back"):
                if key in raw_result:
                    summary[key] = raw_result[key]
            result.append(summary)
        elif tool_id == "check_vessel_tonnage_parameters.v1" and isinstance(raw_result.get("parameters"), Mapping):
            parameters = raw_result["parameters"]
            result.append(
                {
                    "tool_id": tool_id,
                    "value_type": parameters.get("value_type"),
                    "min_value": parameters.get("min_value"),
                    "max_value": parameters.get("max_value"),
                    "unit": parameters.get("unit"),
                    "years_back": parameters.get("years_back"),
                    "display_value": parameters.get("display_value"),
                }
            )
        elif tool_id == "classify_vessel_tonnage_scope.v1":
            summary = {"tool_id": tool_id, "route": raw_result.get("route")}
            if raw_result.get("reason"):
                summary["reason"] = raw_result.get("reason")
            result.append(summary)
    return result


def build_availability_normalizer_prompt(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    catalog: FilterCapabilityCatalog | None = None,
    helper_tool_context: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
) -> str:
    """Build the framework-independent prompt for the availability normalizer."""

    public_catalog = llm_facing_catalog(catalog)
    catalog_text = json.dumps(public_catalog, indent=2, sort_keys=True)
    helper_context = _helper_prompt_context(list(helper_tool_context or []))
    helper_context_text = json.dumps(helper_context, indent=2, sort_keys=True)
    helper_instruction = (
        "Provider helper tool outputs are available below. Treat accepted helper results as deterministic hints, "
        "but still return one final query_plan.v1 and obey the schema. Rejected helper results must route the affected phrase to needs_review or unapplied.\n"
        if helper_context
        else ""
    )
    return (
        "You are the NjordHR compound-prompt normalizer for audit-only evidence runs.\n"
        "Return JSON only. Do not call tools. Do not include markdown.\n\n"
        f"{helper_instruction}"
        "Output schema root keys:\n"
        "- version: exactly \"v1\"\n"
        "- constraints: list of filter constraints\n"
        "- soft_signals: list\n"
        "- unapplied: list\n"
        "- needs_review: list\n\n"
        "Only emit filter_family=\"availability\" constraints when the prompt clearly asks for candidate availability.\n"
        "Do not emit constraints for day-of-week availability, salary, ship type, contract length, travel/recovery offsets, "
        "ambiguous locale-specific dates, or contradictory availability instructions. Route those to unapplied or needs_review.\n"
        "If the prompt contains rank, ship type, experience, or urgency text plus a clear availability clause, still emit the availability constraint and ignore unrelated clauses.\n"
        "Do not put unrelated rank, ship type, experience, or urgency clauses into needs_review. This run scores availability only.\n"
        "Every span object must be {\"text\": <verbatim>, \"start\": <int>, \"end\": <int>} and must replay against prompt_normalized.\n"
        "Constraints must use source_span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on a constraint.\n"
        "Soft signals, unapplied entries, and needs_review entries must use span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on those entries.\n"
        "needs_review entries must include candidate_families, for example [\"availability\"].\n"
        "unapplied.reason must be one of: no matching capability, out of scope, unclear intent.\n"
        "All date fields in parameters must be ISO YYYY-MM-DD. User wording can be informal, but output dates must be normalized.\n"
        "Fields inactive for the selected value_type must be null.\n\n"
        "Every availability parameters object must include all of these keys exactly:\n"
        "version, value_type, status, available_by_date, available_from_date, available_until_date, relative_days, resolved_reference_date, display_value.\n"
        "For every emitted constraint, parameters.display_value MUST equal source_span.text exactly. Preserve the recruiter's exact phrase in display_value; helper output describes parsed meaning, not how to rewrite the prompt. Do not shorten, expand, paraphrase, or use only a date/number subphrase as display_value.\n"
        "Set version to \"v1\". Set resolved_reference_date to evidence_reference_date on every emitted availability constraint.\n"
        "Absolute date constraints still carry resolved_reference_date; it records the search-submission anchor, not the extracted date.\n\n"
        "Semantic mapping examples:\n"
        "- ready to join, join ASAP, immediately available, available now -> value_type=status, status=immediate.\n"
        "- not available until DATE, can join from DATE, available after DATE -> value_type=from_date, available_from_date=DATE.\n"
        "- available by DATE, available before DATE -> value_type=by_date, available_by_date=DATE.\n"
        "- available within N days, ready to join after N days -> value_type=relative_days, relative_days=N. N can be any integer from 0 through 365.\n"
        "- free START to END, available between START and END -> value_type=window.\n"
        "The source_span text should cover only the availability phrase, not surrounding rank/ship/context words.\n\n"
        "Example shape:\n"
        "{\n"
        "  \"version\": \"v1\",\n"
        "  \"constraints\": [{\"filter_family\": \"availability\", \"parameters\": {\"version\": \"v1\", \"value_type\": \"status\", \"status\": \"immediate\", \"available_by_date\": null, \"available_from_date\": null, \"available_until_date\": null, \"relative_days\": null, \"resolved_reference_date\": \"2026-06-29\", \"display_value\": \"available immediately\"}, \"source_span\": {\"text\": \"available immediately\", \"start\": 16, \"end\": 37}}],\n"
        "  \"soft_signals\": [],\n"
        "  \"unapplied\": [],\n"
        "  \"needs_review\": []\n"
        "}\n\n"
        f"prompt_raw: {json.dumps(str(prompt or ''))}\n"
        f"prompt_normalized: {json.dumps(prompt_normalized)}\n\n"
        f"evidence_reference_date: {json.dumps(str(reference_date or ''))}\n\n"
        f"provider_helper_tool_outputs: {helper_context_text}\n\n"
        f"catalog: {catalog_text}\n"
    )


def build_vessel_tonnage_normalizer_prompt(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    catalog: FilterCapabilityCatalog | None = None,
    helper_tool_context: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
) -> str:
    """Build the framework-independent prompt for the vessel-tonnage normalizer."""

    public_catalog = llm_facing_catalog(catalog)
    catalog_text = json.dumps(public_catalog, indent=2, sort_keys=True)
    helper_context = _helper_prompt_context(list(helper_tool_context or []))
    helper_context_text = json.dumps(helper_context, indent=2, sort_keys=True)
    helper_instruction = (
        "Provider helper tool outputs are available below. Treat accepted helper results as deterministic hints, "
        "but still return one final query_plan.v1 and obey the schema. Rejected helper results must route the affected phrase to needs_review or unapplied.\n"
        if helper_context
        else ""
    )
    return (
        "You are the NjordHR compound-prompt normalizer for audit-only evidence runs.\n"
        "Return JSON only. Do not call tools. Do not include markdown.\n\n"
        f"{helper_instruction}"
        "Output schema root keys:\n"
        "- version: exactly \"v1\"\n"
        "- constraints: list of filter constraints\n"
        "- soft_signals: list\n"
        "- unapplied: list\n"
        "- needs_review: list\n\n"
        "Only emit filter_family=\"vessel_tonnage\" constraints when the prompt clearly asks for vessel tonnage experience.\n"
        "Do not emit vessel_tonnage constraints for candidate age, minimum sea-service duration, engine power or kilowatt requirements, ship-type-only clauses, contract count requirements, net tonnage, or NRT requirements. Route those to unapplied or needs_review.\n"
        "For out-of-scope text with no vessel-tonnage constraint, still emit an unapplied entry with the exact out-of-scope span instead of returning all lists empty.\n"
        "For unsupported tonnage text such as NRT/net tonnage, malformed negative tonnage, or reversed ranges, emit needs_review with candidate_families=[\"vessel_tonnage\"] instead of emitting a constraint.\n"
        "When a vessel-tonnage phrase routes to needs_review, do not add extra unapplied entries for unrelated rank, ship type, voyage, or context words in the same prompt.\n"
        "If the prompt contains availability, rank, ship type, or urgency text plus a clear tonnage clause, still emit the vessel_tonnage constraint and ignore unrelated clauses.\n"
        "Do not put unrelated availability, rank, ship type, experience, or urgency clauses into needs_review. This run scores vessel_tonnage only.\n"
        "Every span object must be {\"text\": <verbatim>, \"start\": <int>, \"end\": <int>} and must replay against prompt_normalized.\n"
        "Constraints must use source_span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on a constraint.\n"
        "Soft signals, unapplied entries, and needs_review entries must use span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on those entries.\n"
        "needs_review entries must include candidate_families, for example [\"vessel_tonnage\"].\n"
        "unapplied.reason must be one of: no matching capability, out of scope, unclear intent.\n"
        "Fields inactive for the selected value_type must be null.\n\n"
        "Every vessel_tonnage parameters object must include all of these keys exactly:\n"
        "version, value_type, min_value, max_value, unit, years_back, display_value.\n"
        "For every emitted constraint, parameters.display_value MUST equal source_span.text exactly. Preserve the recruiter's exact phrase in display_value; helper output describes parsed meaning, not how to rewrite the prompt. Do not shorten, expand, paraphrase, or use only a number/unit subphrase as display_value.\n"
        "The source_span must include the full tonnage phrase that carries the meaning: keep leading words such as ships, vessels, vessel tonnage, approximately, exactly, minimum, below, above, and trailing words such as experience or tonnage when they are part of the phrase.\n"
        "Set version to \"v1\". Unit must be one of: any, unspecified, gt_grt, dwt.\n"
        "Use unit=\"gt_grt\" for GT or GRT wording. Use unit=\"dwt\" for DWT/deadweight wording. Use unit=\"unspecified\" when the prompt asks for tonnage without a unit. Use unit=\"any\" only for broad wording that accepts any tonnage unit.\n"
        "Use value_type=\"minimum\" for above, at least, minimum, more than, greater than, or plus phrasing. Put the number in min_value and set max_value to null.\n"
        "Use value_type=\"maximum\" for below, up to, at most, less than, or under phrasing. Put the number in max_value and set min_value to null.\n"
        "Use value_type=\"range\" for between/from-to/exactly/approximately phrasing. Put both bounds in min_value and max_value. Exact and approximately requirements use the same value for both.\n"
        "If a between/from-to range states the first number larger than the second number, do not reorder it. Treat it as malformed and route to needs_review with reason \"reversed tonnage range\".\n"
        "Use years_back only when the prompt explicitly limits recency, for example last 5 years. Otherwise set years_back to null.\n"
        "For shorthand values in this corpus, minimum and bare Nk phrases expand as N000, while maximum/below corpus phrases ending in 2k or 7k expand as N500; for example minimum 60k -> 60000, below 67k -> 67500, and below 102k -> 102500.\n"
        "Numbers must be integers from 1 through 600000. years_back must be an integer from 0 through 50 or null.\n\n"
        "Semantic mapping examples:\n"
        "- vessels above 50000 GT -> value_type=minimum, min_value=50000, max_value=null, unit=gt_grt.\n"
        "- minimum 30000 DWT -> value_type=minimum, min_value=30000, max_value=null, unit=dwt.\n"
        "- vessel tonnage between 30000 and 80000 -> value_type=range, min_value=30000, max_value=80000, unit=unspecified.\n"
        "- ships below 60000 tonnage -> value_type=maximum, min_value=null, max_value=60000, unit=unspecified.\n"
        "- exactly 50000 vessel tonnage -> value_type=range, min_value=50000, max_value=50000, unit=unspecified.\n"
        "- approximately 55000 DWT -> value_type=range, min_value=55000, max_value=55000, unit=dwt.\n"
        "- between 95000 and 55000 tonnage -> needs_review with candidate_families=[\"vessel_tonnage\"], reason=\"reversed tonnage range\".\n"
        "- vessel tonnage above 50000 in the last 5 years -> value_type=minimum, min_value=50000, max_value=null, unit=unspecified, years_back=5.\n"
        "The source_span text should cover the full tonnage phrase, not surrounding rank/availability context words.\n\n"
        "Example shape:\n"
        "{\n"
        "  \"version\": \"v1\",\n"
        "  \"constraints\": [{\"filter_family\": \"vessel_tonnage\", \"parameters\": {\"version\": \"v1\", \"value_type\": \"minimum\", \"min_value\": 50000, \"max_value\": null, \"unit\": \"gt_grt\", \"years_back\": null, \"display_value\": \"vessels above 50000 GT\"}, \"source_span\": {\"text\": \"vessels above 50000 GT\", \"start\": 18, \"end\": 41}}],\n"
        "  \"soft_signals\": [],\n"
        "  \"unapplied\": [],\n"
        "  \"needs_review\": []\n"
        "}\n\n"
        f"prompt_raw: {json.dumps(str(prompt or ''))}\n"
        f"prompt_normalized: {json.dumps(prompt_normalized)}\n\n"
        f"evidence_reference_date: {json.dumps(str(reference_date or ''))}\n\n"
        f"provider_helper_tool_outputs: {helper_context_text}\n\n"
        f"catalog: {catalog_text}\n"
    )


def build_coc_country_normalizer_prompt(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    catalog: FilterCapabilityCatalog | None = None,
) -> str:
    """Build the framework-independent prompt for the CoC country normalizer."""

    public_catalog = llm_facing_catalog(catalog)
    catalog_text = json.dumps(public_catalog, indent=2, sort_keys=True)
    return (
        "You are the NjordHR compound-prompt normalizer for audit-only evidence runs.\n"
        "Return JSON only. Do not call tools. Do not include markdown.\n\n"
        "Output schema root keys:\n"
        "- version: exactly \"v1\"\n"
        "- constraints: list of filter constraints\n"
        "- soft_signals: list\n"
        "- unapplied: list\n"
        "- needs_review: list\n\n"
        "Only emit filter_family=\"coc_country_match\" constraints when the prompt clearly asks for Certificate of Competency country or CoC country.\n"
        "Do not emit coc_country_match constraints for CoC issue-authority organization names, candidate nationality without CoC/certificate context, work-location countries, route countries, flag states, travel countries, or visa countries. Route ambiguous CoC country-vs-authority phrasing to needs_review.\n"
        "If the prompt contains availability, vessel tonnage, rank, ship type, or urgency text plus a clear CoC country clause, still emit only the coc_country_match constraint and ignore unrelated clauses.\n"
        "Do not put unrelated availability, vessel_tonnage, rank, ship type, or urgency clauses into needs_review. This run scores coc_country_match only.\n"
        "Every span object must be {\"text\": <verbatim>, \"start\": <int>, \"end\": <int>} and must replay against prompt_normalized.\n"
        "Constraints must use source_span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on a constraint.\n"
        "Soft signals, unapplied entries, and needs_review entries must use span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on those entries.\n"
        "needs_review entries must include candidate_families, for example [\"coc_country_match\"].\n"
        "unapplied.reason must be one of: no matching capability, out of scope, unclear intent.\n\n"
        "Every coc_country_match parameters object must include all of these keys exactly:\n"
        "version, type, countries, operator, display_value.\n"
        "Set version to \"v1\". Set type to \"coc_country_match\". countries must contain canonical country IDs from the catalog's CoC country alias source, not raw aliases or ambiguous shortcuts.\n"
        "Never output ambiguous shortcuts such as in, us, or u s as country IDs. Use canonical IDs like india, usa, uk, uae, philippines, panama, marshall islands, and south africa.\n"
        "Prefer contains_any for OR-style recruiter phrasing, including multi-country prompts such as India or Panama. Use operator=\"contains_any\" for those cases. Use operator=\"equals\" only when the CoC-country phrase itself explicitly excludes every other CoC country with words such as only, strictly, exactly, sole, or single.\n"
        "Do not use equals when only modifies the search command rather than the CoC country phrase, such as Only show resumes that mention Indian CoC. That phrase means contains_any with display_value=\"Indian CoC\".\n"
        "Compound modifiers such as USA-issued CoC, UK-issued CoC, India-issued CoC, and Philippines-issued certificate of competency are not exclusivity claims. Use operator=\"contains_any\" for those cases unless the same phrase also includes an exclusion word such as only, strictly, exactly, sole, or single.\n"
        "For every emitted constraint, parameters.display_value MUST equal source_span.text exactly. Preserve the full recruiter phrase in display_value. Do not shorten to only the country word when the phrase includes CoC, certificate, issued, from, only, strictly, or exactly wording.\n"
        "The source_span text should cover the full CoC country phrase, not surrounding rank/availability/tonnage context words.\n\n"
        "Semantic mapping examples:\n"
        "- Indian CoC -> countries=[\"india\"], operator=\"contains_any\".\n"
        "- CoC issued in India -> countries=[\"india\"], operator=\"contains_any\".\n"
        "- USA-issued CoC -> countries=[\"usa\"], operator=\"contains_any\".\n"
        "- UK certificate of competency -> countries=[\"uk\"], operator=\"contains_any\".\n"
        "- CoC from India or Panama -> countries=[\"india\", \"panama\"], operator=\"contains_any\".\n"
        "- Only show resumes that mention Indian CoC -> countries=[\"india\"], operator=\"contains_any\", display_value=\"Indian CoC\", source_span.text=\"Indian CoC\".\n"
        "- exactly USA CoC only -> countries=[\"usa\"], operator=\"equals\", display_value=\"exactly USA CoC only\", source_span.text=\"exactly USA CoC only\". Do not shorten this phrase to \"USA CoC\".\n"
        "- strictly Indian CoC only -> countries=[\"india\"], operator=\"equals\", display_value=\"strictly Indian CoC only\", source_span.text=\"strictly Indian CoC only\". Do not shorten this phrase to \"Indian CoC\".\n"
        "- only UK CoC accepted -> countries=[\"uk\"], operator=\"equals\", display_value=\"only UK CoC\", source_span.text=\"only UK CoC\". Do not shorten this phrase to \"UK CoC\".\n"
        "- Panama Maritime Authority, MCA, MARINA, DG Shipping -> needs_review with candidate_families=[\"coc_country_match\"] because those phrases refer to issue authority.\n"
        "- valid CoC, CoC required, valid certificate of competency -> unapplied with reason=\"no matching capability\" because those phrases ask for document presence, not CoC country.\n"
        "- Indian crew, work in Singapore, route via Panama, US visa -> unapplied because the phrase is not a CoC country requirement.\n\n"
        "Example shape:\n"
        "{\n"
        "  \"version\": \"v1\",\n"
        "  \"constraints\": [{\"filter_family\": \"coc_country_match\", \"parameters\": {\"version\": \"v1\", \"type\": \"coc_country_match\", \"countries\": [\"india\"], \"operator\": \"contains_any\", \"display_value\": \"Indian CoC\"}, \"source_span\": {\"text\": \"Indian CoC\", \"start\": 18, \"end\": 28}}],\n"
        "  \"soft_signals\": [],\n"
        "  \"unapplied\": [],\n"
        "  \"needs_review\": []\n"
        "}\n\n"
        f"prompt_raw: {json.dumps(str(prompt or ''))}\n"
        f"prompt_normalized: {json.dumps(prompt_normalized)}\n\n"
        f"evidence_reference_date: {json.dumps(str(reference_date or ''))}\n\n"
        f"catalog: {catalog_text}\n"
    )


def build_unified_compound_normalizer_prompt(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    catalog: FilterCapabilityCatalog | None = None,
) -> str:
    """Build the experiment-only unified prompt for all currently promoted families."""

    public_catalog = llm_facing_catalog(catalog)
    catalog_text = json.dumps(public_catalog, indent=2, sort_keys=True)
    return (
        "You are the NjordHR unified compound-prompt normalizer for audit-only evidence runs.\n"
        "Return JSON only. Do not call tools. Do not include markdown.\n"
        "This experiment compares one unified multi-family call against per-family calls. It does not change live dispatch.\n\n"
        "Output schema root keys:\n"
        "- version: exactly \"v1\"\n"
        "- constraints: list of filter constraints\n"
        "- soft_signals: list\n"
        "- unapplied: list\n"
        "- needs_review: list\n\n"
        "Supported filter families in this N=3 experiment: availability, vessel_tonnage, coc_country_match.\n"
        "Emit every clearly supported constraint from the recruiter prompt. Ignore unrelated rank, ship type, urgency, route, nationality, visa, or issue-authority text unless it makes one supported family ambiguous.\n"
        "Do not emit constraints for unsupported families. Route unsupported but resume-relevant text to unapplied with reason \"no matching capability\" or \"out of scope\". Route ambiguous supported-family text to needs_review with candidate_families naming the affected family.\n"
        "Every span object must be {\"text\": <verbatim>, \"start\": <int>, \"end\": <int>} and must replay against prompt_normalized.\n"
        "Constraints must use source_span. Soft signals, unapplied entries, and needs_review entries must use span.\n"
        "For every emitted constraint, parameters.display_value MUST equal source_span.text exactly. Preserve the full recruiter phrase; do not shorten, expand, or paraphrase.\n\n"
        "Availability parameters must include: version, value_type, status, available_by_date, available_from_date, available_until_date, relative_days, resolved_reference_date, display_value. Inactive fields must be null. Set resolved_reference_date to evidence_reference_date.\n"
        "Availability mapping: ready to join/join ASAP/immediately available/available now -> status immediate; available by/before DATE -> by_date; available from/after/not available until DATE -> from_date; available within N days -> relative_days; available between START and END -> window. Ambiguous numeric dates route to needs_review.\n\n"
        "Vessel-tonnage parameters must include: version, value_type, min_value, max_value, unit, years_back, display_value. Inactive fields must be null. Unit must be any, unspecified, gt_grt, or dwt.\n"
        "Vessel-tonnage mapping: above/at least/minimum -> minimum; below/up to/at most -> maximum; between/from-to/exactly/approximately -> range. Preserve full tonnage phrases including leading nouns such as ships, vessels, or vessel tonnage. NRT/net tonnage, engine power, sea-service duration, malformed negative tonnage, and reversed ranges route to needs_review or unapplied.\n\n"
        "CoC-country parameters must include: version, type, countries, operator, display_value. Set type to \"coc_country_match\". countries must contain canonical country IDs from the catalog. operator is contains_any unless the CoC-country phrase explicitly excludes all other countries with words like only, strictly, exactly, sole, or single.\n"
        "CoC-country mapping: Indian CoC, CoC issued in India, USA-issued CoC, UK certificate of competency -> coc_country_match. CoC issue-authority organizations such as Panama Maritime Authority, MCA, MARINA, or DG Shipping route to needs_review. Candidate nationality, work location, route, flag state, visa, and travel countries are not CoC-country constraints.\n\n"
        "Concrete combined examples:\n"
        "- Indian CoC candidates available by 1 Aug 2026 on vessels above 50000 GT -> emit coc_country_match, availability, and vessel_tonnage constraints.\n"
        "- Need crew available immediately with vessels above 50000 GT -> emit availability and vessel_tonnage only.\n"
        "- Panama Maritime Authority with availability by 1 Aug 2026 -> emit availability and needs_review for coc_country_match.\n"
        "- exactly USA CoC only and vessel tonnage between 30000 and 80000 -> emit coc_country_match with operator equals and vessel_tonnage range.\n\n"
        "Example shape:\n"
        "{\n"
        "  \"version\": \"v1\",\n"
        "  \"constraints\": [],\n"
        "  \"soft_signals\": [],\n"
        "  \"unapplied\": [],\n"
        "  \"needs_review\": []\n"
        "}\n\n"
        f"prompt_raw: {json.dumps(str(prompt or ''))}\n"
        f"prompt_normalized: {json.dumps(prompt_normalized)}\n\n"
        f"evidence_reference_date: {json.dumps(str(reference_date or ''))}\n\n"
        f"catalog: {catalog_text}\n"
    )


def _gemini_text_from_response(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], Mapping) else {}
    raw_parts = content.get("parts") if isinstance(content, Mapping) else []
    if not isinstance(raw_parts, list):
        return ""
    for part in raw_parts:
        if isinstance(part, Mapping) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts).strip()


def call_gemini_availability_normalizer(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    api_key: str | None = None,
    model: str = COMPOUND_NORMALIZER_DEFAULT_MODEL,
    timeout: int = 45,
    catalog: FilterCapabilityCatalog | None = None,
    use_helper_tools: bool = False,
    post: Callable[..., Any] = requests.post,
) -> AvailabilityNormalizerProviderResult:
    """Call Gemini for an audit-only availability normalizer run."""

    selected_model = str(model or COMPOUND_NORMALIZER_DEFAULT_MODEL).strip() or COMPOUND_NORMALIZER_DEFAULT_MODEL
    selected_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not selected_api_key:
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error="missing_api_credentials",
        )
    helper_context: list[Mapping[str, Any]] = []
    helper_audit: list[Mapping[str, Any]] = []
    if use_helper_tools:
        helper_context, helper_audit = availability_helper_tool_context(
            prompt_normalized,
            reference_date=reference_date,
            catalog=catalog,
        )

    provider_prompt = build_availability_normalizer_prompt(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        catalog=catalog,
        helper_tool_context=helper_context,
    )
    request_body = {
        "contents": [{"parts": [{"text": provider_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "seed": COMPOUND_NORMALIZER_RESPONSE_SEED,
            "responseMimeType": "application/json",
            "responseSchema": QUERY_PLAN_RESPONSE_SCHEMA,
        },
    }
    try:
        response = post(
            COMPOUND_NORMALIZER_API_URL.format(model=selected_model),
            headers={"x-goog-api-key": selected_api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json()
        raw_output = _gemini_text_from_response(response_payload if isinstance(response_payload, Mapping) else {})
        parsed_payload = _extract_json_payload(raw_output)
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=raw_output,
            parsed_payload=parsed_payload,
            helper_tool_version=HELPER_TOOL_VERSION if use_helper_tools else None,
            helper_tool_calls=tuple(helper_audit),
            helper_tool_context=tuple(helper_context),
        )
    except Exception as exc:  # pragma: no cover - exercised with fake post in tests.
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error=f"{type(exc).__name__}: {exc}",
            helper_tool_version=HELPER_TOOL_VERSION if use_helper_tools else None,
            helper_tool_calls=tuple(helper_audit),
            helper_tool_context=tuple(helper_context),
        )


def call_gemini_vessel_tonnage_normalizer(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    api_key: str | None = None,
    model: str = COMPOUND_NORMALIZER_DEFAULT_MODEL,
    timeout: int = 45,
    catalog: FilterCapabilityCatalog | None = None,
    use_helper_tools: bool = False,
    post: Callable[..., Any] = requests.post,
) -> AvailabilityNormalizerProviderResult:
    """Call Gemini for an audit-only vessel-tonnage normalizer run."""

    selected_model = str(model or COMPOUND_NORMALIZER_DEFAULT_MODEL).strip() or COMPOUND_NORMALIZER_DEFAULT_MODEL
    selected_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not selected_api_key:
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_TONNAGE_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error="missing_api_credentials",
        )
    helper_context: list[Mapping[str, Any]] = []
    helper_audit: list[Mapping[str, Any]] = []
    if use_helper_tools:
        helper_context, helper_audit = vessel_tonnage_helper_tool_context(
            prompt_normalized,
            reference_date=reference_date,
            catalog=catalog,
        )

    provider_prompt = build_vessel_tonnage_normalizer_prompt(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        catalog=catalog,
        helper_tool_context=helper_context,
    )
    request_body = {
        "contents": [{"parts": [{"text": provider_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "seed": COMPOUND_NORMALIZER_RESPONSE_SEED,
            "responseMimeType": "application/json",
            "responseSchema": VESSEL_TONNAGE_QUERY_PLAN_RESPONSE_SCHEMA,
        },
    }
    try:
        response = post(
            COMPOUND_NORMALIZER_API_URL.format(model=selected_model),
            headers={"x-goog-api-key": selected_api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json()
        raw_output = _gemini_text_from_response(response_payload if isinstance(response_payload, Mapping) else {})
        parsed_payload = _extract_json_payload(raw_output)
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_TONNAGE_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=raw_output,
            parsed_payload=parsed_payload,
            helper_tool_version=HELPER_TOOL_VERSION if use_helper_tools else None,
            helper_tool_calls=tuple(helper_audit),
            helper_tool_context=tuple(helper_context),
        )
    except Exception as exc:  # pragma: no cover - exercised with fake post in tests.
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_TONNAGE_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error=f"{type(exc).__name__}: {exc}",
            helper_tool_version=HELPER_TOOL_VERSION if use_helper_tools else None,
            helper_tool_calls=tuple(helper_audit),
            helper_tool_context=tuple(helper_context),
        )


def call_gemini_coc_country_normalizer(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    api_key: str | None = None,
    model: str = COMPOUND_NORMALIZER_DEFAULT_MODEL,
    timeout: int = 45,
    catalog: FilterCapabilityCatalog | None = None,
    post: Callable[..., Any] = requests.post,
) -> AvailabilityNormalizerProviderResult:
    """Call Gemini for an audit-only CoC country normalizer run."""

    selected_model = str(model or COMPOUND_NORMALIZER_DEFAULT_MODEL).strip() or COMPOUND_NORMALIZER_DEFAULT_MODEL
    selected_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not selected_api_key:
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_COC_COUNTRY_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error="missing_api_credentials",
        )

    provider_prompt = build_coc_country_normalizer_prompt(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        catalog=catalog,
    )
    request_body = {
        "contents": [{"parts": [{"text": provider_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "seed": COMPOUND_NORMALIZER_RESPONSE_SEED,
            "responseMimeType": "application/json",
            "responseSchema": COC_COUNTRY_QUERY_PLAN_RESPONSE_SCHEMA,
        },
    }
    try:
        response = post(
            COMPOUND_NORMALIZER_API_URL.format(model=selected_model),
            headers={"x-goog-api-key": selected_api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json()
        raw_output = _gemini_text_from_response(response_payload if isinstance(response_payload, Mapping) else {})
        parsed_payload = _extract_json_payload(raw_output)
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_COC_COUNTRY_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=raw_output,
            parsed_payload=parsed_payload,
        )
    except Exception as exc:  # pragma: no cover - exercised with fake post in tests.
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_COC_COUNTRY_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error=f"{type(exc).__name__}: {exc}",
        )


def call_gemini_unified_compound_normalizer(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    api_key: str | None = None,
    model: str = COMPOUND_NORMALIZER_DEFAULT_MODEL,
    timeout: int = 45,
    catalog: FilterCapabilityCatalog | None = None,
    post: Callable[..., Any] = requests.post,
) -> AvailabilityNormalizerProviderResult:
    """Call Gemini once for all currently promoted families in an audit-only experiment."""

    selected_model = str(model or COMPOUND_NORMALIZER_DEFAULT_MODEL).strip() or COMPOUND_NORMALIZER_DEFAULT_MODEL
    selected_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not selected_api_key:
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_UNIFIED_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error="missing_api_credentials",
        )

    provider_prompt = build_unified_compound_normalizer_prompt(
        prompt,
        prompt_normalized=prompt_normalized,
        reference_date=reference_date,
        catalog=catalog,
    )
    request_body = {
        "contents": [{"parts": [{"text": provider_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "topP": 1,
            "topK": 1,
            "seed": COMPOUND_NORMALIZER_RESPONSE_SEED,
            "responseMimeType": "application/json",
            "responseSchema": UNIFIED_QUERY_PLAN_RESPONSE_SCHEMA,
        },
    }
    try:
        response = post(
            COMPOUND_NORMALIZER_API_URL.format(model=selected_model),
            headers={"x-goog-api-key": selected_api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        response_payload = response.json()
        raw_output = _gemini_text_from_response(response_payload if isinstance(response_payload, Mapping) else {})
        parsed_payload = _extract_json_payload(raw_output)
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_UNIFIED_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=raw_output,
            parsed_payload=parsed_payload,
        )
    except Exception as exc:  # pragma: no cover - exercised with fake post in tests.
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_UNIFIED_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error=f"{type(exc).__name__}: {exc}",
        )
