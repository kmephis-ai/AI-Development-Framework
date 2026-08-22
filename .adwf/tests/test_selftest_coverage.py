from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent


def _module_inventory(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top_level_tests = sorted(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )
    class_test_methods = sorted(
        f"{node.name}.{member.name}"
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test_")
    )
    return {
        "path": path.name,
        "top_level_tests": top_level_tests,
        "class_test_methods": class_test_methods,
    }


def _coverage_inventory(root: Path) -> dict:
    modules = [_module_inventory(path) for path in sorted(root.rglob("test_*.py"))]
    unsupported = [
        f"{module['path']}::{name}"
        for module in modules
        for name in module["top_level_tests"]
    ]
    return {
        "schema_version": 1,
        "test_files_scanned": len(modules),
        "unittest_style_methods": sum(len(module["class_test_methods"]) for module in modules),
        "unsupported_top_level_tests": unsupported,
    }


class SelfTestCoverageTests(unittest.TestCase):
    def test_no_unittest_invisible_top_level_tests(self) -> None:
        inventory = _coverage_inventory(TESTS_ROOT)
        marker = "ADWF_SELFTEST_COVERAGE=" + json.dumps(
            inventory,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual([], inventory["unsupported_top_level_tests"], marker)
        print(marker)

    def test_pytest_style_top_level_test_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_hidden_failure.py"
            path.write_text(
                "def test_should_never_be_silently_skipped():\n"
                "    assert False\n",
                encoding="utf-8",
            )
            inventory = _coverage_inventory(Path(tmp))
        self.assertEqual(
            ["test_hidden_failure.py::test_should_never_be_silently_skipped"],
            inventory["unsupported_top_level_tests"],
        )


if __name__ == "__main__":
    unittest.main()
