import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.dashboard import overall_message, render_executive_html


class DashboardTests(unittest.TestCase):
    def test_unknown_security_never_becomes_green_overall(self):
        health = {"categories": {name: {"status": "VERIFIED"} for name in (
            "package_integrity", "config_health", "control_plane_health", "product_health")}}
        state = {"health": {"roadmap": "HEALTHY", "architecture": "HEALTHY", "security": "NOT_VERIFIED", "debt": "HEALTHY"}}
        self.assertIn("ОГРАНИЧЕННЫЙ", overall_message(state, health, {"result": "ALLOW"}))
        state["health"]["security"] = "HEALTHY"
        self.assertIn("МОЖНО ПРОДОЛЖАТЬ", overall_message(state, health, {"result": "ALLOW"}))

    def test_executive_html_is_escaped_and_separates_owner_from_machine(self):
        state = {
            "health": {}, "progress": {}, "queue": {}, "orchestration": {}, "active": {},
            "provider": {}, "snapshot": {}, "workspace": {}, "ci_metrics": {}, "cost_usage": {},
            "owner_decisions": [], "blockers": ["<script>alert(1)</script>"],
            "owner_experience": {}, "incident_knowledge": {}, "safe_healing": {}, "main": {}, "gates": {},
        }
        health = {"categories": {name: {"status": "NOT_VERIFIED"} for name in (
            "package_integrity", "config_health", "control_plane_health", "product_health",
        )}}
        result = render_executive_html(state, health, {"result": "ALLOW", "provider": "local"})
        self.assertIn("Проверка автоматикой", result)
        self.assertIn("Решение владельца", result)
        self.assertNotIn("<script>alert(1)</script>", result)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", result)


if __name__ == "__main__":
    unittest.main()
