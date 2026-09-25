from pathlib import Path
from types import SimpleNamespace
import sys

EXTRAS = Path(__file__).resolve().parents[1] / "DockerPilotExtras"
if str(EXTRAS) not in sys.path:
    sys.path.insert(0, str(EXTRAS))

from backend.services import promotion_runtime


class Logger:
    def error(self, *_a, **_k): pass


def test_apply_env_resource_presets():
    deployment={}
    promotion_runtime.apply_env_resource_presets(deployment,"prod")
    assert deployment=={"cpu_limit":"2.0","memory_limit":"2Gi"}


def test_local_promotion_delegates_to_pilot(tmp_path):
    config=tmp_path/"deployment.yml"
    config.write_text("deployment:\n  container_name: api\n", encoding="utf-8")
    calls=[]
    pilot=SimpleNamespace(environment_promotion=lambda *args: calls.append(args) or True)
    ok=promotion_runtime.promote_config_to_server(
        "local", str(config), "staging", "prod", True,
        get_dockerpilot=lambda: pilot,
        get_server_config_by_id=lambda _sid: None,
        write_remote_file_fn=lambda *_a, **_k: None,
        build_dockerpilot_deploy_command=lambda *_a, **_k: "unused",
        execute_command=lambda *_a, **_k: None,
        logger=Logger(),
    )
    assert ok is True
    assert calls==[("staging","prod",str(config),True)]


def test_remote_promotion_writes_config_and_executes_safe_builder(tmp_path):
    config=tmp_path/"deployment.yml"
    config.write_text("deployment:\n  container_name: api\n", encoding="utf-8")
    written=[]; executed=[]
    ok=promotion_runtime.promote_config_to_server(
        "prod-host", str(config), "staging", "prod", False,
        get_dockerpilot=lambda: None,
        get_server_config_by_id=lambda _sid: {"id":"prod-host","username":"dozey"},
        write_remote_file_fn=lambda *args: written.append(args),
        build_dockerpilot_deploy_command=lambda path, dtype, skip_backup=False: f"DEPLOY::{path}::{dtype}::{skip_backup}",
        execute_command=lambda *args, **kwargs: executed.append((args,kwargs)),
        logger=Logger(),
    )
    assert ok is True
    assert written and "/home/dozey/.dockerpilot_extras/deployments/api/deployment-prod.yml" in written[0][1]
    assert executed[0][0][1].endswith("::blue-green::False")
