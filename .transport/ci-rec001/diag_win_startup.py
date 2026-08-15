from __future__ import annotations

from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PORT = 18765


def main() -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    started = time.monotonic()
    help_run = subprocess.run(
        [sys.executable, str(ROOT / ".adwf/adwf.py"), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(f"DIAG_HELP_SECONDS={time.monotonic() - started:.3f}", flush=True)
    print(f"DIAG_HELP_RC={help_run.returncode}", flush=True)
    if help_run.returncode != 0:
        print("DIAG_HELP_STDOUT_BEGIN", flush=True)
        print(help_run.stdout, flush=True)
        print("DIAG_HELP_STDERR_BEGIN", flush=True)
        print(help_run.stderr, flush=True)
        return 2

    out_path = Path(tempfile.gettempdir()) / "adwf-dashboard-diag-stdout.txt"
    err_path = Path(tempfile.gettempdir()) / "adwf-dashboard-diag-stderr.txt"
    body = ""
    result_code = 0
    with out_path.open("w", encoding="utf-8") as out, err_path.open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / ".adwf/adwf.py"),
                "dashboard",
                "serve",
                "--bind",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=err,
            text=True,
        )
        dashboard_started = time.monotonic()
        tcp_ready = False
        try:
            tcp_deadline = dashboard_started + 15.0
            while time.monotonic() < tcp_deadline:
                if proc.poll() is not None:
                    print(f"DIAG_PROCESS_EXIT_BEFORE_TCP;RC={proc.returncode}", flush=True)
                    result_code = 3
                    break
                try:
                    with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                        tcp_ready = True
                        print(f"DIAG_SOCKET_READY_SECONDS={time.monotonic() - dashboard_started:.3f}", flush=True)
                        break
                except OSError:
                    time.sleep(0.1)
            if not tcp_ready and result_code == 0:
                print("DIAG_SOCKET_NOT_READY_WITHIN_15S", flush=True)
                result_code = 4

            if tcp_ready:
                page_started = time.monotonic()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=30) as response:
                        body = response.read().decode("utf-8")
                        page_seconds = time.monotonic() - page_started
                        print(f"DIAG_PAGE_SECONDS={page_seconds:.3f}", flush=True)
                        print(f"DIAG_HTTP_STATUS={response.status}", flush=True)
                        if response.status != 200:
                            result_code = 5
                except Exception as exc:
                    print(f"DIAG_PAGE_ERROR={type(exc).__name__}:{exc}", flush=True)
                    print(f"DIAG_PAGE_ELAPSED_SECONDS={time.monotonic() - page_started:.3f}", flush=True)
                    result_code = 6
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    stdout_text = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
    stderr_text = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
    print("DIAG_DASHBOARD_STDOUT_BEGIN", flush=True)
    print(stdout_text[-8000:], flush=True)
    print("DIAG_DASHBOARD_STDERR_BEGIN", flush=True)
    print(stderr_text[-8000:], flush=True)

    if result_code:
        print(f"DIAG_RESULT=FAIL:{result_code}", flush=True)
        return result_code

    required = ["ADWF v1.6 Executive Portal", "ПРОДОЛЖИТЬ", "Дорожная карта"]
    missing = [item for item in required if item not in body]
    print("DIAG_MISSING=" + ",".join(missing), flush=True)
    print("DIAG_RESULT=PASS" if not missing else "DIAG_RESULT=CONTENT_MISSING", flush=True)
    return 0 if not missing else 7


if __name__ == "__main__":
    raise SystemExit(main())
