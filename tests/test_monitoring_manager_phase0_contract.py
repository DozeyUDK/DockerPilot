"""Phase 0 compatibility contract for MonitoringManager."""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/dockerpilot/monitoring.py"
FIXTURE = ROOT / "tests/fixtures/monitoring_manager_phase0_api.json"


def _manager_class():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MonitoringManager"
    )


def _signature(node: ast.FunctionDef) -> str:
    return ast.unparse(node.args)


def test_monitoring_manager_phase0_api_contract():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manager = _manager_class()
    methods = [node for node in manager.body if isinstance(node, ast.FunctionDef)]

    actual = {node.name: _signature(node) for node in methods}
    expected_methods = {
        item["name"]: item["signature"] for item in expected["methods"]
    }

    assert len(methods) == expected["baseline"]["method_count"]
    assert actual == expected_methods


def test_monitoring_manager_no_regrowth_gate():
    manager = _manager_class()
    class_lines = manager.end_lineno - manager.lineno + 1
    file_lines = len(SOURCE.read_text(encoding="utf-8").splitlines())

    # Phase 0 baseline is intentionally loose during extraction, but prevents
    # the facade from growing beyond the original monolith.
    assert class_lines <= 358
    assert file_lines <= 374
