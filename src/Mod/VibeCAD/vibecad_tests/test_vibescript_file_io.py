# SPDX-License-Identifier: LGPL-2.1-or-later

"""Windows-safe VibeScript project-file publication contracts."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import threading

import pytest


def _access_denied(path: Path) -> PermissionError:
    error = PermissionError(errno.EACCES, "Access is denied", str(path))
    error.winerror = 5
    return error


def test_atomic_write_retries_a_transient_windows_sharing_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADVibeScriptFileIO as file_io

    destination = tmp_path / "program.json"
    real_replace = file_io._replace_path
    attempted_sources: list[Path] = []
    attempts = 0

    def transient_replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        attempted_sources.append(Path(source))
        if attempts < 3:
            raise _access_denied(Path(target))
        real_replace(source, target)

    monkeypatch.setattr(file_io, "_replace_path", transient_replace)

    published = file_io.atomic_write_text(
        destination,
        json.dumps({"revision": 1}),
        replace_timeout_seconds=1.0,
    )

    assert published is True
    assert attempts == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"revision": 1}
    assert len(set(attempted_sources)) == 1
    assert not list(tmp_path.glob("*.tmp"))

    file_io.atomic_write_text(
        destination,
        json.dumps({"revision": 2}),
        replace_timeout_seconds=1.0,
    )
    assert attempted_sources[-1] != attempted_sources[0]
    assert json.loads(destination.read_text(encoding="utf-8")) == {"revision": 2}


def test_best_effort_progress_write_never_rejects_valid_cad_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADVibeScriptFileIO as file_io

    destination = tmp_path / "progress.json"
    destination.write_text('{"phase":"prior"}', encoding="utf-8")

    def permanently_locked(_source: Path, target: Path) -> None:
        raise _access_denied(Path(target))

    monkeypatch.setattr(file_io, "_replace_path", permanently_locked)

    published = file_io.atomic_write_text(
        destination,
        '{"phase":"next"}',
        replace_timeout_seconds=0.0,
        best_effort=True,
    )

    assert published is False
    assert destination.read_text(encoding="utf-8") == '{"phase":"prior"}'
    assert not list(tmp_path.glob("*.tmp"))


def test_worker_item_progress_retains_rate_and_eta(tmp_path: Path) -> None:
    import vibescript_worker_progress as progress

    destination = tmp_path / "progress.json"
    progress.configure(destination, "assembly")
    progress.set_phase("simulation_collision", output="Simulation")
    progress.set_item_progress(
        "collision_frame",
        completed=60,
        total=141,
        current="60",
        rate_per_second=0.075,
        estimated_remaining_seconds=1080.0,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["item_progress"] == {
        "kind": "collision_frame",
        "completed": 60,
        "total": 141,
        "current": "60",
        "rate_per_second": 0.075,
        "estimated_remaining_seconds": 1080.0,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing flags are NT-specific")
def test_windows_reader_allows_atomic_replacement_while_open(tmp_path: Path) -> None:
    import VibeCADVibeScriptFileIO as file_io

    destination = tmp_path / "program.json"
    destination.write_bytes(b"before")

    with file_io.open_shared_binary(destination) as stream:
        assert file_io.atomic_write_text(
            destination,
            "after",
            replace_timeout_seconds=2.0,
        )
        assert stream.read() == b"before"

    assert destination.read_bytes() == b"after"


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing flags are NT-specific")
def test_windows_progress_polling_never_observes_a_partial_snapshot(
    tmp_path: Path,
) -> None:
    import VibeCADVibeScriptFileIO as file_io

    destination = tmp_path / "progress.json"
    file_io.atomic_write_text(destination, json.dumps({"revision": 0}))
    stop = threading.Event()
    failures: list[BaseException] = []
    observed: list[int] = []

    def poll() -> None:
        while not stop.is_set():
            try:
                value = json.loads(file_io.read_text_shared(destination))
                observed.append(int(value["revision"]))
            except BaseException as exc:
                failures.append(exc)
                stop.set()

    reader = threading.Thread(target=poll)
    reader.start()
    try:
        for revision in range(1, 201):
            assert file_io.atomic_write_text(
                destination,
                json.dumps({"revision": revision}),
            )
    finally:
        stop.set()
        reader.join(timeout=5.0)

    assert not reader.is_alive()
    assert failures == []
    assert observed
    assert json.loads(file_io.read_text_shared(destination)) == {"revision": 200}


def test_worker_result_uses_in_memory_progress_when_status_file_is_locked() -> None:
    import vibescript_domain_worker as worker

    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert 'response["worker_progress"] = worker_progress.snapshot()' in source
    assert '(root / "progress.json").read_text' not in source


def test_domain_execution_renews_its_lease_from_worker_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADScriptedProcess as scripted
    import VibeCADVibeScriptDomainRuntime as runtime

    observed: dict[str, object] = {}

    def run_process(_command, **kwargs):
        observed.update(kwargs)
        return {
            "started": True,
            "returncode": -1,
            "cancelled": False,
            "timed_out": True,
            "memory_exceeded": False,
            "cpu_exceeded": False,
            "timeout_mode": "inactivity",
        }

    monkeypatch.setattr(scripted, "run_process", run_process)
    prepared = {
        "tool_name": "vibescript.assembly.edit_source",
        "freecadcmd_executable": "FreeCADCmd",
        "staging": str(tmp_path),
        "timeout_seconds": 3600.0,
        "memory_limit_bytes": 1024,
        "live_outputs_before": {},
    }

    result = runtime.execute_candidate(prepared, cancellation_check=None)

    activity_check = observed["activity_check"]
    assert callable(activity_check)
    assert activity_check() is None
    (tmp_path / "progress.json").write_text('{"frame":1}', encoding="utf-8")
    first = activity_check()
    assert first is not None
    (tmp_path / "progress.json").write_text('{"frame":22}', encoding="utf-8")
    assert activity_check() != first
    assert result["failure_code"] == "DOMAIN_EXECUTION_TIMEOUT"
    assert "made no observable progress for 3600 seconds" in result["error"]
