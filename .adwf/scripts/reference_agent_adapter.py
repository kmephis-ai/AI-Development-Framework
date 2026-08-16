#!/usr/bin/env python3
"""Deterministic offline reference adapter for Creative Agent qualification only."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK_ROOT / ".adwf"))
from lib.ai_work_contracts import build_work_result, path_is_allowed, validate_work_package  # noqa: E402
from lib.strict_json import loads as strict_loads  # noqa: E402

SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")


def fail(code: str) -> int:
    print(code, file=sys.stderr)
    return 2


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError("GIT_FAILED:" + " ".join(args))
    return proc.stdout.strip()


def main() -> int:
    for name in os.environ:
        if name == "ADWF_AGENT_SECRETS_AUTHORITY":
            continue
        if any(marker in name.upper() for marker in SECRET_MARKERS):
            return fail("REFERENCE_AGENT_SECRET_ENVIRONMENT_LEAK")
    request_path = Path(os.environ.get("ADWF_ACTION_REQUEST", "")).resolve()
    result_path = Path(os.environ.get("ADWF_ACTION_RESULT", "")).resolve()
    if not request_path.is_file() or not str(result_path):
        return fail("REFERENCE_AGENT_CHANNEL_MISSING")
    try:
        request = strict_loads(request_path.read_text(encoding="utf-8"))
    except Exception:
        return fail("REFERENCE_AGENT_REQUEST_INVALID")
    if not isinstance(request, dict):
        return fail("REFERENCE_AGENT_REQUEST_NOT_OBJECT")
    package = request.get("work_package")
    if not isinstance(package, dict) or request.get("work_package_digest") != package.get("package_digest"):
        return fail("REFERENCE_AGENT_PACKAGE_BINDING_INVALID")
    errors = validate_work_package(package)
    if errors:
        return fail("REFERENCE_AGENT_PACKAGE_INVALID")
    if os.environ.get("ADWF_RUN_ID") != package.get("run_id") or os.environ.get("ADWF_PHASE") != package.get("phase"):
        return fail("REFERENCE_AGENT_ENV_BINDING_INVALID")
    if os.environ.get("ADWF_AGENT_NETWORK_AUTHORITY") != "NONE" or os.environ.get("ADWF_AGENT_SECRETS_AUTHORITY") != "FORBIDDEN":
        return fail("REFERENCE_AGENT_AUTHORITY_INVALID")
    try:
        current = git("rev-parse", "HEAD")
    except RuntimeError:
        return fail("REFERENCE_AGENT_GIT_REQUIRED")
    if current != package.get("base_sha"):
        return fail("REFERENCE_AGENT_STALE_BASE")
    target = "src/agentqual-output.txt"
    if not path_is_allowed(target, package):
        return fail("REFERENCE_AGENT_WRITE_SURFACE_FORBIDDEN")
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        "qualified-reference-adapter\n"
        + "package_id=" + str(package.get("package_id")) + "\n"
        + "package_digest=" + str(package.get("package_digest")) + "\n",
        encoding="utf-8",
    )
    try:
        git("add", target)
        subprocess.run(
            ["git", "-c", "user.name=ADWF Reference Agent", "-c", "user.email=adwf-reference@invalid", "commit", "-q", "-m", "reference agent qualified change"],
            check=True,
        )
        head = git("rev-parse", "HEAD")
        changed = sorted(line for line in git("diff", "--name-only", str(package["base_sha"]) + ".." + head).splitlines() if line)
    except (RuntimeError, subprocess.CalledProcessError):
        return fail("REFERENCE_AGENT_COMMIT_FAILED")
    try:
        result = build_work_result(
            package,
            outcome="PASS",
            head_sha=head,
            changed_paths=changed,
            verification_claims=["deterministic reference adapter local contract exercised"],
            evidence_claims=["changed_paths", "verification_claims"],
            summary_ru="Детерминированный reference adapter подготовил package-scoped изменение.",
            created_at="2026-08-16T00:00:10Z",
        )
    except ValueError:
        return fail("REFERENCE_AGENT_RESULT_BUILD_FAILED")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
