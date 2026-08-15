from __future__ import annotations

from pathlib import Path
import os
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
        print("DIAG_HELP_STDOUT_BEGIN")
        print(help_run.stdout)
        print("DIAG_HELP_STDERR_BEGIN")
        print(help_run.stderr)
        return 2

    out_path = Path(tempfile.gettempdir()) / "adwf-dashboard-diag-stdout.txt"
    err_path = Path(tempfile.gettempdir()) / "adwf-dashboard-diag-stderr.txt"
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
        next_mark = 5.0
        body = ""
        ready = False
        try:
            while time.monotonic() - dashboard_started < 60.0:
                elapsed = time.monotonic() - dashboard_started
                rc = proc.poll()
                if elapsed >= next_mark:
                    print(f"DIAG_WAIT_SECONDS={elapsed:.3f};PROCESS_RC={rc}", flush=True)
                    next_mark += 5.0
                if rc is not None:
                    print(f"DIAG_PROCESS_EXIT_SECONDS={elapsed:.3f};RC={rc}", flush=True)
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1) as response:
                        body = response.read().decode("utf-8")
                        if response.status == 200:
                            print(f"DIAG_READY_SECONDS={time.monotonic() - dashboard_started:.3f}", flush=True)
                            ready = True
                            break
                except OSError:
                    time.sleep(0.25)
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

    if not ready:
        print("DIAG_RESULT=NOT_READY_WITHIN_60S", flush=True)
        return 3

    required = ["ADWF v1.6 Executive Portal", "ПРОДОЛЖИТЬ", "Дорожная карта"]
    missing = [item for item in required if item not in body]
    print("DIAG_MISSING=" + ",".join(missing), flush=True)
    print("DIAG_RESULT=PASS" if not missing else "DIAG_RESULT=CONTENT_MISSING", flush=True)
    return 0 if not missing else 4


if __name__ == "__main__":
    raise SystemExit(main())
