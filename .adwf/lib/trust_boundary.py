"""Canonical trust-boundary classification for ADWF v1.6.

The classification is loaded only from trusted/default-branch code. A PR may
modify a copy of this module in its own head, but the trusted controller never
executes that copy when deciding whether the PR may be trusted.

This list is intentionally a conservative superset of the base trust policy.
Generated projections remain trust-boundary evidence here even though the PR
classifier may allow them to accompany normal docs/feature changes under R4.
"""
from __future__ import annotations
from fnmatch import fnmatch
from typing import Iterable

TRUST_BOUNDARY_PATTERNS = (
    ".adwf/**",
    ".github/workflows/adwf-*",
    "AGENTS.md",
    "ADWS.md",
    "SPECIFICATION.md",
    ".gitlab-ci.yml",
    "SECURITY.md",
    "docs/governance/**",
    "MANIFEST.json",
    "SHA256SUMS.txt",
    ".gitattributes",
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
