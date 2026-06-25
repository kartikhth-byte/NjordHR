"""In-memory present-rank index built from persisted candidate facts rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from typing import Any, Dict, Iterable, Mapping


@dataclass(frozen=True)
class PresentRankIndexEntry:
    candidate_resume_id: str
    candidate_id: str
    resume_blob_id: str
    file_name: str
    resume_path: str
    applied_rank: str
    present_rank: str
    candidate_facts_hash: str
    candidate_resume_facts_id: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_current_row(row: Mapping[str, Any]) -> bool:
    return bool(row.get("is_current_for_resume"))


def _row_facts(row: Mapping[str, Any]) -> Mapping[str, Any]:
    facts = row.get("facts_json")
    if isinstance(facts, Mapping):
        return facts
    facts = row.get("candidate_facts")
    if isinstance(facts, Mapping):
        return facts
    return {}


def _present_rank_from_facts(facts: Mapping[str, Any]) -> str:
    role = facts.get("role") if isinstance(facts.get("role"), Mapping) else {}
    for key in ("current_rank_normalized", "present_rank_normalized"):
        value = str(role.get(key) or "").strip()
        if value:
            return value
    current_rank = facts.get("current_rank") if isinstance(facts.get("current_rank"), Mapping) else {}
    return str(current_rank.get("value") or current_rank.get("canonical_id") or "").strip()


def _applied_rank_from_facts(facts: Mapping[str, Any]) -> str:
    role = facts.get("role") if isinstance(facts.get("role"), Mapping) else {}
    value = str(role.get("applied_rank_normalized") or "").strip()
    if value:
        return value
    rank = facts.get("rank") if isinstance(facts.get("rank"), Mapping) else {}
    return str(rank.get("value") or "").strip()


def _source_file_name(facts: Mapping[str, Any]) -> str:
    source = facts.get("source") if isinstance(facts.get("source"), Mapping) else {}
    return str(source.get("file_name") or facts.get("candidate_id") or "").strip()


def _find_resume_path(base_folder: str | os.PathLike[str] | None, file_name: str, applied_rank: str = "") -> str:
    if not file_name:
        return ""
    base = Path(base_folder or "").expanduser()
    if not base.exists() or not base.is_dir():
        return ""
    candidate_paths: list[Path] = []
    if applied_rank:
        candidate_paths.extend([
            base / applied_rank / file_name,
            base / applied_rank.replace(" ", "_") / file_name,
        ])
    candidate_paths.append(base / file_name)
    for candidate_path in candidate_paths:
        try:
            if candidate_path.is_file():
                return str(candidate_path.resolve())
        except OSError:
            continue
    try:
        for folder in sorted(base.iterdir(), key=lambda item: item.name.lower()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            candidate_path = folder / file_name
            if candidate_path.is_file():
                return str(candidate_path.resolve())
    except OSError:
        return ""
    return ""


def _resume_mtime(resume_path: str) -> float | None:
    if not resume_path:
        return None
    try:
        return os.path.getmtime(resume_path)
    except OSError:
        return None


def _entry_signature(row: Mapping[str, Any], entry: PresentRankIndexEntry) -> tuple[Any, ...]:
    return (
        entry.candidate_resume_id,
        entry.candidate_resume_facts_id,
        entry.candidate_facts_hash,
        str(row.get("updated_at") or ""),
        entry.resume_path,
        _resume_mtime(entry.resume_path),
    )


class PresentRankIndex:
    """Small in-memory index keyed by canonical current/present rank."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._index: Dict[str, list[PresentRankIndexEntry]] = {}
        self._signatures: tuple[tuple[Any, ...], ...] = ()
        self.version = 0
        self.built_at = ""
        self.row_count = 0
        self.indexed_count = 0
        self.unindexed_count = 0

    def rebuild(self, rows: Iterable[Mapping[str, Any]], *, base_folder: str | os.PathLike[str] | None = None):
        entries_by_rank: Dict[str, list[PresentRankIndexEntry]] = {}
        signatures: list[tuple[Any, ...]] = []
        row_count = 0
        indexed_count = 0
        unindexed_count = 0
        for row in rows or []:
            if not isinstance(row, Mapping) or not _is_current_row(row):
                continue
            row_count += 1
            facts = _row_facts(row)
            present_rank = _present_rank_from_facts(facts)
            if not present_rank:
                unindexed_count += 1
                continue
            file_name = _source_file_name(facts)
            applied_rank = _applied_rank_from_facts(facts)
            entry = PresentRankIndexEntry(
                candidate_resume_id=str(row.get("candidate_resume_id") or ""),
                candidate_id=str(row.get("candidate_id") or (facts.get("source") or {}).get("candidate_id") or ""),
                resume_blob_id=str(row.get("resume_blob_id") or ""),
                file_name=file_name,
                resume_path=_find_resume_path(base_folder, file_name, applied_rank),
                applied_rank=applied_rank,
                present_rank=present_rank,
                candidate_facts_hash=str(row.get("candidate_facts_hash") or ""),
                candidate_resume_facts_id=str(row.get("id") or ""),
            )
            entries_by_rank.setdefault(present_rank, []).append(entry)
            signatures.append(_entry_signature(row, entry))
            indexed_count += 1
        for rank_entries in entries_by_rank.values():
            rank_entries.sort(key=lambda item: (item.applied_rank, item.file_name, item.candidate_resume_id))
        signatures_tuple = tuple(sorted(signatures))
        with self._lock:
            self._index = {rank: list(entries) for rank, entries in sorted(entries_by_rank.items())}
            self._signatures = signatures_tuple
            self.row_count = row_count
            self.indexed_count = indexed_count
            self.unindexed_count = unindexed_count
            self.version += 1
            self.built_at = _utc_now_iso()
            return self.snapshot()

    def refresh(self, rows: Iterable[Mapping[str, Any]], *, base_folder: str | os.PathLike[str] | None = None):
        probe = PresentRankIndex()
        probe.rebuild(rows, base_folder=base_folder)
        with self._lock:
            if self.built_at and probe._signatures == self._signatures:
                return self.snapshot()
        return self.rebuild(rows, base_folder=base_folder)

    def lookup(self, present_rank: str) -> list[PresentRankIndexEntry]:
        with self._lock:
            return list(self._index.get(str(present_rank or "").strip(), []))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": self.version,
                "built_at": self.built_at,
                "row_count": self.row_count,
                "indexed_count": self.indexed_count,
                "unindexed_count": self.unindexed_count,
                "rank_counts": {rank: len(entries) for rank, entries in self._index.items()},
            }
