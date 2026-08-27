# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real process-tree ownership checks for the shared Analysis runner."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from VibeCADScriptedProcess import (
    ExternalProcessCancelled,
    ExternalProcessError,
    run_process_sequence,
)


def _pid_exists(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancel_reaps_descendant_process_tree(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN) "
        "if sys.platform != 'win32' else None; time.sleep(30)"
    )
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )

    def cancelled() -> bool:
        return child_pid_file.exists()

    with pytest.raises(ExternalProcessCancelled):
        run_process_sequence(
            ((sys.executable, ("-c", parent_code)),),
            working_directory=tmp_path,
            environment=os.environ,
            timeout_seconds=10,
            cancellation_check=cancelled,
            poll_seconds=0.01,
        )

    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5.0
    while _pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_exists(child_pid), "cancelled Analysis descendant survived cleanup"


def test_process_creation_uses_shell_free_isolated_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    import VibeCADScriptedProcess as process_runtime

    kwargs = process_runtime._process_creation_kwargs()
    if sys.platform == "win32":
        assert kwargs["start_new_session"] is False
        assert kwargs["creationflags"] & int(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ) == int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        assert kwargs == {"start_new_session": True, "creationflags": 0}


def test_backend_failure_redacts_environment_values_before_error(tmp_path: Path) -> None:
    secret = "solver-token-must-not-escape"
    program = (
        "import os, sys; "
        "print('token=' + os.environ['SOLVER_TOKEN']); sys.exit(9)"
    )
    with pytest.raises(ExternalProcessError) as caught:
        run_process_sequence(
            ((sys.executable, ("-c", program)),),
            working_directory=tmp_path,
            environment={**os.environ, "SOLVER_TOKEN": secret},
            timeout_seconds=10,
            cancellation_check=lambda: False,
            poll_seconds=0.01,
        )

    assert caught.value.reason == "backend_failed"
    assert secret not in caught.value.detail
    assert caught.value.detail == "token=[REDACTED]"
