#!/usr/bin/env python3
"""Emit a read-only Consumer Framework Upgrade compatibility/plan bundle as JSON."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.consumer_installation import ConsumerInstallationError, rebind_snapshot_for_fresh_session  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError, build_upgrade_compatibility, plan_consumer_upgrade  # noqa: E402
from lib.strict_json import load as strict_load  # noqa: E402


def _object(path: str, label: str) -> dict:
    value = strict_load(Path(path))
    if not isinstance(value, dict):
        raise ValueError(label + "_OBJECT_REQUIRED")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="ADWF UPGRADE-001 read-only compatibility planner")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--consumer-root", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--snapshot")
    parser.add_argument("--skill-bindings")
    args = parser.parse_args()

    try:
        snapshot = _object(args.snapshot, "SNAPSHOT") if args.snapshot else rebind_snapshot_for_fresh_session(args.consumer_root, args.source_root)
        bindings = _object(args.skill_bindings, "SKILL_BINDINGS") if args.skill_bindings else None
        compatibility = build_upgrade_compatibility(
            args.source_root,
            args.target_root,
            args.consumer_root,
            source_revision=args.source_revision,
            target_revision=args.target_revision,
            snapshot=snapshot,
            skill_bindings=bindings,
        )
        plan = plan_consumer_upgrade(
            args.source_root,
            args.target_root,
            args.consumer_root,
            source_revision=args.source_revision,
            target_revision=args.target_revision,
            snapshot=snapshot,
            skill_bindings=bindings,
        )
    except (ConsumerInstallationError, ConsumerUpgradeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "BLOCK",
            "reason": str(exc).split("\n", 1)[0],
            "write_performed": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps({"compatibility": compatibility, "plan": plan}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if plan["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
