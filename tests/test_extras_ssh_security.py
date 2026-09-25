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


class RecordingHostKeys:
    def __init__(self, host_name: str, key):
        self._entries = {host_name: {key.get_name(): key}}
        self.saved_to = None

    def lookup(self, name):
        return self._entries.get(name)

    def add(self, host_name, key_type, key):
        self._entries.setdefault(host_name, {})[key_type] = key

    def save(self, path):
        self.saved_to = path
        Path(path).touch()


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


def test_explicit_new_pin_replaces_stale_known_host_key(tmp_path: Path, monkeypatch):
    old_key = FakeKey(b"old-key")
    new_key = FakeKey(b"new-key")
    host_keys = RecordingHostKeys("host.example", old_key)
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: new_key)
    monkeypatch.setattr(ssh_security, "_load_known_hosts", lambda _path: host_keys)
    path = tmp_path / "known_hosts"

    returned = ssh_security.ensure_host_key_trusted(
        {
            "hostname": "host.example",
            "port": 22,
            "host_key_fingerprint": ssh_security.key_fingerprint_sha256(new_key),
        },
        known_hosts_path=path,
    )

    assert returned.asbytes() == new_key.asbytes()
    assert host_keys.lookup("host.example")[new_key.get_name()].asbytes() == new_key.asbytes()
    assert host_keys.saved_to == str(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_stale_known_host_key_without_new_pin_still_fails_closed(tmp_path: Path, monkeypatch):
    old_key = FakeKey(b"old-key")
    new_key = FakeKey(b"new-key")
    host_keys = RecordingHostKeys("host.example", old_key)
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: new_key)
    monkeypatch.setattr(ssh_security, "_load_known_hosts", lambda _path: host_keys)

    with pytest.raises(ssh_security.SSHHostKeyMismatch):
        ssh_security.ensure_host_key_trusted(
            {"hostname": "host.example", "port": 22},
            known_hosts_path=tmp_path / "known_hosts",
        )


def test_explicit_pin_overrides_stale_matching_known_hosts_entry(tmp_path: Path, monkeypatch):
    """A historical known_hosts key must not override a newer explicit pin."""
    observed_key = FakeKey(b"observed-old-key")
    pinned_key = FakeKey(b"operator-pinned-new-key")
    host_keys = RecordingHostKeys("host.example", observed_key)
    monkeypatch.setattr(ssh_security, "probe_host_key", lambda *_a, **_kw: observed_key)
    monkeypatch.setattr(ssh_security, "_load_known_hosts", lambda _path: host_keys)

    with pytest.raises(ssh_security.SSHHostKeyMismatch):
        ssh_security.ensure_host_key_trusted(
            {
                "hostname": "host.example",
                "port": 22,
                "host_key_fingerprint": ssh_security.key_fingerprint_sha256(pinned_key),
            },
            known_hosts_path=tmp_path / "known_hosts",
        )


def test_verified_client_trusts_only_the_verified_key(tmp_path: Path, monkeypatch):
    verified_key = FakeKey(b"verified", "ssh-ed25519")

    class FakeClientHostKeys:
        def __init__(self):
            self.entries = {}

        def add(self, host_name, key_type, key):
            self.entries.setdefault(host_name, {})[key_type] = key

        def lookup(self, host_name):
            return self.entries.get(host_name)

    class FakeRejectPolicy:
        pass

    class FakeSSHClient:
        def __init__(self):
            self.host_keys = FakeClientHostKeys()
            self.policy = None

        def get_host_keys(self):
            return self.host_keys

        def load_system_host_keys(self, *_args, **_kwargs):
            raise AssertionError("system host keys must not be loaded into verified SSH client")

        def load_host_keys(self, *_args, **_kwargs):
            raise AssertionError("managed known_hosts must not broaden the verified trust set")

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

    class FakeParamiko:
        SSHClient = FakeSSHClient
        RejectPolicy = FakeRejectPolicy

    monkeypatch.setitem(sys.modules, "paramiko", FakeParamiko)
    monkeypatch.setattr(
        ssh_security,
        "ensure_host_key_trusted",
        lambda *_a, **_kw: verified_key,
    )

    client = ssh_security.create_verified_ssh_client(
        {"hostname": "host.example", "port": 22},
        known_hosts_path=tmp_path / "known_hosts",
    )

    trusted = client.get_host_keys().lookup("host.example")
    assert trusted is not None
    assert set(trusted) == {verified_key.get_name()}
    assert trusted[verified_key.get_name()].asbytes() == verified_key.asbytes()
    assert isinstance(client.policy, FakeRejectPolicy)
