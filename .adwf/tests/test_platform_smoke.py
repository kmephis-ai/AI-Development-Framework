from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".adwf/scripts/platform_smoke.py"
SPEC = importlib.util.spec_from_file_location("adwf_platform_smoke", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Response:
    status = 200

    def __init__(self, body: str):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body.encode("utf-8")


class _Proc:
    def __init__(self, rc=None):
        self.rc = rc

    def poll(self):
        return self.rc


class PlatformSmokeRecoveryTests(unittest.TestCase):
    def test_tcp_readiness_is_separate_from_full_http_render(self) -> None:
        proc = _Proc()
        with patch.object(smoke.socket, "create_connection", return_value=_Connection()) as connect:
            ready, reason = smoke._wait_for_tcp(proc, "127.0.0.1", 18765, 1.0)
        self.assertTrue(ready)
        self.assertEqual(reason, "TCP_READY")
        connect.assert_called_once()

    def test_process_exit_before_readiness_is_explicit(self) -> None:
        ready, reason = smoke._wait_for_tcp(_Proc(7), "127.0.0.1", 18765, 1.0)
        self.assertFalse(ready)
        self.assertEqual(reason, "PROCESS_EXITED:7")

    def test_full_page_fetch_uses_bounded_15_second_budget(self) -> None:
        observed = {}

        def opener(url, timeout):
            observed["url"] = url
            observed["timeout"] = timeout
            return _Response("ADWF v1.6 Executive Portal ПРОДОЛЖИТЬ Дорожная карта")

        with patch.object(smoke.urllib.request, "urlopen", side_effect=opener):
            status, body = smoke._fetch_page("http://127.0.0.1:18765/")
        self.assertEqual(status, 200)
        self.assertEqual(observed["timeout"], 15.0)
        self.assertIn("ПРОДОЛЖИТЬ", body)

    def test_required_content_assertions_remain_strict(self) -> None:
        self.assertEqual(
            smoke.REQUIRED_PORTAL_CONTENT,
            ["ADWF v1.6 Executive Portal", "ПРОДОЛЖИТЬ", "Дорожная карта"],
        )
        self.assertEqual(smoke.TCP_READY_TIMEOUT_SECONDS, 10.0)
        self.assertEqual(smoke.PAGE_FETCH_TIMEOUT_SECONDS, 15.0)

    def test_failure_output_is_machine_readable_and_preserves_diagnostics(self) -> None:
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            smoke._print_failure("PAGE_FETCH:TimeoutError", "portal out", "portal err")
        output = stream.getvalue()
        self.assertIn("PLATFORM_SMOKE_FAIL:PAGE_FETCH:TimeoutError", output)
        self.assertIn("PLATFORM_SMOKE_PROCESS_STDOUT_BEGIN", output)
        self.assertIn("portal out", output)
        self.assertIn("PLATFORM_SMOKE_PROCESS_STDERR_BEGIN", output)
        self.assertIn("portal err", output)


if __name__ == "__main__":
    unittest.main()
