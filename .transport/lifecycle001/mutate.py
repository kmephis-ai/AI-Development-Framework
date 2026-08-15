from pathlib import Path
import json
import sys


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


caps = load(".adwf/capabilities.json")
if any(x.get("id") == "managed_surface_contract" for x in caps.get("capabilities", [])):
    raise SystemExit("managed_surface_contract already exists")
caps["capabilities"].append(
    {
        "id": "managed_surface_contract",
        "title": "Consumer managed-surface ownership and safe detach planning",
        "implementation_status": "IMPLEMENTED",
        "cost_status": "FREE_VERIFIED",
        "mandatory": True,
        "requires_paid_ai_api": False,
        "acceptance_evidence": [".adwf/tests/test_managed_surface.py"],
    }
)
write(".adwf/capabilities.json", caps)

truth = load(".adwf/capability-traceability.json")
if any(x.get("id") == "MANAGED_SURFACE_CONTRACT" for x in truth.get("capabilities", [])):
    raise SystemExit("MANAGED_SURFACE_CONTRACT already exists")
truth["capabilities"].append(
    {
        "id": "MANAGED_SURFACE_CONTRACT",
        "status": "LIVE_NOT_VERIFIED",
        "execution_mode": "CORE",
        "owner_claim_ru": (
            "ADWF отличает framework-private, shared-guarded и consumer-owned поверхности "
            "и строит fail-closed adoption/detach plan без удаления пользовательских файлов."
        ),
        "entrypoints": [".adwf/scripts/validate_managed_surface.py"],
        "production_paths": [
            ".adwf/lib/managed_surface.py",
            ".adwf/managed-surface-policy.json",
            "MANIFEST.json",
            "SHA256SUMS.txt",
        ],
        "verification": [".adwf/tests/test_managed_surface.py"],
        "live_boundary": (
            "LIVE_VERIFIED требует evidence от реального consumer repository: adoption snapshot "
            "и detach/recovery outcome с доказательством сохранности consumer-owned data; "
            "unit/CI tests подтверждают implementation, но не live consumer truth."
        ),
        "live_evidence": [],
    }
)
write(".adwf/capability-traceability.json", truth)

roadmap = load(".adwf/roadmap.json")
if any(
    task.get("roadmap_id") == "LIFECYCLE-001"
    for goal in roadmap.get("goals", [])
    for task in goal.get("tasks", [])
):
    raise SystemExit("LIFECYCLE-001 already exists")
goal = next((g for g in roadmap["goals"] if g.get("id") == "FOUNDATION-ENGINEERING-OS"), None)
if goal is None:
    raise SystemExit("FOUNDATION-ENGINEERING-OS missing")
goal["tasks"].append(
    {
        "roadmap_id": "LIFECYCLE-001",
        "title_ru": "Managed Surface Contract v1 — project must outlive framework",
        "dependencies": ["TRACE-001"],
        "product_impact": False,
    }
)
write(".adwf/roadmap.json", roadmap)

graph = load(".adwf/decision-requirement-traceability.json")
if graph.get("revision") != 1:
    raise SystemExit("unexpected trace revision")
