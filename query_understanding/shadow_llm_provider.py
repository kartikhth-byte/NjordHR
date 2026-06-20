"""Shadow-only Gemini provider for `query_plan.v1` generation."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from .hard_filter_catalog import (
    CATALOG_VERSION,
    SUPPORTED_FAMILY_IDS,
    canonical_certificate_values,
    canonical_endorsement_values,
    canonical_engine_family_values,
    canonical_rank_values,
    canonical_ship_family_values,
    is_active_family,
    is_unsupported_family,
    legacy_applied_constraint_id,
    legacy_hard_constraint_key,
)
from .llm_normalizer import is_enabled
from .schema import normalize_query_plan_v1

SHADOW_LLM_PROMPT_TEMPLATE_VERSION = "query_understanding.shadow_llm.v1"
SHADOW_LLM_DEFAULT_MODEL = "gemini-3.1-flash-lite"
SHADOW_LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SHADOW_LLM_RESPONSE_SEED = 0
_AGE_TEXT_TO_VALUE = {
    "twenty": 20,
    "twenties": 20,
    "thirty": 30,
    "thirties": 30,
    "forty": 40,
    "forties": 40,
    "fifty": 50,
    "fifties": 50,
    "sixty": 60,
    "sixties": 60,
    "seventy": 70,
    "seventies": 70,
    "eighty": 80,
    "eighties": 80,
}
_SMALL_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_VISA_POLARITY_INVERSION = re.compile(
    r"\b(?:"
    r"visa[-\s]?free|"
    r"no\s+(?:\w+\s+){0,3}?visa\s+(?:required|needed)|"
    r"(?:don'?t|doesn'?t)\s+need\s+(?:a\s+)?visa|"
    r"without\s+(?:a\s+)?visa|"
    r"visa\s+exempt"
    r")\b",
    re.IGNORECASE,
)
_PASSPORT_POLARITY_INVERSION = re.compile(
    r"\b(?:"
    r"no\s+passport\s+(?:required|needed)|"
    r"(?:don'?t|doesn'?t)\s+need\s+(?:a\s+)?passport|"
    r"without\s+(?:a\s+)?passport|"
    r"passport\s+not\s+(?:required|needed)"
    r")\b",
    re.IGNORECASE,
)
_SUFFICIENCY_ONLY_PATTERN = re.compile(
    r"\b(?:alone\s+is\s+enough|is\s+enough|is\s+acceptable|are\s+acceptable|will\s+do|sufficient|that\s+works|works\s+for\s+this|(?:must\s+have|needs?|requires?|requirement\s+is)\s+[^.,;]{0,80}\balone)\b",
    re.IGNORECASE,
)
_VISA_REJECTION_STATUS_PATTERN = re.compile(
    r"\b(?:visa\s+(?:application\s+)?(?:rejected|refused|denied|declined)|(?:rejected|refused|denied|declined)\s+(?:visa|visa\s+application))\b",
    re.IGNORECASE,
)
_VISA_UNUSABLE_STATUS_PATTERN = re.compile(
    r"\bvisa\s+(?:application\s+)?(?:pending|expired|cancelled|canceled)\b",
    re.IGNORECASE,
)
_VISA_CURRENT_STATUS_PATTERN = re.compile(
    r"\b(?:renewed|reissued|extended|valid|current|active)\b",
    re.IGNORECASE,
)
_SUPPORTED_VISA_CONTEXT_CUES = re.compile(
    r"\b(?:"
    r"us|usa|u\.?s\.?|america|american|yankee|states|stateside|"
    r"australia|australian|aussie|australasia|mcv|maritime\s+crew|"
    r"schengen|europe|european|eu|"
    r"c1\s*(?:/|-)?\s*d|d\s+visa|b1/?b2|b\s+one(?:\s+slash|\s*/)?\s*b\s+two|c1|b1|b2|h-?1b|l-?1|f-?1|o-?1"
    r")\b",
    re.IGNORECASE,
)
_STCW_BASIC_CONTEXT_CUES = re.compile(
    r"\b(?:"
    r"stcw|bst|basic\s+safety\s+training|basic\s+stcw|basic\s+training|"
    r"basic\s+cert(?:ificate|ificates|ification)?|"
    r"a-?vi/1|pssr|pst|fpff|efa|personal\s+survival|fire\s+fighting|fire\s+prevention|first\s+aid|"
    r"basic\s+courses?|basic\s+modules?|four[-\s]?pack|all\s+four\s+basic"
    r")\b",
    re.IGNORECASE,
)
_AGE_FIGURATIVE_PATTERNS = re.compile(
    r"\bmiddle[-\s]?aged\b|"
    r"\byoung at heart\b|"
    r"\bsenior officer\b|"
    r"\bsenior captain\b|"
    r"\byouthful\b|"
    r"\baged wisdom\b",
    re.IGNORECASE,
)
_RANK_REQUIREMENT_CUES = re.compile(
    r"\b(?:"
    r"need|needs|required|requirement|looking\s+for|search(?:ing)?\s+for|"
    r"role|slot|applied\s+as|currently|onboard|candidate\s+for|must\s+be|rank"
    r")\b",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _utc_now_year() -> int:
    return datetime.now(timezone.utc).year


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
    age_rule_block = (
        "age_range:\n"
        "- Numeric or spelled year bounds.\n"
        "- 'between N and M (years old)', 'aged N to M', 'N-M years old' -> minimum_years=N, maximum_years=M.\n"
        "- 'N+', 'minimum age N', 'nlt N', 'no younger than N', 'not below N' -> minimum_years=N.\n"
        "- 'no older than N', 'not above N', 'cannot exceed N', 'nmt N' -> maximum_years=N.\n"
        "- 'under N', 'below N', 'younger than N' -> maximum_years=N-1.\n"
        "- 'mid-30s', 'early 40s', 'late 20s', 'forties' -> decade span; 'around N' / 'approximately N' -> approximate band.\n"
    )
    visa_rule_block = (
        "us_visa (family id for USA, Australia, and Schengen visas):\n"
        "- Supported groups: usa (US/USA/American/Yankee/states/H-1B/L-1/F-1/O-1/C1-D/B1-B2), australia (Australia/MCV/Maritime Crew Visa), schengen (Schengen/European/EU).\n"
        "- Apply only when a supported country/class is named; always emit visa_group and include accepted_types for specific classes.\n"
        "- 'visa-free X', 'no visa required', 'doesn't need visa' -> unsupported (opposite intent).\n"
        "- Vague 'visas' / 'proper visas' without a country -> unsupported.\n"
        "- Visas for other countries -> unsupported.\n"
        "- 'MV visa' near Australia -> australia; 'MV' as vessel prefix -> not us_visa.\n"
    )
    stcw_rule_block = (
        "stcw_basic:\n"
        "- 'STCW basic', 'basic STCW', 'basic safety training', 'BST' (bare or qualified), 'STCW A-VI/1' -> stcw_basic.\n"
        "- All four basic components together (PSSR, PST, FPFF, EFA) -> stcw_basic.\n"
        "- Quantifiers like 'all four basic certificates' / '4 basic certificates' -> stcw_basic.\n"
        "- Advanced certificates (AFF, MFA, AFA) belong to certificate_requirement, NOT stcw_basic.\n"
        "- Generic 'safety training' without STCW/BST/basic cues is unsupported.\n"
    )
    engine_rule_block = (
        "engine_experience:\n"
        "- Preserve requested specificity. Generic manufacturer/family prompts stay generic; do not invent a subtype.\n"
        "- Manufacturer/family examples: 'MAN experience' -> man; 'MAN B&W' or 'B&W' -> man_b_w; 'WinGD engine' -> wingd_x_engines.\n"
        "- Subtype examples: 'ME engine' -> man_b_w_me; 'ME-C' -> man_b_w_me_c; 'ME-GI' -> man_b_w_me_gi; 'X-DF' -> wingd_x_df; 'X-DF-HP' -> wingd_x_df_hp; 'RT-flex' -> wartsila_rt_flex.\n"
        "- Broad deterministic buckets: 'dual fuel engine' -> dual_fuel; 'electronic engine' / 'electronically controlled engine' / 'camless engine' -> electronically_controlled_engine; 'mechanical engine' / 'camshaft engine' -> mechanical_engine.\n"
        "- 'Sulzer' -> sulzer; 'UEC-LSII' -> mitsubishi_uec_lsii; 'UEC-LSE/LSH/LSJ' -> electronic Mitsubishi UEC subtypes.\n"
        "- Diesel-electric / HV / Azipod / scrubber / EGR stay semantic or unsupported in v1 unless a canonical engine family is also named.\n"
        "- Fallbacks are evaluator-time only: if the prompt asks for 'ME engine', still emit man_b_w_me even if a resume may later contain only MAN B&W evidence.\n"
    )
    return (
        "You are NjordHR's shadow query normalizer.\n"
        "Return query_plan.v1 JSON only. No markdown/commentary.\n"
        "Supported hard constraints -> applied; unsupported mandatory -> unapplied unsupported_filter_family.\n"
        "Keep only fuzzy suitability language in semantic_query.\n"
        "Cross-family OR -> logical_groups any_of; no duplicate applied children.\n"
        "Prefer degraded over invalid.\n"
        f"catalog_version={catalog_version}.\n"
        f"{age_rule_block}"
        f"{visa_rule_block}"
        f"{stcw_rule_block}"
        f"{engine_rule_block}"
        f"Required schema_version: query_plan.v1.\n"
        "Output shape keys: schema_version, normalizer, input, applied_constraints, unapplied_constraints, semantic_query, unrecognized_residual, warnings, validation.\n"
        "Prompt:\n"
        f"{json.dumps({'raw_prompt': prompt, 'rank_context': rank, 'catalog_version': catalog_version}, ensure_ascii=False)}\n"
    )


def _config_value(config: Any, attr: str, fallback: Any = None) -> Any:
    if config is None:
        return fallback
    try:
        value = getattr(config, attr, fallback)
    except Exception:
        return fallback
    return fallback if value is None else value


def _resolve_reasoning_model(analyzer: Any, fallback: str = SHADOW_LLM_DEFAULT_MODEL) -> str:
    """Resolve the active reasoning model from the analyzer config.

    The admin settings UI persists ``reasoning_model_name`` in ``config.ini``.
    That setting should drive both the live reasoning path and the shadow LLM
    path. Keep the module constant as a fallback only.
    """

    direct_model = _first_string(getattr(analyzer, "reasoning_model", None))
    if direct_model:
        return direct_model

    config = getattr(analyzer, "config", None)
    getter = getattr(config, "get", None)
    if callable(getter):
        for option in ("reasoning_model_name", "reasoning_model"):
            try:
                value = getter("Advanced", option, fallback=None)
            except TypeError:
                try:
                    value = getter("Advanced", option)
                except Exception:
                    value = None
            except Exception:
                value = None
            model = _first_string(value)
            if model:
                return model

    legacy_model = _first_string(_config_value(config, "reasoning_model_name"), _config_value(config, "reasoning_model"))
    if legacy_model:
        return legacy_model

    return fallback


def _resolve_gemini_api_key(analyzer: Any) -> str | None:
    """Resolve the Gemini API key from the analyzer or its config object."""

    direct_key = _first_string(getattr(analyzer, "gemini_api_key", None))
    if direct_key:
        return direct_key

    config = getattr(analyzer, "config", None)
    getter = getattr(config, "get", None)
    if callable(getter):
        for option in ("Gemini_API_Key", "gemini_api_key"):
            try:
                value = getter("Credentials", option, fallback=None)
            except TypeError:
                try:
                    value = getter("Credentials", option)
                except Exception:
                    value = None
            except Exception:
                value = None
            api_key = _first_string(value)
            if api_key:
                return api_key

    legacy_key = _first_string(_config_value(config, "gemini_api_key"), _config_value(config, "Gemini_API_Key"))
    if legacy_key:
        return legacy_key

    env_key = _first_string(os.environ.get("GEMINI_API_KEY"), os.environ.get("GOOGLE_API_KEY"))
    if env_key:
        return env_key

    return None


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


def _canonicalize_family_name(value: Any) -> str:
    family = str(value or "").strip()
    return family


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _is_false_value(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() in {"false", "0", "no", "none"})


class ShadowLLMTranslationError(ValueError):
    """Raised when Gemini returns a shape that should be rejected in shadow mode."""


def _canonical_unapplied_family_id(family: str) -> str:
    if family == "sea_service":
        return "min_sea_service"
    return family


def _extract_parameters(item: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("parameters", "constraint", "payload", "value"):
        value = item.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return parsed if parsed > 0 else None


def _duration_token_to_int(value: Any) -> int | None:
    token = _normalize_text(value).lower().replace("-", " ")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _SMALL_NUMBER_WORDS.get(token)


def _age_token_to_int(value: Any) -> int | None:
    token = _normalize_text(value).lower().replace("-", " ")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _AGE_TEXT_TO_VALUE:
        return _AGE_TEXT_TO_VALUE[token]
    token = token.rstrip("s")
    if token in _AGE_TEXT_TO_VALUE:
        return _AGE_TEXT_TO_VALUE[token]
    return None


def _age_decade_bounds(token: Any, modifier: str | None = None) -> tuple[int | None, int | None]:
    decade = _age_token_to_int(token)
    if decade is None:
        return None, None
    if decade < 10:
        decade *= 10
    if modifier == "early":
        return decade, decade + 2
    if modifier == "mid":
        return decade + 3, decade + 7
    if modifier == "late":
        return decade + 7, decade + 9
    return decade, decade + 9


def _us_visa_is_anchored(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    if not text.strip():
        return False
    if _VISA_POLARITY_INVERSION.search(text) or _visa_negative_status_is_active(text):
        return False
    return bool(_SUPPORTED_VISA_CONTEXT_CUES.search(text))


def _visa_negative_status_is_active(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    if _VISA_REJECTION_STATUS_PATTERN.search(text):
        return True
    if _VISA_UNUSABLE_STATUS_PATTERN.search(text):
        return not _VISA_CURRENT_STATUS_PATTERN.search(text)
    return False


def _passport_validity_is_anchored(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    lowered = text.lower()
    if not lowered.strip() or "passport" not in lowered:
        return False
    if _PASSPORT_POLARITY_INVERSION.search(lowered):
        return False
    if re.search(r"\bpassport[-\s]sized\s+photo\b|\bpassport\s+photo\b", lowered):
        return False
    if re.search(r"\bwithout\s+passport\s+restrictions\b|\bexpired\s+passport\s+ok\b", lowered):
        return False
    return bool(
        re.search(
            r"\b(?:"
            r"valid|validity|current|remaining|left|expire|expires|expiring|expiry|"
            r"renewed|issued|fresh|freshly|not\s+expired|no\s+validity\s+issues|"
            r"must\s+have|should\s+hold|needs?"
            r")\b",
            lowered,
            flags=re.IGNORECASE,
        )
    )


def _extract_shadow_passport_validity_constraint(prompt_text: Any) -> dict[str, Any] | None:
    text = str(prompt_text or "").strip()
    lowered = text.lower()
    if not _passport_validity_is_anchored(lowered):
        return None

    months = None
    duration = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    patterns = (
        (rf"\b{duration}\s+months?\s+of\s+passport\s+validity\b", 1),
        (rf"\bminimum\s+{duration}\s+months?\s+passport\s+validity\b", 1),
        (rf"\bat\s+least\s+{duration}\s+months?\s+passport\s+validity\b", 1),
        (rf"\b{duration}\s+months?\s+passport\s+validity(?:\s+required)?\b", 1),
        (rf"\bpassport\s+validity\s+{duration}\s+months?\b", 1),
        (rf"\b{duration}\s*\+?\s+months?\s+(?:remaining|left|validity|till\s+expiry|before\s+it\s+expires)\b", 1),
        (rf"\bpassport\s+(?:with\s+)?(?:at\s+least\s+|minimum\s+)?{duration}\s*\+?\s+months?\s+(?:remaining|left|validity|till\s+expiry|before\s+it\s+expires)\b", 1),
        (rf"\bpassport\s+remaining\s+validity\s+of\s+{duration}\s+months?\b", 1),
        (rf"\bpassport\s+valid(?:ity)?\s+{duration}\s*\+?\s+months?\b", 1),
        (rf"\bvalid\s+for\s+(?:at\s+least\s+)?{duration}\s+months?\b", 1),
        (rf"\bvalid\s+for\s+(?:at\s+least\s+)?{duration}\s+years?\b", 12),
        (rf"\bminimum\s+{duration}\s+months?\s+validity\s+remaining\b", 1),
        (rf"\bdoesn'?t\s+expire\s+in\s+the\s+next\s+{duration}\s+years?\b", 12),
        (r"\bdoesn'?t\s+expire\s+in\s+the\s+next\s+year\b", 12),
        (rf"\bexpiring\s+within\s+{duration}\s+months?\s+should\s+be\s+rejected\b", 1),
        (rf"\bhas\s+at\s+least\s+{duration}\s+months?\s+left\s+before\s+it\s+expires\b", 1),
    )
    for pattern, multiplier in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        if not match.lastindex:
            months = multiplier
            break
        value = _duration_token_to_int(match.group(1))
        if value is not None:
            months = value * multiplier
            break

    return {
        "type": "passport_validity",
        "must_be_valid": True,
        "minimum_months_remaining": months,
        "display_value": "valid passport",
    }


def _extract_visa_accepted_types(prompt_text: str, visa_group: str, analyzer: Any) -> list[str]:
    lowered = str(prompt_text or "").lower()
    if visa_group == "usa":
        if re.search(r"\bc1\s*(?:/|-)?\s*d\b|\bc1d\b", lowered):
            return ["C1/D (USA)"]
        if re.search(r"\bb1\s*/\s*b2\b|\bb1b2\b|\bb\s+one(?:\s+slash|\s*/)?\s*b\s+two\b", lowered):
            return ["B1/B2 (USA)"]
        if re.search(r"\bh-?1b\b", lowered):
            return ["H1B (USA)"]
        if re.search(r"\bl-?1\b", lowered):
            return ["L1 (USA)"]
        if re.search(r"\bf-?1\b", lowered):
            return ["F1 (USA)"]
        if re.search(r"\bo-?1\b", lowered):
            return ["O1 (USA)"]
        if re.search(r"\bc1\b", lowered):
            return ["C1 (USA)"]
        if re.search(r"\bd\s+visa\b|\bvisa\s+d\b", lowered):
            return ["D (USA)"]
        return _visa_accepted_types_for_group(analyzer, visa_group)
    if visa_group == "australia":
        if re.search(r"\bmcv\b|\bmaritime\s+crew\s+visa\b", lowered):
            return ["MCV (Australia)"]
        return _visa_accepted_types_for_group(analyzer, visa_group)
    if visa_group == "schengen":
        return ["Schengen"]
    return _visa_accepted_types_for_group(analyzer, visa_group)


def _normalize_visa_accepted_types(prompt_text: str, visa_group: str, model_accepted_types: list[str], analyzer: Any) -> list[str]:
    prompt_types = _extract_visa_accepted_types(prompt_text, visa_group, analyzer)
    default_prompt_types = _visa_accepted_types_for_group(analyzer, visa_group)
    generic_prompt_types = {
        "usa": {"US Visa (USA)"},
        "australia": {"MCV (Australia)"},
        "schengen": {"Schengen"},
    }

    def _is_generic_prompt_type_list(values: list[str]) -> bool:
        if not values:
            return False
        if default_prompt_types and values == default_prompt_types:
            return True
        generic_values = generic_prompt_types.get(visa_group, set())
        return len(values) == 1 and values[0] in generic_values

    if prompt_types and not _is_generic_prompt_type_list(prompt_types):
        return prompt_types

    if model_accepted_types:
        if default_prompt_types and model_accepted_types == default_prompt_types:
            return model_accepted_types
        if (
            prompt_types
            and default_prompt_types
            and prompt_types == default_prompt_types
            and len(model_accepted_types) == 1
            and model_accepted_types[0] in generic_prompt_types.get(visa_group, set())
        ):
            return prompt_types
        if visa_group == "usa":
            if len(model_accepted_types) == 1:
                return model_accepted_types
            if "US Visa (USA)" in model_accepted_types:
                return ["US Visa (USA)"]
        if visa_group == "australia":
            if len(model_accepted_types) == 1:
                return model_accepted_types
            if "MCV (Australia)" in model_accepted_types:
                return ["MCV (Australia)"]
        if visa_group == "schengen":
            return ["Schengen"]
        return model_accepted_types

    return prompt_types


def _stcw_basic_is_anchored(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    lowered = text.lower()
    if not lowered.strip():
        return False
    if _SUFFICIENCY_ONLY_PATTERN.search(lowered):
        return False
    if re.search(r"\bstcw\s+endorsement\b", lowered) and not re.search(r"\b(?:basic|bst|a-?vi/1|pssr|pst|fpff|efa)\b", lowered):
        return False
    if re.search(r"\bno\s+basic\b|\bwithout\s+basic\b|\bbasic\s+(?:not|required\s+not)\b", lowered):
        return False
    component_hits = sum(
        bool(re.search(pattern, lowered))
        for pattern in (
            r"\bpssr\b",
            r"\bpst\b|\bpersonal\s+survival\b",
            r"\bfpff\b|\bfire\s+(?:prevention|fighting)\b",
            r"\befa\b|\bfirst\s+aid\b",
        )
    )
    if component_hits >= 4:
        return True
    return bool(_STCW_BASIC_CONTEXT_CUES.search(lowered))


def _age_range_is_anchored(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    if not text.strip():
        return False
    if _AGE_FIGURATIVE_PATTERNS.search(text):
        return False
    minimum_years, maximum_years = _age_bounds_from_text(text)
    return minimum_years is not None or maximum_years is not None


def _rank_match_is_anchored(prompt_text: Any) -> bool:
    text = str(prompt_text or "")
    if not text.strip():
        return False
    if _AGE_FIGURATIVE_PATTERNS.search(text) and not _RANK_REQUIREMENT_CUES.search(text):
        return False
    return True


def _extract_shadow_rank_value(prompt_text: Any) -> str | None:
    text = str(prompt_text or "")
    if not _rank_match_is_anchored(text):
        return None
    normalized = re.sub(r"[/._\-]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    patterns = (
        (r"\bco\b", "chief_officer"),
        (r"\b3rd\s+off\b|\bthird\s+off\b", "3rd_officer"),
        (r"\btrainee\s+engineer\b", "junior_engineer"),
        (r"\bengine\s+cadet\b", "engine_cadet"),
        (r"\bgp\s+rating\b|\bgeneral\s+purpose\s+rating\b", "general_purpose_rating"),
        (r"\bchief\s+galley\s+cook\b|\bgalley\s+cook\b|\bneeds?\s+cook\b|\bcook\b", "chief_cook"),
        (r"\bpump\s+man\b|\bpumpman\b", "pumpman"),
        (r"\bab\s+special\b", "ab"),
    )
    for pattern, rank in patterns:
        if re.search(pattern, normalized):
            return rank if rank in canonical_rank_values() else None
    return None


def _extract_shadow_certificate_values(prompt_text: Any) -> list[str]:
    text = str(prompt_text or "").lower()
    values: list[str] = []
    patterns = (
        (r"\baff\b|\badvanced\s+fire\s*fighting\b", "cert_aff"),
        (r"\bmfa\b|\bmedical\s+first\s+aid\b|\bmedical\s+care\b|\bafa\b|\badvanced\s+first\s+aid\b", "cert_mfa"),
        (r"\bgmdss\b|\broc\b|\bgoc\b", "gmdss"),
        (r"\becdis\b", "cert_ecdis"),
        (r"\bbrm\b|\bbridge\s+resource\s+management\b", "cert_brm_btm"),
        (r"\berm\b|\bengine\s+room\s+resource\s+management\b", "cert_erm"),
        (r"\bpscrb\b|\bsurvival\s+craft\s+and\s+rescue\s+boats?\b", "cert_pscrb"),
        (r"\bsso\b|\bship\s+security\s+officer\b|\bpfso\b|\bport\s+facility\s+security\s+officer\b", "cert_sso"),
        (r"\bccm\b|\bcrowd\s+(?:and\s+)?crisis\s+management\b|\bcrowd\s+management\b|\bcrisis\s+management\b", "cert_ccm"),
        (r"\blms\b|\bleadership\s+and\s+managerial\s+skills\b|\bleadership\s+managerial\s+skills\b", "cert_lms"),
    )
    allowed = canonical_certificate_values()
    for pattern, value in patterns:
        if value in allowed and re.search(pattern, text):
            values.append(value)
    return list(dict.fromkeys(values))


def _extract_shadow_endorsement_values(prompt_text: Any) -> list[str]:
    text = str(prompt_text or "").lower()
    if _SUFFICIENCY_ONLY_PATTERN.search(text):
        return []
    values: list[str] = []
    patterns = (
        (r"\bdpo\b|\bdp\s+operator\b", "dp_operational"),
        (r"\bigf\s+code\b|\bigf\s+(?:cop|certificate|endorsement)\b", "igf_basic_cop"),
        (r"\btanker\s+experience\s+with\s+stcw\s+endorsement\b", "tanker_oil"),
        (r"\boil\s+tanker\s+familiarization\b|\boil\s+tanker\s+familiarisation\b", "tanker_oil_basic_cop"),
        (r"\bchemical\s+tanker\s+familiarization\b|\bchemical\s+tanker\s+familiarisation\b", "tanker_chemical_basic_cop"),
        (r"\b(?:gas|lng|lpg)\s+tanker\s+familiarization\b|\b(?:gas|lng|lpg)\s+tanker\s+familiarisation\b", "tanker_gas_basic_cop"),
        (r"\btanker\s+familiarization\b|\btanker\s+familiarisation\b", "tanker_oil_basic_cop"),
        (r"\badvanced\s+dce\b|\bdce\s+management\b", "tanker_oil_dce"),
        (r"\bdce\b|\bdangerous\s+cargo\s+endorsement\b", "tanker_oil_dce"),
    )
    allowed = canonical_endorsement_values()
    for pattern, value in patterns:
        if value in allowed and re.search(pattern, text):
            values.append(value)
    return list(dict.fromkeys(values))


def _visa_accepted_types_for_group(analyzer: Any, visa_group: str | None) -> list[str]:
    if not visa_group:
        return []
    accepted_types: list[str] = []
    visa_defs = getattr(analyzer, "_visa_type_definitions", None)
    if callable(visa_defs):
        try:
            defs = visa_defs()
        except Exception:
            defs = []
        if isinstance(defs, list):
            accepted_types = [
                str(visa_def.get("canonical")).strip()
                for visa_def in defs
                if isinstance(visa_def, Mapping)
                and visa_def.get("group") == visa_group
                and isinstance(visa_def.get("canonical"), str)
                and str(visa_def.get("canonical")).strip()
            ]
    if visa_group == "usa" and not accepted_types:
        return ["US Visa (USA)"]
    return accepted_types


def _extract_shadow_us_visa_constraint(analyzer: Any, prompt_text: Any) -> dict[str, Any] | None:
    text = str(prompt_text or "").strip().lower()
    if not text:
        return None
    if _VISA_POLARITY_INVERSION.search(text) or _visa_negative_status_is_active(text):
        return None

    if not _SUPPORTED_VISA_CONTEXT_CUES.search(text):
        return None

    visa_group = None
    if re.search(r"\b(?:australia|australian|aussie|australasia|mcv|maritime\s+crew)\b", text, flags=re.IGNORECASE):
        visa_group = "australia"
    elif re.search(r"\b(?:us|usa|u\.?s\.?|america|american|yankee|states|stateside|h-?1b|l-?1|f-?1|o-?1|c1\s*(?:/|-)?\s*d|c1d|b1/?b2|b1b2|b\s+one(?:\s+slash|\s*/)?\s*b\s+two|c1\s+visa|d\s+visa)\b", text, flags=re.IGNORECASE):
        visa_group = "usa"
    elif re.search(r"\b(?:schengen|europe|european|eu)\b", text, flags=re.IGNORECASE):
        visa_group = "schengen"
    else:
        return None

    accepted_types = _extract_visa_accepted_types(text, visa_group, analyzer)

    months = None
    for pattern in (
        r"\b(?:us\s+)?visa\s+is\s+valid\s+(?:at\s+least\s+|minimum\s+)?(?:for\s+)?(\d+)\+?\s+months?\b",
        r"\b(?:us\s+)?visa\s+should\s+be\s+valid\s+(?:at\s+least\s+|minimum\s+)?(?:for\s+)?(\d+)\+?\s+months?\b",
        r"\b(?:minimum\s+)?(\d+)\+?\s+months?\s+validity\s+on\s+(?:us\s+)?visa\b",
        r"\bwith\s+(\d+)\+?\s+months?\s+validity\b",
        r"\bwith\s+(\d+)\+?\s+years?\s+validity\b",
        r"\b(?:c1d|c1\s*(?:/|-)?\s*d|b1b2|b1\s*/\s*b2|h-?1b|l-?1|f-?1|o-?1|(?:eu|european|schengen)\s+travel\s+visa)\s+(?:valid\s+)?(?:for\s+)?(\d+)\+?\s+months?\b",
        r"\b(?:c1d|c1\s*(?:/|-)?\s*d|b1b2|b1\s*/\s*b2|h-?1b|l-?1|f-?1|o-?1|(?:eu|european|schengen)\s+travel\s+visa)\s+(?:valid\s+)?(?:for\s+)?(\d+)\+?\s+years?\b",
        r"\b(?:c1d|c1\s*(?:/|-)?\s*d|b1b2|b1\s*/\s*b2|h-?1b|l-?1|f-?1|o-?1)\s+with\s+(\d+)\+?\s+months?\s+left\b",
        r"\bvalidity\s+with\s+(\d+)\+?\s+months?\b",
        r"\b(?:valid|current|hold(?:ing)?|with)\s+(?:us\s+)?visa(?:\s+is\s+valid|\s+valid)?\s+(?:for\s+)?(?:at\s+least\s+|minimum\s+)?(\d+)\s+months?\b",
        r"\b(?:us\s+)?visa\s+with\s+(\d+)\+?\s+months?\s+validity\b",
        r"\b(\d+)\+?\s+months?\s+(?:us\s+)?visa\b",
        r"\b(\d+)\+?\s+month\s+(?:us\s+)?visa\b",
        r"\b(\d+)\+?\s+years?\s+(?:us\s+)?visa\b",
        r"\b(\d+)\+?\s+year\s+(?:us\s+)?visa\b",
        r"\bvisa\s+valid\s+for\s+(\d+)\+?\s+months?\b",
        r"\bvisa\s+valid\s+for\s+(\d+)\+?\s+years?\b",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _as_positive_int(match.group(1))
            if value is None:
                continue
            months = value * 12 if "year" in pattern else value
            break

    if visa_group is None:
        return None

    payload = {
        "type": "us_visa",
        "required": True,
        "minimum_months_remaining": months,
        "visa_group": visa_group,
        "accepted_types": accepted_types or None,
    }
    return payload


def _extract_shadow_coc_document_gate(prompt_text: Any) -> dict[str, Any] | None:
    text = str(prompt_text or "")
    if not text.strip():
        return None
    lowered = text.lower()
    if re.search(r"\b(?:no|without)\s+(?:valid\s+)?(?:coc|certificate\s+of\s+competency)\b", lowered):
        return None
    if re.search(r"\b(?:valid\s+)?coc\b|\b(?:valid\s+)?certificate\s+of\s+competency\b", lowered):
        return {"type": "coc_document_gate", "required": True}
    return None


def _extract_shadow_recent_contract_vessel_experience(prompt_text: Any) -> dict[str, Any] | None:
    text = str(prompt_text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    has_contract_window = bool(re.search(r"\b(?:last|recent|previous|past)\s+\d+\s+contracts?\b", lowered))
    has_vessel_experience = bool(re.search(r"\b(?:experience|served|service)\b", lowered))
    if not has_contract_window and not has_vessel_experience:
        return None

    months = None
    month_match = re.search(r"\b(?:at\s+least\s+|minimum\s+)?(\d+)\s+months?\b", lowered)
    if month_match:
        months = _as_positive_int(month_match.group(1))

    contract_count = None
    contract_match = re.search(r"\b(?:last|recent|previous|past)\s+(\d+)\s+contracts?\b", lowered)
    if contract_match:
        contract_count = _as_positive_int(contract_match.group(1))

    ship_family = None
    for candidate in sorted(canonical_ship_family_values(), key=len, reverse=True):
        pattern = rf"\b{re.escape(candidate)}\b"
        if re.search(pattern, lowered):
            ship_family = candidate
            break

    if not ship_family:
        return None
    return {
        "type": "recent_contract_vessel_experience",
        "ship_family": ship_family,
        "minimum_months": months,
        "recent_contract_count": contract_count or 1,
    }


def _normalize_vessel_tonnage_unit(value: Any) -> str:
    unit = str(value or "").strip().lower().replace("-", "_")
    unit = re.sub(r"\s+", "_", unit)
    if unit in {"dwt", "deadweight", "dead_weight"}:
        return "dwt"
    if unit in {"gt", "grt", "gt_grt", "gross_tonnage", "gross_registered_tonnage"}:
        return "gt_grt"
    if unit == "unspecified":
        return "unspecified"
    return "any"


_AGE_PLAUSIBLE_MIN = 14
_AGE_PLAUSIBLE_MAX = 80


def _is_plausible_age(value: int | None) -> bool:
    if value is None:
        return True
    return _AGE_PLAUSIBLE_MIN <= value <= _AGE_PLAUSIBLE_MAX


def _age_bounds_from_text(text: Any) -> tuple[int | None, int | None]:
    prompt = str(text or "").strip().lower()
    if not prompt:
        return None, None

    age_token = r"(?:\d{1,2}|twenty|thirty|forty|fifty|sixty|seventy|eighty)"
    decade_plural_token = r"(?:twenties|thirties|forties|fifties|sixties|seventies|eighties)"
    decade_token = r"(?:twenties|thirties|forties|fifties|sixties|seventies|eighties|twenty|thirty|forty|fifty|sixty|seventy|eighty)"
    approx_patterns = [
        rf"(?:around|approximately|about|roughly)\s+({age_token})\s*(?:years?\s+old|yo|yrs?)?",
    ]
    for pattern in approx_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = _age_token_to_int(match.group(1))
            if value is not None:
                return max(0, value - 2), value + 2

    open_min_patterns = [
        rf"\b({age_token})\s+plus\b",
        rf"\b({age_token})\s*\+",
        rf"({age_token})\s*(?:yrs?|years?)?\s+(?:and\s+above|plus|or\s+older)\b",
    ]
    for pattern in open_min_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = _age_token_to_int(match.group(1))
            if value is not None:
                return value, None

    decade_patterns = [
        rf"(?:in\s+(?:his|her|their|the)\s+)?(?:mid|early|late)[-\s]+(\d{{1,2}})s\b",
        rf"\b(?:mid|early|late)[-\s]+(\d{{1,2}})s\b",
        rf"(?:in\s+(?:his|her|their|the)\s+)?(?:mid|early|late)[-\s]+({decade_plural_token})\b",
        rf"(?:in\s+(?:his|her|their|the)\s+)?(\d{{1,2}})s\b",
        rf"(?:in\s+(?:his|her|their|the)\s+)?({decade_plural_token})\b",
        rf"\b({decade_token})-something\b",
    ]
    for pattern in decade_patterns:
        match = re.search(pattern, prompt)
        if match:
            modifier = None
            phrase = match.group(0)
            if "early" in phrase:
                modifier = "early"
            elif "mid" in phrase:
                modifier = "mid"
            elif "late" in phrase:
                modifier = "late"
            decade_value = match.group(1)
            bounds = _age_decade_bounds(decade_value, modifier=modifier)
            if bounds != (None, None):
                return bounds

    range_patterns = [
        rf"older\s+than\s+({age_token})\s+and\s+younger\s+than\s+({age_token})",
        rf"older\s+than\s+({age_token})\s+but\s+younger\s+than\s+({age_token})",
        rf"must\s+be\s+at\s+least\s+({age_token})\s+and\s+no\s+more\s+than\s+({age_token})",
        rf"at\s+least\s+({age_token})\s+and\s+no\s+more\s+than\s+({age_token})",
        rf"between\s+({age_token})\s+(?:and|to)\s+({age_token})\s+years?\s+old",
        rf"between\s+the\s+ages?\s+of\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"within\s+the\s+ages?\s+of\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"within\s+the\s+age\s+of\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"age\s+range\s+of\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"age\s+of\s+({age_token})\s+(?:and|to)\s+({age_token})\s+years?\s+old",
        rf"ages?\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"aged?\s+({age_token})\s*(?:-|to|and)\s*({age_token})",
        rf"between\s+({age_token})\s+(?:and|to)\s+({age_token})",
        rf"({age_token})\s*-\s*({age_token})\s+years?",
        rf"min\s+({age_token})\s+max\s+({age_token})",
        rf"\bnlt\s+({age_token})\s+and\s+nmt\s+({age_token})\b",
    ]
    for pattern in range_patterns:
        match = re.search(pattern, prompt)
        if match:
            lower = _age_token_to_int(match.group(1))
            upper = _age_token_to_int(match.group(2))
            if lower is None or upper is None:
                continue
            if lower > upper:
                lower, upper = upper, lower
            if "older than" in match.group(0):
                lower += 1
            if "younger than" in match.group(0):
                upper -= 1
            if not (_is_plausible_age(lower) and _is_plausible_age(upper)):
                continue
            return lower, upper

    birth_year_patterns = [
        (r"born\s+after\s+(\d{4})", "max"),
        (r"born\s+before\s+(\d{4})", "min"),
    ]
    for pattern, direction in birth_year_patterns:
        match = re.search(pattern, prompt)
        if match:
            year = _age_token_to_int(match.group(1))
            if year is None:
                continue
            age = _utc_now_year() - year
            if not _is_plausible_age(age):
                continue
            if direction == "max":
                return None, age
            return age, None

    max_patterns = [
        (rf"up\s+to\s+({age_token})\s+years?\s+old", "inclusive"),
        (rf"\bmax(?:imum)?\s+({age_token})(?:\s*(?:yo|yrs?|years?))?\b", "inclusive"),
        (rf"no\s+older\s+than\s+({age_token})", "inclusive"),
        (rf"not\s+above\s+({age_token})", "inclusive"),
        (rf"no\s+candidate\s+above\s+(?:the\s+age\s+of\s+)?({age_token})", "inclusive"),
        (rf"cannot\s+exceed\s+({age_token})", "inclusive"),
        (rf"can'?t\s+be\s+older\s+than\s+({age_token})", "inclusive"),
        (rf"(?<!no\s)(?<!not\s)younger\s+than\s+({age_token})", "exclusive"),
        (rf"below\s+the\s+age\s+of\s+({age_token})", "exclusive"),
        (rf"below\s+age\s+({age_token})", "exclusive"),
        (rf"(?<!no\s)(?<!not\s)less\s+than\s+({age_token})\s+years?\s+old", "exclusive"),
        (rf"not\s+more\s+than\s+({age_token})\s+years?\s+old", "inclusive"),
        (rf"under\s+({age_token})", "exclusive"),
        (rf"({age_token})\s+and\s+below", "inclusive"),
        (rf"(?<!no\s)(?<!not\s)below\s+({age_token})", "exclusive"),
        (rf"maximum\s+age\s+(?:of\s+)?({age_token})", "inclusive"),
        (rf"maximum\s+age\s+should\s+be\s+({age_token})", "inclusive"),
        (rf"({age_token})\s*(?:yrs?|years?)?\s+(?:and\s+below|or\s+younger)\b", "inclusive"),
        (rf"\bnmt\s+({age_token})\b", "inclusive"),
    ]
    for pattern, mode in max_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = _age_token_to_int(match.group(1))
            if value is None:
                continue
            if mode == "exclusive":
                value -= 1
            if not _is_plausible_age(value):
                continue
            return None, value

    min_patterns = [
        (rf"at\s+least\s+({age_token})\s+years?\s+old", "inclusive"),
        (rf"(?:no|not)\s+younger\s+than\s+({age_token})", "inclusive"),
        (rf"(?:no|not)\s+below\s+({age_token})", "inclusive"),
        (rf"(?:no|not)\s+less\s+than\s+({age_token})", "inclusive"),
        (rf"age\s+over\s+({age_token})", "inclusive"),
        (rf"older\s+than\s+({age_token})", "exclusive"),
        (rf"over\s+the\s+age\s+of\s+({age_token})(?:\s+years?)?", "exclusive"),
        (rf"over\s+({age_token})", "exclusive"),
        (rf"above\s+the\s+age\s+of\s+({age_token})", "exclusive"),
        (rf"above\s+({age_token})", "exclusive"),
        (rf"minimum\s+age\s+(?:of\s+)?({age_token})", "inclusive"),
        (rf"minimum\s+age\s+should\s+be\s+({age_token})", "inclusive"),
        (rf"age\s+({age_token})\s+minimum", "inclusive"),
        (rf"min(?:imum)?(?:\s+age)?\s+(?:of\s+)?({age_token})", "inclusive"),
        (rf"\b({age_token})\s*\+", "inclusive"),
        (rf"\bnlt\s+({age_token})\b", "inclusive"),
    ]
    for pattern, mode in min_patterns:
        match = re.search(pattern, prompt)
        if match:
            value = _age_token_to_int(match.group(1))
            if value is None:
                continue
            if mode == "exclusive":
                value += 1
            if not _is_plausible_age(value):
                continue
            return value, None

    return None, None


def _canonical_list(values: Any, allowed: set[str] | frozenset[str] | None = None) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    seen: set[str] = set()
    canonical: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if allowed is not None and text not in allowed:
            continue
        if text not in seen:
            seen.add(text)
            canonical.append(text)
    return canonical


def _strip_phrase(text: str, phrase: str) -> str:
    phrase = _normalize_text(phrase)
    if not phrase:
        return text
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    pattern = rf"(?<!\w){escaped}(?!\w)"
    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def _cleanup_semantic_residual(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    while True:
        updated = re.sub(r"^(?:and|or|with)\b\s*", "", cleaned, flags=re.IGNORECASE)
        if updated == cleaned:
            break
        cleaned = updated
    while True:
        updated = re.sub(r"\s+(?:and|or|with)\b\s*$", "", cleaned, flags=re.IGNORECASE)
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned.strip(" ,.-")


def _normalize_with_semantic_repair(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a translated plan, clearing residual mandatory text if needed.

    Gemini often emits the right structured constraint but leaves the same
    requirement phrase in ``semantic_query``. In production mode that is
    correctly invalid, but in shadow mode we can safely repair the translated
    plan when this is the only validation failure.
    """

    normalized = normalize_query_plan_v1(plan, mode="production")
    errors = normalized.get("validation", {}).get("errors") or []
    if not errors or any(error.get("code") != "mandatory_marker_in_semantic_query" for error in errors):
        return normalized

    repaired = dict(normalized)
    repaired["semantic_query"] = ""
    repaired["validation"] = {"status": "valid", "errors": []}
    return normalize_query_plan_v1(repaired, mode="production")


