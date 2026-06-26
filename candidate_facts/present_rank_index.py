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


@dataclass(frozen=True)
class _ResumeFileRef:
    relative_path: str
    mtime: float | None


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


def _build_resume_file_index(base_folder: str | os.PathLike[str] | None) -> tuple[Path | None, Dict[str, list[_ResumeFileRef]]]:
    base = Path(base_folder or "").expanduser()
    if not base.exists() or not base.is_dir():
        return None, {}
    index: Dict[str, list[_ResumeFileRef]] = {}

    def add_file(path: Path) -> None:
        try:
            resolved = path.resolve()
            relative = str(resolved.relative_to(base.resolve()))
            mtime = os.path.getmtime(resolved)
        except (OSError, ValueError):
            return
        index.setdefault(path.name, []).append(
            _ResumeFileRef(
                relative_path=relative,
                mtime=mtime,
            )
        )

    try:
        for child in sorted(base.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and child.name.lower().endswith(".pdf"):
                add_file(child)
                continue
            if not child.is_dir() or child.name.startswith("."):
                continue
            for pdf_path in sorted(child.glob("*.pdf"), key=lambda item: item.name.lower()):
                if pdf_path.is_file():
                    add_file(pdf_path)
    except OSError:
        return base, index
    for refs in index.values():
        refs.sort(key=lambda ref: ref.relative_path.lower())
    return base, index


def _find_resume_file(
    file_index: Mapping[str, list[_ResumeFileRef]],
    file_name: str,
    applied_rank: str = "",
) -> _ResumeFileRef | None:
    if not file_name:
        return None
    refs = list(file_index.get(file_name) or [])
    if not refs:
        return None
    applied_prefixes = {
        str(applied_rank or "").strip().replace("\\", "/").strip("/"),
        str(applied_rank or "").strip().replace(" ", "_").replace("\\", "/").strip("/"),
    }
    applied_prefixes.discard("")
    for prefix in applied_prefixes:
        for ref in refs:
            if ref.relative_path.replace("\\", "/").startswith(f"{prefix}/"):
                return ref
    return refs[0]


def _row_signature(
    row: Mapping[str, Any],
    *,
    present_rank: str,
    applied_rank: str,
    file_name: str,
    resume_ref: _ResumeFileRef | None,
) -> tuple[Any, ...]:
    return (
        str(row.get("candidate_resume_id") or ""),
        str(row.get("id") or ""),
        str(row.get("candidate_facts_hash") or ""),
        str(row.get("updated_at") or ""),
        present_rank,
        applied_rank,
        file_name,
        resume_ref.relative_path if resume_ref else "",
        resume_ref.mtime if resume_ref else None,
    )


def _build_index_state(rows: Iterable[Mapping[str, Any]], *, base_folder: str | os.PathLike[str] | None = None):
    _base, file_index = _build_resume_file_index(base_folder)
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
        file_name = _source_file_name(facts)
        applied_rank = _applied_rank_from_facts(facts)
        resume_ref = _find_resume_file(file_index, file_name, applied_rank)
        signatures.append(
            _row_signature(
                row,
                present_rank=present_rank,
                applied_rank=applied_rank,
                file_name=file_name,
                resume_ref=resume_ref,
            )
        )
        if not present_rank:
            unindexed_count += 1
            continue
        entry = PresentRankIndexEntry(
            candidate_resume_id=str(row.get("candidate_resume_id") or ""),
            candidate_id=str(row.get("candidate_id") or (facts.get("source") or {}).get("candidate_id") or ""),
            resume_blob_id=str(row.get("resume_blob_id") or ""),
            file_name=file_name,
            resume_path=resume_ref.relative_path if resume_ref else "",
            applied_rank=applied_rank,
            present_rank=present_rank,
            candidate_facts_hash=str(row.get("candidate_facts_hash") or ""),
            candidate_resume_facts_id=str(row.get("id") or ""),
        )
        entries_by_rank.setdefault(present_rank, []).append(entry)
        indexed_count += 1
    for rank_entries in entries_by_rank.values():
        rank_entries.sort(key=lambda item: (item.applied_rank, item.file_name, item.candidate_resume_id))
    return {
        "index": {rank: list(entries) for rank, entries in sorted(entries_by_rank.items())},
        "signatures": tuple(sorted(signatures)),
        "row_count": row_count,
        "indexed_count": indexed_count,
        "unindexed_count": unindexed_count,
    }


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
        state = _build_index_state(rows, base_folder=base_folder)
        with self._lock:
            self._index = dict(state["index"])
            self._signatures = state["signatures"]
            self.row_count = state["row_count"]
            self.indexed_count = state["indexed_count"]
            self.unindexed_count = state["unindexed_count"]
            self.version += 1
            self.built_at = _utc_now_iso()
            return self.snapshot()

    def refresh(self, rows: Iterable[Mapping[str, Any]], *, base_folder: str | os.PathLike[str] | None = None):
        state = _build_index_state(rows, base_folder=base_folder)
        with self._lock:
            if self.built_at and state["signatures"] == self._signatures:
                return self.snapshot()
            self._index = dict(state["index"])
            self._signatures = state["signatures"]
            self.row_count = state["row_count"]
            self.indexed_count = state["indexed_count"]
            self.unindexed_count = state["unindexed_count"]
            self.version += 1
            self.built_at = _utc_now_iso()
            return self.snapshot()

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
