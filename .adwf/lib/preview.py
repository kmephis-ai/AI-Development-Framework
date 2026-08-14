"""Reference visual-evidence manifest; Playwright is an optional project adapter."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import hashlib, json, re

SHA = re.compile(r"^[0-9a-f]{40}$")


def build_preview_manifest(*, head_sha: str, desktop: Path, mobile: Path,
                           browser: str, browser_version: str, os_image: str,
                           accessibility_status: str = "NOT_VERIFIED") -> dict[str, Any]:
    if SHA.fullmatch(head_sha) is None: raise ValueError("PREVIEW_SHA_INVALID")
    shots=[]
    for name,path in (("desktop",desktop),("mobile",mobile)):
        if not path.is_file(): raise ValueError(f"PREVIEW_SCREENSHOT_MISSING:{name}")
        shots.append({"name":name,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest={"schema_version":1,"head_sha":head_sha,"browser":browser,"browser_version":browser_version,
              "os_image":os_image,"screenshots":shots,"accessibility_status":accessibility_status,
              "pixel_environment_pinned":True}
    manifest["preview_digest"]=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return manifest
