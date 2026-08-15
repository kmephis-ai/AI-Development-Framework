from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.decision_traceability import (  # noqa: E402
    project_traceability,
    seal_graph,
    validate_revision_transition,
    validate_traceability_graph,
)
from lib.evidence import append_evidence_event  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class DecisionTraceabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = load(ROOT / ".adwf/decision-requirement-traceability.json")
        self.schema = load(ROOT / ".adwf/schemas/decision-requirement-traceability.schema.json")

    def checked(self, graph: dict, *, root: Path = ROOT) -> dict:
        return validate_traceability_graph(graph, root=root, schema=self.schema)

    @staticmethod
    def record(record_id: str, kind: str, version: int, status: str) -> dict:
        return {
            "id": record_id,
            "kind": kind,
            "version": version,
            "status": status,
            "title_ru": "Тестовая долговечная запись",
            "statement_ru": "Эта запись существует только для adversarial проверки traceability semantics.",
            "source_path": None,
            "source_sha256": None,
            "record_sha256": "",
        }

    def evidence_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".adwf/schemas").mkdir(parents=True)
        for name in ("evidence-event.schema.json", "evidence-index.schema.json"):
            shutil.copy2(ROOT / ".adwf/schemas" / name, root / ".adwf/schemas" / name)
        shutil.copy2(ROOT / ".adwf/capability-traceability.json", root / ".adwf/capability-traceability.json")
        shutil.copy2(ROOT / ".adwf/roadmap.json", root / ".adwf/roadmap.json")
        audit = root / "AUDITS/ADWF_FOUNDATION_AUDIT_2026-08-15.md"
        audit.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "AUDITS/ADWF_FOUNDATION_AUDIT_2026-08-15.md", audit)
        return temp, root

    @staticmethod
    def append_real_evidence(root: Path, *, evidence_id: str, subject: str, sha: str, now: datetime) -> None:
        append_evidence_event(
            root,
            {
                "id": evidence_id,
                "kind": "CI",
                "status": "PASS",
                "subject": subject,
                "sha": sha,
                "source": "TRACE-001 focused test",
                "source_type": "LOCAL",
                "command": ["python", ".adwf/tests/test_decision_traceability.py"],
                "runner": "unittest",
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "content_sha256": hashlib.sha256(b"TRACE-001 evidence").hexdigest(),
                "artifact": None,
                "runtime_revision": None,
                "product_impact": False,
                "provenance": {
                    "provider": "local",
                    "source_identity": "focused-test",
                    "trust_domain": "adwf-trusted-runtime",
                    "repository": "kmephis-ai/AI-Development-Framework",
                    "workflow": "focused-test",
                    "invocation_id": "trace001-test",
                },
            },
            now=now,
        )

    def test_01_canonical_graph_is_valid_but_truthfully_incomplete(self) -> None:
        result = self.checked(self.graph)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["projection"]["status"], "INCOMPLETE")
        self.assertEqual(result["projection"]["missing_downstream_evidence"], ["WORKREF-TRACE-001"])

    def test_02_record_digest_tamper_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["records"][1]["statement_ru"] += " tamper"
        result = self.checked(graph)
        self.assertIn("TRACE_RECORD_DIGEST_MISMATCH:REQ-TRACE-001", result["errors"])

    def test_03_dangling_edge_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["to"] = "REQ-DOES-NOT-EXIST"
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertTrue(any(code.startswith("TRACE_EDGE_DANGLING_TO") for code in result["errors"]))

    def test_04_wrong_edge_endpoint_type_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["type"] = "WORK_TO_EVIDENCE"
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertTrue(any(code.startswith("TRACE_EDGE_ENDPOINT_TYPE_INVALID") for code in result["errors"]))

    def test_05_cycle_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["records"].extend([
            self.record("REQ-CYCLE-A", "REQUIREMENT", 2, "ACTIVE"),
            self.record("REQ-CYCLE-B", "REQUIREMENT", 3, "ACTIVE"),
        ])
        graph["edges"].extend([
            {"id": "EDGE-CYCLE-A-B", "type": "REQUIREMENT_SUPERSEDES_REQUIREMENT", "from": "REQ-CYCLE-B", "to": "REQ-CYCLE-A", "edge_sha256": ""},
            {"id": "EDGE-CYCLE-B-A", "type": "REQUIREMENT_SUPERSEDES_REQUIREMENT", "from": "REQ-CYCLE-A", "to": "REQ-CYCLE-B", "edge_sha256": ""},
        ])
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertTrue(any(code.startswith("TRACE_CYCLE") for code in result["errors"]))

    def test_06_duplicate_node_id_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        duplicate = copy.deepcopy(graph["records"][1])
        graph["records"].append(duplicate)
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_NODE_DUPLICATE:REQ-TRACE-001", result["errors"])

    def test_07_supersession_requires_increasing_version(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["records"].append(self.record("DEC-TRACE-001B", "DECISION", 1, "ACCEPTED"))
        graph["edges"].append({
            "id": "EDGE-DEC-SUPERSESSION-BAD", "type": "DECISION_SUPERSEDES_DECISION",
            "from": "DEC-TRACE-001B", "to": "DEC-TRACE-001", "edge_sha256": "",
        })
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_SUPERSESSION_VERSION_NOT_INCREASING:EDGE-DEC-SUPERSESSION-BAD", result["errors"])

    def test_08_ambiguous_supersession_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["records"].extend([
            self.record("DEC-TRACE-002", "DECISION", 2, "ACCEPTED"),
            self.record("DEC-TRACE-003", "DECISION", 3, "ACCEPTED"),
        ])
        graph["edges"].extend([
            {"id": "EDGE-DEC-SUPERSESSION-2", "type": "DECISION_SUPERSEDES_DECISION", "from": "DEC-TRACE-002", "to": "DEC-TRACE-001", "edge_sha256": ""},
            {"id": "EDGE-DEC-SUPERSESSION-3", "type": "DECISION_SUPERSEDES_DECISION", "from": "DEC-TRACE-003", "to": "DEC-TRACE-001", "edge_sha256": ""},
        ])
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_SUPERSESSION_AMBIGUOUS:DEC-TRACE-001", result["errors"])

    def test_09_unknown_capability_reference_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["capability_refs"][0]["capability_id"] = "UNKNOWN_CAPABILITY"
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_CAPABILITY_REF_UNKNOWN:CAPREF-DECISION_REQUIREMENT_TRACEABILITY", result["errors"])

    def test_10_unknown_roadmap_work_reference_fails_closed(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["work_unit_refs"][0]["roadmap_id"] = "TRACE-999"
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_WORK_REF_UNKNOWN_ROADMAP:WORKREF-TRACE-001", result["errors"])

    def test_11_owner_intent_source_digest_is_verified(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["records"][0]["source_sha256"] = "0" * 64
        graph = seal_graph(graph)
        result = self.checked(graph)
        self.assertIn("TRACE_INTENT_SOURCE_DIGEST_MISMATCH:INTENT-FOUNDATION-20260815", result["errors"])

    def test_12_existing_record_cannot_be_silently_rewritten(self) -> None:
        current = copy.deepcopy(self.graph)
        current["revision"] = 2
        current["records"][1]["statement_ru"] += " rewritten"
        current = seal_graph(current)
        errors = validate_revision_transition(self.graph, current)
        self.assertIn("TRACE_IMMUTABLE_ITEM_CHANGED:records:REQ-TRACE-001", errors)

    def test_13_existing_edge_cannot_be_silently_rewritten(self) -> None:
        current = copy.deepcopy(self.graph)
        current["revision"] = 2
        current["edges"][1]["id"] = "EDGE-REQ-DEC-TRACE-001-REPLACED"
        current = seal_graph(current)
        errors = validate_revision_transition(self.graph, current)
        self.assertIn("TRACE_IMMUTABLE_ITEM_REMOVED:edges:EDGE-REQ-DEC-TRACE-001", errors)

    def test_14_orphan_work_is_visible_in_projection(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if edge["type"] != "CAPABILITY_TO_WORK"]
        graph = seal_graph(graph)
        projection = project_traceability(graph, ROOT)
        self.assertEqual(projection["orphan_work_units"], ["WORKREF-TRACE-001"])
        self.assertEqual(projection["status"], "INCOMPLETE")

    def test_15_forged_evidence_binding_never_becomes_verified(self) -> None:
        temp, root = self.evidence_root()
        try:
            now = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
            sha = "1" * 40
            self.append_real_evidence(root, evidence_id="trace-evidence-0001", subject="TRACE-001", sha=sha, now=now)
            graph = copy.deepcopy(self.graph)
            graph["evidence_refs"].append({
                "id": "EVIDREF-TRACE-001", "evidence_id": "trace-evidence-0001",
                "subject": "FORGED-SUBJECT", "sha": sha, "ref_sha256": "",
            })
            graph["edges"].append({
                "id": "EDGE-WORK-EVIDENCE-TRACE-001", "type": "WORK_TO_EVIDENCE",
                "from": "WORKREF-TRACE-001", "to": "EVIDREF-TRACE-001", "edge_sha256": "",
            })
            graph = seal_graph(graph)
            projection = project_traceability(graph, root, now=now + timedelta(minutes=1))
            self.assertEqual(projection["status"], "STRUCTURED_NOT_VERIFIED")
            self.assertIn("TRACE_EVIDENCE_REF_BINDING_MISMATCH:EVIDREF-TRACE-001", projection["evidence_errors"])
        finally:
            temp.cleanup()

    def test_16_complete_chain_requires_real_append_only_evidence(self) -> None:
        temp, root = self.evidence_root()
        try:
            now = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
            sha = "2" * 40
            self.append_real_evidence(root, evidence_id="trace-evidence-0002", subject="TRACE-001", sha=sha, now=now)
            graph = copy.deepcopy(self.graph)
            graph["evidence_refs"].append({
                "id": "EVIDREF-TRACE-001", "evidence_id": "trace-evidence-0002",
                "subject": "TRACE-001", "sha": sha, "ref_sha256": "",
            })
            graph["edges"].append({
                "id": "EDGE-WORK-EVIDENCE-TRACE-001", "type": "WORK_TO_EVIDENCE",
                "from": "WORKREF-TRACE-001", "to": "EVIDREF-TRACE-001", "edge_sha256": "",
            })
            graph = seal_graph(graph)
            result = validate_traceability_graph(graph, root=root, schema=self.schema, now=now + timedelta(minutes=1))
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(result["projection"]["status"], "VERIFIED")
            self.assertEqual(result["projection"]["verified_evidence_refs"], ["EVIDREF-TRACE-001"])
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
