"""Shadow-only Gemini provider for `query_plan.v1` generation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from .hard_filter_catalog import CATALOG_VERSION, SUPPORTED_FAMILY_IDS, legacy_applied_constraint_id, legacy_hard_constraint_key
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


def _normalize_rank_value(analyzer: Any, raw_rank: Any) -> str | None:
    rank_text = str(raw_rank or "").strip()
    if not rank_text:
        return None
    normalize_rank = getattr(analyzer, "_normalize_rank", None)
    if callable(normalize_rank):
        try:
            normalized = normalize_rank(rank_text)
            if isinstance(normalized, (tuple, list)) and normalized:
                canonical_id = normalized[0]
                if isinstance(canonical_id, str) and canonical_id.strip():
                    return canonical_id.strip()
        except Exception:
            pass
    fallback = rank_text.lower().replace(" ", "_")
    return fallback if fallback else None


def _make_applied_constraint(
    family: str,
    constraint: Mapping[str, Any],
    *,
    source_text: str,
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "id": family,
        "mode": "required",
        "constraint": dict(constraint),
        "source_text": source_text,
        "confidence": confidence,
        "compatibility": {
            "legacy_hard_constraints_key": legacy_hard_constraint_key(family),
            "legacy_applied_constraint_id": legacy_applied_constraint_id(family),
        },
    }


def _translate_model_payload(
    parsed: Mapping[str, Any],
    *,
    analyzer: Any,
    raw_prompt: str,
    rank: str | None,
) -> dict[str, Any]:
    raw_input = parsed.get("input") if isinstance(parsed.get("input"), Mapping) else {}
    rank_context = raw_input.get("rank_context") if isinstance(raw_input, Mapping) else None
    canonical_rank = _normalize_rank_value(analyzer, rank or rank_context)
    prompt_text = str(raw_input.get("raw_prompt") or raw_prompt or "").strip()

    applied_constraints: list[dict[str, Any]] = []

    def _append_from_family(family: str, parameters: Mapping[str, Any], item: Mapping[str, Any]) -> None:
        source_text = str(item.get("source_text") or prompt_text or raw_prompt or "").strip()
        confidence = str(item.get("confidence") or "high")

        if family == "passport_validity":
            validity_value = parameters.get("validity") or parameters.get("is_valid")
            months = parameters.get("minimum_months_remaining")
            if validity_value in {"valid", True} or months is not None:
                applied_constraints.append(
                    _make_applied_constraint(
                        "passport_validity",
                        {
                            "type": "passport_validity",
                            "must_be_valid": True,
                            "minimum_months_remaining": months if isinstance(months, int) else None,
                        },
                        source_text=source_text,
                        confidence=confidence,
                    )
                )
            return

        if family == "rank_match":
            rank_value = parameters.get("rank") or parameters.get("rank_normalized") or canonical_rank
            if isinstance(rank_value, str) and rank_value.strip():
                applied_constraints.append(
                    _make_applied_constraint(
                        "rank_match",
                        {
                            "type": "rank_match",
                            "rank": rank_value.strip(),
                        },
                        source_text=source_text,
                        confidence=confidence,
                    )
                )
            return

        if family == "us_visa":
            applied_constraints.append(
                _make_applied_constraint(
                    "us_visa",
                    {
                        "type": "us_visa",
                        "required": True,
                        "minimum_months_remaining": parameters.get("minimum_months_remaining"),
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            )

    if isinstance(parsed.get("hard_constraints"), list) or isinstance(parsed.get("recruiter_requirements"), list):
        for item in parsed.get("hard_constraints") or []:
            if isinstance(item, Mapping):
                _append_from_family(str(item.get("filter_family") or item.get("id") or "").strip(), item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}, item)
        for item in parsed.get("recruiter_requirements") or []:
            if isinstance(item, Mapping):
                _append_from_family(str(item.get("filter_family") or item.get("id") or "").strip(), item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}, item)
    else:
        for item in parsed.get("applied_constraints") or []:
            if not isinstance(item, Mapping):
                continue
            family = str(item.get("filter_family") or item.get("id") or "").strip()
            if family and "constraint" not in item:
                parameters = item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}
                _append_from_family(family, parameters, item)
            elif family:
                applied_constraints.append(dict(item))

    semantic_query = parsed.get("semantic_query")
    if isinstance(semantic_query, Mapping):
        fuzzy = semantic_query.get("fuzzy_suitability")
        if isinstance(fuzzy, list):
            semantic_query = " ".join(str(part).strip() for part in fuzzy if str(part).strip())
        else:
            semantic_query = ""
    elif isinstance(semantic_query, list):
        semantic_query = " ".join(str(part).strip() for part in semantic_query if str(part).strip())
    else:
        semantic_query = str(semantic_query or "").strip()

    return {
        "schema_version": "query_plan.v1",
        "normalizer": {
            "name": "llm",
            "model": str(parsed.get("normalizer", {}).get("model") or _config_value(getattr(analyzer, "config", None), "reasoning_model", SHADOW_LLM_DEFAULT_MODEL)),
            "prompt_template_version": SHADOW_LLM_PROMPT_TEMPLATE_VERSION,
            "catalog_version": str(parsed.get("normalizer", {}).get("catalog_version") or parsed.get("catalog_version") or CATALOG_VERSION),
            "created_at": str(parsed.get("normalizer", {}).get("created_at") or _utc_now_iso()),
        },
        "input": {
            "raw_prompt": prompt_text,
            "rank_context": rank_context if isinstance(rank_context, str) or rank_context is None else str(rank_context),
            "ui_filters": {
                "schema_version": "ui_filters.v1",
                "filters": [],
            },
        },
        "applied_constraints": applied_constraints,
        "unapplied_constraints": list(parsed.get("unapplied_constraints") or []),
        "semantic_query": semantic_query,
        "unrecognized_residual": list(parsed.get("unrecognized_residual") or []),
        "warnings": list(parsed.get("warnings") or []),
        "validation": {"status": "valid", "errors": []},
    }


def _result(plan: Mapping[str, Any] | None, diagnostics: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"plan": plan, "diagnostics": dict(diagnostics)}


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
        return _result(None, {"status": "disabled", "reason": "feature_flag_disabled"})

    config = getattr(analyzer, "config", None)
    api_key = _config_value(config, "gemini_api_key")
    model = _config_value(config, "reasoning_model", SHADOW_LLM_DEFAULT_MODEL)
    timeout = getattr(analyzer, "LLM_REQUEST_TIMEOUT_SECONDS", getattr(getattr(analyzer, "__class__", object), "LLM_REQUEST_TIMEOUT_SECONDS", 45))

    if not api_key or not model:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "missing_api_credentials" if not api_key else "missing_model",
                "model": model,
                "has_api_key": bool(api_key),
            },
        )

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
            return _result(
                legacy_plan,
                {
                    "status": "fallback",
                    "reason": "invalid_model_json",
                    "model": model,
                    "http_status": getattr(response, "status_code", None),
                    "response_excerpt": str(result_text or "")[:500],
                },
            )

        candidate_plan = normalize_query_plan_v1(
            _translate_model_payload(parsed, analyzer=analyzer, raw_prompt=prompt, rank=rank),
            mode="production",
        )
        if candidate_plan.get("validation", {}).get("status") == "invalid":
            return _result(
                legacy_plan,
                {
                    "status": "fallback",
                    "reason": "schema_invalid",
                    "model": model,
                    "http_status": getattr(response, "status_code", None),
                    "response_excerpt": str(result_text or "")[:500],
                },
            )
        candidate_plan["normalizer"]["name"] = "llm"
        candidate_plan["normalizer"]["model"] = str(model)
        candidate_plan["normalizer"]["prompt_template_version"] = SHADOW_LLM_PROMPT_TEMPLATE_VERSION
        candidate_plan["normalizer"]["catalog_version"] = candidate_plan["normalizer"].get("catalog_version") or CATALOG_VERSION
        candidate_plan["normalizer"]["created_at"] = candidate_plan["normalizer"].get("created_at") or _utc_now_iso()
        return _result(
            candidate_plan,
            {
                "status": "success",
                "reason": "ok",
                "model": model,
                "http_status": getattr(response, "status_code", None),
            },
        )
    except Exception as exc:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "request_exception",
                "model": model,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
