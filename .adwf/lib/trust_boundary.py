"""Canonical trust-boundary classification for ADWF v1.6.

The classification is loaded only from trusted/default-branch code. A PR may
modify a copy of this module in its own head, but the trusted controller never
executes that copy when deciding whether the PR may be trusted.
"""
from __future__ import annotations
from fnmatch import fnmatch
from typing import Iterable

TRUST_BOUNDARY_PATTERNS = (
    ".github/workflows/adwf-*",
    ".adwf/scripts/publish_trusted_gate.py",
    ".adwf/scripts/validate_*.py",
    ".adwf/scripts/generate_pipeline.py",
    ".adwf/lib/policy.py",
    ".adwf/lib/policy_runtime.py",
    ".adwf/lib/policy_compiler.py",
    ".adwf/lib/trusted_context.py",
    ".adwf/lib/assurance.py",
    ".adwf/lib/evidence*.py",
    ".adwf/lib/cost_guard.py",
    ".adwf/lib/github_rulesets.py",
    ".adwf/lib/trust_boundary.py",
    ".adwf/policies/**",
    ".adwf/providers.json",
    ".adwf/pipeline-ir.json",
    ".adwf/schemas/pipeline-ir.schema.json",
    ".adwf/schemas/config*.schema.json",
)


def _match(path: str, pattern: str) -> bool:
    # fnmatch handles '*' but not a special globstar contract consistently
    # across platforms, so treat '/**' as an explicit recursive prefix.
    normalized = str(path).replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/").lstrip("./")
    if pat.endswith("/**"):
        return normalized == pat[:-3] or normalized.startswith(pat[:-2])
    return fnmatch(normalized, pat)


def is_trust_boundary_path(path: str) -> bool:
    return any(_match(path, pattern) for pattern in TRUST_BOUNDARY_PATTERNS)


def classify_changed_files(paths: Iterable[str]) -> dict:
    changed = sorted({str(p).replace("\\", "/").lstrip("./") for p in paths if str(p).strip()})
    protected = [p for p in changed if is_trust_boundary_path(p)]
    return {
        "changed_files": changed,
        "trust_boundary_changed": bool(protected),
        "trust_boundary_files": protected,
        "classification": "GOVERNANCE" if protected else "NORMAL",
    }
