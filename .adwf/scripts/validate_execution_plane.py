#!/usr/bin/env python3
"""Validate the canonical GitHub-hosted CI / private-runtime-only boundary."""
from __future__ import annotations

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.execution_plane import validate_execution_plane  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    errors = validate_execution_plane(Path(args.root).resolve())
    if errors:
        print("EXECUTION PLANE: FAIL")
        for error in errors:
            print("- " + error)
        return 1
    print("EXECUTION PLANE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
