"""Trusted-diff classification and exact-head provenance checks.

The trust boundary is always read from the base revision.  PR-controlled policy
must never be allowed to decide whether the same PR is trusted.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
import json
import re
import subprocess

from .evidence import parse_time

SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_TRUE_KEYS = re.compile(
    r"(?:required|enforce|guard|protected|independent|human|fail_closed|"
    r"runtime_truth|separate_trust|block|forbid|mandatory)", re.IGNORECASE
)
_DANGEROUS_TRUE_KEYS = re.compile(
    r"(?:allow_(?:overage|credit|metered|unknown|paid)|secondary_write|"
    r"automatic_(?:merge|execution)|a4_automatic|larger_runners_allowed)", re.IGNORECASE
)
_AUTONOMY = {f"A{index}": index for index in range(5)}
_RISK = {f"R{index}": index for index in range(5)}


def normalize_repo_path(value: str) -> str:
    """Return a safe, repository-relative POSIX path or raise ValueError."""
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise ValueError("unsafe repository path")
    raw = value[2:] if value.startswith("./") else value
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe repository path")
    return path.as_posix()


def _glob_regex(pattern: str) -> re.Pattern[str]:
    normalized = normalize_repo_path(pattern)
    output = ["^"]
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char == "*":
            if index + 1 < len(normalized) and normalized[index + 1] == "*":
                output.append(".*")
                index += 2
                continue
            output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        else:
            output.append(re.escape(char))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def is_protected_path(path: str, protected_patterns: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path)
    for pattern in protected_patterns:
        try:
            if _glob_regex(str(pattern)).fullmatch(normalized):
                return True
        except ValueError:
            # Invalid base policy is not silently widened.  The caller validates
            # all patterns and fails closed before classification.
            continue
    return False


def _walk_changes(old: Any, new: Any, prefix: str = "$") -> list[str]:
    reasons: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{prefix}.{key}"
            if key not in new:
                if _SAFE_TRUE_KEYS.search(key) or key in {"tests", "checks", "required_status_checks"}:
                    reasons.append(f"SAFETY_CONTROL_REMOVED:{child}")
                continue
            if key not in old:
                continue
            old_value, new_value = old[key], new[key]
            if old_value is True and new_value is False and _SAFE_TRUE_KEYS.search(key):
                reasons.append(f"SAFETY_TRUE_TO_FALSE:{child}")
            if old_value is False and new_value is True and _DANGEROUS_TRUE_KEYS.search(key):
                reasons.append(f"DANGEROUS_FALSE_TO_TRUE:{child}")
            if key in {"hard_budget_usd", "monetary_budget"}:
                try:
                    if float(new_value) > float(old_value):
                        reasons.append(f"MONETARY_BUDGET_INCREASED:{child}")
                except (TypeError, ValueError):
                    reasons.append(f"MONETARY_BUDGET_INVALID:{child}")
            if key in {"requested_autonomy", "active_autonomy"} and old_value in _AUTONOMY and new_value in _AUTONOMY:
                if _AUTONOMY[new_value] > _AUTONOMY[old_value]:
                    reasons.append(f"AUTONOMY_INCREASED:{child}")
            if key == "max_autonomous_risk" and old_value in _RISK and new_value in _RISK:
                if _RISK[new_value] > _RISK[old_value]:
                    reasons.append(f"AUTONOMOUS_RISK_INCREASED:{child}")
            reasons.extend(_walk_changes(old_value, new_value, child))
        return reasons
    if isinstance(old, list) and isinstance(new, list):
        removed = old.copy()
        for item in new:
            if item in removed:
                removed.remove(item)
        if removed and re.search(r"(?:checks|tests|protected|required|paths)", prefix, re.IGNORECASE):
            reasons.append(f"REQUIRED_LIST_ITEMS_REMOVED:{prefix}")
    return reasons


def detect_gate_weakening(path: str, old_text: str | None, new_text: str | None, *, status: str = "M") -> list[str]:
    """Conservatively detect weakening of policy, CI, permissions or tests."""
    normalized = normalize_repo_path(path)
    reasons: list[str] = []
    if status.upper().startswith("D") or new_text is None:
        return [f"PROTECTED_FILE_DELETED:{normalized}"]
    if old_text is None:
        return []
    try:
        old_json, new_json = strict_loads(old_text), strict_loads(new_text)
        reasons.extend(_walk_changes(old_json, new_json))
    except (json.JSONDecodeError, TypeError):
        pass

    removed = [line.strip() for line in old_text.splitlines() if line.strip() and line not in new_text.splitlines()]
    for line in removed:
        lowered = line.lower()
        if (
            re.search(r"(?:validate_|self-test|unittest|required_status|human-gated|fail[_ -]?closed)", lowered)
            or re.search(r"(?:contents|issues|checks|pull-requests):\s*read", lowered)
            or re.search(r"(?:required|enforce|protected|independent)[^\n]*(?:true|yes)", lowered)
        ):
            reasons.append(f"PROTECTIVE_LINE_REMOVED:{normalized}")
            break
    if re.search(r"(?:contents|issues|checks|pull-requests|id-token):\s*write", new_text) and not re.search(
        r"(?:contents|issues|checks|pull-requests|id-token):\s*write", old_text
    ):
        reasons.append(f"PERMISSION_EXPANDED:{normalized}")
    if re.search(r"(?:fail_mode|fail-mode):\s*[\"']?OPEN", new_text, re.IGNORECASE):
        reasons.append(f"FAIL_MODE_OPEN:{normalized}")
    return list(dict.fromkeys(reasons))


def _change_record(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"path": normalize_repo_path(item), "status": "M", "old_text": None, "new_text": None}
    if not isinstance(item, dict):
        raise ValueError("invalid diff record")
    status = str(item.get("status", "M")).upper()
    if re.fullmatch(r"[ACDMRTUXB][0-9]*", status) is None:
        raise ValueError("invalid git change status")
    return {
        "path": normalize_repo_path(str(item.get("path", ""))),
        "status": status,
        "old_path": normalize_repo_path(str(item["old_path"])) if item.get("old_path") else None,
        "old_text": item.get("old_text"),
        "new_text": item.get("new_text"),
    }


def classify_diff(changed_files: Iterable[Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Classify a base..head diff using a policy obtained from the base SHA."""
    records = [_change_record(item) for item in changed_files]
    patterns = policy.get("paths")
    if not isinstance(patterns, list) or not patterns:
        return {"result": "BLOCK", "reason_codes": ["BASE_TRUST_POLICY_INVALID"], "changed_files": []}
    try:
        for pattern in patterns:
            _glob_regex(str(pattern))
    except ValueError:
        return {"result": "BLOCK", "reason_codes": ["BASE_TRUST_POLICY_INVALID"], "changed_files": []}
    if not records:
        return {"result": "BLOCK", "reason_codes": ["EMPTY_DIFF"], "changed_files": []}

    protected: list[str] = []
    feature: list[str] = []
    weakening: list[dict[str, Any]] = []
    for record in records:
        candidates = [record["path"]]
        if record.get("old_path"):
            candidates.append(record["old_path"])
        if any(is_protected_path(candidate, patterns) for candidate in candidates):
            protected.append(record["path"])
            detected = detect_gate_weakening(
                record["path"], record.get("old_text"), record.get("new_text"), status=record["status"]
            )
            if detected:
                weakening.append({"path": record["path"], "reason_codes": detected})
        else:
            feature.append(record["path"])

    reasons: list[str] = []
    if protected and feature:
        reasons.append("TRUST_CHANGE_MIXED_WITH_FEATURE")
    if weakening:
        reasons.append("GATE_WEAKENING_DETECTED")
    if protected and policy.get("weakening_requires_human") is not True:
        reasons.append("BASE_TRUST_POLICY_INVALID")
    if protected and policy.get("weakening_is_risk") != "R4":
        reasons.append("BASE_TRUST_POLICY_INVALID")
    if policy.get("self_modification_in_feature_pr") != "FORBIDDEN":
        reasons.append("BASE_TRUST_POLICY_INVALID")

    if "BASE_TRUST_POLICY_INVALID" in reasons or (protected and feature):
        result = "BLOCK"
    elif protected:
        result = "HUMAN_REQUIRED"
    else:
        result = "ALLOW"
    return {
        "result": result,
        "reason_codes": list(dict.fromkeys(reasons)),
        "changed_files": sorted(record["path"] for record in records),
        "protected_files": sorted(set(protected)),
        "feature_files": sorted(set(feature)),
        "weakening": weakening,
        "required_risk": "R4" if protected else None,
        "required_work_type": "GOV" if protected else None,
        "human_required": bool(protected),
    }


