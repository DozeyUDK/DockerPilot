import os
from pathlib import Path

import pytest
import docker

from dockerpilot.mcp.context import MCPConfig
from dockerpilot.mcp.migration import MigrationOps
from dockerpilot.mcp.safety import ToolBlocked


class FakeImage:
    def __init__(self, chunks):
        self._chunks = chunks

    def save(self, named=True):
        assert named is True
        for c in self._chunks:
            yield c


class FakeImages:
    def __init__(self, image_by_ref):
        self._image_by_ref = image_by_ref
        self.loaded = []

    def get(self, ref):
        if ref not in self._image_by_ref:
            raise docker.errors.ImageNotFound("missing")
        return self._image_by_ref[ref]


class FakeAPI:
    def __init__(self):
        self.load_calls = 0

    def load_image(self, data, quiet=None):
        self.load_calls += 1
        # data is a file-like stream; do not read in tests
        return {"status": "ok"}


class FakeVolumes:
    def __init__(self):
        self.created = []

    def create(self, name):
        self.created.append(name)
        return {"Name": name}


class FakeContainerObj:
    def __init__(self, name, attrs):
        self.name = name
        self.attrs = attrs
        self.started = False
        self.removed = False

    def remove(self, force=False):
        self.removed = True

    def start(self):
        self.started = True


class FakeContainers:
    def __init__(self, containers_by_name, create_cb=None):
        self._containers_by_name = containers_by_name
        self._create_cb = create_cb
        self.created = []

    def get(self, name):
        if name not in self._containers_by_name:
            raise docker.errors.NotFound("not found")
        return self._containers_by_name[name]

    def create(self, **kwargs):
        self.created.append(kwargs)
        if self._create_cb:
            return self._create_cb(kwargs)
        # Return a stub container
        return FakeContainerObj(kwargs.get("name") or "new", attrs={})


class FakeDockerClient:
    def __init__(self, *, containers, images, api, volumes):
        self.containers = containers
        self.images = images
        self.api = api
        self.volumes = volumes


def _config(**overrides):
    base = MCPConfig(
        readonly=False,
        allow_destructive=False,
        allowed_containers=[],
        denied_containers=[],
        max_log_lines=200,
        exec_timeout=10,
        redact_secrets=True,
    )
    return base.__class__(**{**base.__dict__, **overrides})


