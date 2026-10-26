import subprocess
import sys

import pytest


@pytest.mark.e2e
def test_demo_path_runs_end_to_end():
    out = subprocess.run(
        [sys.executable, "-m", "app.demo", "проверка"], capture_output=True, text=True, check=True
    )
    assert "→" in out.stdout
