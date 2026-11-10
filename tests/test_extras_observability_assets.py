"""Static checks for the optional Grafana Alloy and dashboard assets."""

import json
from pathlib import Path


EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"


def test_grafana_dashboard_is_importable_and_targets_the_extras_stream():
    dashboard_path = (
        EXTRAS_DIR
        / "observability"
        / "grafana"
        / "dockerpilot-extras-dashboard.json"
    )
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "dockerpilot-extras-logs"
    assert dashboard["__inputs"][0]["pluginId"] == "loki"
    assert len(dashboard["panels"]) >= 3

    expressions = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    assert expressions
    assert all('job="dockerpilot-extras"' in expression for expression in expressions)
    assert any("remote_operation_failed" in expression for expression in expressions)


def test_alloy_example_collects_rotated_jsonl_files_and_forwards_to_loki():
    config_path = (
        EXTRAS_DIR
        / "observability"
        / "alloy"
        / "dockerpilot-extras.alloy"
    )
    config = config_path.read_text(encoding="utf-8")

    assert 'loki.source.file "dockerpilot_extras"' in config
    assert '"/var/log/dockerpilot-extras/extras.jsonl*"' in config
    assert '"job"          = "dockerpilot-extras"' in config
    assert "tail_from_end = true" in config
    assert "file_match {" in config
    assert "loki.write.dockerpilot_extras.receiver" in config


def test_grafana_guide_covers_existing_stack_and_file_permissions():
    guide = (
        EXTRAS_DIR
        / "docs"
        / "observability"
        / "grafana-loki.md"
    ).read_text(encoding="utf-8")

    assert "existing Grafana + Loki + Alloy" in guide
    assert "DOCKERPILOT_EXTRAS_LOG_FILE_MODE=0640" in guide
    assert "Grafana only (no Loki)" in guide
    assert "Grafana → Explore" in guide
