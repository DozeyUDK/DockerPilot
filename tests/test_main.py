"""Tests for DockerPilot CLI bootstrap behavior."""

import dockerpilot.main as main_module


def test_help_does_not_initialize_dockerpilot(monkeypatch):
    calls = {"parser": 0, "pilot": 0}

    class DummyParser:
        def parse_args(self, argv):
            calls["parser"] += 1
            assert argv == ["--help"]

    def fake_build_cli_parser():
        return DummyParser()

    class DummyPilot:
        def __init__(self, *args, **kwargs):
            calls["pilot"] += 1

    monkeypatch.setattr(main_module, "build_cli_parser", fake_build_cli_parser)
    monkeypatch.setattr(main_module, "DockerPilotEnhanced", DummyPilot)

    main_module.main(["--help"])

    assert calls == {"parser": 1, "pilot": 0}
