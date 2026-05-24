"""Disabled LLM normalizer stub.

This module exists so the shadow pipeline can be wired and tested without
changing production search behavior. It intentionally does not call any model.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

SHADOW_LLM_NORMALIZER_ENABLED = False
SHADOW_LLM_NORMALIZER_ENV = "NJORDHR_QUERY_UNDERSTANDING_SHADOW_LLM"


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def is_enabled() -> bool:
    return _parse_bool(os.environ.get(SHADOW_LLM_NORMALIZER_ENV), SHADOW_LLM_NORMALIZER_ENABLED)


def normalize_prompt_to_query_plan_v1(*_args: Any, **_kwargs: Any) -> Mapping[str, Any] | None:
    """Return a shadow query plan only when a provider is attached and the feature is enabled."""

    llm_plan_provider = _kwargs.pop("llm_plan_provider", None)
    return maybe_build_shadow_query_plan(llm_plan_provider, *_args, **_kwargs)


def maybe_build_shadow_query_plan(
    llm_plan_provider: Any | None,
    *args: Any,
    **kwargs: Any,
) -> Mapping[str, Any] | None:
    """Call a supplied shadow provider only when the feature flag is enabled."""

    if not is_enabled() or llm_plan_provider is None:
        return None
    return llm_plan_provider(*args, **kwargs)
