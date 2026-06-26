#!/usr/bin/env python3
"""Rebuild the in-memory present-rank index and print its snapshot."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import argparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the NjordHR present-rank index.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args()

    config_path = backend_server.os.environ.get("NJORDHR_CONFIG_PATH", "")
    active_root = backend_server._active_download_root()
    print(f"Config: {config_path or '(default runtime config)'}", file=sys.stderr)
    print(f"Active corpus root: {active_root}", file=sys.stderr)
    if not args.yes and not sys.stdin.isatty():
        print("Refusing non-interactive rebuild without --yes.", file=sys.stderr)
        return 1
    if not args.yes:
        answer = input("Rebuild the present-rank index for this corpus? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted.", file=sys.stderr)
            return 1
    snapshot = backend_server._rebuild_present_rank_index()
    print(json.dumps(snapshot, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
