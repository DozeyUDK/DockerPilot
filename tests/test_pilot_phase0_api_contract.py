"""Static compatibility gate for the pilot.py modularization.

The fixture was captured from Phase 0 before implementation extraction. The
facade is intentionally kept API-compatible while logic moves to services.
"""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "src/dockerpilot/pilot.py"
BASELINE = ROOT / "tests/fixtures/pilot_phase0_api.json"


def _default(node):
    return ast.unparse(node) if node is not None else None


def _facade_signatures():
    tree = ast.parse(PILOT.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DockerPilotEnhanced"
    )
    out = {}
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        positional = [arg.arg for arg in [*args.posonlyargs, *args.args]]
        defaults = [None] * (len(positional) - len(args.defaults)) + [
            _default(default) for default in args.defaults
        ]
        out[node.name] = {
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
    return out, cls


def test_pilot_facade_exact_phase0_signatures_are_preserved():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current, _ = _facade_signatures()
    assert current == baseline


def test_pilot_facade_stays_below_regrowth_budget():
    _current, cls = _facade_signatures()
    class_lines = cls.end_lineno - cls.lineno + 1
    assert class_lines <= 650, f"DockerPilotEnhanced regrew to {class_lines} lines"