def _repair_us_visa_accepted_types(plan: Mapping[str, Any], prompt_text: Any, analyzer: Any) -> dict[str, Any]:
    """Normalize generic US visa payloads after every model-shape translation path."""

    repaired = dict(plan)
    repaired_constraints: list[dict[str, Any]] = []
    changed = False
    for item in plan.get("applied_constraints") or []:
        if not isinstance(item, Mapping):
            repaired_constraints.append(item)
            continue
        item_copy = dict(item)
        constraint = item.get("constraint")
        if item.get("id") == "us_visa" and isinstance(constraint, Mapping):
            constraint_copy = dict(constraint)
            visa_group = _first_string(constraint_copy.get("visa_group"))
            if isinstance(visa_group, str):
                visa_group = visa_group.strip().lower() or None
            if visa_group:
                accepted_types = _canonical_list(constraint_copy.get("accepted_types") or [])
                normalized_types = _normalize_visa_accepted_types(str(prompt_text or ""), visa_group, accepted_types, analyzer)
                if normalized_types != accepted_types:
                    constraint_copy["accepted_types"] = normalized_types or None
                    item_copy["constraint"] = constraint_copy
                    changed = True
        repaired_constraints.append(item_copy)
    if changed:
        repaired["applied_constraints"] = repaired_constraints
    return repaired


