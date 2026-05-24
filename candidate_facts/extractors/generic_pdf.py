"""Generic PDF candidate-facts extractor stub."""

from __future__ import annotations

from typing import Any, Dict

from ._stub import unimplemented_extractor

SOURCE_NAME = "generic_pdf"


def extract_candidate_facts(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return unimplemented_extractor(SOURCE_NAME, *args, **kwargs)
