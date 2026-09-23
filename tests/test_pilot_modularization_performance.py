"""Low-noise performance gates for the pilot.py modularization.

These benchmarks target CPU-only helpers/delegates affected by the refactor.
The budgets are intentionally generous enough for shared CI while catching
accidental O(n^2) work, sleeps, I/O or heavy object construction in hot paths.
"""

from time import perf_counter

from dockerpilot.pilot import DockerPilotEnhanced
from dockerpilot.services.runtime_support import get_database_config, parse_multi_target


class _FakeManager:
    def __init__(self):
        self.calls = 0

    def list_containers(self, show_all=True, format_output="table"):
        self.calls += 1
        return ()


def _time_calls(iterations, func):
    start = perf_counter()
    for _ in range(iterations):
        func()
    return perf_counter() - start


def _legacy_parse_multi_target(value):
    if not value:
        return []
    return [target.strip() for target in value.split(",") if target.strip()]


def _legacy_get_database_config(defaults, image_tag):
    database_services = defaults.get("database_services", {})
    image_lower = image_tag.lower()
    for db_name in sorted(database_services.keys(), key=len, reverse=True):
        if db_name in image_lower:
            return database_services[db_name]
    return {}


def test_parse_multi_target_refactor_has_no_material_regression():
    raw = "web, api,, worker ,redis,postgresql"
    iterations = 100_000
    legacy = _time_calls(iterations, lambda: _legacy_parse_multi_target(raw))
    current = _time_calls(iterations, lambda: parse_multi_target(raw))

    assert current < 2.0
    assert current <= legacy * 3.0 + 0.05


def test_database_matching_refactor_has_no_material_regression():
    defaults = {
        "database_services": {
            "sql": {"kind": "generic"},
            "mysql": {"kind": "mysql"},
            "mariadb": {"kind": "maria"},
            "postgres": {"kind": "postgres-short"},
            "postgresql": {"kind": "postgres"},
            "mongodb": {"kind": "mongo"},
            "redis": {"kind": "redis"},
        }
    }
    image = "registry.local/team/postgresql:17"
    iterations = 100_000
    legacy = _time_calls(iterations, lambda: _legacy_get_database_config(defaults, image))
    current = _time_calls(iterations, lambda: get_database_config(defaults, image))

    assert current < 3.0
    assert current <= legacy * 3.0 + 0.05


def test_facade_delegation_overhead_stays_negligible():
    manager = _FakeManager()
    pilot = object.__new__(DockerPilotEnhanced)
    pilot.container_manager = manager

    iterations = 200_000
    direct = _time_calls(iterations, lambda: manager.list_containers(True, "json"))
    manager.calls = 0
    facade = _time_calls(iterations, lambda: pilot.list_containers(True, "json"))

    assert manager.calls == iterations
    assert facade < 2.0
    assert facade <= direct * 4.0 + 0.05