def test_import_bundle_conflict_fail_returns_dry_run_plan(tmp_path):
    bundle = tmp_path / "b.tar"
    # Minimal tar bundle with manifest.json + image.tar
    import tarfile, io, json

    manifest = {"container_name": "app", "image": "myimg:latest", "payload": {"volumes": []}}
    with tarfile.open(bundle, "w") as t:
        m = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(m)
        t.addfile(info, io.BytesIO(m))
        img = b"fake"
        info2 = tarfile.TarInfo("image.tar")
        info2.size = len(img)
        t.addfile(info2, io.BytesIO(img))

    existing = FakeContainerObj("app", attrs={})
    client = FakeDockerClient(
        containers=FakeContainers({"app": existing}),
        images=FakeImages({"myimg:latest": FakeImage([b"x"])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, _config())
    out = ops.import_bundle(bundle_path=bundle, target_name="app", start=False, dry_run=True, confirm=True)
    assert out["dry_run"] is True
    assert "target_exists" in out.get("warnings", [])


def test_import_bundle_conflict_rename_picks_new_name(tmp_path):
    import tarfile, io, json

    bundle = tmp_path / "b.tar"
    manifest = {"container_name": "app", "image": "myimg:latest", "payload": {"volumes": []}}
    with tarfile.open(bundle, "w") as t:
        m = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(m)
        t.addfile(info, io.BytesIO(m))
        img = b"fake"
        info2 = tarfile.TarInfo("image.tar")
        info2.size = len(img)
        t.addfile(info2, io.BytesIO(img))

    existing = FakeContainerObj("app", attrs={})
    client = FakeDockerClient(
        containers=FakeContainers({"app": existing}),
        images=FakeImages({"myimg:latest": FakeImage([b"x"])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, _config())
    out = ops.import_bundle(
        bundle_path=bundle,
        target_name="app",
        start=False,
        dry_run=True,
        on_conflict="rename",
        allow_replace=False,
        confirm=True,
    )
    assert out["dry_run"] is True
    assert any("on_conflict=rename" in a for a in out["planned_actions"])


def test_export_bundle_restricts_output_dir_to_base(tmp_path, monkeypatch):
    base = tmp_path / "migrations"
    base.mkdir()
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATIONS_DIR", str(base))
    cfg = _config(migration_allow_arbitrary_output_dir=False)

    attrs = {"Config": {"Image": "myimg:latest", "Env": [], "Labels": {}}, "Mounts": []}
    src_container = FakeContainerObj("app", attrs=attrs)

    client = FakeDockerClient(
        containers=FakeContainers({"app": src_container}),
        images=FakeImages({"myimg:latest": FakeImage([b"x" * 10])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, cfg)

    with pytest.raises(ToolBlocked):
        ops.export_bundle(container_name="app", include_data=False, output_dir=tmp_path, confirm=True)

    # Within base is allowed
    out = ops.export_bundle(container_name="app", include_data=False, output_dir=base, confirm=True)
    assert out.path.exists()


def test_export_bundle_enforces_max_size(tmp_path, monkeypatch):
    base = tmp_path / "migrations"
    base.mkdir()
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATIONS_DIR", str(base))
    cfg = _config(migration_allow_arbitrary_output_dir=False, migration_max_bundle_bytes=50)

    attrs = {"Config": {"Image": "myimg:latest", "Env": [], "Labels": {}}, "Mounts": []}
    src_container = FakeContainerObj("app", attrs=attrs)
    client = FakeDockerClient(
        containers=FakeContainers({"app": src_container}),
        images=FakeImages({"myimg:latest": FakeImage([b"x" * 200])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, cfg)
    with pytest.raises(ToolBlocked):
        ops.export_bundle(container_name="app", include_data=False, output_dir=base, confirm=True)


def test_export_bundle_encrypts_when_enabled(tmp_path, monkeypatch):
    import tarfile

    pytest.importorskip("cryptography")

    base = tmp_path / "migrations"
    base.mkdir()
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATIONS_DIR", str(base))
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATION_ENCRYPTION", "true")
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATION_PASSPHRASE", "pw")
    cfg = _config(migration_allow_arbitrary_output_dir=False, migration_max_bundle_bytes=10_000_000)

    attrs = {"Config": {"Image": "myimg:latest", "Env": [], "Labels": {}}, "Mounts": []}
    src_container = FakeContainerObj("app", attrs=attrs)
    client = FakeDockerClient(
        containers=FakeContainers({"app": src_container}),
        images=FakeImages({"myimg:latest": FakeImage([b"x" * 10])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, cfg)
    out = ops.export_bundle(container_name="app", include_data=False, output_dir=base, confirm=True)
    assert str(out.path).endswith(".tar.enc")


def test_export_bundle_encrypts_with_age_when_enabled(tmp_path, monkeypatch):
    base = tmp_path / "migrations"
    base.mkdir()
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATIONS_DIR", str(base))
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATION_ENCRYPTION", "age")
    monkeypatch.setenv("DOCKERPILOT_MCP_MIGRATION_AGE_RECIPIENTS", "age1dummy")

    import dockerpilot.mcp.age_crypto as age_crypto

    def fake_which():
        return "/usr/bin/age"

    def fake_run(args):
        # args: age -r recip -o out in
        out_idx = args.index("-o") + 1
        in_path = Path(args[-1])
        out_path = Path(args[out_idx])
        out_path.write_bytes(in_path.read_bytes())

    monkeypatch.setattr(age_crypto, "_which_age", fake_which)
    monkeypatch.setattr(age_crypto, "_run_age", fake_run)

    cfg = _config(migration_allow_arbitrary_output_dir=False, migration_max_bundle_bytes=10_000_000)
    attrs = {"Config": {"Image": "myimg:latest", "Env": [], "Labels": {}}, "Mounts": []}
    src_container = FakeContainerObj("app", attrs=attrs)
    client = FakeDockerClient(
        containers=FakeContainers({"app": src_container}),
        images=FakeImages({"myimg:latest": FakeImage([b"x" * 10])}),
        api=FakeAPI(),
        volumes=FakeVolumes(),
    )
    ops = MigrationOps(client, cfg)
    out = ops.export_bundle(container_name="app", include_data=False, output_dir=base, confirm=True)
    assert str(out.path).endswith(".tar.age")
