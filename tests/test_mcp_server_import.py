import importlib


def test_server_module_imports_without_docker_daemon(monkeypatch):
    import docker

    called = {"from_env": 0}

    def fake_from_env():
        called["from_env"] += 1
        raise AssertionError("docker.from_env should not be called on import")

    monkeypatch.setattr(docker, "from_env", fake_from_env)

    importlib.import_module("dockerpilot.mcp.server")
    assert called["from_env"] == 0

