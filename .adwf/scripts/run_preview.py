#!/usr/bin/env python3
"""Start exact consumer revision in a disposable safety envelope and capture preview."""
from __future__ import annotations

from pathlib import Path
import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_profile import ConsumerProfileError, load_effective_config  # noqa: E402
from lib.preview_engine import capture_preview  # noqa: E402
from lib.project_execution import ProjectExecutionError, ProjectExecutionSession, load_bound_project_pack  # noqa: E402


def wait(url: str, timeout: int = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.5)
    raise ValueError("PREVIEW_SERVER_NOT_READY")


def _framework_self_host() -> bool:
    try:
        config = load_effective_config(ROOT, ROOT)
    except (OSError, ConsumerProfileError):
        return False
    project = config.get("project") or {}
    return project.get("type") == "framework" and project.get("runtime_product") is False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-playwright", action="store_true")
    parser.add_argument("--baseline-url")
    parser.add_argument("--output")
    args = parser.parse_args()
    if _framework_self_host():
        print(json.dumps({"status": "NOT_APPLICABLE", "reason": "FRAMEWORK_SELF_HOST_PROJECT_PACK_NOT_APPLICABLE"}))
        return 0
    try:
        binding = load_bound_project_pack(ROOT, ROOT)
        commands = binding.get("commands") or {}
        preview = binding.get("preview") or {}
        start = commands.get("start") or {}
        start_command = start.get("command") if start.get("available") is True else None
        url = preview.get("default_url")
        if not start_command or not url:
            print(json.dumps({"status": "NOT_APPLICABLE", "reason": "PROJECT_PACK_HAS_NO_PREVIEW_START", "pack": binding.get("pack")}))
            return 0
        install = commands.get("install") or {}
        with ProjectExecutionSession(ROOT, ROOT, binding, purpose="preview") as session:
            if install.get("available") is True and install.get("command"):
                observation = session.run("install", install["command"], pack_bound=True, capture_output=True)
                if observation.process.returncode or observation.safety_status != "PASS":
                    raise ProjectExecutionError("PROJECT_PREVIEW_INSTALL_BLOCKED")
            if session.workspace is None:
                raise ProjectExecutionError("PROJECT_EXECUTION_SESSION_NOT_STARTED")
            server = session.popen("start", start_command, pack_bound=True)
            process_observation = None
            try:
                wait(str(url))

                def safe_tool_runner(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
                    observation = session.run(
                        "preview-tool", command, pack_bound=False, cwd=cwd, timeout=timeout, capture_output=True,
                    )
                    if observation.safety_status != "PASS":
                        raise ProjectExecutionError("PREVIEW_FRAMEWORK_TOOL_SAFETY_BLOCK")
                    return observation.process

                output = args.output or str(ROOT / ".adwf-runtime/preview" / session.head_sha[:12])
                manifest = capture_preview(
                    session.workspace,
                    url=str(url),
                    head_sha=session.head_sha,
                    baseline_url=args.baseline_url,
                    output_dir=output,
                    install=args.install_playwright,
                    command_runner=safe_tool_runner,
                    runtime_root=ROOT,
                )
            finally:
                controlled_shutdown = server.poll() is None
                if controlled_shutdown:
                    try:
                        os.killpg(server.pid, signal.SIGTERM)
                    except Exception:
                        server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill(); server.wait(timeout=5)
                process_observation = session.record_process(
                    "start", start_command, server, pack_bound=True, expected_termination=controlled_shutdown
                )
            if process_observation.safety_status != "PASS":
                raise ProjectExecutionError("PROJECT_PREVIEW_START_SAFETY_BLOCK")
            marker = {
                "schema_version": 1,
                "head_sha": manifest["head_sha"],
                "preview_digest": manifest["preview_digest"],
                "attestation_id": manifest["attestation_id"],
                "source_attestation": manifest["source_attestation"],
                "runtime_environment": manifest["runtime_environment"],
                "screenshot_digests": [item.get("sha256") for item in manifest.get("screenshots") or []],
                "accessibility_status": (manifest.get("accessibility") or {}).get("status"),
                "project_execution_id": session.execution_id,
                "pack_digest": binding["pack_digest"],
                "network_enforcement": "DECLARATION_ONLY_NOT_ENFORCED",
            }
            encoded = base64.urlsafe_b64encode(
                json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).decode().rstrip("=")
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            print("ADWF_PREVIEW_ATTESTATION_V1=" + encoded)
            return 0
    except (OSError, ValueError, ConsumerProfileError, ProjectExecutionError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "NOT_VERIFIED", "reason": str(exc).split(":", 1)[0]}, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
