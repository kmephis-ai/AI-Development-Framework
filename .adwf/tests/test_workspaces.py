import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.workspaces import cleanup_workspace, complete_workspace, create_workspace, heartbeat_workspace, read_registry, reconcile_workspaces, schedule_retry


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "adwf@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "ADWF Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.root, check=True, capture_output=True)
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        self.now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        self.config = {"root": ".adwf-runtime/workspaces", "max_active": 1, "stall_timeout_minutes": 45,
                       "retry_base_seconds": 30, "retry_max_seconds": 900, "max_retries": 3, "require_clean_cleanup": True}

    def tearDown(self):
        self.temp.cleanup()

    def lease(self, suffix="1"):
        key = f"rm-{suffix}-issue-{suffix}"
        return {"lease_id": f"lease-{suffix}", "issue_id": suffix, "roadmap_id": f"RM-{suffix}", "worker_id": f"writer-{suffix}",
                "base_sha": self.sha, "workspace_id": key, "workspace_path": f".adwf-runtime/workspaces/{key}",
                "branch": f"adwf/{key}", "heartbeat_at": "2026-08-13T10:00:00Z", "expires_at": "2026-08-13T12:00:00Z", "status": "ACTIVE"}

    def test_create_isolated_worktree_and_idempotent_resume(self):
        created = create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        self.assertEqual(created["result"], "CREATED")
        self.assertTrue((self.root / created["path"] / ".git").exists())
        again = create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        self.assertEqual(again["result"], "ALREADY_ACTIVE")

    def test_second_workspace_is_bounded(self):
        create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        with self.assertRaisesRegex(ValueError, "WORKSPACE_CONCURRENCY_LIMIT"):
            create_workspace(self.root, self.lease("2"), self.config, apply=True, now=self.now)

    def test_stall_retry_backoff_and_heartbeat(self):
        created = create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        stalled = reconcile_workspaces(self.root, self.config, now=self.now + timedelta(minutes=46), apply=True)
        self.assertIn(f"{created['workspace_id']}:STALLED", stalled["findings"])
        retry = schedule_retry(self.root, created["workspace_id"], "temporary failure", self.config, now=self.now + timedelta(minutes=46))
        self.assertEqual(retry["status"], "RETRY_WAIT")
        ready = reconcile_workspaces(self.root, self.config, now=self.now + timedelta(minutes=47), apply=True)
        self.assertIn(f"{created['workspace_id']}:RETRY_READY", ready["findings"])
        active = heartbeat_workspace(self.root, created["workspace_id"], "writer-1", now=self.now + timedelta(minutes=47))
        self.assertEqual(active["status"], "ACTIVE")

    def test_dirty_workspace_cannot_be_completed_or_deleted(self):
        created = create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        (self.root / created["path"] / "dirty.txt").write_text("unsaved\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "WORKSPACE_DIRTY"):
            complete_workspace(self.root, created["workspace_id"])

    def test_clean_completed_workspace_can_be_removed_without_force(self):
        created = create_workspace(self.root, self.lease(), self.config, apply=True, now=self.now)
        complete_workspace(self.root, created["workspace_id"])
        preview = cleanup_workspace(self.root, created["workspace_id"], self.config)
        self.assertEqual(preview["result"], "DRY_RUN")
        cleaned = cleanup_workspace(self.root, created["workspace_id"], self.config, apply=True)
        self.assertEqual(cleaned["result"], "CLEANED")
        self.assertFalse((self.root / created["path"]).exists())
        self.assertEqual(read_registry(self.root)["workspaces"][0]["status"], "CLEANED")


if __name__ == "__main__":
    unittest.main()
