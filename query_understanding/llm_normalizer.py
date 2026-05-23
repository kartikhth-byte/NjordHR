"""Disabled LLM normalizer stub.

This module exists so the shadow pipeline can be wired and tested without
changing production search behavior. It intentionally does not call any model.
"""

from __future__ import annotations

from typing import Any, Mapping

SHADOW_LLM_NORMALIZER_ENABLED = False


def is_enabled() -> bool:
    return SHADOW_LLM_NORMALIZER_ENABLED


def normalize_prompt_to_query_plan_v1(*_args: Any, **_kwargs: Any) -> Mapping[str, Any] | None:
    """Return no plan while the LLM normalizer remains disabled."""

    return None
