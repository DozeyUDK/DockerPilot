from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tarfile
from typing import Any, Iterable, Optional

import docker

from .context import MCPConfig
from .safety import ToolBlocked, cap_text, env_list_to_dict, redact_env_dict, redact_labels, require_container_allowed


def _crypto():
    try:
        from .crypto import CryptoError, CryptoUnavailable, EncryptionSettings, decrypt_file, encrypt_file
    except Exception as exc:  # pragma: no cover
        raise ToolBlocked(
            "Migration encryption support is unavailable. Install with: pip install -e '.[mcp-crypto]'"
        ) from exc
    return CryptoError, CryptoUnavailable, EncryptionSettings, decrypt_file, encrypt_file


def _age_crypto():
    try:
        from .age_crypto import AgeError, AgeUnavailable, AgeSettings, decrypt_file_age, encrypt_file_age
    except Exception as exc:  # pragma: no cover
        raise ToolBlocked("age encryption support is unavailable.") from exc
    return AgeError, AgeUnavailable, AgeSettings, decrypt_file_age, encrypt_file_age


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    # conservative filename component
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in (name or ""))


def _base_migrations_dir() -> Path:
    raw = os.environ.get("DOCKERPILOT_MCP_MIGRATIONS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".dockerpilot" / "migrations"


def _container_named_volumes(container_attrs: dict[str, Any]) -> list[dict[str, Any]]:
    mounts = container_attrs.get("Mounts") or []
    out: list[dict[str, Any]] = []
    for m in mounts:
        if not isinstance(m, dict):
            continue
        if (m.get("Type") or "").lower() != "volume":
            continue
        name = m.get("Name")
        dest = m.get("Destination")
        if name and dest:
            out.append({"name": str(name), "destination": str(dest), "mode": m.get("Mode"), "rw": m.get("RW")})
    return out


def _container_bind_mounts(container_attrs: dict[str, Any]) -> list[dict[str, Any]]:
    mounts = container_attrs.get("Mounts") or []
    out: list[dict[str, Any]] = []
    for m in mounts:
        if not isinstance(m, dict):
            continue
        if (m.get("Type") or "").lower() != "bind":
            continue
        src = m.get("Source")
        dest = m.get("Destination")
        if src and dest:
            out.append({"source": str(src), "destination": str(dest), "mode": m.get("Mode"), "rw": m.get("RW")})
    return out


@dataclass
class MigrationBundle:
    path: Path
    manifest: dict[str, Any]


class MigrationOps:
    """Implements local export/import bundle workflow.

    Cross-host transport (scp/rsync) is intentionally out of scope: copy the bundle out-of-band.
    """

    def __init__(self, client: docker.DockerClient, config: MCPConfig):
        self._client = client
        self._config = config

    def plan(self, container_name: str, include_data: bool) -> dict[str, Any]:
        container = self._client.containers.get(container_name)
        attrs = container.attrs or {}
        cfg = attrs.get("Config") or {}

        env_dict = env_list_to_dict(cfg.get("Env"))
        redacted_env = redact_env_dict(env_dict) if self._config.redact_secrets else env_dict
        labels = cfg.get("Labels") or {}
        redacted_labels = redact_labels(labels) if self._config.redact_secrets else labels

        named_volumes = _container_named_volumes(attrs)
        bind_mounts = _container_bind_mounts(attrs)

        warnings: list[str] = []
        if bind_mounts:
            warnings.append(
                f"Bind mounts are not included in migration bundles by default ({len(bind_mounts)} bind mount(s) detected)."
            )
        if include_data and not named_volumes:
            warnings.append("include_data=true but no named volumes detected; only image+config will be exported.")

        return {
            "container": container_name,
            "include_data": bool(include_data),
            "image": (cfg.get("Image") or attrs.get("Image")),
            "env_keys": sorted(list(env_dict.keys())),
            "env_redacted": True if self._config.redact_secrets else False,
            "labels_redacted": True if self._config.redact_secrets else False,
            "labels": redacted_labels,
            "volumes": {"named": named_volumes, "bind": bind_mounts},
            "warnings": warnings,
            "timestamp": _now_utc_iso(),
        }

    def export_bundle(
        self,
        *,
        container_name: str,
        include_data: bool,
        output_dir: Path,
        confirm: bool,
    ) -> MigrationBundle:
        if self._config.readonly:
            raise ToolBlocked("Migration export requires DOCKERPILOT_MCP_READONLY=false.")
        if confirm is not True:
            raise ToolBlocked("Migration export requires confirm=true.")
        require_container_allowed(self._config, container_name)

        output_dir = output_dir.expanduser()
        if not self._config.migration_allow_arbitrary_output_dir:
            base = _base_migrations_dir().expanduser().resolve()
            resolved = output_dir.resolve()
            if not (resolved == base or resolved.is_relative_to(base)):
                raise ToolBlocked(
                    f"output_dir must be within {base} (set DOCKERPILOT_MCP_MIGRATION_ALLOW_ARBITRARY_OUTPUT_DIR=true to override)."
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_name(container_name) or "container"
        base_name = f"dockerpilot-migration-{safe}-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar"
        bundle_path = output_dir / base_name

        container = self._client.containers.get(container_name)
        attrs = container.attrs or {}
        cfg = attrs.get("Config") or {}
        image_ref = cfg.get("Image") or attrs.get("Image")
        if not image_ref:
            raise ToolBlocked("Unable to determine container image reference for export.")

        plan = self.plan(container_name, include_data=include_data)
        payload: dict[str, Any] = {"image_tar_path": "image.tar", "volumes": []}

        # Create tar bundle containing: image.tar, volume tar(s), manifest.json (written last)
        with bundle_path.open("wb") as out_f:
            with tarfile.open(fileobj=out_f, mode="w") as bundle_tar:
                # image.tar
                try:
                    image = self._client.images.get(str(image_ref))
                except docker.errors.ImageNotFound:
                    # Try by id if ref points to digest-like
                    image = self._client.images.get(attrs.get("Image"))
                image_stream = image.save(named=True)

                image_info = tarfile.TarInfo(name="image.tar")
                image_info.size = 0  # unknown; tarfile requires exact size, so we stream via temp file

                # Stream to temp file to get size without loading into memory.
                tmp_image = bundle_path.with_suffix(".image.tmp")
                try:
                    with tmp_image.open("wb") as img_f:
                        for chunk in image_stream:
                            img_f.write(chunk)
                    image_size = tmp_image.stat().st_size
                    image_info = tarfile.TarInfo(name="image.tar")
                    image_info.size = image_size
                    image_info.mtime = int(datetime.now(tz=timezone.utc).timestamp())
                    with tmp_image.open("rb") as img_f:
                        bundle_tar.addfile(image_info, img_f)
                finally:
                    try:
                        tmp_image.unlink(missing_ok=True)  # type: ignore[arg-type]
                    except TypeError:  # pragma: no cover (py<3.8)
                        if tmp_image.exists():
                            tmp_image.unlink()

                if include_data:
                    volumes = _container_named_volumes(attrs)
                    for vol in volumes:
                        vol_name = vol["name"]
                        dest = vol["destination"]
                        vol_tar_name = f"volumes/{_safe_name(vol_name)}.tar"
                        tmp_vol = bundle_path.with_suffix(f".{_safe_name(vol_name)}.vol.tmp")
                        self._export_named_volume_to_tar(volume_name=vol_name, destination=dest, out_path=tmp_vol)
                        try:
                            st = tmp_vol.stat()
                            vol_info = tarfile.TarInfo(name=vol_tar_name)
                            vol_info.size = st.st_size
                            vol_info.mtime = int(datetime.now(tz=timezone.utc).timestamp())
                            with tmp_vol.open("rb") as vf:
                                bundle_tar.addfile(vol_info, vf)
                            payload["volumes"].append({"name": vol_name, "destination": dest, "bundle_path": vol_tar_name})
                        finally:
                            try:
                                tmp_vol.unlink(missing_ok=True)  # type: ignore[arg-type]
                            except TypeError:  # pragma: no cover
                                if tmp_vol.exists():
                                    tmp_vol.unlink()

                manifest: dict[str, Any] = {
                    "format": "dockerpilot-migration-bundle/v1",
                    "created_at": _now_utc_iso(),
                    "container_name": container_name,
                    "image": str(image_ref),
                    "include_data": bool(include_data),
                    "plan": plan,
                    "payload": payload,
                }
                manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
                info = tarfile.TarInfo(name="manifest.json")
                info.size = len(manifest_bytes)
                info.mtime = int(datetime.now(tz=timezone.utc).timestamp())
                bundle_tar.addfile(info, io.BytesIO(manifest_bytes))

        sha = _sha256_file(bundle_path)
        manifest["bundle_sha256"] = sha
        try:
            size = bundle_path.stat().st_size
        except Exception:
            size = None
        if size is not None and size > int(self._config.migration_max_bundle_bytes):
            try:
                bundle_path.unlink()
            except Exception:
                pass
            raise ToolBlocked(
                f"Bundle size {size} exceeds DOCKERPILOT_MCP_MIGRATION_MAX_BUNDLE_BYTES={self._config.migration_max_bundle_bytes}."
            )
        _, _, _EncryptionSettings, _, _ = _crypto()
        enc = _EncryptionSettings.from_env()
        if enc.enabled:
            if enc.mode == "age":
                encrypted_path = bundle_path.with_suffix(bundle_path.suffix + ".age")
                try:
                    _, _, AgeSettings, _, _encrypt_age = _age_crypto()
                    _encrypt_age(in_path=bundle_path, out_path=encrypted_path, settings=AgeSettings.from_env())
                except Exception as exc:
                    raise ToolBlocked(str(exc)) from exc
                try:
                    bundle_path.unlink()
                except Exception:
                    pass
                manifest["encryption"] = {"enabled": True, "mode": "age"}
                return MigrationBundle(path=encrypted_path, manifest=manifest)
            encrypted_path = bundle_path.with_suffix(bundle_path.suffix + ".enc")
            try:
                CryptoError, CryptoUnavailable, _EncryptionSettings, _decrypt_file, _encrypt_file = _crypto()
                _encrypt_file(in_path=bundle_path, out_path=encrypted_path, settings=enc)
            except Exception as exc:
                raise ToolBlocked(str(exc)) from exc
            try:
                bundle_path.unlink()
            except Exception:
                pass
            manifest["encryption"] = {"enabled": True, "mode": "aesgcm"}
            return MigrationBundle(path=encrypted_path, manifest=manifest)
        manifest["encryption"] = {"enabled": False}
        return MigrationBundle(path=bundle_path, manifest=manifest)

    def _export_named_volume_to_tar(self, *, volume_name: str, destination: str, out_path: Path) -> None:
        """Export a named volume to a tar file using an ephemeral helper container.

        This does not modify the source container. It does create and run a short-lived helper container.
        """
        # Use a minimal image; alpine is commonly present, but we can't assume. If missing, Docker will pull.
        # We do not want implicit network pulls; so we try busybox, then alpine, and fail if neither exists.
        helper_image = self._pick_local_helper_image()
        if helper_image is None:
            raise ToolBlocked("No local helper image available for volume export (need busybox or alpine present locally).")

        tmp_dir = out_path.parent
        tmp_dir.mkdir(parents=True, exist_ok=True)
        produced = tmp_dir / "volume.tar"
        if produced.exists():
            produced.unlink()

        helper = self._client.containers.create(
            image=helper_image,
            command=["sh", "-c", "tar -C /data -cf /out/volume.tar ."],
            volumes={
                volume_name: {"bind": "/data", "mode": "ro"},
                str(tmp_dir): {"bind": "/out", "mode": "rw"},
            },
            working_dir="/",
        )
        try:
            helper.start()
            res = helper.wait(timeout=_migration_timeout_seconds())
            status = (res or {}).get("StatusCode")
            if status not in (0, "0", None):
                logs = helper.logs(tail=50)
                text = logs.decode("utf-8", errors="replace") if isinstance(logs, (bytes, bytearray)) else str(logs)
                text, _ = cap_text(text, 8_000)
                raise ToolBlocked(f"Volume export helper failed for {volume_name} (exit={status}). Logs: {text}")
        finally:
            try:
                helper.remove(force=True)
            except Exception:
                pass

        if not produced.exists():
            raise ToolBlocked(f"Volume export did not produce expected tar for {volume_name}.")
        produced.replace(out_path)

    def _pick_local_helper_image(self) -> Optional[str]:
        for candidate in ("busybox:latest", "busybox", "alpine:latest", "alpine"):
            try:
                self._client.images.get(candidate)
                return candidate
            except Exception:
                continue
        return None

    def import_bundle(
        self,
        *,
        bundle_path: Path,
        target_name: Optional[str],
        start: bool,
        dry_run: bool,
        on_conflict: str = "fail",
        allow_replace: bool = False,
        confirm: bool,
    ) -> dict[str, Any]:
        if self._config.readonly:
            raise ToolBlocked("Migration import requires DOCKERPILOT_MCP_READONLY=false.")
        if confirm is not True:
            raise ToolBlocked("Migration import requires confirm=true.")

        bundle_path = bundle_path.expanduser()
        if not bundle_path.exists():
            raise ToolBlocked(f"Bundle not found: {bundle_path}")

        # If encrypted, decrypt to a temp tar first.
        temp_plain: Optional[Path] = None
        try:
            if bundle_path.name.endswith(".enc"):
                try:
                    CryptoError, CryptoUnavailable, _EncryptionSettings, _decrypt_file, _encrypt_file = _crypto()
                    enc = _EncryptionSettings.from_env()
                    tmp_dir = bundle_path.parent / ".dockerpilot-migration-tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    temp_plain = tmp_dir / (bundle_path.name[:-4])
                    _decrypt_file(in_path=bundle_path, out_path=temp_plain, settings=enc)
                    bundle_path = temp_plain
                except Exception as exc:
                    raise ToolBlocked(str(exc)) from exc
            elif bundle_path.name.endswith(".age"):
                try:
                    _, _, AgeSettings, _decrypt_age, _ = _age_crypto()
                    tmp_dir = bundle_path.parent / ".dockerpilot-migration-tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    temp_plain = tmp_dir / (bundle_path.name[:-4])
                    _decrypt_age(in_path=bundle_path, out_path=temp_plain, settings=AgeSettings.from_env())
                    bundle_path = temp_plain
                except Exception as exc:
                    raise ToolBlocked(str(exc)) from exc

            with bundle_path.open("rb") as f:
                with tarfile.open(fileobj=f, mode="r") as bundle_tar:
                    try:
                        manifest_member = bundle_tar.getmember("manifest.json")
                        manifest_bytes = bundle_tar.extractfile(manifest_member).read()  # type: ignore[union-attr]
                        manifest = json.loads(manifest_bytes.decode("utf-8"))
                    except Exception as exc:
                        raise ToolBlocked(f"Invalid bundle (manifest.json missing or unreadable): {exc}") from exc

                    container_name = target_name or manifest.get("container_name")
                    if not container_name:
                        raise ToolBlocked("Bundle missing container_name; provide target_name.")
                    require_container_allowed(self._config, str(container_name))

                    # Conflict behavior: default is fail (do not remove/replace target container).
                    exists = False
                    try:
                        self._client.containers.get(container_name)
                        exists = True
                    except Exception:
                        exists = False

                    target_existed = exists
                    planned: list[str] = []
                    if exists:
                        if on_conflict not in ("fail", "rename", "replace"):
                            raise ToolBlocked("on_conflict must be one of: fail, rename, replace.")
                        if on_conflict == "fail":
                            planned.append(
                                f"Would fail: target container '{container_name}' already exists (on_conflict=fail)."
                            )
                            return {"changed": False, "dry_run": True, "planned_actions": planned, "warnings": ["target_exists"]}
                        if on_conflict == "rename":
                            new_name = self._pick_available_name(str(container_name))
                            planned.append(f"Target exists; would use new name '{new_name}' (on_conflict=rename).")
                            container_name = new_name
                            require_container_allowed(self._config, str(container_name))
                            exists = False
                        elif on_conflict == "replace":
                            if not allow_replace:
                                planned.append(
                                    "Would fail: on_conflict=replace requires allow_replace=true (and DOCKERPILOT_MCP_ALLOW_DESTRUCTIVE=true)."
                                )
                                return {"changed": False, "dry_run": True, "planned_actions": planned, "warnings": ["target_exists"]}
                            planned.append(f"Would remove existing target container '{container_name}' (on_conflict=replace).")

                    planned.append(f"Would load image from bundle image.tar for '{container_name}'.")

                    if dry_run:
                        return {"changed": False, "dry_run": True, "planned_actions": planned, "warnings": []}

                    if target_existed and on_conflict == "replace":
                        target = self._client.containers.get(container_name)
                        target.remove(force=True)

                    # Load image
                    img_member = bundle_tar.getmember("image.tar")
                    img_f = bundle_tar.extractfile(img_member)
                    if img_f is None:
                        raise ToolBlocked("Bundle missing image.tar.")
                    self._client.api.load_image(img_f)

                    image_ref = manifest.get("image")
                    if not image_ref:
                        raise ToolBlocked("Bundle missing image reference.")

                    # Restore named volumes into freshly created target volumes.
                    volume_binds: dict[str, dict[str, str]] = {}
                    warnings: list[str] = []
                    helper_image = self._pick_local_helper_image()
                    if helper_image is None and (manifest.get("payload", {}) or {}).get("volumes"):
                        raise ToolBlocked(
                            "Bundle includes volumes but no local helper image available for restore (need busybox or alpine present locally)."
                        )

                    payload = manifest.get("payload") or {}
                    for vol in payload.get("volumes") or []:
                        if not isinstance(vol, dict):
                            continue
                        src_vol_name = str(vol.get("name") or "")
                        dest_path = str(vol.get("destination") or "")
                        member_path = str(vol.get("bundle_path") or "")
                        if not src_vol_name or not dest_path or not member_path:
                            continue
                        target_vol_name = f"migrated_{_safe_name(container_name)}_{_safe_name(src_vol_name)}"
                        planned.append(
                            f"Restoring named volume '{src_vol_name}' into new volume '{target_vol_name}' at '{dest_path}'."
                        )

                        # Create volume
                        self._client.volumes.create(name=target_vol_name)

                        # Extract volume tar to temp path, then untar into volume via helper container
                        tmp_dir = bundle_path.parent / ".dockerpilot-migration-tmp"
                        tmp_dir.mkdir(parents=True, exist_ok=True)
                        tmp_tar = tmp_dir / f"{_safe_name(target_vol_name)}.tar"
                        try:
                            m = bundle_tar.getmember(member_path)
                            vol_f = bundle_tar.extractfile(m)
                            if vol_f is None:
                                raise ToolBlocked(f"Missing volume payload: {member_path}")
                            with tmp_tar.open("wb") as out:
                                out.write(vol_f.read())

                            helper = self._client.containers.create(
                                image=helper_image,
                                command=["sh", "-c", "tar -C /data -xf /in/volume.tar"],
                                volumes={
                                    target_vol_name: {"bind": "/data", "mode": "rw"},
                                    str(tmp_dir): {"bind": "/in", "mode": "ro"},
                                },
                                working_dir="/",
                            )
                            try:
                                helper.start()
                                res = helper.wait(timeout=_migration_timeout_seconds())
                                status = (res or {}).get("StatusCode")
                                if status not in (0, "0", None):
                                    logs = helper.logs(tail=50)
                                    text = (
                                        logs.decode("utf-8", errors="replace")
                                        if isinstance(logs, (bytes, bytearray))
                                        else str(logs)
                                    )
                                    text, _ = cap_text(text, 8_000)
                                    raise ToolBlocked(
                                        f"Volume restore helper failed for {target_vol_name} (exit={status}). Logs: {text}"
                                    )
                            finally:
                                try:
                                    helper.remove(force=True)
                                except Exception:
                                    pass
                        finally:
                            try:
                                tmp_tar.unlink(missing_ok=True)  # type: ignore[arg-type]
                            except TypeError:  # pragma: no cover
                                if tmp_tar.exists():
                                    tmp_tar.unlink()

                        volume_binds[target_vol_name] = {"bind": dest_path, "mode": "rw"}

                    # Create container from image + restored volumes.
                    # Env values are not restored (secrets redaction) - user should configure secrets separately.
                    new_container = self._client.containers.create(
                        image=str(image_ref),
                        name=container_name,
                        detach=True,
                        volumes=volume_binds or None,
                    )
                    if start:
                        new_container.start()

                    warnings.append("env_not_restored")
                    return {"changed": True, "dry_run": False, "planned_actions": planned, "warnings": warnings}
        finally:
            if temp_plain is not None:
                try:
                    temp_plain.unlink()
                except Exception:
                    pass

    def _pick_available_name(self, base: str) -> str:
        safe = _safe_name(base) or "container"
        for i in range(1, 1000):
            candidate = f"{safe}-migrated-{i}"
            try:
                self._client.containers.get(candidate)
            except Exception:
                return candidate
        raise ToolBlocked("Unable to find an available container name for rename conflict policy.")


def _migration_timeout_seconds() -> int:
    try:
        val = int((os.environ.get("DOCKERPILOT_MCP_MIGRATION_TIMEOUT") or "").strip() or "300")
    except Exception:
        val = 300
    return max(1, val)
