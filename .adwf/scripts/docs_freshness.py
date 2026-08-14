#!/usr/bin/env python3
"""Проверить или обновить source digests документации."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.docs_freshness import check_docs, updated_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--reviewed-at", default="2026-08-13T00:00:00Z")
    parser.add_argument("--valid-until", default="2026-11-13T00:00:00Z")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    path = root / ".adwf/docs-registry.json"
    if args.write:
        try:
            value = updated_registry(root, args.reviewed_at, args.valid_until)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            print(f"DOCS FRESHNESS: WRITE BLOCKED:{type(exc).__name__}")
            return 1
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("DOCS FRESHNESS: WRITTEN")
        return 0
    errors = check_docs(root)
    if errors:
        print("DOCS FRESHNESS: STALE")
        for error in errors:
            print(f"- {error}")
        return 1
    print("DOCS FRESHNESS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
