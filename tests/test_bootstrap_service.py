from types import SimpleNamespace

from dockerpilot.services import bootstrap


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message, **_kwargs):
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", str(message)))

    def info(self, message):
        self.messages.append(("info", str(message)))

    def warning(self, message):
        self.messages.append(("warning", str(message)))

    def error(self, message):
        self.messages.append(("error", str(message)))


class FakeClient:
    def __init__(self, base_url="fake"):
        self.api = SimpleNamespace(base_url=base_url)
        self.pings = 0

    def ping(self):
        self.pings += 1
        return True


def test_load_config_preserves_yaml_semantics(tmp_path):
    config = tmp_path / "deployment.yml"
    config.write_text("service: web\nreplicas: 2\n", encoding="utf-8")
    logger = FakeLogger()

    assert bootstrap.load_config(logger, str(config)) == {"service": "web", "replicas": 2}
    assert any(level == "info" and "Configuration loaded" in message for level, message in logger.messages)


def test_load_config_returns_empty_dict_on_error(tmp_path):
    logger = FakeLogger()
    assert bootstrap.load_config(logger, str(tmp_path / "missing.yml")) == {}
    assert any(level == "error" for level, _ in logger.messages)


def test_initialize_docker_client_prefers_active_cli_context(monkeypatch):
    calls = []

    def fake_check_output(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[1:3] == ["context", "show"]:
            return "desktop-linux\n"
        return '"unix:///run/user/1000/docker.sock"\n'

    created = []

    def fake_docker_client(*, base_url):
        client = FakeClient(base_url)
        created.append(client)
        return client

    monkeypatch.setattr(bootstrap.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(bootstrap.docker, "DockerClient", fake_docker_client)
    monkeypatch.setattr(
        bootstrap.docker,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("from_env should not be used")),
    )

    logger = FakeLogger()
    client = bootstrap.initialize_docker_client(FakeConsole(), logger, max_retries=1)

    assert client is created[0]
    assert client.api.base_url == "unix:///run/user/1000/docker.sock"
    assert client.pings == 1
    assert len(calls) == 2


def test_initialize_docker_client_falls_back_to_from_env(monkeypatch):
    monkeypatch.setattr(
        bootstrap.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("docker cli unavailable")),
    )
    expected = FakeClient("from-env")
    monkeypatch.setattr(bootstrap.docker, "from_env", lambda: expected)

    client = bootstrap.initialize_docker_client(FakeConsole(), FakeLogger(), max_retries=1)
    assert client is expected
    assert expected.pings == 1


def test_initialize_docker_client_retries_and_fails_closed(monkeypatch):
    monkeypatch.setattr(
        bootstrap.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no context")),
    )
    attempts = {"count": 0}

    def broken_from_env():
        attempts["count"] += 1
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(bootstrap.docker, "from_env", broken_from_env)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)
    console = FakeConsole()
    logger = FakeLogger()

    assert bootstrap.initialize_docker_client(console, logger, max_retries=3) is None
    assert attempts["count"] == 3
    assert any("Cannot connect to Docker daemon" in message for message in console.messages)
    assert any(level == "error" and "after 3 attempts" in message for level, message in logger.messages)
