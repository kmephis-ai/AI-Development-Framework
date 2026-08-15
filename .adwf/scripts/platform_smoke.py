#!/usr/bin/env python3
"""Cross-platform functional smoke: start localhost portal, wait for TCP, GET /, stop."""
from __future__ import annotations

from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 18765
TCP_READY_TIMEOUT_SECONDS = 10.0
PAGE_FETCH_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.1
REQUIRED_PORTAL_CONTENT = ["ADWF v1.6 Executive Portal", "ПРОДОЛЖИТЬ", "Дорожная карта"]


def _wait_for_tcp(proc: subprocess.Popen[str], host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "NOT_ATTEMPTED"
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            return False, f"PROCESS_EXITED:{rc}"
        remaining = max(0.05, deadline - time.monotonic())
        try:
            with socket.create_connection((host, port), timeout=min(0.5, remaining)):
                return True, "TCP_READY"
        except OSError as exc:
            last_error = type(exc).__name__
            time.sleep(POLL_INTERVAL_SECONDS)
    return False, f"TCP_NOT_READY:{last_error}"


def _fetch_page(url: str, timeout_seconds: float = PAGE_FETCH_TIMEOUT_SECONDS) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        return int(response.status), response.read().decode("utf-8")


def _stop_and_collect(proc: subprocess.Popen[str]) -> tuple[str, str]:
    if proc.poll() is None:
        proc.terminate()
        try:
            return proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.communicate(timeout=5)


def _print_failure(reason: str, stdout: str, stderr: str) -> None:
    print(f"PLATFORM_SMOKE_FAIL:{reason}")
    if stdout.strip():
        print("PLATFORM_SMOKE_PROCESS_STDOUT_BEGIN")
        print(stdout[-8000:])
    if stderr.strip():
        print("PLATFORM_SMOKE_PROCESS_STDERR_BEGIN")
        print(stderr[-8000:])


def main() -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        help_run = subprocess.run(
            [sys.executable, str(ROOT / ".adwf/adwf.py"), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("PLATFORM_SMOKE_FAIL:CLI_HELP_TIMEOUT")
        return 1
    if help_run.returncode != 0:
        _print_failure(f"CLI_HELP_EXIT:{help_run.returncode}", help_run.stdout, help_run.stderr)
        return 1

    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / ".adwf/adwf.py"),
            "dashboard",
            "serve",
            "--bind",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    reason = ""
    body = ""
    try:
        ready, readiness_reason = _wait_for_tcp(proc, HOST, PORT, TCP_READY_TIMEOUT_SECONDS)
        if not ready:
            reason = readiness_reason
        else:
            try:
                status, body = _fetch_page(
                    f"http://{HOST}:{PORT}/",
                    timeout_seconds=PAGE_FETCH_TIMEOUT_SECONDS,
                )
                if status != 200:
                    reason = f"HTTP_STATUS:{status}"
            except Exception as exc:
                reason = f"PAGE_FETCH:{type(exc).__name__}:{exc}"
    finally:
        stdout, stderr = _stop_and_collect(proc)

    if reason:
        _print_failure(reason, stdout, stderr)
        return 1

    missing = [item for item in REQUIRED_PORTAL_CONTENT if item not in body]
    if missing:
        _print_failure("PORTAL_CONTENT_MISSING:" + ",".join(missing), stdout, stderr)
        return 1

    print("PLATFORM SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
