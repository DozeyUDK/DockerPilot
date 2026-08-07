"""Offline parser/tests for Secure Deploy broker systemd units — no live service."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROD_SERVICE = ROOT / "deploy" / "systemd" / "dockerpilot-secure-broker.service"
PROD_SOCKET = ROOT / "deploy" / "systemd" / "dockerpilot-secure-broker.socket"
PREVIEW_SERVICE = ROOT / "deploy" / "systemd" / "dockerpilot-secure-broker.service.example"
EXTRAS_SERVICE = ROOT / "deploy" / "systemd" / "dockerpilot-extras.service.example"
CANARY_WORKDIR = "/var/lib/dockerpilot-secure-broker/canary/dockerpilot-secure-canary"
BUILDER = ROOT / "tools" / "secure_deploy" / "build_root_broker_staging.py"


def _parse_unit(text: str) -> dict[str, list[str]]:
    """Minimal systemd unit parser: section -> list of 'Key=Value' assignment lines."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current is None or "=" not in line:
            continue
        sections[current].append(line.strip())
    return sections


def _values(sections: dict[str, list[str]], section: str, key: str) -> list[str]:
    prefix = f"{key}="
    return [line[len(prefix) :] for line in sections.get(section, []) if line.startswith(prefix)]


def _joined(sections: dict[str, list[str]], section: str, key: str) -> str:
    return " ".join(_values(sections, section, key))


def _tokens(sections: dict[str, list[str]], section: str, key: str) -> set[str]:
    out: set[str] = set()
    for value in _values(sections, section, key):
        out.update(part for part in value.split() if part)
    return out


def _paths(sections: dict[str, list[str]], section: str, key: str) -> set[str]:
    """Collect path tokens, stripping optional '-' ignore-missing prefix."""
    return {p[1:] if p.startswith("-") else p for p in _tokens(sections, section, key)}


@pytest.fixture(scope="module")
def prod_text() -> str:
    return PROD_SERVICE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prod(prod_text: str) -> dict[str, list[str]]:
    return _parse_unit(prod_text)