graph["revision"] = 2
graph["records"].extend(
    [
        {
            "id": "REQ-LIFECYCLE-001",
            "kind": "REQUIREMENT",
            "version": 1,
            "status": "ACTIVE",
            "title_ru": "Consumer project должен переживать ADWF",
            "statement_ru": (
                "ADWF обязан отличать свои managed surfaces от shared и consumer-owned файлов; "
                "adoption/update/detach не могут молча перезаписывать или удалять неизвестные, "
                "изменённые или пользовательские данные."
            ),
            "source_path": None,
            "source_sha256": None,
            "record_sha256": "",
        },
        {
            "id": "DEC-LIFECYCLE-001",
            "kind": "DECISION",
            "version": 1,
            "status": "ACCEPTED",
            "title_ru": "Package inventory остаётся SSOT, consumer ownership задаётся отдельным тонким contract",
            "statement_ru": (
                "Переиспользовать MANIFEST.json/SHA256SUMS.txt как единственный inventory release-файлов "
                "и добавить только ownership policy + read-only adoption/snapshot/detach semantics; "
                "destructive apply не входит в v1."
            ),
            "source_path": None,
            "source_sha256": None,
            "record_sha256": "",
        },
    ]
)
graph["capability_refs"].append(
    {
        "id": "CAPREF-MANAGED-SURFACE-CONTRACT",
        "capability_id": "MANAGED_SURFACE_CONTRACT",
        "ref_sha256": "",
    }
)
graph["work_unit_refs"].append(
    {
        "id": "WORKREF-LIFECYCLE-001",
        "roadmap_id": "LIFECYCLE-001",
        "issue_number": 47,
        "ai_work_package_id": None,
        "ref_sha256": "",
    }
)
graph["edges"].extend(
    [
        {"id": "EDGE-INTENT-REQ-LIFECYCLE-001", "type": "INTENT_TO_REQUIREMENT", "from": "INTENT-FOUNDATION-20260815", "to": "REQ-LIFECYCLE-001", "edge_sha256": ""},
        {"id": "EDGE-REQ-DEC-LIFECYCLE-001", "type": "REQUIREMENT_TO_DECISION", "from": "REQ-LIFECYCLE-001", "to": "DEC-LIFECYCLE-001", "edge_sha256": ""},
        {"id": "EDGE-DEC-CAP-LIFECYCLE-001", "type": "DECISION_TO_CAPABILITY", "from": "DEC-LIFECYCLE-001", "to": "CAPREF-MANAGED-SURFACE-CONTRACT", "edge_sha256": ""},
        {"id": "EDGE-CAP-WORK-LIFECYCLE-001", "type": "CAPABILITY_TO_WORK", "from": "CAPREF-MANAGED-SURFACE-CONTRACT", "to": "WORKREF-LIFECYCLE-001", "edge_sha256": ""},
    ]
)
sys.path.insert(0, str(Path(".adwf").resolve()))
from lib.decision_traceability import seal_graph
write(".adwf/decision-requirement-traceability.json", seal_graph(graph))

cap_validator = Path(".adwf/scripts/validate_capabilities.py")
text = cap_validator.read_text(encoding="utf-8")
old = '        "DELIVERY_OBSERVATION", "WINDOWS_HOSTED_SMOKE",\n'
new = '        "DELIVERY_OBSERVATION", "WINDOWS_HOSTED_SMOKE", "MANAGED_SURFACE_CONTRACT",\n'
if old not in text:
    raise SystemExit("validate_capabilities insertion point missing")
cap_validator.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

framework = Path(".adwf/scripts/validate_framework.py")
text = framework.read_text(encoding="utf-8")
old = (
    '        ".adwf/lib/decision_traceability.py", '
    '".adwf/schemas/decision-requirement-traceability.schema.json", '
    '".adwf/decision-requirement-traceability.json", '
    '".adwf/scripts/validate_traceability.py",\n'
)
new = old + (
    '        ".adwf/lib/managed_surface.py", ".adwf/managed-surface-policy.json", '
    '".adwf/schemas/managed-surface-policy.schema.json", '
    '".adwf/schemas/managed-surface-snapshot.schema.json", '
    '".adwf/schemas/managed-surface-plan.schema.json", '
    '".adwf/scripts/validate_managed_surface.py",\n'
)
if old not in text:
    raise SystemExit("validate_framework required insertion point missing")
text = text.replace(old, new, 1)
old_pair = (
    '        (".adwf/decision-requirement-traceability.json", '
    '".adwf/schemas/decision-requirement-traceability.schema.json"),\n'
)
new_pair = old_pair + (
    '        (".adwf/managed-surface-policy.json", '
    '".adwf/schemas/managed-surface-policy.schema.json"),\n'
)
if old_pair not in text:
    raise SystemExit("validate_framework pair insertion point missing")
text = text.replace(old_pair, new_pair, 1)
old_loop = '"validate_capabilities.py", "validate_traceability.py", "validate_skills.py")'
new_loop = '"validate_capabilities.py", "validate_traceability.py", "validate_managed_surface.py", "validate_skills.py")'
if old_loop not in text:
    raise SystemExit("validate_framework loop insertion point missing")
framework.write_text(text.replace(old_loop, new_loop, 1), encoding="utf-8", newline="\n")
