#!/usr/bin/env python3
"""Temporary read-only final-projection probe for SELFTEST_COVERAGE-001."""
from __future__ import annotations

from pathlib import Path
import base64
import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.docs_freshness import source_digest  # noqa: E402
from lib.strict_json import load as strict_json_load  # noqa: E402


def main() -> int:
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    event = json.loads(event_path.read_text(encoding="utf-8"))
    base_sha = str((((event.get("pull_request") or {}).get("base") or {}).get("sha") or ""))
    if len(base_sha) != 40:
        print("ADWF_PROJECTION_PROBE=FAIL:BASE_SHA")
        return 1

    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
        capture_output=True,
        check=False,
    )
    if archive.returncode:
        print("ADWF_PROJECTION_PROBE=FAIL:ARCHIVE")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
            bundle.extractall(target, filter="data")

        for relative in (
            ".adwf/scripts/validate_framework.py",
            ".adwf/scripts/validate_pipeline_ir.py",
        ):
            base_file = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{base_sha}:{relative}"],
                capture_output=True,
                check=False,
            )
            if base_file.returncode:
                print("ADWF_PROJECTION_PROBE=FAIL:BASE_FILE:" + relative)
                return 1
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base_file.stdout)

        registry_path = target / ".adwf/docs-registry.json"
        registry = strict_json_load(registry_path)
        changed: dict[str, str] = {}
        for item in registry.get("documents", []):
            digest = source_digest(target, item.get("watched") or [])
            if digest != item.get("source_digest"):
                item["source_digest"] = digest
                changed[str(item.get("path") or "")] = digest
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

        generated = subprocess.run(
            [sys.executable, str(target / ".adwf/scripts/generate_manifest.py"), "--root", str(target)],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        )
        if generated.returncode:
            print("ADWF_PROJECTION_PROBE=FAIL:MANIFEST:" + generated.stdout[-400:])
            return 1

        files = {}
        for relative in (".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt"):
            raw = (target / relative).read_bytes()
            files[relative] = base64.b64encode(raw).decode("ascii")
        payload = json.dumps({"changed_docs": changed, "files_b64": files}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        packed = gzip.compress(payload, compresslevel=9, mtime=0)
        print("ADWF_PROJECTION_BUNDLE_B64=" + base64.b64encode(packed).decode("ascii"))
        print("ADWF_PROJECTION_PROBE=PASS")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
