"""Run the dependency-free JavaScript regression scenarios for GitHub issue #3."""

import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = PROJECT_ROOT / "tests" / "js" / "upload_progress_state_machine.cjs"


def test_upload_progress_state_machine() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the JavaScript state-machine regression test"

    subprocess.run(  # noqa: S603 -- fixed local executable and checked-in test scenario
        [node, str(SCENARIO)],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=10,
    )
