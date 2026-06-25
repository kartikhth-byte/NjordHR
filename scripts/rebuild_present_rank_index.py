#!/usr/bin/env python3
"""Rebuild the in-memory present-rank index and print its snapshot."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend_server  # noqa: E402


def main() -> int:
    snapshot = backend_server._rebuild_present_rank_index()
    print(json.dumps(snapshot, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
