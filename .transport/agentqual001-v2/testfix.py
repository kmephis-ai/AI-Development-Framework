#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: testfix.py <candidate-root>")
path = Path(sys.argv[1]).resolve() / ".adwf/tests/test_creative_agent_qualification.py"
text = path.read_text(encoding="utf-8")
needle = '''            env = {"ADWF_AGENT_ADAPTER_ID": "reference-local", "ADWF_ALLOW_REFERENCE_AGENT": "1"}
            with patch.dict(os.environ, env, clear=False), patch("lib.action_executors.subprocess.run", side_effect=subprocess.TimeoutExpired(["agent"], 60)):
                result = _run_agent_command(root, state(), "a" * 64, envelope())'''
replacement = '''            env = {"ADWF_AGENT_ADAPTER_ID": "reference-local", "ADWF_ALLOW_REFERENCE_AGENT": "1"}
            pkg = compile_work_package(state(), {"task_ru": "Проверить fail-closed command execution cases"}, created_at="2026-08-16T00:00:00Z")
            with patch.dict(os.environ, env, clear=False), patch("lib.action_executors.subprocess.run", side_effect=subprocess.TimeoutExpired(["agent"], 60)):
                result = _run_agent_command(root, state(), "a" * 64, envelope(pkg))'''
if text.count(needle) != 1:
    raise SystemExit("TIMEOUT_TEST_BASE_MISMATCH")
text = text.replace(needle, replacement, 1)
text = text.replace('result = _run_agent_command(root, state(), "b" * 64, envelope())', 'result = _run_agent_command(root, state(), "b" * 64, envelope(pkg))', 1)
text = text.replace('result = _run_agent_command(root, state(), "c" * 64, envelope())', 'result = _run_agent_command(root, state(), "c" * 64, envelope(pkg))', 1)
path.write_text(text, encoding="utf-8", newline="\n")
print("AGENTQUAL_TESTFIX_V2: PASS")
