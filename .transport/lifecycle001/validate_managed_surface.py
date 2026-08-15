#!/usr/bin/env python3
"""Validate Managed Surface Contract v1 or emit a read-only adoption/detach plan."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.managed_surface import (  # noqa: E402
    ManagedSurfaceError,
    plan_adoption,
    plan_detach,
    validate_canonical_contract,
)
from lib.strict_json import load as strict_load  # noqa: E402


def _head_sha(root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, timeout=5
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--consumer-root")
    parser.add_argument("--source-revision")
    parser.add_argument("--detach-snapshot")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        contract = validate_canonical_contract(root)
        if args.detach_snapshot:
            if not args.consumer_root:
                raise ManagedSurfaceError("CONSUMER_ROOT_REQUIRED")
            snapshot = strict_load(Path(args.detach_snapshot))
            if not isinstance(snapshot, dict):
                raise ManagedSurfaceError("SNAPSHOT_OBJECT_REQUIRED")
            plan = plan_detach(args.consumer_root, snapshot, framework_root=root)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1 if plan["status"] == "BLOCK" else 0
        if args.consumer_root:
            revision = args.source_revision or _head_sha(root)
            plan = plan_adoption(root, args.consumer_root, source_revision=revision)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1 if plan["status"] == "BLOCK" else 0
        print("MANAGED SURFACE CONTRACT: PASS")
        print(
            f"framework_files={contract['framework_files']} "
            f"shared_guarded={contract['shared_guarded']} "
            f"manifest_sha256={contract['manifest_sha256']}"
        )
        print("WRITE MODE: NOT AVAILABLE IN V1")
        return 0
    except (ManagedSurfaceError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("MANAGED SURFACE CONTRACT: BLOCK")
        print("-", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
