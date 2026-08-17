#!/usr/bin/env python3
"""Explicit UPGRADE-002 apply/recover/rollback CLI."""
from __future__ import annotations
from pathlib import Path
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_installation import ConsumerInstallationError, rebind_snapshot_for_fresh_session  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError  # noqa: E402
from lib.consumer_upgrade_transaction import apply_upgrade, recover_upgrade, rollback_upgrade  # noqa: E402
from lib.strict_json import load as strict_load  # noqa: E402


def _obj(path: str, label: str) -> dict:
    value = strict_load(Path(path))
    if not isinstance(value, dict): raise ConsumerUpgradeError(label + "_OBJECT_REQUIRED")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="ADWF transactional consumer framework upgrade")
    sub = parser.add_subparsers(dest="operation", required=True)
    apply_p = sub.add_parser("apply")
    for name in ("source-root", "target-root", "consumer-root", "compatibility", "plan"):
        apply_p.add_argument("--" + name, required=True)
    apply_p.add_argument("--source-snapshot")
    for op in ("recover", "rollback"):
        p = sub.add_parser(op)
        for name in ("source-root", "target-root", "consumer-root", "transaction-id"):
            p.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "apply":
            result = apply_upgrade(
                args.source_root, args.target_root, args.consumer_root,
                _obj(args.compatibility, "COMPATIBILITY"), _obj(args.plan, "PLAN"),
                _obj(args.source_snapshot, "SOURCE_SNAPSHOT") if args.source_snapshot else rebind_snapshot_for_fresh_session(args.consumer_root, args.source_root),
            )
        elif args.operation == "recover":
            result = recover_upgrade(args.source_root, args.target_root, args.consumer_root, args.transaction_id)
        else:
            result = rollback_upgrade(args.source_root, args.target_root, args.consumer_root, args.transaction_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"COMMITTED", "ALREADY_COMMITTED", "ROLLED_BACK"} else 2
    except (ConsumerInstallationError, ConsumerUpgradeError) as exc:
        print(json.dumps({"status": "BLOCK", "reason": str(exc), "write_performed": False}, ensure_ascii=False, sort_keys=True))
        return 2

if __name__ == "__main__": raise SystemExit(main())
