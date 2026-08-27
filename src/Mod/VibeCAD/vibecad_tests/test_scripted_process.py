# SPDX-License-Identifier: LGPL-2.1-or-later

"""Process-isolation checks for scripted CAD workers."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

from VibeCADScriptedProcess import run_process


def test_windows_termination_targets_the_entire_worker_tree(monkeypatch) -> None:
    import VibeCADScriptedProcess as scripted

    calls = []

    class Process:
        pid = 4321
        returncode = None

        def poll(self):
            return None if self.returncode is None else self.returncode

        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = -1
            return self.returncode

        def terminate(self):
            pytest.fail("a Windows worker cancellation must terminate its process tree")

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(scripted.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(
        scripted.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0),
    )

    scripted.terminate_process_tree(Process())

    assert calls[0][0] == [
        r"C:\Windows\System32\taskkill.exe",
        "/PID",
        "4321",
        "/T",
        "/F",
    ]
    assert calls[0][1]["shell"] is False


@pytest.mark.parametrize("attempt", range(4))
def test_large_worker_output_cannot_fill_a_parent_pipe(
    tmp_path: Path,
    attempt: int,
) -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import sys;"
            f"sys.stdout.write('o' * 2_000_000 + 'STDOUT_END_{attempt}\\n');"
            f"sys.stderr.write('e' * 2_000_000 + 'STDERR_END_{attempt}\\n')"
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
    assert result["stdout"].endswith(f"STDOUT_END_{attempt}{os.linesep}")
    assert result["stderr"].endswith(f"STDERR_END_{attempt}{os.linesep}")
    assert len(result["stdout"]) <= 16_000
    assert len(result["stderr"]) <= 16_000
    assert result["termination_reason"] == "process_exit"
    assert result["cancelled_by"] is None
    assert result["limit_reached"] is None
    assert result["timeout_seconds"] == 10.0


def test_worker_cancellation_reports_exact_actor_and_preserved_limit() -> None:
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=Path.cwd(),
        environment=dict(os.environ),
        cancellation_check=lambda: True,
        timeout_seconds=7.0,
        memory_limit_bytes=123_456,
    )

    assert result["started"] is True
    assert result["cancelled"] is True
    assert result["cancelled_by"] == "host"
    assert result["termination_reason"] == "host_cancellation_request"
    assert result["limit_reached"] is None
    assert result["timeout_seconds"] == 7.0
    assert result["memory_limit_bytes"] == 123_456


def test_worker_cpu_signal_reports_cpu_limit_instead_of_generic_exit() -> None:
    if sys.platform == "win32" or not hasattr(signal, "SIGXCPU"):
        return

    result = run_process(
        [
            sys.executable,
            "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGXCPU)",
        ],
        cwd=Path.cwd(),
        environment=dict(os.environ),
        cancellation_check=None,
        timeout_seconds=7.0,
        memory_limit_bytes=0,
    )

    assert result["started"] is True
    assert result["returncode"] == -int(signal.SIGXCPU)
    assert result["cpu_exceeded"] is True
    assert result["cancelled"] is False
    assert result["termination_reason"] == "cpu_time_limit"
    assert result["limit_reached"] == "cpu_seconds"
