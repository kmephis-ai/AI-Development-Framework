#!/usr/bin/env python3
"""Fail-closed materializer for ADWF connector-delivered Git bundle artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


class MaterializationError(RuntimeError):
    pass


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        rendered = " ".join(args)
        detail = (result.stderr or result.stdout).strip()
        raise MaterializationError(f"command failed ({rendered}): {detail}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if os.path.commonpath([destination.resolve(), target]) != str(destination.resolve()):
                raise MaterializationError(f"unsafe ZIP member: {member.filename}")
        zf.extractall(destination)


def _find_exact(root: Path, name: str) -> Path:
    matches = [p for p in root.rglob(name) if p.is_file()]
    if len(matches) != 1:
        raise MaterializationError(f"expected exactly one {name}, found {len(matches)}")
    return matches[0]


def _verify_checksum_file(checksum_file: Path, bundle: Path, actual_sha: str) -> None:
    lines = [line.strip() for line in checksum_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise MaterializationError("SHA256SUMS.txt must contain exactly one non-empty line")
    parts = lines[0].split()
    if len(parts) != 2 or parts[1].lstrip("*") != bundle.name:
        raise MaterializationError("SHA256SUMS.txt has unexpected format or filename")
    if parts[0].lower() != actual_sha:
        raise MaterializationError("SHA256SUMS.txt hash mismatch")


def materialize(
    artifact_zip: Path,
    target: Path,
    source_sha: str,
    source_branch: str,
    remote_url: str | None = None,
) -> dict[str, object]:
    if not artifact_zip.is_file():
        raise MaterializationError(f"artifact ZIP not found: {artifact_zip}")
    if target.exists() and any(target.iterdir()):
        raise MaterializationError(f"target already exists and is not empty: {target}")

    with tempfile.TemporaryDirectory(prefix="adwf-local-mirror-") as tmp:
        tmp_path = Path(tmp)
        extracted = tmp_path / "artifact"
        extracted.mkdir()
        try:
            _safe_extract(artifact_zip, extracted)
        except (zipfile.BadZipFile, OSError) as exc:
            raise MaterializationError(f"invalid artifact ZIP: {exc}") from exc

        bundle = _find_exact(extracted, "repository.bundle")
        manifest_path = _find_exact(extracted, "manifest.json")
        checksum_path = _find_exact(extracted, "SHA256SUMS.txt")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise MaterializationError(f"invalid manifest.json: {exc}") from exc

        if manifest.get("schema_version") != 1:
            raise MaterializationError("unsupported manifest schema_version")
        if manifest.get("source_sha") != source_sha:
            raise MaterializationError("manifest source_sha does not match requested exact SHA")
        if manifest.get("source_branch") != source_branch:
            raise MaterializationError("manifest source_branch does not match requested branch")
        if manifest.get("bundle_ref") != "refs/heads/adwf-source":
            raise MaterializationError("unexpected bundle_ref")

        actual_sha = _sha256(bundle)
        expected_sha = str(manifest.get("bundle_sha256", "")).lower()
        if actual_sha != expected_sha:
            raise MaterializationError("bundle SHA-256 does not match manifest")
        if bundle.stat().st_size != manifest.get("bundle_bytes"):
            raise MaterializationError("bundle size does not match manifest")
        _verify_checksum_file(checksum_path, bundle, actual_sha)

        verify_repo = tmp_path / "verify.git"
        _run(["git", "init", "--bare", "-q", str(verify_repo)])
        _run(["git", "-C", str(verify_repo), "bundle", "verify", str(bundle)])

        target.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q", str(target)])
        _run(
            [
                "git",
                "-C",
                str(target),
                "fetch",
                "-q",
                "--tags",
                str(bundle),
                "refs/heads/adwf-source:refs/remotes/adwf/source",
            ]
        )
        _run(["git", "-C", str(target), "cat-file", "-e", f"{source_sha}^{{commit}}"])
        _run(["git", "-C", str(target), "checkout", "-q", "-B", source_branch, source_sha])

        if remote_url:
            _run(["git", "-C", str(target), "remote", "add", "origin", remote_url])

        head = _run(["git", "-C", str(target), "rev-parse", "HEAD"]).stdout.strip()
        if head != source_sha:
            raise MaterializationError(f"final HEAD mismatch: expected {source_sha}, got {head}")
        _run(["git", "-C", str(target), "fsck", "--full", "--no-dangling"])
        commit_count = int(
            _run(["git", "-C", str(target), "rev-list", "--count", source_sha]).stdout.strip()
        )

        return {
            "status": "PASS",
            "source_sha": source_sha,
            "source_branch": source_branch,
            "bundle_sha256": actual_sha,
            "target": str(target.resolve()),
            "head": head,
            "commit_count": commit_count,
            "fsck": "PASS",
            "remote_url": remote_url,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-zip", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--remote-url")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = materialize(
            args.artifact_zip,
            args.target,
            args.source_sha,
            args.source_branch,
            args.remote_url,
        )
    except MaterializationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
