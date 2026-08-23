from __future__ import annotations

import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path

# Temporary PR-only bridge so the existing main-branch pull_request workflow
# executes the ASXN bounded probe without changing production or private repos.
if importlib.util.find_spec("playwright") is None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "playwright"],
        check=True,
        timeout=120,
    )

runpy.run_path(str(Path(__file__).with_name("asxn_live_probe.py")), run_name="__main__")
