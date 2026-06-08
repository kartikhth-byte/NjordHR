#!/usr/bin/env python3
"""Shared helpers for building NjordHR release bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

EXCLUDED_FILENAMES = {"checksums.txt", "manifest.json", "INSTALL.md"}


def collect_artifacts(release_dir: str | Path, excluded_names: Iterable[str] = EXCLUDED_FILENAMES) -> list[Path]:
    release_path = Path(release_dir)
    excluded = set(excluded_names)
    artifacts = [
        path
        for path in sorted(release_path.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.name not in excluded and not path.name.endswith(".sig")
    ]
    return artifacts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(version: str, release_dir: str | Path, artifacts: Iterable[Path]) -> dict:
    release_path = Path(release_dir)
    artifact_entries = []
    for path in artifacts:
        signature = ""
        signature_path = path.with_suffix(path.suffix + ".sig")
        if signature_path.is_file():
            try:
                signature = signature_path.read_text(encoding="utf-8").strip()
            except Exception:
                signature = ""
        artifact_entries.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "signature": signature,
            }
        )

    return {
        "version": version,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifact_count": len(artifact_entries),
        "artifacts": artifact_entries,
        "release_dir": str(release_path),
    }


def write_checksums(release_dir: str | Path, artifacts: Iterable[Path]) -> Path:
    release_path = Path(release_dir)
    checksums_path = release_path / "checksums.txt"
    lines = []
    for artifact in artifacts:
        lines.append(f"{_sha256_file(artifact)}  {artifact.name}")
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return checksums_path


def write_manifest(release_dir: str | Path, manifest: dict) -> Path:
    release_path = Path(release_dir)
    manifest_path = release_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def write_release_metadata(version: str, release_dir: str | Path) -> dict:
    artifacts = collect_artifacts(release_dir)
    if not artifacts:
        raise ValueError(f"No artifacts found in release dir: {release_dir}")
    write_checksums(release_dir, artifacts)
    manifest = build_manifest(version, release_dir, artifacts)
    write_manifest(release_dir, manifest)
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write NjordHR release bundle metadata.")
    parser.add_argument("--release-dir", required=True, help="Release directory to process")
    parser.add_argument("--version", required=True, help="Release version string")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = write_release_metadata(args.version, args.release_dir)
    print(json.dumps({"release_dir": args.release_dir, "artifact_count": manifest["artifact_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
