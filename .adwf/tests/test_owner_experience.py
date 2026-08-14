import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.dashboard import render_dashboard
from lib.assurance import snapshot_digest
from lib.owner_experience import (
    ceo_control_center_projection,
    compute_preview_digest,
    create_preview,
    create_product_brief,
    evaluate_owner_acceptance,
    record_owner_acceptance,
    render_human_changelog,
)


class OwnerExperienceTests(unittest.TestCase):
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    def brief(self):
        return create_product_brief({
            "goal_ru": "Добавить понятную регистрацию для owner@example.org.",
            "value_ru": "Новый клиент начинает работу без технической помощи.",
            "outcome_ru": "Форма показывает подтверждение и отправляет письмо клиенту.",
            "acceptance_criteria_ru": ["Форма открывается", "Письмо отправляется"],
            "visual_expectation_ru": "Владелец видит готовую страницу.",
            "constraints_ru": ["token=abcdefghijklmnopqrstuvwxyz не сохранять"],
            "owner_request_original": "Сделайте регистрацию для owner@example.org.",
        }, now=self.now)

    def preview(self):
        return create_preview(
            head_sha="c" * 40,
            preview_digest="d" * 64,
            created_at="2026-08-13T12:30:00Z",
            url="https://preview.local/?token=abcdefghijklmnopqrstuvwxyz",
            screenshots=[{"name": "desktop", "digest": "e" * 64, "artifact": "preview/desktop.png"}],
        )

    def acceptance(self, brief, preview):
        return record_owner_acceptance(
            brief_id=brief["brief_id"], decision="ACCEPTED",
            head_sha=preview["head_sha"], preview_digest=preview["preview_digest"],
            decided_by="owner@example.org", note_ru="Готово, token=abcdefghijklmnop скрыть.",
            nonce="0123456789abcdef", policy_hash="f" * 64,
            source="LOCAL_AUTHENTICATED", now=self.now,
        )

    def test_brief_preview_and_acceptance_are_sanitized_and_exact(self):
        brief = self.brief()
        preview = self.preview()
        acceptance = self.acceptance(brief, preview)
        self.assertIn("[REDACTED_EMAIL]", brief["goal_ru"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", " ".join(brief["constraints_ru"]))
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", preview["url"])
        self.assertEqual(acceptance["decided_by"], "[REDACTED_EMAIL]")
        self.assertNotIn("abcdefghijklmnop", acceptance["note_ru"])
        self.assertEqual(compute_preview_digest(b"immutable preview"), compute_preview_digest(b"immutable preview"))

    def test_new_sha_or_preview_digest_makes_acceptance_stale(self):
        brief = self.brief()
        preview = self.preview()
        acceptance = self.acceptance(brief, preview)
        fresh = evaluate_owner_acceptance(
            acceptance, current_head_sha="c" * 40, current_preview_digest="d" * 64,
        )
        stale_sha = evaluate_owner_acceptance(
            acceptance, current_head_sha="a" * 40, current_preview_digest="d" * 64,
        )
        stale_preview = evaluate_owner_acceptance(
            acceptance, current_head_sha="c" * 40, current_preview_digest="b" * 64,
        )
        self.assertEqual(fresh["status"], "ACCEPTED")
        self.assertEqual(stale_sha["status"], "STALE")
        self.assertIn("HEAD_SHA_CHANGED", stale_sha["stale_reason"])
        self.assertEqual(stale_preview["status"], "STALE")

    def test_ceo_view_separates_machine_verification_from_owner_decision(self):
        brief = self.brief()
        preview = self.preview()
        acceptance = self.acceptance(brief, preview)
        state = {
            "main": {"head": "c" * 40},
            "gates": {"ci": "PASS", "review": "PASS", "docs": "PASS"},
            "health": {"product": "VERIFIED"},
            "owner_experience": {"current_preview": preview, "acceptance": acceptance, "release_summary_ru": "Добавлена регистрация."},
            "incident_knowledge": {"open_count": 0, "repeated_count": 0},
            "safe_healing": {"status": "IDLE", "level": None, "circuit_open": False},
        }
        assurance = {
            "schema_version": 1, "subject_sha": "c" * 40, "policy_hash": "f" * 64,
            "verified_at": "2026-08-13T12:00:00Z", "expires_at": "2026-09-13T12:00:00Z",
            "health": {"package_integrity": "VERIFIED", "config_health": "VERIFIED", "control_plane_health": "VERIFIED", "product_health": "VERIFIED"},
            "required_gates": ["ci", "review", "docs"], "gates": {"ci": "PASS", "review": "PASS", "docs": "PASS"},
            "evidence": {"refs_resolved": True}, "provider": {"readback_verified": True},
            "cost": {"status": "VERIFIED_ZERO", "projected_cost_usd": 0},
        }
        assurance["snapshot_digest"] = snapshot_digest(assurance)
        state["assurance_snapshot"] = assurance
        view = ceo_control_center_projection(state)
        self.assertEqual(view["machine_verified"], "VERIFIED")
        self.assertEqual(view["owner_acceptance"], "ACCEPTED")
        changed = copy.deepcopy(state)
        changed["main"]["head"] = "a" * 40
        stale = ceo_control_center_projection(changed)
        self.assertEqual(stale["machine_verified"], "STALE")
        self.assertEqual(stale["owner_acceptance"], "STALE")
        self.assertEqual(stale["preview_status"], "STALE")

    def test_deterministic_changelog_is_human_readable_without_ai(self):
        rendered = render_human_changelog([{
            "summary_ru": "Добавлена форма регистрации.",
            "verification_ru": "Проверена отправка письма.",
            "value_ru": "Клиент начинает работу самостоятельно.",
        }])
        self.assertIn("Что изменилось", rendered)
        self.assertIn("Что проверено", rendered)
        self.assertIn("Нужно ли ваше действие", rendered)
        self.assertIn("Добавлена форма регистрации", rendered)

    def test_main_dashboard_contains_owner_product_projection(self):
        state = {
            "owner_experience": {}, "health": {}, "progress": {}, "queue": {},
            "orchestration": {}, "active": {}, "provider": {}, "snapshot": {},
            "workspace": {}, "ci_metrics": {}, "cost_usage": {}, "blockers": [],
            "owner_decisions": [],
        }
        health = {"categories": {}}
        rendered = render_dashboard(state, health, {"result": "NOT_VERIFIED", "reason_codes": []})
        self.assertIn("Продукт для владельца", rendered)
        self.assertIn("Решение владельца", rendered)
        self.assertIn("Самовосстановление", rendered)


if __name__ == "__main__":
    unittest.main()
