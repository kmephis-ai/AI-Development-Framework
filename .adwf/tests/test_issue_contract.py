import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.issue_contract import parse_issue_form, parse_issue_marker, parse_pr_contract, validate_one_to_one

BODY = """## Контракт
Roadmap-ID: RM-42
Issue: #17
Writer-Lease: 123e4567-e89b-12d3-a456-426614174000

## Что изменено и зачем
Причина.

## Scope
See also #999 in prose.

## Проверки
PASS.

## Risk / rollback
Risk: R1

## Trust boundary
Не ослаблена.
"""


class IssueContractTests(unittest.TestCase):
    def test_arbitrary_hash_number_is_not_linked_issue(self):
        result = parse_pr_contract(BODY)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["issue_number"], 17)

    def test_duplicate_structured_issue_blocks(self):
        result = parse_pr_contract(BODY + "\nIssue: #18\n")
        self.assertFalse(result["valid"])

    def test_issue_lease_and_roadmap_must_match(self):
        contract = parse_pr_contract(BODY)
        issue = {"number": 17, "roadmap_id": "RM-42", "state": "IN_PROGRESS", "lease_id": "wrong"}
        self.assertIn("LEASE_MISMATCH", validate_one_to_one(contract, issue))

    def test_issue_form_becomes_strict_provider_neutral_item(self):
        body = """### Roadmap ID
RM-42
### Цель
Получить один проверяемый результат
### Зачем это владельцу или продукту
Показать правдивый итог владельцу
### Что входит в работу
Один contract
### Что точно не входит
Deployment
### Критерии приёмки
- Contract PASS
### План проверки и evidence
- Запустить unit test
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
42
### Риск
R1
### Влияет на реальный продукт
NO
### Требуется решение владельца
NO
"""
        body += "\n<!-- ADWF-CONTRACT Roadmap-ID: RM-42 Writer: reviewer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-42-issue-17 State: IN_PROGRESS Heartbeat: 2099-08-13T09:30:00Z Expires: 2099-08-13T10:00:00Z -->\n"
        issue, errors = parse_issue_form(body, number=17, title="[RM-42] Проверить contract", state="READY")
        self.assertEqual(errors, [])
        self.assertEqual(issue["roadmap_id"], "RM-42")
        self.assertTrue(issue["dependencies_resolved"])
        self.assertTrue(issue["autonomy_allowed"])

    def test_issue_form_missing_field_fails_closed(self):
        issue, errors = parse_issue_form("### Roadmap ID\nRM-42\n", number=17, title="[RM-42] Incomplete", state="READY")
        self.assertTrue(errors)
        self.assertFalse(issue["dependencies_resolved"])

    def test_issue_marker_rejects_non_uuid_lease(self):
        body = "<!-- ADWF-CONTRACT Roadmap-ID: RM-42 Writer: writer-1 Writer-Lease: 00000000-0000-0000-0000-000000000000 Workspace: rm-42-issue-17 State: IN_PROGRESS Heartbeat: 2026-08-13T09:30:00Z Expires: 2026-08-13T10:00:00Z -->"
        result = parse_issue_marker(body)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