def _constraint_signature(item: Mapping[str, Any]) -> tuple[str, str] | None:
    family_id = str(item.get("id") or "").strip()
    constraint = item.get("constraint")
    if not family_id or not isinstance(constraint, Mapping):
        return None
    if family_id == "experience_ship_type":
        ship_family = str(constraint.get("ship_family") or "").strip()
        return (family_id, ship_family) if ship_family else None
    if family_id == "engine_experience":
        engine_family = str(constraint.get("engine_family") or "").strip()
        return (family_id, engine_family) if engine_family else None
    try:
        payload = json.dumps(constraint, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = repr(constraint)
    return family_id, payload


def _synthesize_logical_groups_from_legacy(plan: Mapping[str, Any], prompt_text: str, rank: str | None, analyzer: Any) -> dict[str, Any]:
    if plan.get("logical_groups"):
        return dict(plan)
    prompt_text = str(prompt_text or "").strip()
    if not prompt_text:
        return dict(plan)

    extract_job_constraints = getattr(analyzer, "_extract_job_constraints", None)
    if not callable(extract_job_constraints):
        return dict(plan)

    try:
        legacy_constraints = extract_job_constraints(prompt_text, rank=rank)
    except Exception:
        return dict(plan)
    if not isinstance(legacy_constraints, Mapping) or not legacy_constraints.get("logical_groups"):
        return dict(plan)

    try:
        from .legacy_parser_adapter import LegacyParserAdapter

        legacy_plan = LegacyParserAdapter(analyzer).from_legacy_constraints(
            legacy_constraints,
            user_prompt=prompt_text,
            rank=rank,
            prompt_template_version="legacy.parser.v1",
            prompt_id=None,
        )
    except Exception:
        return dict(plan)

    logical_groups = [
        group
        for group in (legacy_plan.get("logical_groups") or [])
        if isinstance(group, Mapping) and group.get("type") == "any_of" and len(group.get("children") or []) >= 2
    ]
    if not logical_groups:
        return dict(plan)

    child_signatures = {
        signature
        for group in logical_groups
        for child in (group.get("children") or [])
        if isinstance(child, Mapping)
        for signature in [_constraint_signature(child)]
        if signature is not None
    }

    repaired = dict(plan)
    if child_signatures:
        repaired["applied_constraints"] = [
            item
            for item in (plan.get("applied_constraints") or [])
            if not (isinstance(item, Mapping) and _constraint_signature(item) in child_signatures)
        ]
    repaired["logical_groups"] = list(plan.get("logical_groups") or []) + [dict(group) for group in logical_groups]
    semantic_query = str(repaired.get("semantic_query") or "").strip()
    if semantic_query and (
        _normalize_text(semantic_query).lower() == _normalize_text(prompt_text).lower()
        or (
            not repaired.get("applied_constraints")
            and not repaired.get("unapplied_constraints")
        )
    ):
        repaired["semantic_query"] = ""
    return repaired


def _diagnostic_validation_errors(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    errors = plan.get("validation", {}).get("errors") if isinstance(plan.get("validation"), Mapping) else []
    if not isinstance(errors, list):
        return []
    return [dict(error) for error in errors if isinstance(error, Mapping)]


def _normalized_certificate_source(text: Any) -> str:
    normalized = _normalize_text(str(text or "").lower())
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("-", " ")
    return " ".join(normalized.split())


def _has_certificate_specific_terms(text: Any) -> bool:
    normalized = _normalized_certificate_source(text)
    if not normalized:
        return False
    certificate_terms = {
        "certificate",
        "certificates",
        "coc",
        "competency",
        "endorsement",
        "endorsements",
        "yellow fever",
        "stcw",
    }
    return any(term in normalized for term in certificate_terms)


def _has_visa_specific_terms(text: Any) -> bool:
    normalized = _normalized_certificate_source(text)
    if not normalized:
        return False
    visa_terms = {
        "visa",
        "us visa",
        "valid visa",
        "c1 d",
        "c1d",
        "c1/d",
        "d visa",
    }
    return any(term in normalized for term in visa_terms)


def _certificate_requirement_consumed_by_repair(
    item: Mapping[str, Any],
    applied_constraints: list[dict[str, Any]],
) -> bool:
    detail_text = _first_string(item.get("details"), item.get("display_value"), item.get("value"))
    values_text = None
    values = item.get("values")
    if isinstance(values, list):
        values_text = _first_string(*values)
    elif values is not None:
        values_text = _first_string(values)
    source_text = _first_string(detail_text, values_text, item.get("source_text"))
    source = _normalized_certificate_source(source_text)
    if not source:
        return False

    if any(constraint.get("id") in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"} for constraint in applied_constraints):
        for constraint in applied_constraints:
            if constraint.get("id") not in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"}:
                continue
            constraint_source = _normalized_certificate_source(constraint.get("source_text") or (constraint.get("constraint") or {}).get("display_value"))
            if constraint_source and (constraint_source in source or source in constraint_source):
                return True
            constraint_payload = constraint.get("constraint") if isinstance(constraint.get("constraint"), Mapping) else {}
            known_fragments = []
            if isinstance(constraint_payload, Mapping):
                known_fragments.extend(
                    [
                        constraint_payload.get("type"),
                        constraint_payload.get("rank"),
                        constraint_payload.get("grade"),
                    ]
                )
                known_fragments.extend(constraint_payload.get("certificates_required") or [])
                known_fragments.extend(constraint_payload.get("endorsements_required") or [])
            for fragment in known_fragments:
                fragment_source = _normalized_certificate_source(fragment)
                if fragment_source and fragment_source in source:
                    return True

    if any(constraint.get("id") == "us_visa" for constraint in applied_constraints):
        source_text = _first_string(detail_text, values_text, item.get("source_text"))
        if source_text and not _has_certificate_specific_terms(source_text) and _has_visa_specific_terms(source_text):
            return True

    return False


def _family_to_canonical_items(
    family: str,
    item: Mapping[str, Any],
    *,
    analyzer: Any,
    raw_prompt: str,
    rank: str | None,
    canonical_rank: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (applied_items, unapplied_items, semantic_fragments) for a Gemini family item."""

    parameters = _extract_parameters(item)
    prompt_text = str(raw_prompt or "").strip()
    source_text = _first_string(item.get("source_text"), item.get("display_value"), parameters.get("display_value"), parameters.get("label"), raw_prompt) or raw_prompt
    confidence = _first_string(item.get("confidence"), parameters.get("confidence")) or "high"
    family = _canonicalize_family_name(family)

    if not family:
        return [], [], []

    if family in {"age_range"}:
        if not _age_range_is_anchored(prompt_text or source_text or raw_prompt):
            source = _first_string(source_text, prompt_text, raw_prompt) or raw_prompt
            return [], [
                {
                    "id": "age_range",
                    "mode": "required",
                    "reason": "unsupported_filter_family",
                    "source_text": source,
                    "suggested_handling": "block_search",
                    "confidence": "low",
                }
            ], []
        text_minimum_years, text_maximum_years = _age_bounds_from_text(
            _first_string(
                item.get("source_text"),
                item.get("details"),
                item.get("display_value"),
                parameters.get("display_value"),
                parameters.get("label"),
                raw_prompt,
            )
        )
        minimum_years = _as_positive_int(
            parameters.get("minimum_years")
            if parameters.get("minimum_years") is not None
            else parameters.get("minimum_age") if parameters.get("minimum_age") is not None else parameters.get("min_age")
            if parameters.get("minimum_age") is not None or parameters.get("min_age") is not None
            else item.get("minimum_years")
            if item.get("minimum_years") is not None
            else item.get("minimum_age")
            if item.get("minimum_age") is not None
            else item.get("min_age")
        )
        maximum_years = _as_positive_int(
            parameters.get("maximum_years")
            if parameters.get("maximum_years") is not None
            else parameters.get("maximum_age") if parameters.get("maximum_age") is not None else parameters.get("max_age")
            if parameters.get("maximum_age") is not None or parameters.get("max_age") is not None
            else item.get("maximum_years")
            if item.get("maximum_years") is not None
            else item.get("maximum_age")
            if item.get("maximum_age") is not None
            else item.get("max_age")
        )
        if text_minimum_years is not None:
            minimum_years = text_minimum_years
        if text_maximum_years is not None:
            maximum_years = text_maximum_years
        if minimum_years is None and maximum_years is None:
            return [], [], []
        fragments = []
        if minimum_years is not None:
            fragments.extend(
                [
                    f"older than {minimum_years}",
                    f"older than {minimum_years - 1}" if minimum_years > 0 else "",
                    f"over {minimum_years}",
                    f"over {minimum_years - 1}" if minimum_years > 0 else "",
                    f"above {minimum_years}",
                    f"above {minimum_years - 1}" if minimum_years > 0 else "",
                    f"more than {minimum_years}",
                    f"more than {minimum_years - 1}" if minimum_years > 0 else "",
                ]
            )
        if maximum_years is not None:
            fragments.extend(
                [
                    f"under {maximum_years}",
                    f"under {maximum_years + 1}",
                    f"younger than {maximum_years}",
                    f"younger than {maximum_years + 1}",
                    f"below {maximum_years}",
                    f"below {maximum_years + 1}",
                    f"less than {maximum_years}",
                    f"less than {maximum_years + 1}",
                ]
            )
        return [
            _make_applied_constraint(
                "age_range",
                {"type": "age_range", "minimum_years": minimum_years, "maximum_years": maximum_years},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], fragments

    if family == "rank_match":
        if not _rank_match_is_anchored(prompt_text or source_text or raw_prompt):
            return [], [], []
        rank_value = _first_string(
            parameters.get("rank"),
            parameters.get("rank_normalized"),
            parameters.get("applied_rank_normalized"),
            canonical_rank,
        )
        if not rank_value:
            extract_rank = getattr(analyzer, "_extract_rank_constraint", None)
            if callable(extract_rank):
                try:
                    inferred_rank = extract_rank(prompt_text)
                except Exception:
                    inferred_rank = None
                if isinstance(inferred_rank, Mapping):
                    rank_value = _first_string(*(inferred_rank.get("applied_rank_normalized") or []), canonical_rank)
        if not rank_value:
            rank_value = _extract_shadow_rank_value(prompt_text or source_text or raw_prompt)
        if not rank_value:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_match",
                {"type": "rank_match", "rank": rank_value},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [rank_value.replace("_", " "), rank_value]

    if family == "coc_document_gate":
        required_value = _first_present(parameters.get("required"), parameters.get("must_have"), parameters.get("validity"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("coc_document_gate explicitly marked false")
        if not _extract_shadow_coc_document_gate(prompt_text or source_text or raw_prompt):
            return [], [], []
        return [
            _make_applied_constraint(
                "coc_document_gate",
                {"type": "coc_document_gate", "required": True},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], ["valid coc", "coc required", "certificate of competency required", "certificate of competency", "coc"]

    if family == "coc_grade_match":
        grade = _first_string(parameters.get("grade"), parameters.get("coc_grade"), parameters.get("required_grade"))
        if not grade:
            extract_coc_grade = getattr(analyzer, "_extract_coc_grade_constraint", None)
            if callable(extract_coc_grade):
                try:
                    coc_grade = extract_coc_grade(prompt_text)
                except Exception:
                    coc_grade = None
                if isinstance(coc_grade, Mapping):
                    grades = coc_grade.get("required_grades") or []
                    grade = _first_string(*grades)
        if not grade:
            return [], [], []
        return [
            _make_applied_constraint(
                "coc_grade_match",
                {"type": "coc_grade_match", "grade": grade},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [grade.replace("_", " ")]

    if family == "coc_country_match":
        raw_countries = parameters.get("countries") or parameters.get("country") or parameters.get("issue_authority")
        if isinstance(raw_countries, str):
            raw_countries = [raw_countries]
        countries = [
            " ".join(str(country or "").lower().split())
            for country in (raw_countries or [])
            if str(country or "").strip()
        ]
        if not countries:
            extract_coc_country = getattr(analyzer, "_extract_coc_country_constraint", None)
            if callable(extract_coc_country):
                try:
                    coc_country = extract_coc_country(prompt_text)
                except Exception:
                    coc_country = None
                if isinstance(coc_country, Mapping):
                    countries = [
                        " ".join(str(country or "").lower().split())
                        for country in (coc_country.get("countries") or [])
                        if str(country or "").strip()
                    ]
        if not countries:
            return [], [], []
        return [
            _make_applied_constraint(
                "coc_country_match",
                {"type": "coc_country_match", "countries": countries, "operator": "contains_any"},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], countries

    if family == "stcw_basic":
        required_value = _first_present(parameters.get("required"), parameters.get("validity"), parameters.get("must_have"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("stcw_basic explicitly marked false")
        if not _stcw_basic_is_anchored(prompt_text or source_text or raw_prompt):
            source = _first_string(source_text, prompt_text, raw_prompt) or raw_prompt
            return [], [
                {
                    "id": "stcw_basic",
                    "mode": "required",
                    "reason": "unsupported_filter_family",
                    "source_text": source,
                    "suggested_handling": "block_search",
                    "confidence": "low",
                }
            ], []
        return [
            _make_applied_constraint(
                "stcw_basic",
                {"type": "stcw_basic", "required": True},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [
            "stcw basic",
            "basic stcw",
            "basic safety training",
            "bst",
            "basic training package",
            "stcw a-vi/1",
            "all four basic stcw",
            "all four basic courses",
            "all four basic certificates",
            "four basic certificates",
            "pssr",
            "pst",
            "fpff",
            "efa",
            "valid stcw basic",
            "stcw basic required",
            "basic stcw required",
            "valid stcw basic safety",
        ]

    if family == "us_visa":
        required_value = _first_present(parameters.get("required"), parameters.get("validity"), parameters.get("must_have"))
        if _is_false_value(required_value):
            raise ShadowLLMTranslationError("us_visa explicitly marked false")
        if not _us_visa_is_anchored(prompt_text or source_text or raw_prompt):
            source = _first_string(source_text, prompt_text, raw_prompt) or raw_prompt
            return [], [
                {
                    "id": "us_visa",
                    "mode": "required",
                    "reason": "unsupported_filter_family",
                    "source_text": source,
                    "suggested_handling": "block_search",
                    "confidence": "low",
                }
            ], []
        months = _as_positive_int(
            parameters.get("minimum_months_remaining")
            if parameters.get("minimum_months_remaining") is not None
            else parameters.get("months_remaining")
            if parameters.get("months_remaining") is not None
            else parameters.get("minimum_months")
        )
        visa_group = _first_string(parameters.get("visa_group"), item.get("visa_group"))
        if isinstance(visa_group, str):
            visa_group = visa_group.strip().lower() or None
        shadow_visa = _extract_shadow_us_visa_constraint(analyzer, prompt_text or source_text or raw_prompt)
        if not visa_group:
            if isinstance(shadow_visa, Mapping):
                visa_group = _first_string(shadow_visa.get("visa_group"))
                if isinstance(visa_group, str):
                    visa_group = visa_group.strip().lower() or None
        if months is None and isinstance(shadow_visa, Mapping):
            months = _as_positive_int(shadow_visa.get("minimum_months_remaining"))
        elif isinstance(shadow_visa, Mapping):
            text_months = _as_positive_int(shadow_visa.get("minimum_months_remaining"))
            if text_months is not None:
                months = text_months
        model_accepted_types = _canonical_list(parameters.get("accepted_types") or item.get("accepted_types") or [])
        accepted_types = model_accepted_types
        if visa_group:
            accepted_types = _normalize_visa_accepted_types(prompt_text or source_text or raw_prompt, visa_group, model_accepted_types, analyzer)
        return [
            _make_applied_constraint(
                "us_visa",
                {
                    "type": "us_visa",
                    "required": True,
                    "minimum_months_remaining": months,
                    "visa_group": visa_group,
                    "accepted_types": accepted_types or None,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], ["valid us visa", "us visa", "visa required", "valid visa"] + accepted_types

    if family == "passport_validity":
        if _PASSPORT_POLARITY_INVERSION.search(prompt_text or source_text or raw_prompt):
            source = _first_string(source_text, prompt_text, raw_prompt) or raw_prompt
            return [], [
                {
                    "id": "passport_validity",
                    "mode": "required",
                    "reason": "unsupported_filter_family",
                    "source_text": source,
                    "suggested_handling": "block_search",
                    "confidence": "low",
                }
            ], []
        validity_value = parameters.get("validity") or parameters.get("is_valid") or parameters.get("required")
        months = _as_positive_int(parameters.get("minimum_months_remaining") or parameters.get("months_remaining"))
        shadow_passport = _extract_shadow_passport_validity_constraint(prompt_text or source_text or raw_prompt)
        if months is None and isinstance(shadow_passport, Mapping):
            months = _as_positive_int(shadow_passport.get("minimum_months_remaining"))
        if validity_value in {"valid", True, "true", "True"} or months is not None:
            return [
                _make_applied_constraint(
                    "passport_validity",
                    {"type": "passport_validity", "must_be_valid": True, "minimum_months_remaining": months},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["valid passport", "passport required", "passport mandatory"]
        if isinstance(shadow_passport, Mapping):
            return [
                _make_applied_constraint(
                    "passport_validity",
                    {"type": "passport_validity", "must_be_valid": True, "minimum_months_remaining": months},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["valid passport", "passport required", "passport mandatory", "passport validity"]
        return [], [], []

    if family == "stcw_endorsement":
        if _SUFFICIENCY_ONLY_PATTERN.search(prompt_text or source_text or raw_prompt):
            return [], [], []
        endorsements = _canonical_list(
            parameters.get("endorsements_required")
            if parameters.get("endorsements_required") is not None
            else parameters.get("endorsements")
            if parameters.get("endorsements") is not None
            else parameters.get("endorsement")
            if parameters.get("endorsement") is not None
            else [],
            allowed=canonical_endorsement_values(),
        )
        certificates = _canonical_list(
            parameters.get("certificates_required")
            if parameters.get("certificates_required") is not None
            else parameters.get("certificates")
            if parameters.get("certificates") is not None
            else [],
            allowed=canonical_certificate_values(),
        )
        if not endorsements and not certificates:
            extract_endorsement = getattr(analyzer, "_extract_endorsement_constraint", None)
            if callable(extract_endorsement):
                try:
                    endorsement = extract_endorsement(prompt_text)
                except Exception:
                    endorsement = None
                if isinstance(endorsement, Mapping):
                    endorsements = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_endorsement_values())
                    certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values())
        if not endorsements:
            endorsements = _extract_shadow_endorsement_values(prompt_text or source_text or raw_prompt)
        if certificates:
            family_id = "rank_certificate_expectation" if _first_string(parameters.get("rank"), parameters.get("rank_normalized")) or rank else "certificate_requirement"
            payload = {"type": family_id, "certificates_required": certificates}
            if family_id == "rank_certificate_expectation":
                payload["rank"] = _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank)
                payload["endorsements_required"] = endorsements
            return [
                _make_applied_constraint(
                    family_id,
                    payload,
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [_humanized.replace("_", " ") for _humanized in certificates]
        if endorsements:
            return [
                _make_applied_constraint(
                    "stcw_endorsement",
                    {"type": "stcw_endorsement", "endorsements_required": endorsements},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [token.replace("_", " ") for token in endorsements] + [f"{token.replace('_', ' ')} endorsement" for token in endorsements]
        return [], [], []

    if family == "rank_certificate_expectation":
        certificates = _canonical_list(parameters.get("certificates_required") or parameters.get("certificates") or [], allowed=canonical_certificate_values())
        endorsements = _canonical_list(parameters.get("endorsements_required") or parameters.get("endorsements") or [], allowed=canonical_endorsement_values())
        if not certificates and not endorsements:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_certificate_expectation",
                {
                    "type": "rank_certificate_expectation",
                    "rank": _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank),
                    "certificates_required": certificates,
                    "endorsements_required": endorsements,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [token.replace("_", " ") for token in certificates + endorsements]

    if family == "certificate_requirement":
        certificates = _canonical_list(
            parameters.get("certificates_required")
            if parameters.get("certificates_required") is not None
            else parameters.get("certificates")
            if parameters.get("certificates") is not None
            else item.get("values")
            if item.get("values") is not None
            else item.get("value")
            if item.get("value") is not None
            else [],
            allowed=canonical_certificate_values(),
        )
        if not certificates:
            extract_endorsement = getattr(analyzer, "_extract_endorsement_constraint", None)
            if callable(extract_endorsement):
                try:
                    endorsement = extract_endorsement(prompt_text)
                except Exception:
                    endorsement = None
                if isinstance(endorsement, Mapping):
                    certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values())
        text_certificates = _extract_shadow_certificate_values(prompt_text or source_text or raw_prompt)
        if text_certificates:
            certificates = list(dict.fromkeys(certificates + text_certificates))
        prompt_lower = str(raw_prompt or "").lower()
        source_lower = str(source_text or "").lower()
        if not certificates and ("certificate of competency" in prompt_lower or "coc" in prompt_lower or "certificate of competency" in source_lower or "coc" in source_lower):
            return [
                _make_applied_constraint(
                    "coc_document_gate",
                    {"type": "coc_document_gate", "required": True},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["valid coc", "coc required", "certificate of competency required", "certificate of competency", "coc"]
        if not certificates and (
            re.search(r"\bmedical\s+(?:valid|current|required|mandatory)\b", prompt_lower)
            or re.search(r"\bvalid\s+medical\b", prompt_lower)
            or re.search(r"\bmedical\s+(?:valid|current|required|mandatory)\b", source_lower)
            or re.search(r"\bvalid\s+medical\b", source_lower)
        ):
            return [
                _make_applied_constraint(
                    "certificate_requirement",
                    {"type": "certificate_requirement", "certificates_required": ["cert_medical_care"]},
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], ["medical care", "medical certificate", "valid medical", "medical valid"]
        if not certificates:
            return [], [], []
        return [
            _make_applied_constraint(
                "certificate_requirement",
                {"type": "certificate_requirement", "certificates_required": certificates},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [token.replace("_", " ") for token in certificates]

    if family == "recent_contract_vessel_experience":
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        shadow_recent = _extract_shadow_recent_contract_vessel_experience(prompt_text or source_text or raw_prompt)
        if isinstance(shadow_recent, Mapping):
            ship_family = _first_string(shadow_recent.get("ship_family"), ship_family)
            minimum_months = _as_positive_int(shadow_recent.get("minimum_months")) if shadow_recent.get("minimum_months") is not None else minimum_months
            recent_contract_count = _as_positive_int(shadow_recent.get("recent_contract_count")) or recent_contract_count
        if ship_family in canonical_ship_family_values():
            return [
                _make_applied_constraint(
                    "recent_contract_vessel_experience",
                    {
                        "type": "recent_contract_vessel_experience",
                        "ship_family": ship_family,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count or 1,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [ship_family]
        return [], [], []

    if family == "engine_experience":
        engine_family = _first_string(parameters.get("engine_family"), parameters.get("engine_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        if engine_family in canonical_engine_family_values():
            return [
                _make_applied_constraint(
                    "engine_experience",
                    {
                        "type": "engine_experience",
                        "engine_family": engine_family,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [engine_family.replace("_", " ")]
        return [], [], []

    if family == "engine_vessel_experience":
        engine_family = _first_string(parameters.get("engine_family"), parameters.get("engine_type"))
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        recent_contract_count = _as_positive_int(parameters.get("recent_contract_count") or parameters.get("lookback_contracts"))
        if engine_family in canonical_engine_family_values():
            return [
                _make_applied_constraint(
                    "engine_vessel_experience",
                    {
                        "type": "engine_vessel_experience",
                        "engine_family": engine_family,
                        "ship_family": ship_family if ship_family in canonical_ship_family_values() else None,
                        "minimum_months": minimum_months,
                        "recent_contract_count": recent_contract_count,
                    },
                    source_text=source_text,
                    confidence=confidence,
                )
            ], [], [engine_family.replace("_", " ")] + ([ship_family] if ship_family in canonical_ship_family_values() else [])
        return [], [], []

    if family == "company_continuity":
        minimum_contracts = _as_positive_int(parameters.get("minimum_contracts") or parameters.get("min_same_company_contract_count"))
        if not minimum_contracts:
            return [], [], []
        return [
            _make_applied_constraint(
                "company_continuity",
                {
                    "type": "company_continuity",
                    "minimum_contracts": minimum_contracts,
                    "same_company_required": True,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"{minimum_contracts} contracts", f"same company", f"same employer"]

    if family == "recency":
        maximum_months = _as_positive_int(parameters.get("maximum_months_since_last_contract") or parameters.get("max_months_since_sign_off"))
        must_be_currently_sailing = parameters.get("must_be_currently_sailing")
        if must_be_currently_sailing not in {True, False, None}:
            must_be_currently_sailing = None
        if maximum_months is None and must_be_currently_sailing is None:
            return [], [], []
        return [
            _make_applied_constraint(
                "recency",
                {
                    "type": "recency",
                    "maximum_months_since_last_contract": maximum_months,
                    "must_be_currently_sailing": must_be_currently_sailing,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"last {maximum_months} months" if maximum_months else "", "recent contract", "signed off"]

    if family == "rank_duration_experience":
        minimum_months = _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months"))
        if not minimum_months:
            return [], [], []
        return [
            _make_applied_constraint(
                "rank_duration_experience",
                {
                    "type": "rank_duration_experience",
                    "rank": _first_string(parameters.get("rank"), parameters.get("rank_normalized"), rank, canonical_rank),
                    "minimum_months": minimum_months,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [f"{minimum_months} months", "experience as", "rank experience"]

    if family == "vessel_tonnage":
        minimum_value = _as_positive_int(
            _first_present(
                parameters.get("min_value"),
                parameters.get("minimum_value"),
                parameters.get("min_tonnage"),
                parameters.get("minimum_tonnage"),
                item.get("min_value"),
                item.get("minimum_value"),
                item.get("min_tonnage"),
                item.get("minimum_tonnage"),
            )
        )
        maximum_value = _as_positive_int(
            _first_present(
                parameters.get("max_value"),
                parameters.get("maximum_value"),
                parameters.get("max_tonnage"),
                parameters.get("maximum_tonnage"),
                item.get("max_value"),
                item.get("maximum_value"),
                item.get("max_tonnage"),
                item.get("maximum_tonnage"),
            )
        )
        if minimum_value is None and maximum_value is None:
            return [], [], []
        unit = _normalize_vessel_tonnage_unit(
            _first_present(
                parameters.get("unit"),
                parameters.get("tonnage_unit"),
                item.get("unit"),
                item.get("tonnage_unit"),
            )
        )
        fragments = []
        if minimum_value is not None:
            fragments.append(f"minimum vessel tonnage {minimum_value}")
        if maximum_value is not None:
            fragments.append(f"maximum vessel tonnage {maximum_value}")
        fragments.append(unit)
        return [
            _make_applied_constraint(
                "vessel_tonnage",
                {
                    "type": "vessel_tonnage",
                    "min_value": minimum_value,
                    "max_value": maximum_value,
                    "unit": unit,
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], fragments

    if family == "experience_ship_type":
        ship_family = _first_string(parameters.get("ship_family"), parameters.get("vessel_type"))
        if ship_family not in canonical_ship_family_values():
            return [], [], []
        return [
            _make_applied_constraint(
                "experience_ship_type",
                {
                    "type": "experience_ship_type",
                    "ship_family": ship_family,
                    "minimum_months": _as_positive_int(parameters.get("minimum_months") or parameters.get("min_months")),
                },
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [ship_family]

    if family == "availability":
        status = _first_string(parameters.get("status")) or "available"
        available_by = _first_string(parameters.get("available_by"), parameters.get("available_by_date"))
        if not available_by and callable(getattr(analyzer, "_extract_availability_constraint", None)):
            try:
                availability = analyzer._extract_availability_constraint(prompt_text)
            except Exception:
                availability = None
            if isinstance(availability, Mapping):
                value_type = availability.get("value_type")
                if value_type == "status":
                    status = "available"
                    available_by = None
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
                elif value_type == "date":
                    status = "available_by_date"
                    available_by = _first_string(availability.get("available_from_date")) or available_by
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
                elif value_type == "relative_phrase":
                    status = "available_by_date"
                    available_by = None
                    source_text = _first_string(availability.get("display_value"), source_text, raw_prompt) or source_text
        return [
            _make_applied_constraint(
                "availability",
                {"type": "availability", "status": status, "available_by": available_by},
                source_text=source_text,
                confidence=confidence,
            )
        ], [], [status, available_by or ""]

    if family in {"vessel_type", "sea_service"} or is_unsupported_family(family):
        canonical_id = _canonical_unapplied_family_id(family)
        return [], [
            {
                "id": canonical_id,
                "mode": "required",
                "reason": "unsupported_filter_family",
                "source_text": source_text,
                "suggested_handling": "block_search",
                "confidence": confidence or "medium",
            }
        ], [source_text, canonical_id.replace("_", " ")]

    if not is_active_family(family):
        canonical_id = _canonical_unapplied_family_id(family)
        return [], [
            {
                "id": canonical_id,
                "mode": "required",
                "reason": "insufficient_schema",
                "source_text": source_text,
                "suggested_handling": "block_search",
                "confidence": confidence or "medium",
            }
        ], [source_text, canonical_id.replace("_", " ")]

    return [], [], []


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
    logical_groups: list[dict[str, Any]] = []
    unapplied_constraints: list[dict[str, Any]] = []
    semantic_fragments: list[str] = []

    if isinstance(parsed.get("hard_constraints"), list) or isinstance(parsed.get("recruiter_requirements"), list):
        for item in parsed.get("hard_constraints") or []:
            if isinstance(item, Mapping):
                family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
                translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                    family,
                    item,
                    analyzer=analyzer,
                    raw_prompt=raw_prompt,
                    rank=rank,
                    canonical_rank=canonical_rank,
                )
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
        for item in parsed.get("recruiter_requirements") or []:
            if isinstance(item, Mapping):
                family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
                translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                    family,
                    item,
                    analyzer=analyzer,
                    raw_prompt=raw_prompt,
                    rank=rank,
                    canonical_rank=canonical_rank,
                )
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
    else:
        for item in parsed.get("applied_constraints") or []:
            if not isinstance(item, Mapping):
                continue
            family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
            translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
                family,
                item,
                analyzer=analyzer,
                raw_prompt=raw_prompt,
                rank=rank,
                canonical_rank=canonical_rank,
            )
            if translated_applied or translated_unapplied:
                applied_constraints.extend(translated_applied)
                unapplied_constraints.extend(translated_unapplied)
                semantic_fragments.extend(translated_fragments)
                continue
            if isinstance(item.get("constraint"), Mapping) and family:
                applied_constraints.append(dict(item))

    for item in parsed.get("logical_groups") or []:
        if isinstance(item, Mapping):
            logical_groups.append(dict(item))

    for item in parsed.get("unapplied_constraints") or []:
        if not isinstance(item, Mapping):
            continue
        family = str(item.get("filter_family") or item.get("family") or item.get("id") or "").strip()
        if not family:
            continue
        translated_applied, translated_unapplied, translated_fragments = _family_to_canonical_items(
            family,
            item,
            analyzer=analyzer,
            raw_prompt=raw_prompt,
            rank=rank,
            canonical_rank=canonical_rank,
        )
        if translated_applied or translated_unapplied:
            applied_constraints.extend(translated_applied)
            unapplied_constraints.extend(translated_unapplied)
            semantic_fragments.extend(translated_fragments)
            continue

        if is_active_family(family):
            if family == "certificate_requirement":
                source_text = _first_string(item.get("source_text"), item.get("details"), item.get("display_value"), raw_prompt) or raw_prompt
                canonical_unapplied = {
                    "id": "certificate_requirement",
                    "mode": item.get("mode") if item.get("mode") in {"required", "preferred"} else "required",
                    "reason": item.get("reason") if item.get("reason") in {"unsupported_filter_family", "unsupported_value", "ambiguous_value", "insufficient_schema", "validation_failed"} else "insufficient_schema",
                    "source_text": source_text,
                    "suggested_handling": item.get("suggested_handling") if item.get("suggested_handling") in {"block_search", "semantic_with_warning", "ignore_with_warning"} else "block_search",
                    "confidence": item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium",
                }
                unapplied_constraints.append(canonical_unapplied)
                semantic_fragments.append(source_text)
                semantic_fragments.append("certificate requirement")
                continue
            raise ShadowLLMTranslationError(f"active family {family} could not be translated from unapplied_constraints")

        allowed_keys = {"id", "mode", "reason", "source_text", "suggested_handling", "confidence"}
        if set(item.keys()).issubset(allowed_keys) and isinstance(item.get("id"), str) and item.get("id"):
            canonical_unapplied = dict(item)
            canonical_unapplied["id"] = _canonical_unapplied_family_id(str(canonical_unapplied["id"]))
            unapplied_constraints.append(canonical_unapplied)
            semantic_fragments.append(str(canonical_unapplied.get("source_text") or ""))
            semantic_fragments.append(str(canonical_unapplied.get("id") or "").replace("_", " "))
            continue

        source_text = _first_string(item.get("source_text"), item.get("details"), item.get("display_value"), raw_prompt) or raw_prompt
        canonical_id = _canonical_unapplied_family_id(family)
        mode = item.get("mode") if item.get("mode") in {"required", "preferred"} else "required"
        reason = item.get("reason") if item.get("reason") in {"unsupported_filter_family", "unsupported_value", "ambiguous_value", "insufficient_schema", "validation_failed"} else (
            "unsupported_filter_family" if is_unsupported_family(family) or family in {"vessel_type", "sea_service"} else "insufficient_schema"
        )
        suggested_handling = item.get("suggested_handling") if item.get("suggested_handling") in {"block_search", "semantic_with_warning", "ignore_with_warning"} else ("block_search" if mode == "required" else "semantic_with_warning")
        confidence = item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "medium"
        canonical_unapplied = {
            "id": canonical_id,
            "mode": mode,
            "reason": reason,
            "source_text": source_text,
            "suggested_handling": suggested_handling,
            "confidence": confidence,
        }
        unapplied_constraints.append(canonical_unapplied)
        semantic_fragments.append(source_text)
        semantic_fragments.append(canonical_id.replace("_", " "))

    if _rank_match_is_anchored(prompt_text) and not any(constraint.get("id") == "rank_match" for constraint in applied_constraints) and not any(
        constraint.get("id") == "coc_grade_match" for constraint in applied_constraints
    ):
        rank_value = None
        extract_rank_constraint = getattr(analyzer, "_extract_rank_constraint", None)
        if callable(extract_rank_constraint):
            try:
                inferred_rank = extract_rank_constraint(prompt_text)
            except Exception:
                inferred_rank = None
            if isinstance(inferred_rank, Mapping):
                inferred_ranks = inferred_rank.get("applied_rank_normalized") or []
                if inferred_ranks:
                    rank_value = _first_string(*inferred_ranks, canonical_rank)
        if not rank_value:
            rank_value = _extract_shadow_rank_value(prompt_text)
        if rank_value:
            applied_constraints.append(
                _make_applied_constraint(
                    "rank_match",
                    {"type": "rank_match", "rank": rank_value},
                    source_text=prompt_text or raw_prompt,
                    confidence="high",
                )
            )
            semantic_fragments.extend([rank_value.replace("_", " "), rank_value])

    if not any(constraint.get("id") == "age_range" for constraint in applied_constraints):
        minimum_years = maximum_years = None
        extract_age_constraint = getattr(analyzer, "_extract_age_constraint", None)
        if callable(extract_age_constraint):
            try:
                age_constraint = extract_age_constraint(prompt_text)
            except Exception:
                age_constraint = None
            if isinstance(age_constraint, Mapping):
                minimum_years = _first_present(age_constraint.get("min_age"), age_constraint.get("minimum_years"))
                maximum_years = _first_present(age_constraint.get("max_age"), age_constraint.get("maximum_years"))
        if minimum_years is None and maximum_years is None:
            age_cue_pattern = re.compile(
                r"\b(?:age|aged|ages|years?\s+old|yo|yrs?\s+old|under|older|younger|below|above|between|range|minimum\s+age|maximum\s+age|at\s+least|no\s+older|not\s+above|no\s+younger|not\s+below|nlt|nmt|plus|thirty-something|forty-something|fifty-something|twenties|thirties|forties|fifties|sixties|seventies|eighties)\b|"
                r"\bborn\s+(?:after|before)\b|"
                r"\b(?:in\s+(?:his|her|their|the)\s+)?(?:mid|early|late)[-\s]+\d{1,2}s\b|"
                r"\b\d{1,2}s\b",
                flags=re.IGNORECASE,
            )
            bare_plus_prompt = bool(re.fullmatch(r"\d{1,2}\s*\+", prompt_text.strip()))
            if (
                bare_plus_prompt
                or (
                    age_cue_pattern.search(prompt_text)
                    and not re.search(r"\bmiddle[-\s]?aged\b|\byoung at heart\b|\bsenior officer\b", prompt_text, flags=re.IGNORECASE)
                )
            ):
                minimum_years, maximum_years = _age_bounds_from_text(prompt_text)
        if minimum_years is not None or maximum_years is not None:
            applied_constraints.append(
                _make_applied_constraint(
                    "age_range",
                    {
                        "type": "age_range",
                        "minimum_years": minimum_years,
                        "maximum_years": maximum_years,
                    },
                    source_text=prompt_text or raw_prompt,
                    confidence="high",
                )
            )
            if minimum_years is not None:
                semantic_fragments.extend(
                    [
                        f"at least {minimum_years} years old",
                        f"minimum age {minimum_years}",
                        f"older than {minimum_years}",
                        f"over {minimum_years}",
                    ]
                )
            if maximum_years is not None:
                semantic_fragments.extend(
                    [
                        f"up to {maximum_years} years old",
                        f"maximum age {maximum_years}",
                        f"younger than {maximum_years}",
                        f"below {maximum_years}",
                    ]
                )

    if not any(constraint.get("id") == "passport_validity" for constraint in applied_constraints):
        shadow_passport = _extract_shadow_passport_validity_constraint(prompt_text)
        if isinstance(shadow_passport, Mapping):
            applied_constraints.append(
                _make_applied_constraint(
                    "passport_validity",
                    {
                        "type": "passport_validity",
                        "must_be_valid": True,
                        "minimum_months_remaining": _as_positive_int(shadow_passport.get("minimum_months_remaining")),
                    },
                    source_text=_first_string(shadow_passport.get("display_value"), prompt_text) or prompt_text,
                    confidence="high",
                )
            )
            semantic_fragments.extend(
                [
                    "valid passport",
                    "passport required",
                    "passport mandatory",
                    "passport validity",
                    "passport current",
                    "fresh passport",
                ]
            )

    if not any(constraint.get("id") == "coc_document_gate" for constraint in applied_constraints):
        shadow_coc = _extract_shadow_coc_document_gate(prompt_text)
        if isinstance(shadow_coc, Mapping):
            applied_constraints.append(
                _make_applied_constraint(
                    "coc_document_gate",
                    {"type": "coc_document_gate", "required": True},
                    source_text=prompt_text or raw_prompt,
                    confidence="high",
                )
            )
            semantic_fragments.extend(["valid coc", "coc required", "certificate of competency required", "certificate of competency", "coc"])

    if not any(constraint.get("id") == "coc_country_match" for constraint in applied_constraints):
        extract_coc_country_constraint = getattr(analyzer, "_extract_coc_country_constraint", None)
        if callable(extract_coc_country_constraint):
            try:
                coc_country = extract_coc_country_constraint(prompt_text)
            except Exception:
                coc_country = None
            if isinstance(coc_country, Mapping):
                countries = [
                    " ".join(str(country or "").lower().split())
                    for country in (coc_country.get("countries") or [])
                    if str(country or "").strip()
                ]
                if countries:
                    applied_constraints.append(
                        _make_applied_constraint(
                            "coc_country_match",
                            {"type": "coc_country_match", "countries": countries, "operator": "contains_any"},
                            source_text=_first_string(coc_country.get("display_value"), prompt_text) or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend(countries)

    if not any(constraint.get("id") == "recent_contract_vessel_experience" for constraint in applied_constraints):
        shadow_recent = _extract_shadow_recent_contract_vessel_experience(prompt_text)
        if isinstance(shadow_recent, Mapping):
            applied_constraints.append(
                _make_applied_constraint(
                    "recent_contract_vessel_experience",
                    {
                        "type": "recent_contract_vessel_experience",
                        "ship_family": shadow_recent.get("ship_family"),
                        "minimum_months": _as_positive_int(shadow_recent.get("minimum_months")),
                        "recent_contract_count": _as_positive_int(shadow_recent.get("recent_contract_count")) or 1,
                    },
                    source_text=prompt_text or raw_prompt,
                    confidence="high",
                )
            )
            semantic_fragments.extend(
                [
                    str(shadow_recent.get("ship_family") or ""),
                    f"{shadow_recent.get('minimum_months')} months" if shadow_recent.get("minimum_months") else "",
                    f"last {shadow_recent.get('recent_contract_count')} contracts" if shadow_recent.get("recent_contract_count") else "",
                ]
            )

    if not any(constraint.get("id") == "vessel_tonnage" for constraint in applied_constraints):
        extract_vessel_tonnage_constraint = getattr(analyzer, "_extract_vessel_tonnage_constraint", None)
        if callable(extract_vessel_tonnage_constraint):
            try:
                vessel_tonnage = extract_vessel_tonnage_constraint(prompt_text)
            except Exception:
                vessel_tonnage = None
            if isinstance(vessel_tonnage, Mapping):
                applied_constraints.append(
                    _make_applied_constraint(
                        "vessel_tonnage",
                        {
                            "type": "vessel_tonnage",
                            "min_value": _as_positive_int(vessel_tonnage.get("min_value")),
                            "max_value": _as_positive_int(vessel_tonnage.get("max_value")),
                            "unit": _normalize_vessel_tonnage_unit(vessel_tonnage.get("unit")),
                        },
                        source_text=_first_string(vessel_tonnage.get("display_value"), prompt_text) or prompt_text,
                        confidence="high",
                    )
                )
                semantic_fragments.extend(
                    [
                        str(vessel_tonnage.get("min_value") or ""),
                        str(vessel_tonnage.get("max_value") or ""),
                        str(vessel_tonnage.get("unit") or ""),
                        "vessel tonnage",
                    ]
                )

    if not any(constraint.get("id") == "us_visa" for constraint in applied_constraints):
        shadow_visa = _extract_shadow_us_visa_constraint(analyzer, prompt_text)
        if isinstance(shadow_visa, Mapping):
            required = _first_present(shadow_visa.get("required"), shadow_visa.get("must_be_valid"))
            if not _is_false_value(required):
                visa_group = _first_string(shadow_visa.get("visa_group"))
                accepted_types = _canonical_list(shadow_visa.get("accepted_types") or [])
                if visa_group:
                    accepted_types = _normalize_visa_accepted_types(prompt_text, visa_group, accepted_types, analyzer)
                applied_constraints.append(
                    _make_applied_constraint(
                        "us_visa",
                        {
                            "type": "us_visa",
                            "required": True,
                            "minimum_months_remaining": _as_positive_int(shadow_visa.get("minimum_months_remaining")),
                            "visa_group": visa_group,
                            "accepted_types": accepted_types,
                        },
                        source_text=_first_string(shadow_visa.get("display_value"), prompt_text) or prompt_text,
                        confidence="high",
                    )
                )
                semantic_fragments.extend(
                    ["valid us visa", "us visa", "visa required", "valid visa"]
                    + [str(visa_type).lower() for visa_type in (shadow_visa.get("accepted_types") or []) if isinstance(visa_type, str)]
                )
                shadow_visa = None

    if (
        not any(constraint.get("id") == "us_visa" for constraint in applied_constraints)
        and not _VISA_POLARITY_INVERSION.search(prompt_text)
        and not _visa_negative_status_is_active(prompt_text)
    ):
        extract_us_visa_constraint = getattr(analyzer, "_extract_us_visa_constraint", None)
        if callable(extract_us_visa_constraint):
            try:
                visa = extract_us_visa_constraint(prompt_text)
            except Exception:
                visa = None
            if isinstance(visa, Mapping):
                required = _first_present(visa.get("required"), visa.get("must_be_valid"))
                if not _is_false_value(required):
                    accepted_types = visa.get("accepted_types") or []
                    display_value = _first_string(visa.get("requested_label"), visa.get("display_value"), prompt_text)
                    months_remaining = _as_positive_int(visa.get("minimum_months_remaining") or visa.get("months_remaining"))
                    visa_group = _first_string(visa.get("visa_group"))
                    if not accepted_types and visa_group:
                        accepted_types = _extract_visa_accepted_types(prompt_text, visa_group, analyzer)
                    applied_constraints.append(
                        _make_applied_constraint(
                            "us_visa",
                            {
                                "type": "us_visa",
                                "required": True,
                                "minimum_months_remaining": months_remaining,
                                "visa_group": visa_group.lower() if isinstance(visa_group, str) else visa_group,
                                "accepted_types": accepted_types or None,
                            },
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend(
                        ["valid us visa", "us visa", "visa required", "valid visa", "visa"]
                        + [str(visa_type).lower() for visa_type in accepted_types if isinstance(visa_type, str)]
                    )

    if not any(constraint.get("id") == "stcw_basic" for constraint in applied_constraints):
        extract_stcw_basic_constraint = getattr(analyzer, "_extract_stcw_basic_constraint", None)
        stcw_basic = None
        if callable(extract_stcw_basic_constraint):
            try:
                stcw_basic = extract_stcw_basic_constraint(prompt_text)
            except Exception:
                stcw_basic = None
        required = True
        if isinstance(stcw_basic, Mapping):
            required = _first_present(stcw_basic.get("required"), stcw_basic.get("must_have"), stcw_basic.get("validity"))
        if not _is_false_value(required) and _stcw_basic_is_anchored(prompt_text or raw_prompt):
            applied_constraints.append(
                _make_applied_constraint(
                    "stcw_basic",
                    {"type": "stcw_basic", "required": True},
                    source_text=_first_string(stcw_basic.get("display_value") if isinstance(stcw_basic, Mapping) else None, prompt_text) or prompt_text,
                    confidence="high",
                )
            )
            semantic_fragments.extend(
                [
                    "stcw basic",
                    "basic stcw",
                    "basic safety training",
                    "bst",
                    "basic training package",
                    "stcw a-vi/1",
                    "a-vi/1",
                    "all four basic stcw",
                    "all four basic modules",
                    "all four basic courses",
                    "all four basic certificates",
                    "four basic certificates",
                    "four-pack",
                    "pssr",
                    "pst",
                    "fpff",
                    "efa",
                    "personal survival",
                    "fire fighting",
                    "fire prevention",
                    "first aid",
                ]
            )

    if not any(constraint.get("id") == "availability" for constraint in applied_constraints):
        extract_availability_constraint = getattr(analyzer, "_extract_availability_constraint", None)
        if callable(extract_availability_constraint):
            try:
                availability = extract_availability_constraint(prompt_text)
            except Exception:
                availability = None
            if isinstance(availability, Mapping):
                value_type = availability.get("value_type")
                display_value = _first_string(availability.get("display_value"), prompt_text)
                if value_type == "status":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {"type": "availability", "status": "available", "available_by": None},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "join immediately", "available immediately"])
                elif value_type == "date":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {
                                "type": "availability",
                                "status": "available_by_date",
                                "available_by": availability.get("available_from_date"),
                            },
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "available from"])
                elif value_type == "relative_phrase":
                    applied_constraints.append(
                        _make_applied_constraint(
                            "availability",
                            {"type": "availability", "status": "available_by_date", "available_by": None},
                            source_text=display_value or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([display_value, "joinable in", "available by date"])

    if not any(constraint.get("id") in {"certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"} for constraint in applied_constraints):
        extract_endorsement_constraint = getattr(analyzer, "_extract_endorsement_constraint", None)
        certificates = _extract_shadow_certificate_values(prompt_text)
        endorsements = _extract_shadow_endorsement_values(prompt_text)
        display_value = prompt_text
        if callable(extract_endorsement_constraint) and not _SUFFICIENCY_ONLY_PATTERN.search(prompt_text):
            try:
                endorsement = extract_endorsement_constraint(prompt_text)
            except Exception:
                endorsement = None
            if isinstance(endorsement, Mapping):
                endorsements = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_endorsement_values()) or endorsements
                certificates = _canonical_list(endorsement.get("endorsements_required") or [], allowed=canonical_certificate_values()) or certificates
                display_value = _first_string(endorsement.get("display_value"), prompt_text)
        if endorsements:
            applied_constraints.append(
                _make_applied_constraint(
                    "stcw_endorsement",
                    {"type": "stcw_endorsement", "endorsements_required": endorsements},
                    source_text=display_value or prompt_text,
                    confidence="high",
                    )
            )
            semantic_fragments.extend([token.replace("_", " ") for token in endorsements] + [f"{token.replace('_', ' ')} endorsement" for token in endorsements])
        if certificates and not any(constraint.get("id") in {"certificate_requirement", "rank_certificate_expectation"} for constraint in applied_constraints):
            applied_constraints.append(
                _make_applied_constraint(
                    "certificate_requirement",
                    {"type": "certificate_requirement", "certificates_required": certificates},
                    source_text=display_value or prompt_text,
                    confidence="high",
                )
            )
            semantic_fragments.extend([token.replace("_", " ") for token in certificates])

    if not any(constraint.get("id") == "coc_grade_match" for constraint in applied_constraints):
        extract_coc_grade_constraint = getattr(analyzer, "_extract_coc_grade_constraint", None)
        if callable(extract_coc_grade_constraint):
            try:
                coc_grade = extract_coc_grade_constraint(prompt_text)
            except Exception:
                coc_grade = None
            if isinstance(coc_grade, Mapping):
                grades = coc_grade.get("required_grades") or []
                grade_value = _first_string(*grades)
                if grade_value:
                    applied_constraints.append(
                        _make_applied_constraint(
                            "coc_grade_match",
                            {"type": "coc_grade_match", "grade": grade_value},
                            source_text=_first_string(coc_grade.get("display_value"), prompt_text) or prompt_text,
                            confidence="high",
                        )
                    )
                    semantic_fragments.extend([grade_value.replace("_", " ")])

    if any(
        constraint.get("id") in {"us_visa", "certificate_requirement", "stcw_endorsement", "rank_certificate_expectation"}
        for constraint in applied_constraints
    ):
        unapplied_constraints = [
            item
            for item in unapplied_constraints
            if item.get("id") != "certificate_requirement" or not _certificate_requirement_consumed_by_repair(item, applied_constraints)
        ]

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
    for fragment in semantic_fragments:
        semantic_query = _strip_phrase(str(semantic_query or ""), fragment)
    semantic_query = _cleanup_semantic_residual(semantic_query)
    if (
        semantic_query
        and (applied_constraints or logical_groups or unapplied_constraints)
        and _normalize_text(semantic_query).lower() == _normalize_text(prompt_text).lower()
    ):
        semantic_query = ""
    if semantic_fragments and semantic_query.lower() in {"valid", "required", "mandatory", "must"}:
        semantic_query = ""

    return {
        "schema_version": "query_plan.v1",
        "normalizer": {
            "name": "llm",
            "model": str(parsed.get("normalizer", {}).get("model") or _resolve_reasoning_model(analyzer)),
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
        "logical_groups": logical_groups,
        "unapplied_constraints": unapplied_constraints,
        "semantic_query": semantic_query,
        "unrecognized_residual": [],
        "warnings": [],
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
    force_enabled: bool = False,
) -> Mapping[str, Any] | None:
    """Call Gemini in shadow mode and normalize the returned JSON plan."""

    if not (is_enabled() or force_enabled):
        return _result(None, {"status": "disabled", "reason": "feature_flag_disabled"})

    prompt_text = str(prompt or "").strip()
    canonical_rank = _normalize_rank_value(analyzer, rank)
    config = getattr(analyzer, "config", None)
    api_key = _resolve_gemini_api_key(analyzer)
    model = _resolve_reasoning_model(analyzer)
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

        candidate_plan = _normalize_with_semantic_repair(
            _synthesize_logical_groups_from_legacy(
                _repair_us_visa_accepted_types(
                    _translate_model_payload(parsed, analyzer=analyzer, raw_prompt=prompt, rank=rank),
                    prompt_text,
                    analyzer,
                ),
                prompt_text,
                rank,
                analyzer,
            ),
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
                    "validation_errors": _diagnostic_validation_errors(candidate_plan),
                },
            )
        candidate_plan["normalizer"]["name"] = "llm"
        candidate_plan["normalizer"]["model"] = str(model)
        candidate_plan["normalizer"]["prompt_template_version"] = SHADOW_LLM_PROMPT_TEMPLATE_VERSION
        candidate_plan["normalizer"]["catalog_version"] = candidate_plan["normalizer"].get("catalog_version") or CATALOG_VERSION
        candidate_plan["normalizer"]["created_at"] = candidate_plan["normalizer"].get("created_at") or _utc_now_iso()
        applied_constraints = list(candidate_plan.get("applied_constraints") or [])
        unapplied_constraints = list(candidate_plan.get("unapplied_constraints") or [])
        if _rank_match_is_anchored(prompt_text) and not any(constraint.get("id") == "rank_match" for constraint in applied_constraints) and not any(
            constraint.get("id") == "coc_grade_match" for constraint in applied_constraints
        ):
            rank_value = None
            extract_rank_constraint = getattr(analyzer, "_extract_rank_constraint", None)
            if callable(extract_rank_constraint):
                try:
                    inferred_rank = extract_rank_constraint(prompt_text)
                except Exception:
                    inferred_rank = None
                if isinstance(inferred_rank, Mapping):
                    inferred_ranks = inferred_rank.get("applied_rank_normalized") or []
                    rank_value = _first_string(*inferred_ranks, canonical_rank)
            if not rank_value:
                rank_value = _extract_shadow_rank_value(prompt_text)
            if rank_value:
                unapplied_constraints = [item for item in unapplied_constraints if item.get("id") != "rank_match"]
                applied_constraints.append(
                    _make_applied_constraint(
                        "rank_match",
                        {"type": "rank_match", "rank": rank_value},
                        source_text=prompt_text or raw_prompt,
                        confidence="high",
                    )
                )
        coc_grade_values = [
            _first_string(
                (constraint.get("constraint") or {}).get("grade"),
                (constraint.get("constraint") or {}).get("coc_grade"),
                (constraint.get("constraint") or {}).get("required_grade"),
            )
            for constraint in applied_constraints
            if constraint.get("id") == "coc_grade_match"
        ]
        coc_grade_values = [value for value in coc_grade_values if value]
        if coc_grade_values:
            grade_phrases = {value.replace("_", " ").lower() for value in coc_grade_values}
            applied_constraints = [
                constraint
                for constraint in applied_constraints
                if not (
                    constraint.get("id") == "rank_match"
                    and any(phrase and phrase in str(constraint.get("source_text") or "").lower() for phrase in grade_phrases)
                )
            ]
        candidate_plan["applied_constraints"] = applied_constraints
        candidate_plan["unapplied_constraints"] = unapplied_constraints
        candidate_plan = _normalize_with_semantic_repair(
            _repair_us_visa_accepted_types(candidate_plan, prompt_text, analyzer),
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
    except ShadowLLMTranslationError as exc:
        return _result(
            legacy_plan,
            {
                "status": "fallback",
                "reason": "schema_invalid",
                "model": model,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
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
