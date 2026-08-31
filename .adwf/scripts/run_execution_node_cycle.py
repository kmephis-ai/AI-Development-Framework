#!/usr/bin/env python3
"""Run one bounded provider-reconciled execution-node cycle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.execution_node import run_execution_node_cycle  # noqa: E402


def _provider_context(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("PROVIDER_CONTEXT_OBJECT_REQUIRED")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-context", required=True, type=Path)
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()
    result = run_execution_node_cycle(
        ROOT,
        _provider_context(args.provider_context),
        max_steps=args.max_steps,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
