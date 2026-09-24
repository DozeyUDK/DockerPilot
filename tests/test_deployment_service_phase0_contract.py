"""Compatibility gate for DeploymentServiceMixin modularization.

The fixture is a Phase 0 snapshot captured before orchestration extraction.
Refactors may move implementation out of deployment_service.py, but must keep
this compatibility surface stable unless an API change is intentional.
"""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "dockerpilot" / "deployment_service.py"
BASELINE = ROOT / "tests" / "fixtures" / "deployment_service_phase0_api.json"


def _default(node):
    return ast.unparse(node) if node is not None else None


def _current_contract():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DeploymentServiceMixin"
    )
    methods = {}
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        positional = [arg.arg for arg in [*args.posonlyargs, *args.args]]
        defaults = [None] * (len(positional) - len(args.defaults)) + [
            _default(default) for default in args.defaults
        ]
        methods[node.name] = {
            "positional": positional,
            "defaults": defaults,
            "vararg": args.vararg.arg if args.vararg else None,
            "kwonly": {
                arg.arg: _default(default)
                for arg, default in zip(args.kwonlyargs, args.kw_defaults)
            },
            "kwarg": args.kwarg.arg if args.kwarg else None,
            "returns": _default(node.returns),
        }
    return methods, cls


def test_deployment_service_preserves_phase0_signatures():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current, _cls = _current_contract()
    assert current == baseline["methods"]


def test_deployment_service_does_not_regrow_past_phase0():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    _methods, cls = _current_contract()
    class_lines = cls.end_lineno - cls.lineno + 1
    assert class_lines <= baseline["class_lines"], (
        f"DeploymentServiceMixin regrew to {class_lines} lines; "
        f"Phase 0 baseline is {baseline['class_lines']}"
    )
