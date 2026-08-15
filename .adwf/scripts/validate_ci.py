#!/usr/bin/env python3
"""Static supply-chain checks для GitHub Actions и GitLab CI."""
from __future__ import annotations

from pathlib import Path
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.strict_json import load as strict_json_load
PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")
METERED_AI_SECRETS = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "COHERE_API_KEY", "MISTRAL_API_KEY"}


def validate_github(path: Path, action_lock: dict) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("uses:"):
            value = stripped.split(":", 1)[1].strip()
            if value.startswith("./"):
                continue
            if not PIN.fullmatch(value):
                errors.append(f"{path.name}:{line_no}:ACTION_NOT_PINNED")
                continue
            unannotated = value.split("#", 1)[0].strip()
            action_name, sha = unannotated.split("@", 1)
            locked = action_lock.get("actions", {}).get(action_name)
            if not locked or locked.get("sha") != sha:
                errors.append(f"{path.name}:{line_no}:ACTION_NOT_IN_TRUSTED_LOCK")
            elif locked.get("runtime") != "node24":
                errors.append(f"{path.name}:{line_no}:ACTION_RUNTIME_NOT_NODE24")
        if "${{ inputs." in line and re.search(r"^\s*run:", stripped):
            errors.append(f"{path.name}:{line_no}:INPUT_IN_RUN")
    if "pull_request_target:" in text:
        errors.append(f"{path.name}:PULL_REQUEST_TARGET_FORBIDDEN")
    has_write = re.search(r"^\s+(issues|pull-requests|contents):\s*write\s*$", text, re.MULTILINE) is not None
    if re.search(r"\bpull_request:\s*(?:\n|\r\n)", text) and has_write:
        errors.append(f"{path.name}:PR_WORKFLOW_HAS_WRITE_PERMISSION")
    if has_write and "ref: ${{ github.event.repository.default_branch }}" not in text:
        errors.append(f"{path.name}:WRITE_WORKFLOW_NOT_PINNED_TO_DEFAULT_BRANCH")
    if has_write and "persist-credentials: false" not in text:
        errors.append(f"{path.name}:WRITE_WORKFLOW_PERSISTS_CHECKOUT_CREDENTIALS")
    if re.search(r"^\s*if:.*secrets\.", text, re.MULTILINE):
        errors.append(f"{path.name}:SECRET_IN_IF")
    if path.name == "adwf-pr.yml" and "secrets." in text:
        errors.append(f"{path.name}:UNTRUSTED_PR_SECRET_REFERENCE")
    for secret in sorted(METERED_AI_SECRETS):
        if secret in text:
            errors.append(f"{path.name}:METERED_AI_SECRET_FORBIDDEN:{secret}")
    if re.search(r"^\s*(?:container|services):", text, re.MULTILINE):
        errors.append(f"{path.name}:UNREGISTERED_CONTAINER_PROVIDER")
    if re.search(r"^\s*id-token:\s*write\s*$", text, re.MULTILINE):
        errors.append(f"{path.name}:OIDC_WRITE_NOT_REGISTERED")
    if re.search(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash)\b", text):
        errors.append(f"{path.name}:REMOTE_PIPE_TO_SHELL")
    if "ubuntu-latest" in text or "python-version: '3.x'" in text or 'python-version: "3.x"' in text:
        errors.append(f"{path.name}:FLOATING_RUNTIME")
    runner_declarations = re.findall(r"^\s*runs-on:\s*([^\n#]+)", text, re.MULTILINE)
    if len(runner_declarations) != len(re.findall(r"^\s*runs-on:", text, re.MULTILINE)):
        errors.append(f"{path.name}:RUNNER_DECLARATION_NOT_STATIC")
    for runner in runner_declarations:
        value = runner.strip()
        platform_matrix = path.name == "adwf-platform-smoke.yml" and value == "${{ matrix.os }}" and "os: [ubuntu-24.04, windows-2022]" in text
        if value != "ubuntu-24.04" and not platform_matrix:
            errors.append(f"{path.name}:NONSTANDARD_OR_UNKNOWN_RUNNER:{value}")
        if "self-hosted" in value:
            errors.append(f"{path.name}:SELF_HOSTED_DEFAULT_FORBIDDEN")
    for value in re.findall(r"timeout-minutes:\s*([^\s#]+)", text):
        try:
            if int(value) < 1 or int(value) > 30:
                errors.append(f"{path.name}:TIMEOUT_OUTSIDE_FREE_ONLY_BOUND")
        except ValueError:
            errors.append(f"{path.name}:TIMEOUT_NOT_STATIC")
    if "timeout-minutes:" not in text:
        errors.append(f"{path.name}:TIMEOUT_MISSING")
    if "actions/upload-artifact@" in text:
        if "if: failure()" not in text:
            errors.append(f"{path.name}:ARTIFACT_NOT_FAILURE_ONLY")
        if re.search(r"retention-days:\s*1\s*$", text, re.MULTILINE) is None:
            errors.append(f"{path.name}:ARTIFACT_RETENTION_NOT_ONE_DAY")
    if path.name in {"adwf-pr.yml", "adwf-main.yml"}:
        if "cancel-in-progress: true" not in text:
            errors.append(f"{path.name}:SUPERSEDED_RUN_NOT_CANCELLED")
    if path.name == "adwf-pr.yml":
        if "ref: ${{ github.event.pull_request.head.sha }}" not in text:
            errors.append(f"{path.name}:PR_CHECKOUT_NOT_EXACT_HEAD")
        if not re.search(r"fetch-depth:\s*(?:0|2|[3-9]|[1-9][0-9]+)", text):
            errors.append(f"{path.name}:BASE_DIFF_HISTORY_UNAVAILABLE")
        if '--base-sha "$ADWF_BASE_SHA" --head-sha "$ADWF_HEAD_SHA"' not in text:
            errors.append(f"{path.name}:TRUST_DIFF_SHA_BINDING_MISSING")
    return errors


