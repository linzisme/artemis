"""The project-local entrypoint must work independently of the caller's cwd."""

import os
from pathlib import Path
import subprocess
import sys


def test_codex_local_help_from_another_directory(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/codex_local.py"), "--help"],
        cwd=tmp_path,
        env={**os.environ, "ARTEMIS_CODEX_STATE_DIR": str(tmp_path / "runtime")},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout and "mcp" in result.stdout
    assert (tmp_path / "runtime").is_dir()
