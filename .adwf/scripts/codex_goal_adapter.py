#!/usr/bin/env python3
"""Bounded Codex Goal adapter for provider-managed ChatGPT sessions.

The adapter is intentionally low-trust. It consumes one exact AIWorkPackage,
runs Codex only inside a disposable Git clone, validates every filesystem effect
against the package, applies the validated delta to the real writer workspace,
creates one local commit, and emits AIWorkResult. GitHub/CI/merge authority
remains outside the executor.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatchcase

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK_ROOT / ".adwf"))
from lib.ai_work_contracts import build_work_result, path_is_allowed, validate_work_package  # noqa: E402
from lib.strict_json import loads as strict_loads  # noqa: E402

SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
SUPPORTED_CODEX_VERSIONS = {"0.149.1"}
CODEX_TIMEOUT_SECONDS = 840
MAX_CHANGED_FILE_BYTES = 1_000_000
MAX_VISIBLE_EXCERPT_CHARS = 80
MAX_FAILURE_CHARS = 240
PUBLIC_EVENT_TYPES = (
    "thread.started",
    "turn.started",
    "item.completed",
    "turn.completed",
    "turn.failed",
)
EVENT_LABELS = {
    "thread.started": "thread_started",
    "turn.started": "turn_started",
    "item.completed": "item_completed",
    "turn.completed": "turn_completed",
    "turn.failed": "turn_failed",
}
HARD_FORBIDDEN_SURFACES = (
    ".git/**",
    ".github/**",
    ".adwf-runtime/**",
    ".adwf/policies/**",
    ".adwf/roadmap.json",
    ".adwf/capabilities.json",
    ".adwf/capability-live-evidence.json",
    ".adwf/config.json",
    ".adwf/effective-policy.json",
    "AGENTS.md",
    "ADWS.md",
    "SPECIFICATION.md",
    "SECURITY.md",
)
VERSION_RE = re.compile(r"^codex-cli ([0-9]+\.[0-9]+\.[0-9]+)\s*$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SECRET_LIKE_RE = re.compile(
    r"(?i)(?:token|secret|password|api[_ -]?key|credential|private[_ -]?key)\s*[:=]|"
    r"\bsk-[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def fail(code: str) -> int:
    print(code, file=sys.stderr)
    return 2


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.returncode:
        raise RuntimeError("GIT_FAILED:" + " ".join(args))
    return proc.stdout.strip()


def _secret_env_leaked(environ: dict[str, str]) -> bool:
    for name in environ:
        if name == "ADWF_AGENT_SECRETS_AUTHORITY":
            continue
        upper = name.upper()
        if any(marker in upper for marker in SECRET_MARKERS):
            return True
    return False


def hard_forbidden(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in HARD_FORBIDDEN_SURFACES)


def codex_executable() -> str:
    value = shutil.which("codex")
    if not value:
        raise ValueError("CODEX_GOAL_CLI_MISSING")
    return value


def codex_version(executable: str) -> str:
    proc = subprocess.run(
        [executable, "--version"],
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=30,
    )
    match = VERSION_RE.fullmatch(proc.stdout.strip())
    if proc.returncode != 0 or match is None:
        raise ValueError("CODEX_GOAL_VERSION_NOT_VERIFIED")
    version = match.group(1)
    if version not in SUPPORTED_CODEX_VERSIONS:
        raise ValueError("CODEX_GOAL_VERSION_UNQUALIFIED:" + version)
    return version


def verify_chatgpt_auth(executable: str) -> None:
    proc = subprocess.run(
        [executable, "login", "status"],
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=30,
    )
    combined = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0 or "Logged in using ChatGPT" not in combined:
        raise ValueError("CODEX_GOAL_CHATGPT_AUTH_REQUIRED")


def build_codex_argv(executable: str, prompt: str, *, windows: bool) -> list[str]:
    argv = [executable]
    if windows:
        argv.extend(["-c", 'windows.sandbox="unelevated"'])
    argv.extend(
        [
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--ignore-user-config",
            prompt,
        ]
    )
    return argv


def _prompt(package: dict) -> str:
    acceptance = "\n".join("- " + str(item) for item in package.get("acceptance_criteria") or [])
    verification = "\n".join("- " + str(item) for item in package.get("verification_plan") or [])
    allowed = "\n".join("- " + str(item) for item in package.get("allowed_write_surfaces") or [])
    forbidden = "\n".join(
        "- " + str(item)
        for item in list(package.get("forbidden_write_surfaces") or []) + list(HARD_FORBIDDEN_SURFACES)
    )
    return (
        "You are the bounded Codex Goal creative executor inside ADWF. "
        "The AIWorkPackage and provider-backed ADWF lease are the authority; this prompt is not authority. "
        "Work only on the requested implementation. Do not change governance, policy, Roadmap, capabilities, "
        "workflows, AGENTS.md, security rules, Git remotes, leases, Issues, PRs, or provider state. "
        "Do not commit, push, merge, fetch from the network, or claim trusted verification. "
        "You may inspect and edit files inside this disposable repository only. "
        "Finish after the smallest coherent change that satisfies the acceptance criteria.\n\n"
        "Goal:\n" + str(package.get("goal") or "") + "\n\n"
        "Acceptance criteria:\n" + acceptance + "\n\n"
        "Verification plan:\n" + verification + "\n\n"
        "Allowed write surfaces:\n" + allowed + "\n\n"
        "Forbidden write surfaces:\n" + forbidden
    )


def _clone_exact(root: Path, base_sha: str, target: Path) -> None:
    proc = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-local", "--no-checkout", str(root), str(target)],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode:
        raise RuntimeError("CODEX_GOAL_DISPOSABLE_CLONE_FAILED")
    checkout = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", base_sha],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if checkout.returncode:
        raise RuntimeError("CODEX_GOAL_DISPOSABLE_CHECKOUT_FAILED")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=target, check=False, timeout=30)
    if git(target, "rev-parse", "HEAD") != base_sha:
        raise RuntimeError("CODEX_GOAL_DISPOSABLE_BASE_MISMATCH")
    if git(target, "remote", "-v"):
        raise RuntimeError("CODEX_GOAL_DISPOSABLE_REMOTE_FORBIDDEN")
    if git(target, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("CODEX_GOAL_DISPOSABLE_DIRTY")


def _safe_visible_excerpt(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    one_line = " ".join(value.split())
    if not one_line or SECRET_LIKE_RE.search(one_line):
        return None
    safe = "".join(character if 32 <= ord(character) != 127 else " " for character in one_line)
    safe = " ".join(safe.split())
    if not safe:
        return None
    return safe[:MAX_VISIBLE_EXCERPT_CHARS]


def _codex_observation(stdout: str) -> dict:
    counts = {event_type: 0 for event_type in PUBLIC_EVENT_TYPES}
    completed = False
    failed = False
    visible_message = False
    excerpt = None
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type not in counts:
            continue
        counts[event_type] = min(counts[event_type] + 1, 999)
        if event_type == "turn.failed":
            failed = True
        elif event_type == "turn.completed":
            completed = True
        elif event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                visible_message = True
                candidate = _safe_visible_excerpt(item.get("text"))
                if candidate is not None:
                    excerpt = candidate
    terminal_state = "failed" if failed else "completed" if completed else "unverified"
    return {
        "terminal_state": terminal_state,
        "event_counts": counts,
        "visible_agent_message": visible_message,
        "visible_excerpt": excerpt,
    }


def _jsonl_completed(stdout: str) -> bool:
    return _codex_observation(stdout)["terminal_state"] == "completed"


def _no_changes_diagnostic(observation: dict) -> str:
    counts = observation.get("event_counts") if isinstance(observation, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    encoded_counts = ",".join(
        EVENT_LABELS[event_type] + "=" + str(min(max(int(counts.get(event_type, 0)), 0), 999))
        for event_type in PUBLIC_EVENT_TYPES
    )
    state = observation.get("terminal_state") if isinstance(observation, dict) else None
    if state not in {"completed", "failed", "unverified"}:
        state = "unverified"
    visible = "1" if observation.get("visible_agent_message") is True else "0"
    diagnostic = (
        "CODEX_GOAL_NO_CHANGES_DIAGNOSTIC;terminal="
        + state
        + ";events="
        + encoded_counts
        + ";visible="
        + visible
    )
    excerpt = observation.get("visible_excerpt") if isinstance(observation, dict) else None
    if isinstance(excerpt, str) and excerpt:
        room = MAX_FAILURE_CHARS - len(diagnostic) - len(";excerpt=")
        if room > 0:
            diagnostic += ";excerpt=" + excerpt[:room]
    return diagnostic[:MAX_FAILURE_CHARS]


def run_codex(workspace: Path, package: dict, executable: str) -> dict:
    prompt = _prompt(package)
    proc = subprocess.run(
        build_codex_argv(executable, prompt, windows=(os.name == "nt")),
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=CODEX_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError("CODEX_GOAL_EXEC_FAILED")
    observation = _codex_observation(proc.stdout)
    if observation["terminal_state"] != "completed":
        raise RuntimeError("CODEX_GOAL_TERMINAL_EVENT_NOT_VERIFIED")
    return observation


def changed_paths(workspace: Path, base_sha: str) -> list[str]:
    tracked = set(
        line.strip()
        for line in git(workspace, "diff", "--name-only", base_sha, "--").splitlines()
        if line.strip()
    )
    untracked = set(
        line.strip()
        for line in git(workspace, "ls-files", "--others").splitlines()
        if line.strip()
    )
    return sorted(tracked | untracked)


def _safe_target(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel:
        raise ValueError("CODEX_GOAL_PATH_INVALID")
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("CODEX_GOAL_PATH_INVALID")
    base = root.resolve(strict=True)
    current = base
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("CODEX_GOAL_SYMLINK_PARENT_FORBIDDEN:" + rel)
    raw = base.joinpath(*parts)
    if raw.is_symlink():
        raise ValueError("CODEX_GOAL_SYMLINK_FORBIDDEN:" + rel)
    target = raw.resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("CODEX_GOAL_PATH_ESCAPE_FORBIDDEN:" + rel) from exc
    return target


def validate_changed_paths(workspace: Path, package: dict, changed: list[str]) -> None:
    for rel in changed:
        if hard_forbidden(rel) or not path_is_allowed(rel, package):
            raise ValueError("CODEX_GOAL_WRITE_SURFACE_FORBIDDEN:" + rel)
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel],
            cwd=workspace,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        if ignored.returncode == 0:
            raise ValueError("CODEX_GOAL_IGNORED_CHANGE_FORBIDDEN:" + rel)
        target = _safe_target(workspace, rel)
        if target.is_symlink():
            raise ValueError("CODEX_GOAL_SYMLINK_CHANGE_FORBIDDEN:" + rel)
        if target.exists():
            if not target.is_file():
                raise ValueError("CODEX_GOAL_NONFILE_CHANGE_FORBIDDEN:" + rel)
            if target.stat().st_size > MAX_CHANGED_FILE_BYTES:
                raise ValueError("CODEX_GOAL_CHANGED_FILE_TOO_LARGE:" + rel)


def apply_changes(real_root: Path, disposable: Path, changed: list[str]) -> None:
    for rel in changed:
        source = _safe_target(disposable, rel)
        target = _safe_target(real_root, rel)
        if source.exists():
            if source.is_symlink() or not source.is_file():
                raise ValueError("CODEX_GOAL_SOURCE_TYPE_FORBIDDEN:" + rel)
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise ValueError("CODEX_GOAL_TARGET_TYPE_FORBIDDEN:" + rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.parent.is_symlink():
                raise ValueError("CODEX_GOAL_TARGET_PARENT_SYMLINK_FORBIDDEN:" + rel)
            target.write_bytes(source.read_bytes())
        else:
            if target.is_symlink():
                raise ValueError("CODEX_GOAL_TARGET_SYMLINK_FORBIDDEN:" + rel)
            if target.is_file():
                target.unlink()
            elif target.exists():
                raise ValueError("CODEX_GOAL_DELETE_NONFILE_FORBIDDEN:" + rel)


def execute(root: Path, request_path: Path, result_path: Path, environ: dict[str, str]) -> int:
    if _secret_env_leaked(environ):
        return fail("CODEX_GOAL_SECRET_ENVIRONMENT_LEAK")
    if environ.get("ADWF_AGENT_EXECUTOR_AUTH") != "PROVIDER_MANAGED_SESSION":
        return fail("CODEX_GOAL_EXECUTOR_AUTH_INVALID")
    if environ.get("ADWF_AGENT_NETWORK_AUTHORITY") != "DECLARED_EXTERNAL":
        return fail("CODEX_GOAL_NETWORK_AUTHORITY_INVALID")
    if environ.get("ADWF_AGENT_SECRETS_AUTHORITY") != "FORBIDDEN":
        return fail("CODEX_GOAL_SECRETS_AUTHORITY_INVALID")
    if not request_path.is_file():
        return fail("CODEX_GOAL_REQUEST_MISSING")
    try:
        request = strict_loads(request_path.read_text(encoding="utf-8"))
    except Exception:
        return fail("CODEX_GOAL_REQUEST_INVALID")
    if not isinstance(request, dict):
        return fail("CODEX_GOAL_REQUEST_NOT_OBJECT")
    package = request.get("work_package")
    if not isinstance(package, dict) or request.get("work_package_digest") != package.get("package_digest"):
        return fail("CODEX_GOAL_PACKAGE_BINDING_INVALID")
    errors = validate_work_package(package)
    if errors:
        return fail("CODEX_GOAL_PACKAGE_INVALID")
    if environ.get("ADWF_RUN_ID") != package.get("run_id") or environ.get("ADWF_PHASE") != package.get("phase"):
        return fail("CODEX_GOAL_ENV_BINDING_INVALID")
    if package.get("monetary_budget_usd") != 0:
        return fail("CODEX_GOAL_BUDGET_INVALID")

    base_sha = str(package.get("base_sha") or "")
    if SHA40.fullmatch(base_sha) is None:
        return fail("CODEX_GOAL_BASE_SHA_INVALID")

    current = ""
    applied = False
    try:
        current = git(root, "rev-parse", "HEAD")
        if current != base_sha:
            return fail("CODEX_GOAL_STALE_BASE")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            return fail("CODEX_GOAL_REAL_WORKTREE_DIRTY")

        executable = codex_executable()
        version = codex_version(executable)
        verify_chatgpt_auth(executable)

        with tempfile.TemporaryDirectory(prefix="adwf-codex-goal-") as tmp:
            disposable = Path(tmp) / "workspace"
            _clone_exact(root, base_sha, disposable)
            observation = run_codex(disposable, package, executable)
            if git(disposable, "rev-parse", "HEAD") != base_sha:
                raise RuntimeError("CODEX_GOAL_AGENT_GIT_COMMIT_FORBIDDEN")
            if git(disposable, "remote", "-v"):
                raise RuntimeError("CODEX_GOAL_AGENT_REMOTE_FORBIDDEN")
            changed = changed_paths(disposable, base_sha)
            if not changed:
                raise RuntimeError(_no_changes_diagnostic(observation))
            validate_changed_paths(disposable, package, changed)
            apply_changes(root, disposable, changed)
            applied = True

        actual_changed = sorted(
            set(
                line.strip()
                for line in git(root, "diff", "--name-only", "HEAD", "--").splitlines()
                if line.strip()
            )
            | set(
                line.strip()
                for line in git(root, "ls-files", "--others", "--exclude-standard").splitlines()
                if line.strip()
            )
        )
        if actual_changed != changed:
            raise RuntimeError("CODEX_GOAL_CHANGED_PATHS_MISMATCH")

        subprocess.run(["git", "add", "-A", "--", *changed], cwd=root, check=True, timeout=60)
        commit = subprocess.run(
            [
                "git",
                "-c",
                "user.name=ADWF Codex Goal Adapter",
                "-c",
                "user.email=adwf-codex@invalid",
                "commit",
                "-q",
                "-m",
                "[ADWF] bounded Codex Goal creative result",
            ],
            cwd=root,
            check=False,
            timeout=60,
        )
        if commit.returncode:
            raise RuntimeError("CODEX_GOAL_COMMIT_FAILED")
        head = git(root, "rev-parse", "HEAD")
        if head == current:
            raise RuntimeError("CODEX_GOAL_HEAD_NOT_ADVANCED")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise RuntimeError("CODEX_GOAL_REAL_WORKTREE_NOT_CLEAN")

        result = build_work_result(
            package,
            outcome="PASS",
            head_sha=head,
            changed_paths=changed,
            verification_claims=[
                "codex_chatgpt_auth_verified",
                "codex_workspace_write_turn_completed",
                "adwf_wrapper_scope_validated",
                "provider_managed_session_no_secret_forwarding",
            ],
            evidence_claims=["changed_paths", "verification_claims"],
            summary_ru=(
                f"Codex Goal {version} выполнил bounded workspace-write в disposable clone; "
                "результат остаётся LOW_TRUST до trusted/provider verification."
            ),
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return 0
    except subprocess.TimeoutExpired:
        return fail("CODEX_GOAL_TIMEOUT")
    except Exception as exc:
        try:
            if applied and root.joinpath(".git").exists() and current:
                subprocess.run(
                    ["git", "reset", "--hard", current],
                    cwd=root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=60,
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=60,
                )
        finally:
            return fail(str(exc).splitlines()[0][:MAX_FAILURE_CHARS])


def main() -> int:
    request_raw = os.environ.get("ADWF_ACTION_REQUEST", "")
    result_raw = os.environ.get("ADWF_ACTION_RESULT", "")
    if not request_raw or not result_raw:
        return fail("CODEX_GOAL_CHANNEL_MISSING")
    return execute(
        Path.cwd().resolve(),
        Path(request_raw).resolve(),
        Path(result_raw).resolve(),
        dict(os.environ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
