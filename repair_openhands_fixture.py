#!/usr/bin/env python3
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    source = (root.parent / "source") if root.name != "source" else root
    path = source / ".adwf/tests/test_creative_agent_qualification.py"
    text = path.read_text(encoding="utf-8")
    old = '''            target.write_bytes((ROOT / rel).read_bytes())\n        return root\n\n    def test_timeout_nonzero_and_missing_result_fail_closed(self):\n'''
    new = '''            target.write_bytes((ROOT / rel).read_bytes())\n        # Keep this fixture intentionally minimal. Production now has multiple\n        # qualified adapters; copying the whole production registry would make\n        # an unrelated adapter's command/report files mandatory in this isolated\n        # reference-adapter timeout/nonzero/missing-result test.\n        registry_path = root / ".adwf/creative-agent-adapters.json"\n        registry = json.loads(registry_path.read_text(encoding="utf-8"))\n        registry["adapters"] = [\n            item for item in registry.get("adapters") or []\n            if item.get("id") == "reference-local"\n        ]\n        if len(registry["adapters"]) != 1:\n            raise AssertionError("minimal reference adapter fixture identity")\n        registry_path.write_text(\n            json.dumps(seal_registry(registry), ensure_ascii=False, indent=2) + "\\n",\n            encoding="utf-8",\n        )\n        return root\n\n    def test_timeout_nonzero_and_missing_result_fail_closed(self):\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"OPENHANDS_FIXTURE_PATCH_CONTEXT_MISMATCH:count={count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("OPENHANDS_FIXTURE_PATCH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
