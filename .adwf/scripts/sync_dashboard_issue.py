#!/usr/bin/env python3
"""Обновить ровно один pinned Dashboard Issue без потока комментариев."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.cost_guard import evaluate_provider  # noqa: E402
from lib.dashboard import render_dashboard  # noqa: E402
from lib.health import active_state_path, doctor  # noqa: E402
from lib.github_provider import GitHubClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--apply", action="store_true"); parser.add_argument("--require-configured", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
    project = config.get("github", {}).get("project", {})
    number = project.get("dashboard_issue_number")
    if not project.get("enabled") or not number:
        print("Dashboard Issue не настроен.")
        return 1 if args.require_configured else 0
    state = json.loads(active_state_path(ROOT).read_text(encoding="utf-8"))
    registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))
    capability = state.get("cost_usage", {}).get("capability") or "local_deterministic"
    cost = evaluate_provider(registry, {"provider": capability, "projected_cost": 0}, canonical_provider=config.get("provider", {}).get("mode"))
    body = render_dashboard(state, doctor(ROOT), cost)
    if not args.apply:
        print(f"DRY_RUN dashboard issue #{number}; body bytes={len(body.encode('utf-8'))}")
        return 0
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        raise SystemExit("GITHUB_REPOSITORY/GITHUB_TOKEN missing")
    client = GitHubClient(repo, token)
    client.patch(f"/repos/{repo}/issues/{int(number)}", {"body": body})
    observed = client.get(f"/repos/{repo}/issues/{int(number)}")
    if observed.get("body") != body:
        raise SystemExit("DASHBOARD_READBACK_MISMATCH")
    print(f"Dashboard Issue #{number} обновлён.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
