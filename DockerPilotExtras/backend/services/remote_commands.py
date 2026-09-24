"""Builders for intentionally remote shell commands with strict quoting."""

from __future__ import annotations

import base64
import shlex


def build_remote_file_write_command(remote_path: str, content: str) -> str:
    """Build a remote Python command that writes exact bytes without shell interpolation."""
    payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = (
        "import base64,pathlib; "
        f"p=pathlib.Path({remote_path!r}); "
        "p.parent.mkdir(parents=True,exist_ok=True); "
        f"p.write_bytes(base64.b64decode({payload!r})); "
        "print('OK')"
    )
    return f"python3 -c {shlex.quote(script)}"


def build_dockerpilot_deploy_command(
    remote_config_path: str,
    deployment_type: str,
    *,
    skip_backup: bool = False,
) -> str:
    allowed_types = {"rolling", "blue-green", "canary", "quick"}
    if deployment_type not in allowed_types:
        raise ValueError(f"Unsupported deployment type: {deployment_type}")
    argv = ["dockerpilot", "deploy", "config", remote_config_path, "--type", deployment_type]
    if skip_backup:
        argv.append("--skip-backup")
    return shlex.join(argv)
