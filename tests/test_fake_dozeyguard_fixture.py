from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_DG = ROOT / "tests" / "fixtures" / "secure_deploy_preview" / "fake_dozeyguard.py"
CONTROLLED_PATH = "/usr/local/bin:/usr/bin:/bin"


def test_fake_dozeyguard_runs_in_restricted_adapter_environment():
    payload = b'{"services":{}}'
    completed = subprocess.run(
        [str(FAKE_DG), "scan", "--input", "-"],
        input=payload,
        capture_output=True,
        timeout=5,
        shell=False,
        close_fds=True,
        env={"PATH": CONTROLLED_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        cwd="/",
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    report = json.loads(completed.stdout.decode("utf-8"))
    assert report["scanner"]["name"] == "dozeyguard"
    assert report["result"]["exit_code"] == 0
    assert len(report["result"]["result_sha256"]) == 64
