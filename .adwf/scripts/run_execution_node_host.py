#!/usr/bin/env python3
"""Run the persistent private ADWF Execution Node host loop."""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.execution_node_host import LinuxProcessLock, read_private_token, run_host_loop  # noqa: E402
from scripts.run_active_supervisor import run_active_supervisor  # noqa: E402

_STOP = False


def _stop(*_args) -> None:
    global _STOP
    _STOP = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--token-file", type=Path, default=None)
    args = parser.parse_args()

    token_file = args.token_file or (Path(os.environ["ADWF_GITHUB_TOKEN_FILE"]) if os.environ.get("ADWF_GITHUB_TOKEN_FILE") else None)
    if token_file is not None:
        os.environ["GITHUB_TOKEN"] = read_private_token(token_file)
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("EXECNODE_HOST_GITHUB_CREDENTIAL_REQUIRED")
    os.environ.setdefault("GITHUB_REPOSITORY", "kmephis-ai/AI-Development-Framework")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    state_dir = ROOT / ".adwf-runtime" / "execution-node-host"
    cycles = run_host_loop(
        ROOT,
        cycle_runner=lambda root: run_active_supervisor(root),
        evidence_path=state_dir / "evidence.jsonl",
        lock_factory=lambda: LinuxProcessLock(state_dir / "host.lock"),
        interval_seconds=args.interval_seconds,
        max_cycles=args.max_cycles,
        should_stop=lambda: _STOP,
    )
    return 0 if cycles >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
