"""Provider adapter for availability compound-prompt normalizer evidence runs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

from candidate_facts.aliases.filter_capability_catalog import (
    FilterCapabilityCatalog,
    llm_facing_catalog,
)


COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION = "compound_prompt_normalizer.availability.v1"
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
                            "display_value": {"type": "STRING"},
                        },
                        "required": [
                            "version",
                            "value_type",
                            "status",
                            "available_by_date",
                            "available_from_date",
                            "available_until_date",
                            "relative_days",
                            "resolved_reference_date",
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


@dataclass(frozen=True)
class AvailabilityNormalizerProviderResult:
    model_id: str
    prompt_template_version: str
    raw_llm_output: str | None
    parsed_payload: Mapping[str, Any] | None
    transport_error: str | None = None


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


def build_availability_normalizer_prompt(
    prompt: str,
    *,
    prompt_normalized: str,
    reference_date: str | None = None,
    catalog: FilterCapabilityCatalog | None = None,
) -> str:
    """Build the framework-independent prompt for the availability normalizer."""

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
        "Only emit filter_family=\"availability\" constraints when the prompt clearly asks for candidate availability.\n"
        "Do not emit constraints for day-of-week availability, salary, ship type, contract length, travel/recovery offsets, "
        "ambiguous locale-specific dates, or contradictory availability instructions. Route those to unapplied or needs_review.\n"
        "If the prompt contains rank, ship type, experience, or urgency text plus a clear availability clause, still emit the availability constraint and ignore unrelated clauses.\n"
        "Do not put unrelated rank, ship type, experience, or urgency clauses into needs_review. The v1 catalog contains only availability.\n"
        "Every span object must be {\"text\": <verbatim>, \"start\": <int>, \"end\": <int>} and must replay against prompt_normalized.\n"
        "Constraints must use source_span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on a constraint.\n"
        "Soft signals, unapplied entries, and needs_review entries must use span: {\"text\": ..., \"start\": ..., \"end\": ...}. Do not put text/start/end directly on those entries.\n"
        "needs_review entries must include candidate_families, for example [\"availability\"].\n"
        "unapplied.reason must be one of: no matching capability, out of scope, unclear intent.\n"
        "All date fields in parameters must be ISO YYYY-MM-DD. User wording can be informal, but output dates must be normalized.\n"
        "Fields inactive for the selected value_type must be null.\n\n"
        "Every availability parameters object must include all of these keys exactly:\n"
        "version, value_type, status, available_by_date, available_from_date, available_until_date, relative_days, resolved_reference_date, display_value.\n"
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

    provider_prompt = build_availability_normalizer_prompt(
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
        )
    except Exception as exc:  # pragma: no cover - exercised with fake post in tests.
        return AvailabilityNormalizerProviderResult(
            model_id=selected_model,
            prompt_template_version=COMPOUND_NORMALIZER_PROMPT_TEMPLATE_VERSION,
            raw_llm_output=None,
            parsed_payload=None,
            transport_error=f"{type(exc).__name__}: {exc}",
        )
