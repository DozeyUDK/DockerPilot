from pathlib import Path
import sys

import pytest

EXTRAS_DIR = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS_DIR) not in sys.path:
    sys.path.insert(0, str(EXTRAS_DIR))

from backend.services import ssh_security


class FakeKey:
    def __init__(self, raw=b"host-key", name="ssh-ed25519"):
        self._raw = raw
        self._name = name

    def asbytes(self):
        return self._raw

    def get_name(self):
        return self._name


class EmptyHostKeys:
    def lookup(self, _name):
        return None

    def add(self, *_args, **_kwargs):
        raise AssertionError("unexpected add")

    def save(self, *_args, **_kwargs):
        raise AssertionError("unexpected save")


def test_unknown_host_requires_explicit_fingerprint(tmp_path: Path, monkeypatch):
    key = FakeKey()
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: key)
    monkeypatch.setattr(ssh_security, "_load_known_hosts", lambda _path: EmptyHostKeys())

    with pytest.raises(ssh_security.SSHHostKeyRequired) as exc:
        ssh_security.ensure_host_key_trusted(
            {"hostname": "example.invalid", "port": 22},
            known_hosts_path=tmp_path / "known_hosts",
        )

    assert exc.value.fingerprint == ssh_security.key_fingerprint_sha256(key)


def test_pinned_fingerprint_is_added_to_known_hosts(tmp_path: Path, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    key = paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: key)
    path = tmp_path / "known_hosts"
    config = {
        "hostname": "host.example",
        "port": 2222,
        "host_key_fingerprint": ssh_security.key_fingerprint_sha256(key),
    }

    ssh_security.ensure_host_key_trusted(config, known_hosts_path=path)

    loaded = paramiko.HostKeys(str(path))
    assert loaded.lookup("[host.example]:2222")[key.get_name()].asbytes() == key.asbytes()
    assert path.stat().st_mode & 0o777 == 0o600


def test_pinned_fingerprint_mismatch_is_rejected(tmp_path: Path, monkeypatch):
    key = FakeKey()
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: key)
    monkeypatch.setattr(ssh_security, "_load_known_hosts", lambda _path: EmptyHostKeys())
    with pytest.raises(ssh_security.SSHHostKeyMismatch):
        ssh_security.ensure_host_key_trusted(
            {
                "hostname": "host.example",
                "port": 22,
                "host_key_fingerprint": "SHA256:not-the-key",
            },
            known_hosts_path=tmp_path / "known_hosts",
        )
