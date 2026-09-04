import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.reconciliation import _cost_status, reconcile_snapshot


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((ROOT / ".adwf/project-state.json").read_text(encoding="utf-8"))
        self.config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
        self.config["provider"]["mode"] = "github"
        self.now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        self.issue = {"number": 7, "title": "[RM-7] Проверить snapshot", "body": self.issue_body(),
                      "labels": [{"name": "roadmap:ready"}], "updated_at": "2026-08-13T09:00:00Z"}

    @staticmethod
    def issue_body() -> str:
        return """### Roadmap ID

RM-7

### Цель

Получить проверяемый snapshot проекта

### Зачем это владельцу или продукту

Владелец видит правдивое состояние

### Что входит в работу

Только snapshot

### Что точно не входит

Deployment

### Критерии приёмки

- Snapshot имеет exact SHA

### План проверки и evidence

- Запустить contract suite

### Зависимости

NONE

### Зависимости проверены

YES

### Контур конфликта

control-plane

### Тип работы

verification

### Приоритет

P1

### Порядок в Roadmap

7

### Риск

R1

### Влияет на реальный продукт

NO

### Требуется решение владельца

NO
"""

    def test_fresh_provider_facts_create_exact_sha_snapshot(self):
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[self.issue], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["snapshot"]["source_main_sha"], "a" * 40)
        self.assertEqual(result["queue"]["ready"], 1)
        self.assertEqual(result["cost_usage"]["status"], "ALLOW_ZERO_COST")
        self.assertEqual(result["health"]["adwf"], "VERIFIED")

    def test_duplicate_roadmap_or_provider_mismatch_forces_recovery(self):
        duplicate = copy.deepcopy(self.issue); duplicate["number"] = 8
        result = reconcile_snapshot(self.state, self.config, provider="gitlab", main_sha="b" * 40,
                                    issues=[self.issue, duplicate], pulls=[], runs=[], cost={"result": "BLOCK", "provider": "mystery", "reason_codes": ["UNKNOWN_PROVIDER"]},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["status"], "RECOVERY")
        self.assertEqual(result["health"]["adwf"], "BROKEN")
        self.assertTrue(any("ROADMAP_ID_NOT_ONE_TO_ONE" in item for item in result["blockers"]))

    def test_short_main_revision_is_never_verified(self):
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="abc",
                                    issues=[self.issue], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["health"]["adwf"], "BROKEN")
        self.assertTrue(any("MAIN_SHA_NOT_EXACT" in item for item in result["blockers"]))

    def test_cost_projection_distinguishes_quota_and_paid_blocks(self):
        self.assertEqual(_cost_status({"result": "BLOCK", "reason_codes": ["QUOTA_NOT_VERIFIED"]}), "BLOCK_QUOTA")
        self.assertEqual(_cost_status({"result": "BLOCK", "classification": "PAID", "reason_codes": []}), "BLOCK_PAID")

    def test_ready_label_cannot_replace_complete_issue_contract(self):
        invalid = {"number": 7, "title": "[RM-7] Incomplete", "body": "### Roadmap ID\n\nRM-7\n",
                   "labels": [{"name": "roadmap:ready"}], "updated_at": "2026-08-13T09:00:00Z"}
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[invalid], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["health"]["adwf"], "BROKEN")
        self.assertTrue(any("ISSUE_CONTRACT_INVALID" in item for item in result["blockers"]))

    def test_declared_dependency_must_exist_as_done_item(self):
        issue = copy.deepcopy(self.issue)
        issue["body"] = issue["body"].replace("### Зависимости\n\nNONE", "### Зависимости\n\nRM-1")
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[issue], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertTrue(any("DEPENDENCY_STATUS_MISMATCH" in item for item in result["blockers"]))

    def test_unrelated_closed_done_history_cannot_poison_live_reconciliation(self):
        historical = {
            "number": 99,
            "title": "[OLD-1] Historical governance item",
            "state": "closed",
            "labels": [],
            "updated_at": "2026-08-01T09:00:00Z",
            "body": "### Roadmap ID\n\nOLD-1\n\n<!-- ADWF-CONTRACT Roadmap-ID: OLD-1 Writer: old-writer Writer-Lease: 123e4567-e89b-12d3-a456-426614174099 Workspace: old-1 State: DONE -->\n",
        }
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[historical], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["health"]["adwf"], "VERIFIED")
        self.assertEqual(result["work_items"], [])
        self.assertEqual(result["queue"]["ready"], 0)

    def test_live_dependency_can_use_closed_done_terminal_certificate(self):
        dependency = copy.deepcopy(self.issue)
        dependency["number"] = 6
        dependency["title"] = "[RM-1] Завершённая зависимость"
        dependency["state"] = "closed"
        dependency["labels"] = []
        dependency["body"] = dependency["body"].replace("RM-7", "RM-1")
        dependency["body"] += (
            "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-1 Writer: old-writer "
            "Writer-Lease: 123e4567-e89b-12d3-a456-426614174098 Workspace: rm-1 State: DONE "
            "Heartbeat: 2026-08-01T09:00:00Z Expires: 2026-08-01T09:00:00Z -->\n"
        )
        live = copy.deepcopy(self.issue)
        live["body"] = live["body"].replace("### Зависимости\n\nNONE", "### Зависимости\n\nRM-1")
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[dependency, live], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["health"]["adwf"], "VERIFIED")
        by_id = {item["roadmap_id"]: item for item in result["work_items"]}
        self.assertEqual(by_id["RM-1"]["state"], "DONE")
        self.assertEqual(by_id["RM-1"]["dependencies"], [])
        self.assertTrue(by_id["RM-7"]["dependencies_resolved"])

    def test_review_marker_also_requires_fresh_lease_heartbeat(self):
        issue = copy.deepcopy(self.issue)
        issue["labels"] = [{"name": "roadmap:review"}]
        issue["body"] += "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-7 Writer: writer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-7-issue-7 State: REVIEW Heartbeat: 2026-08-13T08:00:00Z Expires: 2026-08-13T11:00:00Z -->\n"
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[issue], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertTrue(any("ACTIVE_ISSUE_LEASE_STALE" in item for item in result["blockers"]))

    def test_multiple_active_items_force_recovery(self):
        first = copy.deepcopy(self.issue)
        first["labels"] = [{"name": "roadmap:review"}]
        first["body"] += "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-7 Writer: writer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-7-issue-7 State: REVIEW Heartbeat: 2026-08-13T09:45:00Z Expires: 2026-08-13T11:00:00Z -->\n"
        second = copy.deepcopy(first)
        second["number"] = 8
        second["title"] = "[RM-8] Проверить второй snapshot"
        second["body"] = second["body"].replace("RM-7", "RM-8").replace("issue-7", "issue-8").replace(
            "123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174001")
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[first, second], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertTrue(any("MULTIPLE_ACTIVE_ITEMS" in item for item in result["blockers"]))

    def test_active_github_item_resets_project_projection_until_readback(self):
        issue = copy.deepcopy(self.issue)
        issue["labels"] = [{"name": "roadmap:review"}]
        issue["body"] += "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-7 Writer: writer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-7-issue-7 State: REVIEW Heartbeat: 2026-08-13T09:45:00Z Expires: 2026-08-13T11:00:00Z -->\n"
        self.config["github"]["project"].update({"enabled": True, "owner": "owner", "number": 1, "dashboard_issue_number": 2})
        result = reconcile_snapshot(self.state, self.config, provider="github", main_sha="a" * 40,
                                    issues=[issue], pulls=[], runs=[], cost={"result": "ALLOW", "provider": "github_self_hosted"},
                                    workspace_registry={"schema_version": 1, "workspaces": []}, now=self.now)
        self.assertEqual(result["project_projection"]["status"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
