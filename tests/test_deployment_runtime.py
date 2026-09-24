from types import SimpleNamespace

from dockerpilot.deployment_runtime import (
    apply_container_command,
    offset_port_mapping,
    requires_privileged_mode,
)


def test_offset_port_mapping_preserves_keys_and_shifts_host_ports():
    assert offset_port_mapping({"80/tcp": "8080", 443: 8443}, 1000) == {
        "80/tcp": "9080",
        443: "9443",
    }
    assert offset_port_mapping(None, 1000) == {}


def test_requires_privileged_mode_respects_explicit_config_and_infrastructure_images():
    assert requires_privileged_mode(SimpleNamespace(privileged=True, image_tag="demo"))

    messages = []
    assert requires_privileged_mode(
        SimpleNamespace(privileged=False, image_tag="rancher/k3s:v1"),
        log_info=messages.append,
    )
    assert any("k3s" in message for message in messages)


def test_requires_privileged_mode_can_inherit_active_container_setting():
    messages = []
    active = SimpleNamespace(attrs={"HostConfig": {"Privileged": True}})
    assert requires_privileged_mode(
        SimpleNamespace(privileged=False, image_tag="demo"),
        active,
        log_info=messages.append,
        active_copy_description="final container",
    )
    assert any("final container" in message for message in messages)


def test_requires_privileged_mode_handles_missing_active_metadata():
    debug = []

    class BrokenActive:
        @property
        def attrs(self):
            raise RuntimeError("boom")

    assert not requires_privileged_mode(
        SimpleNamespace(privileged=False, image_tag="demo"),
        BrokenActive(),
        log_debug=debug.append,
    )
    assert any("boom" in message for message in debug)


def test_apply_container_command_prefers_config_and_keeps_legacy_alpine_fallback():
    kwargs = {}
    config = SimpleNamespace(command=["python", "app.py"], image_tag="alpine:latest")
    assert apply_container_command(kwargs, config)["command"] == ["python", "app.py"]

    kwargs = {}
    config = SimpleNamespace(command=None, image_tag="alpine:3.20")
    assert apply_container_command(kwargs, config)["command"] == ["sh", "-c", "sleep 3600"]

    kwargs = {}
    config = SimpleNamespace(command=None, image_tag="nginx:latest")
    assert apply_container_command(kwargs, config) == {}
