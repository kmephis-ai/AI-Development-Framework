import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.evidence import verify_evidence


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / ".adwf/schemas/evidence.schema.json").read_text(encoding="utf-8"))
        cls.sha = "a" * 40

    def record(self, content_hash, artifact="result.txt"):
        return {
            "id": "EV-00001", "kind": "CI", "status": "PASS", "subject": f"commit {self.sha}",
            "sha": self.sha, "source": "local-test", "source_type": "LOCAL",
            "command": ["python", "-m", "unittest"], "runner": "test",
            "created_at": "2026-08-13T10:00:00Z", "expires_at": "2026-08-13T14:00:00Z",
            "content_sha256": content_hash, "artifact": artifact, "runtime_revision": None,
            "product_impact": False,
        }

    def test_valid_fresh_exact_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.txt"
            path.write_text("PASS\n", encoding="utf-8")
            record = self.record(hashlib.sha256(path.read_bytes()).hexdigest())
            result = verify_evidence(record, self.schema, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc), expected_sha=self.sha, root=tmp)
            self.assertTrue(result["valid"], result)

    def test_stale_blocks(self):
        record = self.record("a" * 64, artifact=None)
        result = verify_evidence(record, self.schema, now=datetime(2026, 8, 14, tzinfo=timezone.utc))
        self.assertFalse(result["valid"])
        self.assertIn("STALE", result["errors"])

    def test_sha_mismatch_blocks(self):
        record = self.record("a" * 64, artifact=None)
        result = verify_evidence(record, self.schema, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc), expected_sha="b" * 40)
        self.assertIn("SHA_MISMATCH", result["errors"])

    def test_false_pass_without_hash_blocks(self):
        record = self.record("", artifact=None)
        result = verify_evidence(record, self.schema, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
