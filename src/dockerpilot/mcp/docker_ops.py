from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Any, Optional, Tuple

import docker

from .context import MCPConfig
from .safety import ToolBlocked, cap_text, env_list_to_dict, redact_env_dict, redact_labels, redact_obj, redact_text


class DockerUnavailable(RuntimeError):
    pass


def _iso_from_epoch(seconds: int | float | None) -> Optional[str]:
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _epoch_from_created(created: Any) -> Optional[int]:
    if created is None:
        return None
    if isinstance(created, (int, float)):
        return int(created)
    if isinstance(created, str):
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            return None
    return None


def _short_id(full_id: str | None) -> str:
    if not full_id:
        return ""
    return full_id.split(":", 1)[-1][:12]


def _ports_to_mapping(ports: list[dict[str, Any]] | None) -> dict[str, list[dict[str, str]]] | None:
    if not ports:
        return None
    out: dict[str, list[dict[str, str]]] = {}
    for p in ports:
        private = p.get("PrivatePort")
        proto = p.get("Type") or "tcp"
        if not private:
            continue
        key = f"{private}/{proto}"
        host_ip = p.get("IP")
        host_port = p.get("PublicPort")
        if host_ip is None or host_port is None:
            out.setdefault(key, [])
            continue
        out.setdefault(key, []).append({"HostIp": str(host_ip), "HostPort": str(host_port)})
    return out or None


@dataclass
class DockerClientProvider:
    config: MCPConfig
    _client: Optional[docker.DockerClient] = None

    def client(self) -> docker.DockerClient:
        if self._client is not None:
            return self._client
        try:
            self._client = docker.from_env()
            # Do not force a ping here; keep init lazy.
            return self._client
        except Exception as exc:
            raise DockerUnavailable(str(exc)) from exc


