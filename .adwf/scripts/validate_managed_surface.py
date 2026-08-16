#!/usr/bin/env python3
"""Validate Managed Surface Contract or explicitly run bounded lifecycle transactions."""
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
from lib.managed_surface_transaction import (  # noqa: E402
    apply_adoption,
    apply_detach,
    recover_adoption,
    recover_detach,
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
    parser.add_argument("--apply", action="store_true", help="explicit transactional adoption apply")
    parser.add_argument("--detach-apply", action="store_true", help="explicit guarded transactional detach apply")
    parser.add_argument("--recover-transaction", help="recover/rollback exact adoption transaction id")
    parser.add_argument("--recover-detach-transaction", help="recover/rollback exact detach transaction id")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        contract = validate_canonical_contract(root)
        mutation_modes = sum(
            bool(value)
            for value in (
                args.apply,
                args.detach_apply,
                args.recover_transaction,
                args.recover_detach_transaction,
            )
        )
        if mutation_modes > 1:
            raise ManagedSurfaceError("LIFECYCLE_MODE_CONFLICT")
        if args.detach_apply and not args.detach_snapshot:
            raise ManagedSurfaceError("DETACH_SNAPSHOT_REQUIRED")
        if args.detach_snapshot and (args.apply or args.recover_transaction or args.recover_detach_transaction):
            raise ManagedSurfaceError("LIFECYCLE_MODE_CONFLICT")

        if args.recover_detach_transaction:
            if not args.consumer_root:
                raise ManagedSurfaceError("CONSUMER_ROOT_REQUIRED")
            result = recover_detach(root, args.consumer_root, args.recover_detach_transaction)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] in {"ROLLED_BACK", "COMMITTED"} else 1

        if args.recover_transaction:
            if not args.consumer_root:
                raise ManagedSurfaceError("CONSUMER_ROOT_REQUIRED")
            result = recover_adoption(root, args.consumer_root, args.recover_transaction)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] in {"ROLLED_BACK", "COMMITTED"} else 1

        if args.detach_snapshot:
            if not args.consumer_root:
                raise ManagedSurfaceError("CONSUMER_ROOT_REQUIRED")
            snapshot = strict_load(Path(args.detach_snapshot))
            if not isinstance(snapshot, dict):
                raise ManagedSurfaceError("SNAPSHOT_OBJECT_REQUIRED")
            plan = plan_detach(args.consumer_root, snapshot, framework_root=root)
            if args.detach_apply:
                if plan["status"] != "READY":
                    print(json.dumps(plan, ensure_ascii=False, indent=2))
                    return 1
                result = apply_detach(root, args.consumer_root, snapshot, plan)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["status"] in {"COMMITTED", "ALREADY_COMMITTED"} else 1
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1 if plan["status"] == "BLOCK" else 0

        if args.consumer_root:
            revision = args.source_revision or _head_sha(root)
            plan = plan_adoption(root, args.consumer_root, source_revision=revision)
            if args.apply:
                if plan["status"] != "READY":
                    print(json.dumps(plan, ensure_ascii=False, indent=2))
                    return 1
                result = apply_adoption(root, args.consumer_root, plan)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["status"] in {"COMMITTED", "ALREADY_COMMITTED"} else 1
            if args.detach_apply:
                raise ManagedSurfaceError("DETACH_SNAPSHOT_REQUIRED")
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1 if plan["status"] == "BLOCK" else 0

        if mutation_modes:
            raise ManagedSurfaceError("CONSUMER_ROOT_REQUIRED")
        print("MANAGED SURFACE CONTRACT: PASS")
        print(
            f"framework_files={contract['framework_files']} "
            f"shared_guarded={contract['shared_guarded']} "
            f"manifest_sha256={contract['manifest_sha256']}"
        )
        print("WRITE MODE: EXPLICIT --apply / --detach-apply ONLY; default remains dry-run")
        return 0
    except (ManagedSurfaceError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("MANAGED SURFACE CONTRACT: BLOCK")
        print("-", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
