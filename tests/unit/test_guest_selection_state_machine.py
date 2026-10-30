"""Exercise guest selection success, rejection and network failure in Node."""

import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = PROJECT_ROOT / "tests" / "js" / "selection_failure.cjs"


def test_guest_selection_state_machine() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the JavaScript state-machine regression test"
    subprocess.run(  # noqa: S603 -- fixed local executable and checked-in scenario
        [node, str(SCENARIO)], cwd=PROJECT_ROOT, check=True, timeout=10
    )
