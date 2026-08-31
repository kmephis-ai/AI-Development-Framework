from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

MODULE_PATH = ROOT / ".adwf/scripts/codex_goal_adapter.py"
SPEC = importlib.util.spec_from_file_location("adwf_codex_goal_adapter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex)

from lib.ai_work_contracts import compile_work_package  # noqa: E402


def package(base_sha: str = "a" * 40, **updates):
    state = {
        "run_id": "run-codex-goal-test",
        "roadmap_id": "CODEX_EXECUTOR-001",
        "issue_id": "255",
        "revision": 1,
        "phase": "EXECUTE",
        "work_type": "feature",
        "risk": "R1",
        "subject_sha": base_sha,
        "allowed_write_surfaces": ["src/**"],
        "forbidden_write_surfaces": [".github/**"],
        "required_evidence": ["changed_paths", "verification_claims"],
    }
    state.update(updates)
    return compile_work_package(
        state,
        {
            "task_ru": "Проверить bounded Codex Goal adapter",
            "verification": ["Trusted provider CI validates the exact head."],
        },
        created_at="2026-08-30T00:00:00Z",
    )


class CodexGoalAdapterTests(unittest.TestCase):
    def test_windows_argv_is_exact_qualified_surface(self):
        argv = codex.build_codex_argv("codex.exe", "goal", windows=True)
        self.assertEqual(
            argv,
            [
                "codex.exe",
                "-c",
                'windows.sandbox="unelevated"',
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "--ignore-user-config",
                "goal",
            ],
        )
        joined = " ".join(argv)
        for forbidden in ("--ask-for-approval", "--full-auto", "--yolo", "--skip-git-repo-check"):
            self.assertNotIn(forbidden, joined)

    def test_non_windows_argv_keeps_same_bounded_exec_contract(self):
        argv = codex.build_codex_argv("codex", "goal", windows=False)
        self.assertEqual(argv[0:5], ["codex", "exec", "--json", "--sandbox", "workspace-write"])
        self.assertNotIn("windows.sandbox", " ".join(argv))

    def test_jsonl_requires_completed_and_rejects_failed(self):
        good = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t"}),
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.completed"}),
            ]
        )
        self.assertTrue(codex._jsonl_completed(good))
        self.assertFalse(codex._jsonl_completed(json.dumps({"type": "turn.started"})))
        failed = good + "\n" + json.dumps({"type": "turn.failed"})
        self.assertFalse(codex._jsonl_completed(failed))

    def test_public_visible_agent_message_is_bounded_and_allowlisted(self):
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "private-thread-id"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "  No files needed.\nEverything already matches.  "},
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {"secret": "not retained"}}),
            ]
        )
        observation = codex._codex_observation(stdout)
        self.assertEqual(observation["terminal_state"], "completed")
        self.assertEqual(observation["event_counts"]["item.completed"], 1)
        self.assertTrue(observation["visible_agent_message"])
        self.assertEqual(observation["visible_excerpt"], "No files needed. Everything already matches.")
        self.assertEqual(
            set(observation),
            {"terminal_state", "event_counts", "visible_agent_message", "visible_excerpt"},
        )
        self.assertNotIn("private-thread-id", repr(observation))
        self.assertNotIn("not retained", repr(observation))

    def test_reasoning_unknown_and_command_payloads_are_excluded(self):
        stdout = "\n".join(
            [
                json.dumps({"type": "reasoning", "text": "hidden chain"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "command_execution", "stdout": "command-secret", "stderr": "error-secret"},
                    }
                ),
                json.dumps({"type": "future.event", "payload": "unknown-secret"}),
                json.dumps({"type": "turn.completed"}),
            ]
        )
        observation = codex._codex_observation(stdout)
        rendered = repr(observation)
        for forbidden in ("hidden chain", "command-secret", "error-secret", "unknown-secret"):
            self.assertNotIn(forbidden, rendered)
        self.assertFalse(observation["visible_agent_message"])
        self.assertIsNone(observation["visible_excerpt"])

    def test_visible_excerpt_length_is_deterministic(self):
        text = "z" * (codex.MAX_VISIBLE_EXCERPT_CHARS + 50)
        stdout = "\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}),
                json.dumps({"type": "turn.completed"}),
            ]
        )
        first = codex._codex_observation(stdout)
        second = codex._codex_observation(stdout)
        self.assertEqual(first, second)
        self.assertEqual(first["visible_excerpt"], "z" * codex.MAX_VISIBLE_EXCERPT_CHARS)

    def test_secret_like_visible_message_is_omitted_fail_closed(self):
        for text in (
            "API_KEY=abcdef123456",
            "password: hunter2",
            "Use sk-abcdefgh12345678 for access",
            "-----BEGIN PRIVATE KEY----- material",
        ):
            with self.subTest(text=text):
                stdout = "\n".join(
                    [
                        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}),
                        json.dumps({"type": "turn.completed"}),
                    ]
                )
                observation = codex._codex_observation(stdout)
                self.assertTrue(observation["visible_agent_message"])
                self.assertIsNone(observation["visible_excerpt"])
                self.assertNotIn(text, codex._no_changes_diagnostic(observation))

    def test_completed_no_diff_raises_safe_diagnostic_before_apply(self):
        observation = {
            "terminal_state": "completed",
            "event_counts": {event_type: 1 for event_type in codex.PUBLIC_EVENT_TYPES},
            "visible_agent_message": True,
            "visible_excerpt": "No repository changes were necessary.",
        }
        diagnostic = codex._no_changes_diagnostic(observation)
        self.assertTrue(diagnostic.startswith("CODEX_GOAL_NO_CHANGES_DIAGNOSTIC;"))
        self.assertNotIn("\n", diagnostic)
        self.assertLessEqual(len(diagnostic), 240)
        with self.assertRaises(RuntimeError) as raised:
            raise RuntimeError(diagnostic)
        self.assertEqual(str(raised.exception), diagnostic)

    def test_no_change_diagnostic_is_deterministically_bounded(self):
        observation = {
            "terminal_state": "completed",
            "event_counts": {event_type: 1000000 for event_type in codex.PUBLIC_EVENT_TYPES},
            "visible_agent_message": True,
            "visible_excerpt": "x" * 1000,
        }
        first = codex._no_changes_diagnostic(observation)
        second = codex._no_changes_diagnostic(observation)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("CODEX_GOAL_NO_CHANGES_DIAGNOSTIC;"))
        self.assertLessEqual(len(first), 240)
        self.assertNotIn("\n", first)

    def test_prompt_preserves_authority_boundary(self):
        value = codex._prompt(package())
        self.assertIn("this prompt is not authority", value)
        self.assertIn("Do not commit, push, merge", value)
        self.assertIn(".adwf/roadmap.json", value)
        self.assertIn(".github/**", value)
        self.assertIn("src/**", value)

    def test_version_is_pinned_to_measured_cli(self):
        good = subprocess.CompletedProcess(["codex", "--version"], 0, stdout="codex-cli 0.149.1\n", stderr="")
        with patch.object(codex.subprocess, "run", return_value=good) as run:
            self.assertEqual(codex.codex_version("codex"), "0.149.1")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")
        drift = subprocess.CompletedProcess(["codex", "--version"], 0, stdout="codex-cli 0.150.0\n", stderr="")
        with patch.object(codex.subprocess, "run", return_value=drift):
            with self.assertRaisesRegex(ValueError, "CODEX_GOAL_VERSION_UNQUALIFIED"):
                codex.codex_version("codex")

    def test_provider_auth_must_be_chatgpt(self):
        good = subprocess.CompletedProcess(
            ["codex", "login", "status"], 0, stdout="Logged in using ChatGPT — готово\n", stderr=""
        )
        with patch.object(codex.subprocess, "run", return_value=good) as run:
            codex.verify_chatgpt_auth("codex")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")
        api = subprocess.CompletedProcess(["codex", "login", "status"], 0, stdout="Logged in using API key\n", stderr="")
        with patch.object(codex.subprocess, "run", return_value=api):
            with self.assertRaisesRegex(ValueError, "CODEX_GOAL_CHATGPT_AUTH_REQUIRED"):
                codex.verify_chatgpt_auth("codex")

    def test_codex_exec_uses_strict_utf8_and_returns_observation(self):
        completed = subprocess.CompletedProcess(
            ["codex", "exec"],
            0,
            stdout="\n".join(
                [
                    json.dumps(
                        {"type": "item.completed", "item": {"type": "agent_message", "text": "готово ✓"}},
                        ensure_ascii=False,
                    ),
                    json.dumps({"type": "turn.completed"}),
                ]
            )
            + "\n",
            stderr="",
        )
        with patch.object(codex.subprocess, "run", return_value=completed) as run:
            observation = codex.run_codex(ROOT, package(), "codex")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")
        self.assertEqual(observation["terminal_state"], "completed")
        self.assertEqual(observation["visible_excerpt"], "готово ✓")

    def test_failed_terminal_event_remains_failure(self):
        failed = subprocess.CompletedProcess(
            ["codex", "exec"],
            0,
            stdout="\n".join(
                [json.dumps({"type": "turn.completed"}), json.dumps({"type": "turn.failed", "error": "private"})]
            ),
            stderr="",
        )
        with patch.object(codex.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "CODEX_GOAL_TERMINAL_EVENT_NOT_VERIFIED"):
                codex.run_codex(ROOT, package(), "codex")

    def test_malformed_codex_utf8_cannot_become_successful_terminal_event(self):
        decode_error = UnicodeDecodeError("utf-8", b'\x98', 0, 1, "invalid start byte")
        with patch.object(codex.subprocess, "run", side_effect=decode_error) as run:
            with self.assertRaises(UnicodeDecodeError):
                codex.run_codex(ROOT, package(), "codex")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")

    def test_secret_environment_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = codex.execute(
                root,
                root / "missing-request.json",
                root / "result.json",
                {
                    "OPENAI_API_KEY": "must-not-forward",
                    "ADWF_AGENT_EXECUTOR_AUTH": "PROVIDER_MANAGED_SESSION",
                    "ADWF_AGENT_NETWORK_AUTHORITY": "DECLARED_EXTERNAL",
                    "ADWF_AGENT_SECRETS_AUTHORITY": "FORBIDDEN",
                },
            )
        self.assertEqual(code, 2)

    def test_hard_forbidden_and_package_scope_are_enforced(self):
        pkg = package()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "src").mkdir()
            (root / "src/ok.txt").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "base"],
                cwd=root,
                check=True,
            )
            codex.validate_changed_paths(root, pkg, ["src/ok.txt"])
            with self.assertRaisesRegex(ValueError, "WRITE_SURFACE_FORBIDDEN"):
                codex.validate_changed_paths(root, pkg, [".github/workflows/bad.yml"])
            with self.assertRaisesRegex(ValueError, "WRITE_SURFACE_FORBIDDEN"):
                codex.validate_changed_paths(root, pkg, ["docs/outside.md"])

    def test_hard_forbidden_surfaces_override_wildcard_package_scope(self):
        pkg = package(allowed_write_surfaces=["**"])
        for path in (".adwf/roadmap.json", "AGENTS.md"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as raised:
                    codex.validate_changed_paths(ROOT, pkg, [path])
                self.assertEqual(
                    str(raised.exception),
                    "CODEX_GOAL_WRITE_SURFACE_FORBIDDEN:" + path,
                )

    def test_ignored_change_is_rejected(self):
        pkg = package(allowed_write_surfaces=["src/**"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("src/ignored.txt\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src/ignored.txt").write_text("ignored\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "base"],
                cwd=root,
                check=True,
            )
            with self.assertRaisesRegex(ValueError, "IGNORED_CHANGE_FORBIDDEN"):
                codex.validate_changed_paths(root, pkg, ["src/ignored.txt"])


if __name__ == "__main__":
    unittest.main()
