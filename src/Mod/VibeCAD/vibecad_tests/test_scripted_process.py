# SPDX-License-Identifier: LGPL-2.1-or-later

"""Process-isolation checks for scripted CAD workers."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from VibeCADScriptedProcess import run_process


def test_large_worker_output_cannot_fill_a_parent_pipe(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import sys;"
            "sys.stdout.write('o' * 2_000_000 + 'STDOUT_END\\n');"
            "sys.stderr.write('e' * 2_000_000 + 'STDERR_END\\n')"
        ),
    ]

    result = run_process(
        command,
        cwd=tmp_path,
        environment=dict(os.environ),
        cancellation_check=None,
        timeout_seconds=10.0,
        memory_limit_bytes=0,
    )

    assert result["started"] is True
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert result["stdout"].endswith(f"STDOUT_END{os.linesep}")
    assert result["stderr"].endswith(f"STDERR_END{os.linesep}")
    assert len(result["stdout"]) <= 16_000
    assert len(result["stderr"]) <= 16_000
