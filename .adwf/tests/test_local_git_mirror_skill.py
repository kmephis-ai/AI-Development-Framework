import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "adwf-local-git-mirror" / "scripts" / "materialize_bundle.py"
SPEC = importlib.util.spec_from_file_location("adwf_local_git_mirror", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class LocalGitMirrorSkillTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is required")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.email", "test@example.com"], check=True)
        (self.source / "hello.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", "hello.txt"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-q", "-m", "one"], check=True)
        (self.source / "hello.txt").write_text("two\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "commit", "-q", "-am", "two"], check=True)
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(self.source), "tag", "v-test"], check=True)
        self.artifact = self._make_artifact()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_artifact(self) -> Path:
        artifact_dir = self.root / "artifact"
        artifact_dir.mkdir()
        subprocess.run(
            ["git", "-C", str(self.source), "branch", "-f", "adwf-source", self.sha],
            check=True,
        )
        bundle = artifact_dir / "repository.bundle"
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "bundle",
                "create",
                str(bundle),
                "refs/heads/adwf-source",
                "--tags",
            ],
            check=True,
        )
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        (artifact_dir / "SHA256SUMS.txt").write_text(
            f"{digest}  repository.bundle\n", encoding="utf-8"
        )
        (artifact_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_repository": "example/repo",
                    "source_branch": "main",
                    "source_sha": self.sha,
                    "transport_sha": "0" * 40,
                    "bundle_sha256": digest,
                    "bundle_bytes": bundle.stat().st_size,
                    "bundle_ref": "refs/heads/adwf-source",
                }
            ),
            encoding="utf-8",
        )
        archive = self.root / "artifact.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
            for file in artifact_dir.iterdir():
                zf.write(file, arcname=file.name)
        return archive

    def test_materializes_exact_sha_with_history(self):
        target = self.root / "workspace"
        result = MODULE.materialize(self.artifact, target, self.sha, "main", "https://example.invalid/repo.git")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["head"], self.sha)
        self.assertGreaterEqual(result["commit_count"], 2)
        head = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(head, self.sha)
        tags = subprocess.check_output(["git", "-C", str(target), "tag"], text=True)
        self.assertIn("v-test", tags)

    def test_rejects_wrong_requested_source_sha(self):
        with self.assertRaisesRegex(MODULE.MaterializationError, "manifest source_sha"):
            MODULE.materialize(self.artifact, self.root / "wrong", "f" * 40, "main")

    def test_rejects_corrupted_bundle(self):
        corrupted = self.root / "corrupted.zip"
        with zipfile.ZipFile(self.artifact) as src, zipfile.ZipFile(corrupted, "w", zipfile.ZIP_STORED) as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "repository.bundle":
                    data = data + b"corruption"
                dst.writestr(name, data)
        with self.assertRaisesRegex(MODULE.MaterializationError, "SHA-256"):
            MODULE.materialize(corrupted, self.root / "corrupt-target", self.sha, "main")

    def test_rejects_missing_bundle(self):
        broken = self.root / "missing.zip"
        with zipfile.ZipFile(self.artifact) as src, zipfile.ZipFile(broken, "w", zipfile.ZIP_STORED) as dst:
            for name in src.namelist():
                if name != "repository.bundle":
                    dst.writestr(name, src.read(name))
        with self.assertRaisesRegex(MODULE.MaterializationError, "repository.bundle"):
            MODULE.materialize(broken, self.root / "missing-target", self.sha, "main")

    def test_rejects_zip_traversal(self):
        unsafe = self.root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("../escape", b"x")
        with self.assertRaisesRegex(MODULE.MaterializationError, "unsafe ZIP member"):
            MODULE.materialize(unsafe, self.root / "unsafe-target", self.sha, "main")


if __name__ == "__main__":
    unittest.main()
