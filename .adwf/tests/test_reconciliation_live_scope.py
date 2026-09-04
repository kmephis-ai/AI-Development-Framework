import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.reconciliation import reconcile_snapshot


class ReconciliationLiveScopeTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads((ROOT / ".adwf/project-state.json").read_text(encoding="utf-8"))
        self.config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
        self.config["provider"]["mode"] = "github"
        self.now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        self.ready = {
            "number": 7,
            "title": "[RM-7] Проверить snapshot",
            "body": self.issue_body(),
            "labels": [{"name": "roadmap:ready"}],
            "state": "open",
            "updated_at": "2026-08-13T09:00:00Z",
        }

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

    def reconcile(self, issues):
        return reconcile_snapshot(
            self.state,
            self.config,
            provider="github",
            main_sha="a" * 40,
            issues=issues,
            pulls=[],
            runs=[],
            cost={"result": "ALLOW", "provider": "github_self_hosted"},
            workspace_registry={"schema_version": 1, "workspaces": []},
            now=self.now,
        )

    def test_open_roadmap_form_without_machine_state_label_is_planning_only(self):
        planning = copy.deepcopy(self.ready)
        planning["labels"] = []
        result = self.reconcile([planning, self.ready])
        self.assertEqual(result["health"]["adwf"], "VERIFIED")
        self.assertEqual(result["queue"]["ready"], 1)
        self.assertEqual([item["number"] for item in result["work_items"]], [7])

    def test_closed_historical_issue_without_done_evidence_is_not_queue_authority(self):
        historical = copy.deepcopy(self.ready)
        historical["number"] = 8
        historical["labels"] = []
        historical["state"] = "closed"
        historical["body"] += (
            "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-8 Writer: writer-1 "
            "Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 "
            "Workspace: rm-8-issue-8 State: REVIEW Heartbeat: 2026-08-13T09:45:00Z "
            "Expires: 2026-08-13T11:00:00Z -->\n"
        )
        result = self.reconcile([historical, self.ready])
        self.assertEqual(result["health"]["adwf"], "VERIFIED")
        self.assertFalse(any(item["number"] == 8 for item in result["work_items"]))

    def test_closed_issue_with_live_machine_state_label_still_fails_closed(self):
        stale_active = copy.deepcopy(self.ready)
        stale_active["state"] = "closed"
        result = self.reconcile([stale_active])
        self.assertEqual(result["health"]["adwf"], "BROKEN")
        self.assertTrue(
            any("CLOSED_ACTIVE_ISSUE_WITHOUT_DONE_EVIDENCE:7" in item for item in result["blockers"])
        )


if __name__ == "__main__":
    unittest.main()