classify_trusted_diff = classify_diff


def _git(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True,
            text=text, check=False, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("git inspection timed out") from exc


def _require_sha(value: str) -> str:
    normalized = str(value).lower()
    if SHA.fullmatch(normalized) is None:
        raise ValueError("exact 40-character commit SHA required")
    return normalized


def _git_blob(root: Path, sha: str, path: str) -> str | None:
    process = _git(root, "show", f"{_require_sha(sha)}:{normalize_repo_path(path)}")
    if process.returncode:
        return None
    if len(process.stdout) > 2 * 1024 * 1024:
        raise ValueError("protected file exceeds inspection limit")
    return process.stdout.decode("utf-8", errors="replace")


def load_base_trust_policy(root: str | Path, base_sha: str, policy_path: str = ".adwf/policies/trust-boundary.json") -> dict[str, Any]:
    base = Path(root).resolve()
    raw = _git_blob(base, base_sha, policy_path)
    if raw is None:
        raise ValueError("base trust policy missing")
    value = strict_loads(raw)
    if not isinstance(value, dict):
        raise ValueError("base trust policy invalid")
    return value


def git_diff_records(
    root: str | Path, base_sha: str, head_sha: str, *, protected_patterns: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return exact base...head records with blobs needed for weakening checks."""
    base, head = _require_sha(base_sha), _require_sha(head_sha)
    repository = Path(root).resolve()
    process = _git(repository, "diff", "--name-status", "-z", "--find-renames", f"{base}...{head}", "--")
    if process.returncode:
        raise ValueError("unable to inspect exact base...head diff")
    tokens = process.stdout.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", errors="strict")
        index += 1
        if index >= len(tokens):
            raise ValueError("malformed git diff")
        old_path: str | None = None
        if status.startswith(("R", "C")):
            old_path = tokens[index].decode("utf-8", errors="strict")
            index += 1
            if index >= len(tokens):
                raise ValueError("malformed git rename")
        path = tokens[index].decode("utf-8", errors="strict")
        index += 1
        normalized = normalize_repo_path(path)
        old_normalized = normalize_repo_path(old_path) if old_path else normalized
        inspect_content = protected_patterns is None or any(
            is_protected_path(candidate, protected_patterns)
            for candidate in (normalized, old_normalized)
        )
        old_text = None
        new_text = None
        if inspect_content:
            old_text = None if status.startswith("A") else _git_blob(repository, base, old_normalized)
            new_text = None if status.startswith("D") else _git_blob(repository, head, normalized)
            if not status.startswith("A") and old_text is None:
                raise ValueError("unable to inspect base blob")
            if not status.startswith("D") and new_text is None:
                raise ValueError("unable to inspect head blob")
        records.append({
            "path": normalized,
            "old_path": normalize_repo_path(old_path) if old_path else None,
            "status": status,
            "old_text": old_text,
            "new_text": new_text,
        })
        if len(records) > 10000:
            raise ValueError("diff exceeds inspection limit")
    return records


def classify_git_diff(root: str | Path, base_sha: str, head_sha: str) -> dict[str, Any]:
    policy = load_base_trust_policy(root, base_sha)
    return classify_diff(git_diff_records(root, base_sha, head_sha, protected_patterns=policy.get("paths", [])), policy)


def _utc(value: Any) -> datetime:
    parsed = parse_time(str(value)).astimezone(timezone.utc)
    return parsed


def validate_check_provenance(
    checks: Iterable[dict[str, Any]], *, expected_sha: str, expected_names: Iterable[str],
    trusted_app_slugs: Iterable[str], now: datetime | None = None, ttl_hours: int = 24,
) -> dict[str, Any]:
    """Require one successful, fresh, trusted-app check per exact name and HEAD."""
    errors: list[str] = []
    sha = _require_sha(expected_sha)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    allowed_apps = set(trusted_app_slugs)
    names = list(expected_names)
    if not names or len(names) != len(set(names)):
        errors.append("EXPECTED_CHECK_NAMES_INVALID")
    if not allowed_apps:
        errors.append("TRUSTED_CHECK_APPS_EMPTY")
    if not isinstance(ttl_hours, int) or ttl_hours < 1:
        errors.append("CHECK_TTL_INVALID")
    values = list(checks)
    for name in names:
        matches = [item for item in values if item.get("name") == name]
        if len(matches) != 1:
            errors.append(f"CHECK_CARDINALITY:{name}:{len(matches)}")
            continue
        item = matches[0]
        if str(item.get("head_sha", "")).lower() != sha:
            errors.append(f"CHECK_HEAD_MISMATCH:{name}")
        if item.get("status") != "completed" or item.get("conclusion") != "success":
            errors.append(f"CHECK_NOT_SUCCESSFUL:{name}")
        app = item.get("app") or {}
        if app.get("slug") not in allowed_apps:
            errors.append(f"CHECK_APP_UNTRUSTED:{name}")
        try:
            completed = _utc(item["completed_at"])
            age = (current - completed).total_seconds()
            if completed > current or not isinstance(ttl_hours, int) or ttl_hours < 1 or age > ttl_hours * 3600:
                errors.append(f"CHECK_STALE:{name}")
        except (KeyError, TypeError, ValueError):
            errors.append(f"CHECK_TIME_INVALID:{name}")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "head_sha": sha}


def validate_review_provenance(
    reviews: Iterable[dict[str, Any]], *, expected_sha: str, author_login: str,
    trusted_reviewer_logins: Iterable[str], now: datetime | None = None,
    ttl_hours: int = 168, required_approvals: int = 1,
) -> dict[str, Any]:
    """Require fresh independent approvals explicitly bound to the current HEAD."""
    errors: list[str] = []
    sha = _require_sha(expected_sha)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    allowed = {str(login).casefold() for login in trusted_reviewer_logins if str(login)}
    author = str(author_login).casefold()
    if not allowed:
        errors.append("TRUSTED_REVIEWERS_EMPTY")
    if not isinstance(ttl_hours, int) or ttl_hours < 1:
        errors.append("REVIEW_TTL_INVALID")
    if not isinstance(required_approvals, int) or required_approvals < 1:
        errors.append("REQUIRED_APPROVAL_COUNT_INVALID")
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for review in reviews:
        user = review.get("user") or review.get("author") or {}
        login = str(user.get("login", "")).casefold()
        if not login:
            continue
        try:
            submitted = _utc(review["submitted_at"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"REVIEW_TIME_INVALID:{login}")
            continue
        if login not in latest or submitted > latest[login][0]:
            latest[login] = (submitted, review)
    accepted: list[str] = []
    for login in sorted(allowed):
        if login not in latest:
            continue
        submitted, review = latest[login]
        if login == author:
            errors.append(f"REVIEW_NOT_INDEPENDENT:{login}")
            continue
        if review.get("state") != "APPROVED":
            continue
        if str(review.get("commit_id", "")).lower() != sha:
            errors.append(f"REVIEW_HEAD_MISMATCH:{login}")
            continue
        age = (current - submitted).total_seconds()
        if submitted > current or not isinstance(ttl_hours, int) or ttl_hours < 1 or age > ttl_hours * 3600:
            errors.append(f"REVIEW_STALE:{login}")
            continue
        accepted.append(login)
    if isinstance(required_approvals, int) and required_approvals >= 1 and len(accepted) < required_approvals:
        errors.append(f"REQUIRED_APPROVALS_MISSING:{len(accepted)}/{required_approvals}")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "head_sha": sha, "reviewers": accepted}


validate_reviewer_provenance = validate_review_provenance
