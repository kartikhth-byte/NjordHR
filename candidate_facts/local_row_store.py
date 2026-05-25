"""Portable local storage for candidate resume facts rows."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


ROWS_FILENAME = "candidate_resume_facts_rows.jsonl"


def candidate_resume_facts_rows_store_path(base_dir: str) -> str:
    return str(Path(base_dir).expanduser().resolve() / ROWS_FILENAME)


def load_candidate_resume_facts_rows(base_dir: str) -> List[Dict[str, Any]]:
    path = Path(candidate_resume_facts_rows_store_path(base_dir))
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    rows.append(dict(record))
    except OSError:
        return []
    return rows


def save_candidate_resume_facts_rows(base_dir: str, rows: Iterable[Mapping[str, Any]]) -> None:
    path = Path(candidate_resume_facts_rows_store_path(base_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="candidate_resume_facts_rows_", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(dict(row), sort_keys=True))
                fh.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