class DockerOps:
    def __init__(self, provider: DockerClientProvider):
        self._provider = provider

    def _client(self) -> docker.DockerClient:
        return self._provider.client()

    def system_summary(self) -> dict[str, Any]:
        warnings: list[str] = []
        try:
            client = self._client()
            version_info = client.version()
            server_version = version_info.get("Version")
        except Exception as exc:
            return {
                "docker_available": False,
                "server_version": None,
                "containers": {"total": 0, "running": 0, "exited": 0, "unhealthy": 0},
                "images": {"total": 0},
                "warnings": [f"Docker daemon unavailable: {exc}"],
            }

        containers = client.api.containers(all=True)
        total = len(containers)
        running = sum(1 for c in containers if (c.get("State") or "").lower() == "running")
        exited = sum(1 for c in containers if (c.get("State") or "").lower() == "exited")

        unhealthy = 0
        for c in containers:
            if (c.get("State") or "").lower() != "running":
                continue
            cid = c.get("Id")
            if not cid:
                continue
            try:
                attrs = client.api.inspect_container(cid)
                health = (((attrs.get("State") or {}).get("Health") or {}).get("Status") or "").lower()
                if health == "unhealthy":
                    unhealthy += 1
            except Exception:
                continue

        try:
            images = client.images.list()
            image_total = len(images)
        except Exception as exc:
            image_total = 0
            warnings.append(f"Unable to list images: {exc}")

        return {
            "docker_available": True,
            "server_version": str(server_version) if server_version else None,
            "containers": {"total": total, "running": running, "exited": exited, "unhealthy": unhealthy},
            "images": {"total": image_total},
            "warnings": warnings,
        }

    def list_containers(self, all_containers: bool, include_ports: bool, include_labels: bool) -> dict[str, Any]:
        client = self._client()
        containers = client.api.containers(all=all_containers)
        result: list[dict[str, Any]] = []
        for c in containers:
            cid = c.get("Id") or ""
            names = c.get("Names") or []
            name = names[0].lstrip("/") if names else ""
            ports = _ports_to_mapping(c.get("Ports")) if include_ports else None
            labels = (c.get("Labels") or None) if include_labels else None
            if labels and self._provider.config.redact_secrets:
                labels = redact_labels(labels)
            created = _iso_from_epoch(c.get("Created"))
            entry = {
                "id": cid,
                "short_id": _short_id(cid),
                "name": name,
                "image": c.get("Image") or "",
                "status": c.get("Status") or "",
                "state": (c.get("State") or "").lower(),
                "health": None,
                "ports": ports,
                "created": created,
                "labels": labels,
            }
            result.append(entry)
        return {"containers": result}

    def get_container(self, name: str):
        client = self._client()
        try:
            return client.containers.get(name)
        except docker.errors.NotFound as exc:
            raise ToolBlocked(f"Container not found: {name}") from exc

    def inspect_container_curated(self, name: str, redact: bool) -> dict[str, Any]:
        container = self.get_container(name)
        attrs = container.attrs or {}
        cfg = attrs.get("Config") or {}
        state = attrs.get("State") or {}
        host_cfg = attrs.get("HostConfig") or {}
        net_settings = attrs.get("NetworkSettings") or {}

        env_dict = env_list_to_dict(cfg.get("Env"))
        labels = cfg.get("Labels") or {}
        curated = {
            "id": attrs.get("Id"),
            "name": (attrs.get("Name") or "").lstrip("/") or container.name,
            "image": cfg.get("Image"),
            "state": {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "paused": state.get("Paused"),
                "restarting": state.get("Restarting"),
                "oom_killed": state.get("OOMKilled"),
                "dead": state.get("Dead"),
                "pid": state.get("Pid"),
                "exit_code": state.get("ExitCode"),
                "error": state.get("Error"),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "restart_count": state.get("RestartCount"),
            },
            "health": state.get("Health"),
            "ports": net_settings.get("Ports"),
            "mounts": [
                {
                    "type": m.get("Type"),
                    "source": m.get("Source"),
                    "destination": m.get("Destination"),
                    "mode": m.get("Mode"),
                    "rw": m.get("RW"),
                    "propagation": m.get("Propagation"),
                }
                for m in (attrs.get("Mounts") or [])
            ],
            "networks": net_settings.get("Networks"),
            "restart_policy": (host_cfg.get("RestartPolicy") or {}),
            "env": env_dict,
            "labels": labels,
        }

        effective_redact = True if self._provider.config.redact_secrets else bool(redact)
        if effective_redact:
            curated["env"] = redact_env_dict(curated["env"])
            curated["labels"] = redact_labels(curated.get("labels"))
            curated["health"] = redact_obj(curated.get("health"))
            curated["networks"] = redact_obj(curated.get("networks"))
            curated["mounts"] = redact_obj(curated.get("mounts"))
            curated["ports"] = redact_obj(curated.get("ports"))
            curated["restart_policy"] = redact_obj(curated.get("restart_policy"))
        return {"container": curated, "redacted": bool(effective_redact)}

    def container_logs(self, name: str, tail: int, since: Optional[str], timestamps: bool, redact: bool) -> dict[str, Any]:
        config = self._provider.config
        container = self.get_container(name)
        effective_tail = min(max(1, int(tail)), config.max_log_lines)
        truncated = effective_tail != tail

        since_arg: Any = None
        if since:
            s = since.strip()
            if s.isdigit():
                since_arg = int(s)
            else:
                try:
                    since_arg = datetime.fromisoformat(s.replace("Z", "+00:00"))
                except Exception:
                    since_arg = None

        raw = container.logs(tail=effective_tail, since=since_arg, timestamps=timestamps)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        effective_redact = True if config.redact_secrets else bool(redact)
        if effective_redact:
            text = redact_text(text)
        text, truncated_bytes = cap_text(text, config.max_log_bytes)
        truncated = truncated or truncated_bytes
        return {"name": name, "tail": effective_tail, "logs": text, "truncated": bool(truncated)}

    def container_stats(self, name: str) -> dict[str, Any]:
        container = self.get_container(name)
        stats = container.stats(stream=False) or {}
        cpu_percent = _cpu_percent(stats)
        mem_usage = ((stats.get("memory_stats") or {}).get("usage")) if isinstance(stats.get("memory_stats"), dict) else None
        mem_limit = ((stats.get("memory_stats") or {}).get("limit")) if isinstance(stats.get("memory_stats"), dict) else None
        mem_percent = None
        try:
            if mem_usage is not None and mem_limit:
                mem_percent = float(mem_usage) / float(mem_limit) * 100.0
        except Exception:
            mem_percent = None

        net_rx, net_tx = _network_bytes(stats)
        pids = ((stats.get("pids_stats") or {}).get("current")) if isinstance(stats.get("pids_stats"), dict) else None

        return {
            "name": name,
            "cpu_percent": cpu_percent,
            "memory_usage": mem_usage,
            "memory_limit": mem_limit,
            "memory_percent": mem_percent,
            "network_rx": net_rx,
            "network_tx": net_tx,
            "pids": pids,
        }

    def list_images(self, all_images: bool, hide_untagged: bool) -> dict[str, Any]:
        client = self._client()
        images = client.images.list(all=all_images)
        out: list[dict[str, Any]] = []
        for img in images:
            tags = list(img.tags or [])
            if hide_untagged and (not tags or tags == ["<none>:<none>"]):
                continue
            out.append(
                {
                    "id": img.id,
                    "short_id": _short_id(img.id),
                    "tags": tags,
                    "created": _epoch_from_created((img.attrs or {}).get("Created")),
                    "size": (img.attrs or {}).get("Size"),
                }
            )
        return {"images": out}

    def list_networks(self) -> dict[str, Any]:
        client = self._client()
        networks = client.networks.list()
        out: list[dict[str, Any]] = []
        for n in networks:
            attrs = n.attrs or {}
            containers = list((attrs.get("Containers") or {}).keys()) if isinstance(attrs.get("Containers"), dict) else []
            out.append(
                {
                    "id": attrs.get("Id") or n.id,
                    "name": attrs.get("Name") or n.name,
                    "driver": attrs.get("Driver"),
                    "scope": attrs.get("Scope"),
                    "containers": containers,
                }
            )
        return {"networks": out}

    def list_volumes(self, redact: bool = True) -> dict[str, Any]:
        client = self._client()
        vols = client.volumes.list()
        out: list[dict[str, Any]] = []
        for v in vols:
            attrs = v.attrs or {}
            labels = attrs.get("Labels")
            entry = {
                "name": attrs.get("Name") or v.name,
                "driver": attrs.get("Driver"),
                "mountpoint": attrs.get("Mountpoint"),
                "labels": labels,
            }
            effective_redact = True if self._provider.config.redact_secrets else bool(redact)
            if effective_redact and labels:
                entry["labels"] = redact_labels(labels)
            out.append(entry)
        return {"volumes": out}

    def container_start(self, name: str, dry_run: bool) -> Tuple[bool, str]:
        container = self.get_container(name)
        if dry_run:
            return False, f"Dry run: would start container '{name}'."
        container.start()
        return True, f"Started container '{name}'."

    def container_stop(self, name: str, timeout: int, dry_run: bool) -> Tuple[bool, str]:
        container = self.get_container(name)
        if dry_run:
            return False, f"Dry run: would stop container '{name}' (timeout={timeout})."
        container.stop(timeout=timeout)
        return True, f"Stopped container '{name}'."

    def container_restart(self, name: str, timeout: int, dry_run: bool) -> Tuple[bool, str]:
        container = self.get_container(name)
        if dry_run:
            return False, f"Dry run: would restart container '{name}' (timeout={timeout})."
        container.restart(timeout=timeout)
        return True, f"Restarted container '{name}'."

    def container_remove(self, name: str, force: bool, dry_run: bool) -> Tuple[bool, str]:
        container = self.get_container(name)
        if dry_run:
            return False, f"Dry run: would remove container '{name}' (force={force})."
        container.remove(force=force)
        return True, f"Removed container '{name}'."

    def image_prune(self, dangling_only: bool, dry_run: bool) -> dict[str, Any]:
        client = self._client()
        if dry_run:
            # Estimate candidates without pruning.
            images = client.images.list(all=True)
            deleted: list[str] = []
            for img in images:
                tags = img.tags or []
                if dangling_only and tags:
                    continue
                if not tags or tags == ["<none>:<none>"]:
                    deleted.append(img.id)
            return {
                "changed": False,
                "dry_run": True,
                "deleted": deleted,
                "space_reclaimed": None,
                "message": f"Dry run: would prune {'dangling' if dangling_only else 'unused'} images ({len(deleted)} candidates).",
            }
        filters = {"dangling": dangling_only}
        result = client.images.prune(filters=filters)
        deleted_raw = result.get("ImagesDeleted") or []
        deleted: list[str] = []
        for item in deleted_raw:
            if isinstance(item, str):
                deleted.append(item)
            elif isinstance(item, dict):
                if item.get("Deleted"):
                    deleted.append(str(item["Deleted"]))
                if item.get("Untagged"):
                    deleted.append(str(item["Untagged"]))
        return {
            "changed": True,
            "dry_run": False,
            "deleted": deleted,
            "space_reclaimed": result.get("SpaceReclaimed"),
            "message": "Image prune completed.",
        }

    def container_exec(self, name: str, command: Any, timeout: int | None) -> dict[str, Any]:
        config = self._provider.config
        container = self.get_container(name)
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ToolBlocked("command must be a non-empty list[str].")
        cmd = [item for item in command if item.strip()]
        if not cmd:
            raise ToolBlocked("command must be a non-empty list[str].")

        effective_timeout = int(timeout) if timeout is not None else int(config.exec_timeout)
        if effective_timeout < 1:
            effective_timeout = 1
        timed_out = False

        stdout_text = ""
        stderr_text = ""
        exit_code: Optional[int] = None
        exec_error: Optional[dict[str, str]] = None

        def run_exec() -> None:
            nonlocal stdout_text, stderr_text, exit_code, exec_error
            try:
                # Explicitly pass list[str] (no shell wrapping), no TTY, demuxed output.
                res = container.exec_run(cmd, demux=True, tty=False)
                exit_code = getattr(res, "exit_code", None)
                out = getattr(res, "output", None)
                if isinstance(out, tuple) and len(out) == 2:
                    out_stdout, out_stderr = out
                else:
                    out_stdout, out_stderr = out, b""
                if isinstance(out_stdout, (bytes, bytearray)):
                    stdout_text = out_stdout.decode("utf-8", errors="replace")
                else:
                    stdout_text = str(out_stdout or "")
                if isinstance(out_stderr, (bytes, bytearray)):
                    stderr_text = out_stderr.decode("utf-8", errors="replace")
                else:
                    stderr_text = str(out_stderr or "")
            except Exception as exc:
                exec_error = {"kind": exc.__class__.__name__, "message": str(exc)}

        thread = threading.Thread(target=run_exec, daemon=True)
        thread.start()
        thread.join(timeout=float(effective_timeout))
        if thread.is_alive():
            timed_out = True
            exec_error = {"kind": "Timeout", "message": f"Exec exceeded timeout of {effective_timeout}s."}

        truncated = False
        stdout_text, trunc_out = cap_text(stdout_text, config.max_exec_bytes)
        stderr_text, trunc_err = cap_text(stderr_text, config.max_exec_bytes)
        truncated = trunc_out or trunc_err

        if config.redact_secrets:
            stdout_text = redact_text(stdout_text)
            stderr_text = redact_text(stderr_text)

        result: dict[str, Any] = {
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": bool(timed_out),
            "truncated": bool(truncated),
        }
        if exec_error is not None:
            result["error"] = exec_error
        return result


def _cpu_percent(stats: dict[str, Any]) -> Optional[float]:
    try:
        cpu_stats = stats.get("cpu_stats") or {}
        precpu_stats = stats.get("precpu_stats") or {}
        cpu_delta = (cpu_stats.get("cpu_usage") or {}).get("total_usage", 0) - (precpu_stats.get("cpu_usage") or {}).get(
            "total_usage", 0
        )
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
        online_cpus = cpu_stats.get("online_cpus") or len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or [])
        if system_delta <= 0 or cpu_delta < 0 or not online_cpus:
            return None
        return float(cpu_delta) / float(system_delta) * float(online_cpus) * 100.0
    except Exception:
        return None


def _network_bytes(stats: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    try:
        networks = stats.get("networks") or {}
        if not isinstance(networks, dict):
            return None, None
        rx = 0
        tx = 0
        for iface in networks.values():
            if not isinstance(iface, dict):
                continue
            rx += int(iface.get("rx_bytes") or 0)
            tx += int(iface.get("tx_bytes") or 0)
        return rx, tx
    except Exception:
        return None, None
