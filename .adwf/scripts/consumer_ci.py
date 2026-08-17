#!/usr/bin/env python3
"""Resolve CI mode and, for installed consumers, read exact native gate evidence."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_ci import ConsumerCIRouteError, resolve_route, wait_for_native_phase  # noqa: E402
from lib.github_provider import GitHubClient  # noqa: E402


def _write_output(path: str | None, key: str, value: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{key}={value}\n")


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    route = sub.add_parser("route")
    route.add_argument("--phase", choices=["pr", "main"], required=True)
    route.add_argument("--subject-sha", required=True)
    route.add_argument("--anchor-sha")
    route.add_argument("--repository", required=True)
    route.add_argument("--github-output")
    delegate = sub.add_parser("delegate")
    delegate.add_argument("--phase", choices=["pr", "main"], required=True)
    delegate.add_argument("--subject-sha", required=True)
    delegate.add_argument("--repository", required=True)
    delegate.add_argument("--attempts", type=int, default=30)
    delegate.add_argument("--interval-seconds", type=int, default=10)
    args = p.parse_args()
    try:
        if args.command == "route":
            result = resolve_route(ROOT, ROOT, phase=args.phase, subject_sha=args.subject_sha, anchor_sha=args.anchor_sha, expected_repository=args.repository)
            _write_output(args.github_output, "mode", result["mode"])
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ConsumerCIRouteError("CONSUMER_CI_GITHUB_TOKEN_REQUIRED")
        client = GitHubClient(args.repository, token)
        result = wait_for_native_phase(
            ROOT, ROOT, client,
            phase=args.phase,
            subject_sha=args.subject_sha,
            attempts=args.attempts,
            interval_seconds=args.interval_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ConsumerCIRouteError, OSError, ValueError) as exc:
        print("CONSUMER CI ROUTING: BLOCK", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
