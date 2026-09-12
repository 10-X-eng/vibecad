# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the process-lifetime native isolation runtime."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from io import StringIO
from contextlib import contextmanager

import pytest


@pytest.mark.parametrize("raises", [False, True])
def test_windows_job_enters_and_leaves_its_numerical_lease(tmp_path, monkeypatch, raises):
    import VibeCADIsolationWorker as worker
    import VibeCADNumericalRuntime as numerical

    (tmp_path / "worker.py").touch()
    calls = []

    @contextmanager
    def scope(slots, memory):
        calls.append(("enter", slots, memory))
        try:
            yield slots
        finally:
            calls.append(("leave", slots, memory))

    def execute(*args, **kwargs):
        assert calls == [("enter", 3, 1024)]
        assert worker.os.environ["OPENBLAS_NUM_THREADS"] == "1"
        if raises:
            raise RuntimeError("job failed")

    monkeypatch.setattr(worker.sys, "platform", "win32")
    monkeypatch.setattr(worker.App, "listDocuments", lambda: {}, raising=False)
    monkeypatch.setattr(worker.runpy, "run_path", execute)
    monkeypatch.setattr(numerical, "numerical_thread_limits", scope)
    before = dict(worker.os.environ)
    request = {"working_directory": str(tmp_path), "script": "worker.py",
               "environment": {}, "memory_limit_bytes": 1024}
    if raises:
        with pytest.raises(RuntimeError, match="job failed"):
            worker._run(request, 3)
    else:
        assert worker._run(request, 3)["returncode"] == 0
    assert calls == [("enter", 3, 1024), ("leave", 3, 1024)]
    assert dict(worker.os.environ) == before


def test_completion_record_starts_on_its_own_line_after_native_progress(monkeypatch):
    import VibeCADIsolationWorker as worker
    output = StringIO()
    monkeypatch.setattr(worker.sys, "__stdout__", output)
    output.write("\r\t\t(5 %)")
    record = f"{worker.PROTOCOL} RESULT 7 {worker._encode({'returncode': 0})}"
    worker._write(record)
    # QProcess consumes newline-delimited records and matches their prefix.
    records = output.getvalue().split("\n")
    assert any(line.startswith(f"{worker.PROTOCOL} RESULT 7 ") for line in records)
    assert records.count(record) == 1


def test_staged_script_uses_the_configured_process_lifetime_pool(
    tmp_path: Path,
) -> None:
    import VibeCADHostIsolation as isolation

    executable = tmp_path / "bin" / "FreeCADCmd.exe"
    executable.parent.mkdir()
    executable.touch()
    module_root = tmp_path / "Mod" / "VibeCAD"
    module_root.mkdir(parents=True)
    (module_root / "VibeCADIsolationWorker.py").touch()
    staging = tmp_path / "job"
    staging.mkdir()
    (staging / "worker.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    configured: list[tuple[str, str, int]] = []
    submitted: list[tuple[str, object, int, int]] = []

    def execute(
        request: str,
        cancellation_check: object,
        memory_limit: int,
        cpu_slots: int,
    ):
        submitted.append((request, cancellation_check, memory_limit, cpu_slots))
        response = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "schema": isolation.RESPONSE_SCHEMA,
                    "returncode": 0,
                    "stdout": "worker output",
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        return {
            "job_id": 17,
            "status": "completed",
            "response": response,
            "diagnostic": "",
            "output_tail": "native output",
            "memory_exceeded": False,
            "observed_memory_bytes": 123,
        }

    app = SimpleNamespace(
        configureHostIsolationRuntime=lambda exe, root, workers=0: configured.append(
            (exe, root, workers)
        )
        or {"workers": 3, "ready": 3},
        executeHostIsolationRequest=execute,
    )
    cancelled = lambda: False

    result = isolation.execute_staged_script(
        app=app,
        executable=executable,
        module_root=module_root,
        staging=staging,
        script="worker.py",
        environment={"VIBECAD_TEST": "1"},
        cancellation_check=cancelled,
        memory_limit_bytes=456,
    )

    assert configured == [(str(executable.resolve()), str(module_root.resolve()), 0)]
    assert len(submitted) == 1
    encoded_request, actual_cancellation, actual_limit, actual_cpu_slots = submitted[0]
    request = json.loads(base64.urlsafe_b64decode(encoded_request).decode("utf-8"))
    assert request == {
        "schema": isolation.REQUEST_SCHEMA,
        "working_directory": str(staging.resolve()),
        "script": "worker.py",
        "environment": {"VIBECAD_TEST": "1"},
        "memory_limit_bytes": 456,
    }
    assert actual_cancellation is cancelled
    assert actual_limit == 456
    assert actual_cpu_slots == 1
    assert result.pop("elapsed_seconds") >= 0.0
    assert result == {
        "started": True,
        "returncode": 0,
        "stdout": "worker output",
        "stderr": "native output",
        "cancelled": False,
        "timed_out": False,
        "memory_exceeded": False,
        "cpu_exceeded": False,
        "cancelled_by": None,
        "limit_reached": None,
        "termination_reason": "process_exit",
        "timeout_mode": "none",
        "activity_observations": 0,
        "memory_limit_bytes": 456,
        "observed_memory_bytes": 123,
        "job_id": 17,
        "worker_pid": 0,
        "worker_sequence": 0,
    }


def test_cancelled_pool_job_is_reported_without_a_process_fallback(
    tmp_path: Path,
) -> None:
    import VibeCADHostIsolation as isolation

    executable = tmp_path / "FreeCADCmd.exe"
    executable.touch()
    module_root = tmp_path / "module"
    module_root.mkdir()
    (module_root / "VibeCADIsolationWorker.py").touch()
    staging = tmp_path / "job"
    staging.mkdir()
    (staging / "worker.py").touch()

    app = SimpleNamespace(
        configureHostIsolationRuntime=lambda *_args: {"workers": 1, "ready": 1},
        executeHostIsolationRequest=lambda *_args: {
            "job_id": 9,
            "status": "cancelled",
            "response": "",
            "diagnostic": "Isolation job was cancelled",
            "output_tail": "",
            "memory_exceeded": False,
            "observed_memory_bytes": 0,
        },
    )

    result = isolation.execute_staged_script(
        app=app,
        executable=executable,
        module_root=module_root,
        staging=staging,
        script="worker.py",
        environment={},
        cancellation_check=None,
        memory_limit_bytes=0,
    )

    assert result["started"] is True
    assert result["cancelled"] is True
    assert result["cancelled_by"] == "host"
    assert result["termination_reason"] == "host_cancellation_request"
