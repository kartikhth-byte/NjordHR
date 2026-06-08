"""Shared stub helper for source-specific candidate-facts extractors."""

from __future__ import annotations

from typing import Any, Dict


def unimplemented_extractor(source_name: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    raise NotImplementedError(f"{source_name} candidate-facts extraction is not implemented yet")
