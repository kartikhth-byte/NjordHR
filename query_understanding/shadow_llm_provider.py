"""Shadow-only Gemini provider for `query_plan.v1` generation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from .hard_filter_catalog import CATALOG_VERSION, SUPPORTED_FAMILY_IDS
from .llm_normalizer import is_enabled
from .schema import normalize_query_plan_v1

SHADOW_LLM_PROMPT_TEMPLATE_VERSION = "query_understanding.shadow_llm.v1"
SHADOW_LLM_DEFAULT_MODEL = "gemini-2.0-flash-lite"
SHADOW_LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SHADOW_LLM_RESPONSE_SEED = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def build_shadow_llm_prompt(
    prompt: str,
    *,
    rank: str | None = None,
    catalog_version: str = CATALOG_VERSION,
) -> str:
    supported_families = ", ".join(sorted(SUPPORTED_FAMILY_IDS))
    return (
        "You are NjordHR's shadow query normalizer.\n"
        "Return only valid JSON for a single query_plan.v1 object. No markdown, no commentary.\n"
        "Preserve mandatory recruiter requirements as hard filters when supported.\n"
        "Put unsupported mandatory requirements into unapplied_constraints with reason "
        '"unsupported_filter_family" instead of smuggling them into semantic_query.\n'
        "Remove supported hard constraints from semantic_query. Keep only fuzzy suitability language there.\n"
        "If the prompt is ambiguous, prefer degraded over invalid unless the schema truly cannot be satisfied.\n"
        f"The catalog_version is {catalog_version}.\n"
        f"Supported families: {supported_families}.\n"
        f"Required schema_version: query_plan.v1.\n"
        "Output shape:\n"
        "{\n"
        '  "schema_version": "query_plan.v1",\n'
        '  "normalizer": {\n'
        '    "name": "llm",\n'
        '    "model": "string",\n'
        '    "prompt_template_version": "query_understanding.shadow_llm.v1",\n'
        '    "catalog_version": "string",\n'
        '    "created_at": "ISO-8601 timestamp"\n'
        "  },\n"
        '  "input": {\n'
        f'    "raw_prompt": {json.dumps(prompt)},\n'
        f'    "rank_context": {json.dumps(rank)},\n'
        '    "ui_filters": {"schema_version": "ui_filters.v1", "filters": []}\n'
        "  },\n"
        '  "applied_constraints": [],\n'
        '  "unapplied_constraints": [],\n'
        '  "semantic_query": "string",\n'
        '  "unrecognized_residual": [],\n'
        '  "warnings": [],\n'
        '  "validation": {"status": "valid", "errors": []}\n'
        "}\n"
        "Prompt:\n"
        f"{json.dumps({'raw_prompt': prompt, 'rank_context': rank, 'catalog_version': catalog_version}, ensure_ascii=False)}\n"
    )


def _config_value(config: Any, attr: str, fallback: Any = None) -> Any:
    if config is None:
        return fallback
    value = getattr(config, attr, fallback)
    return fallback if value is None else value


def build_shadow_llm_query_plan(
    analyzer: Any,
    *,
    prompt: str,
    rank: str | None = None,
    prompt_id: str | None = None,
    legacy_plan: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    """Call Gemini in shadow mode and normalize the returned JSON plan."""

    if not is_enabled():
        return None

    config = getattr(analyzer, "config", None)
    api_key = _config_value(config, "gemini_api_key")
    model = _config_value(config, "reasoning_model", SHADOW_LLM_DEFAULT_MODEL)
    timeout = getattr(analyzer, "LLM_REQUEST_TIMEOUT_SECONDS", getattr(getattr(analyzer, "__class__", object), "LLM_REQUEST_TIMEOUT_SECONDS", 45))

    if not api_key or not model:
        return legacy_plan

    request_payload = {
        "contents": [{"parts": [{"text": build_shadow_llm_prompt(prompt, rank=rank)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "candidateCount": 1,
            "seed": SHADOW_LLM_RESPONSE_SEED,
        },
    }
    api_url = SHADOW_LLM_API_URL.format(model=model)
    headers = {"Content-Type": "application/json", "x-goog-api-key": str(api_key)}

    try:
        response = requests.post(api_url, headers=headers, json=request_payload, timeout=timeout)
        response.raise_for_status()
        body = response.json() if hasattr(response, "json") else {}
        result_text = body.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
        parsed = _extract_json_payload(result_text)
        if not parsed:
            return legacy_plan

        candidate_plan = normalize_query_plan_v1(parsed, mode="production")
        if candidate_plan.get("validation", {}).get("status") == "invalid":
            return legacy_plan
        candidate_plan["normalizer"]["name"] = "llm"
        candidate_plan["normalizer"]["model"] = str(model)
        candidate_plan["normalizer"]["prompt_template_version"] = SHADOW_LLM_PROMPT_TEMPLATE_VERSION
        candidate_plan["normalizer"]["catalog_version"] = candidate_plan["normalizer"].get("catalog_version") or CATALOG_VERSION
        candidate_plan["normalizer"]["created_at"] = candidate_plan["normalizer"].get("created_at") or _utc_now_iso()
        return candidate_plan
    except Exception:
        return legacy_plan