def validate_gitlab(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if re.search(r"^\s*(remote|template|project):", text, re.MULTILINE):
        errors.append(f"{path}:REMOTE_INCLUDE_FORBIDDEN")
    if "latest" in text:
        errors.append(f"{path}:FLOATING_LATEST")
    if re.search(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash)\b", text):
        errors.append(f"{path}:REMOTE_PIPE_TO_SHELL")
    if re.search(r"^\s*(?:image|services):", text, re.MULTILINE):
        errors.append(f"{path}:UNREGISTERED_CONTAINER_PROVIDER")
    for secret in sorted(METERED_AI_SECRETS):
        if secret in text:
            errors.append(f"{path}:METERED_AI_SECRET_FORBIDDEN:{secret}")
    if "artifacts:" in text:
        if re.search(r"when:\s*on_failure", text) is None:
            errors.append(f"{path}:ARTIFACT_NOT_FAILURE_ONLY")
        if re.search(r"expire_in:\s*(?:1 day|24 hours)", text) is None:
            errors.append(f"{path}:ARTIFACT_RETENTION_NOT_ONE_DAY")
    return errors


def validate_runtime_hygiene(root: Path) -> list[str]:
    errors: list[str] = []
    ignore = root / ".gitignore"
    if not ignore.is_file():
        errors.append("GITIGNORE_MISSING")
    else:
        lines = {line.strip() for line in ignore.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}
        if not ({"/.adwf-runtime/", ".adwf-runtime/", ".adwf-runtime"} & lines):
            errors.append("ADWF_RUNTIME_NOT_IGNORED")
    for name in ("MANIFEST.json", "SHA256SUMS.txt"):
        path = root / name
        if path.is_file() and re.search(r"(?:^|[\s\"/])\.adwf-runtime(?:/|[\"\s]|$)", path.read_text(encoding="utf-8"), re.MULTILINE):
            errors.append(f"{name}:ADWF_RUNTIME_PACKAGED")
    process = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", ".adwf-runtime", ".adwf-runtime/**"],
        capture_output=True, text=True, check=False,
    )
    if process.returncode == 0 and process.stdout.strip():
        errors.append("ADWF_RUNTIME_TRACKED")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors: list[str] = validate_runtime_hygiene(root)
    try:
        action_lock = strict_json_load(root / ".adwf/actions-lock.json")
        verified = datetime.fromisoformat(action_lock["verified_at"].replace("Z", "+00:00"))
        valid_until = datetime.fromisoformat(action_lock["valid_until"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if verified > now or valid_until <= now or verified >= valid_until:
            errors.append("ACTIONS_LOCK_STALE")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        action_lock = {}
        errors.append(f"ACTIONS_LOCK_INVALID:{type(exc).__name__}")
    for path in sorted((root / ".github/workflows").glob("*.yml")):
        errors.extend(validate_github(path, action_lock))
    for path in [root / ".gitlab-ci.yml", *sorted((root / ".adwf/providers/gitlab/ci").glob("*.yml"))]:
        if path.exists():
            errors.extend(validate_gitlab(path))
    root_gitlab = (root / ".gitlab-ci.yml").read_text(encoding="utf-8") if (root / ".gitlab-ci.yml").is_file() else ""
    # v1.6 is GitHub-public-first. The top-level GitLab pipeline is deliberately
    # disabled until a quota-attested secondary pipeline is generated. This
    # removes the old mandatory self-hosted dependency without pretending that
    # GitLab shared compute is an unlimited $0 default.
    if "when: never" not in root_gitlab:
        errors.append(".gitlab-ci.yml:SECONDARY_PIPELINE_NOT_FAIL_CLOSED")
    if re.search(r"^\s*tags:\s*.*self-hosted", root_gitlab, re.MULTILINE | re.IGNORECASE):
        errors.append(".gitlab-ci.yml:SELF_HOSTED_DEFAULT_FORBIDDEN")
    dag_requirements = {
        "test.yml": 'needs: ["adwf:validate"]',
        "control.yml": 'needs: ["adwf:test"]',
        "release.yml": 'needs: ["adwf:test"]',
    }
    for name, required in dag_requirements.items():
        path = root / ".adwf/providers/gitlab/ci" / name
        if path.is_file() and required not in path.read_text(encoding="utf-8"):
            errors.append(f"{path}:DAG_NEEDS_MISSING")
    # The legacy self-hosted GitLab templates are retained only as optional
    # adapters. When used, their trust-domain labels remain mandatory.
    for name in ("control.yml", "release.yml"):
        path = root / ".adwf/providers/gitlab/ci" / name
        if path.is_file() and "adwf-trusted" not in path.read_text(encoding="utf-8"):
            errors.append(f"{path}:TRUSTED_RUNNER_LABEL_MISSING")
    if errors:
        print("CI STATIC SECURITY: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("CI STATIC SECURITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
