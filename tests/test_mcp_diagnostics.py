from dockerpilot.mcp.diagnostics import detect_unhealthy, explain_container_state


class FakeOps:
    def __init__(self, containers, inspected_by_name):
        self._containers = containers
        self._inspected_by_name = inspected_by_name

    def list_containers(self, all_containers, include_ports, include_labels):
        assert all_containers is True
        return {"containers": self._containers}

    def inspect_container_curated(self, name: str, redact: bool):
        assert redact is True
        return {"container": self._inspected_by_name[name]}


def test_detect_unhealthy_flags_expected_conditions():
    containers = [
        {"name": "svc1", "state": "running"},
        {"name": "svc2", "state": "exited"},
    ]
    inspected = {
        "svc1": {
            "state": {"status": "running", "exit_code": 0, "oom_killed": False, "restart_count": 7},
            "health": {"Status": "unhealthy"},
        },
        "svc2": {
            "state": {"status": "exited", "exit_code": 42, "oom_killed": True, "restart_count": 0},
            "health": None,
        },
    }
    issues = detect_unhealthy(FakeOps(containers, inspected), include_exited=True)["issues"]
    kinds = {i["kind"] for i in issues}
    assert "healthcheck_unhealthy" in kinds
    assert "high_restart_count" in kinds
    assert "exited_nonzero" in kinds
    assert "oom_killed" in kinds


def test_explain_container_state_builds_recommendations():
    inspected = {
        "state": {"status": "exited", "exit_code": 1, "oom_killed": False, "restart_count": 2},
        "health": None,
    }
    result = explain_container_state(FakeOps([], {"svc": inspected}), name="svc")
    assert result["name"] == "svc"
    assert result["status"] == "exited"
    assert result["exit_code"] == 1
    tools = {a["tool"] for a in result["recommended_actions"]}
    assert "dockerpilot_container_logs" in tools
    assert "dockerpilot_container_start" in tools