@pytest.fixture(scope="module")
def preview() -> dict[str, list[str]]:
    return _parse_unit(PREVIEW_SERVICE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def socket_unit() -> dict[str, list[str]]:
    return _parse_unit(PROD_SOCKET.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extras() -> dict[str, list[str]]:
    return _parse_unit(EXTRAS_SERVICE.read_text(encoding="utf-8"))


def test_canary_unit_rejects_private_network(prod: dict[str, list[str]], prod_text: str):
    values = {v.strip().lower() for v in _values(prod, "Service", "PrivateNetwork")}
    assert "true" not in values
    assert "yes" not in values
    assert "1" not in values
    assert re.search(r"PrivateNetwork\s+must remain", prod_text, re.I)


def test_canary_unit_address_families(prod: dict[str, list[str]]):
    families = _tokens(prod, "Service", "RestrictAddressFamilies")
    assert "AF_UNIX" in families
    assert "AF_INET" in families
    assert "AF_INET6" not in families


def test_canary_unit_loopback_ip_policy(prod: dict[str, list[str]]):
    assert "any" in {v.strip().lower() for v in _values(prod, "Service", "IPAddressDeny")}
    allows = _tokens(prod, "Service", "IPAddressAllow")
    assert "127.0.0.1/32" in allows
    assert not any(a.startswith("::") or a.lower() == "localhost" for a in allows)


def test_canary_unit_docker_socket_not_inaccessible(prod: dict[str, list[str]]):
    inaccessible = _paths(prod, "Service", "InaccessiblePaths")
    assert "/var/run/docker.sock" not in inaccessible
    assert "/run/docker.sock" not in inaccessible


def test_canary_unit_filesystem_write_paths(prod: dict[str, list[str]]):
    rw = _paths(prod, "Service", "ReadWritePaths")
    assert rw == {
        "/var/lib/dockerpilot-secure-broker",
        "/run/dockerpilot-secure-broker",
    }
    assert CANARY_WORKDIR.startswith("/var/lib/dockerpilot-secure-broker/")
    ro = _paths(prod, "Service", "ReadOnlyPaths")
    assert "/usr/libexec/dockerpilot-secure-broker" in ro
    assert "/etc/dockerpilot-secure-broker" in ro
    assert _joined(prod, "Service", "ProtectSystem").lower() == "strict"
    assert _joined(prod, "Service", "ProtectHome").lower() == "true"


def test_canary_unit_syscall_and_priv_hardening(prod: dict[str, list[str]]):
    assert _joined(prod, "Service", "NoNewPrivileges").lower() == "true"
    assert _joined(prod, "Service", "User") == "root"
    assert _joined(prod, "Service", "Group") == "root"
    assert any(v.strip() == "" for v in _values(prod, "Service", "CapabilityBoundingSet"))
    assert any(v.strip() == "" for v in _values(prod, "Service", "AmbientCapabilities"))
    filters = _values(prod, "Service", "SystemCallFilter")
    assert any(v.strip() == "@system-service" for v in filters)
    assert any(v.strip().startswith("~") for v in filters)


def test_socket_activation_contract(prod: dict[str, list[str]], socket_unit: dict[str, list[str]]):
    assert "dockerpilot-secure-broker.socket" in _joined(prod, "Unit", "Requires")
    assert "dockerpilot-secure-broker.socket" in _joined(prod, "Unit", "After")
    assert _joined(socket_unit, "Socket", "ListenStream") == "/run/dockerpilot-secure-broker/broker.sock"
    assert _joined(socket_unit, "Socket", "SocketUser") == "root"
    assert _joined(socket_unit, "Socket", "SocketGroup") == "dockerpilot-secure-broker"
    assert _joined(socket_unit, "Socket", "SocketMode") == "0660"
    assert _joined(socket_unit, "Socket", "RemoveOnStop").lower() == "true"


def test_preview_only_unit_keeps_strict_hardening(preview: dict[str, list[str]]):
    families = _tokens(preview, "Service", "RestrictAddressFamilies")
    assert families == {"AF_UNIX"}
    assert "any" in {v.strip().lower() for v in _values(preview, "Service", "IPAddressDeny")}
    assert not _values(preview, "Service", "IPAddressAllow")
    inaccessible = _paths(preview, "Service", "InaccessiblePaths")
    assert "/var/run/docker.sock" in inaccessible
    assert "/run/docker.sock" in inaccessible
    assert "AF_INET" not in families


def test_extras_boundary_no_docker_sock_or_docker_group(extras: dict[str, list[str]]):
    text = EXTRAS_SERVICE.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in text
    assert "/run/docker.sock" not in text
    assert re.search(r"(?m)^SupplementaryGroups=docker\b", text) is None
    assert re.search(r"(?m)^Group=docker$", text) is None
    assert "NEVER docker" in text
    assert "18080" not in text
    assert "/var/run/docker.sock" not in _paths(extras, "Service", "ReadWritePaths")
    assert "/var/run/docker.sock" not in _paths(extras, "Service", "ReadOnlyPaths")


def test_builder_copies_approved_systemd_templates_verbatim(tmp_path):
    """Generated staged units must be identical to committed deploy/systemd templates."""
    sys.path.insert(0, str(ROOT / "tools" / "secure_deploy"))
    spec = importlib.util.spec_from_file_location("build_root_broker_staging", BUILDER)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    fake_src = tmp_path / "fake-dozeyguard-src"
    fake_src.mkdir()
    fake_bin = tmp_path / "dozeyguard"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    old_staging = builder.STAGING
    old_bundle = builder.BUNDLE
    old_build = builder.build_dozeyguard
    builder.STAGING = tmp_path / "11d2a"
    builder.BUNDLE = builder.STAGING / "bundle"
    builder.build_dozeyguard = lambda: (fake_bin, ROOT)
    try:
        builder.main()
        for unit in ("dockerpilot-secure-broker.service", "dockerpilot-secure-broker.socket"):
            staged = builder.BUNDLE / "etc" / "systemd" / "system" / unit
            approved = ROOT / "deploy" / "systemd" / unit
            assert staged.is_file()
            assert staged.read_bytes() == approved.read_bytes()
    finally:
        builder.STAGING = old_staging
        builder.BUNDLE = old_bundle
        builder.build_dozeyguard = old_build


def test_systemd_analyze_verify_production_templates_offline(tmp_path):
    """Rewrite ExecStart to a throwaway binary and verify units — never start the service."""
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not available")

    fake_broker = tmp_path / "broker"
    fake_broker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_broker.chmod(0o755)

    service_dst = tmp_path / "dockerpilot-secure-broker.service"
    socket_dst = tmp_path / "dockerpilot-secure-broker.socket"
    original = PROD_SERVICE.read_text(encoding="utf-8")
    rewritten = []
    for line in original.splitlines():
        if line.startswith("ExecStart="):
            rewritten.append(f"ExecStart={fake_broker}")
        else:
            rewritten.append(line)
    service_dst.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    shutil.copy2(PROD_SOCKET, socket_dst)

    assert PROD_SERVICE.read_text(encoding="utf-8") == original

    proc = subprocess.run(
        ["systemd-analyze", "verify", str(service_dst), str(socket_dst)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_canary_code_uses_ipv4_loopback_only():
    canary = (ROOT / "src/dockerpilot/secure_deploy_broker/canary.py").read_text(encoding="utf-8")
    assert 'HOST = "127.0.0.1"' in canary
    assert "AF_INET6" not in canary
    assert "socket.AF_INET" in canary
    assert "::1" not in canary


def test_hardening_docs_mention_unix_vs_ip_filters():
    hardening = (ROOT / "docs/security/ROOT_BROKER_SYSTEMD_HARDENING.md").read_text(encoding="utf-8")
    threat = (ROOT / "docs/security/SECURE_DEPLOY_BROKER_THREAT_MODEL.md").read_text(encoding="utf-8")
    unit = PROD_SERVICE.read_text(encoding="utf-8")
    assert "IPAddressAllow=127.0.0.1/32" in hardening
    assert "PrivateNetwork" in hardening
    assert "Unix sockets" in hardening
    assert "do NOT gate Unix sockets" in unit
    assert "Canary / systemd network posture" in threat
    assert "/var/run/docker.sock" in threat
