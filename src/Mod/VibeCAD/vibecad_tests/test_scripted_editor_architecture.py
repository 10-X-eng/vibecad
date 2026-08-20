# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression contracts for the lightweight, native model code editor."""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

import VibeCADVibeScriptDomains as domains
import VibeCADVibeScriptDomainRuntime as runtime
import VibeCADScriptedEditor as editor
from VibeCADScriptedEditor import _diagnostic_detail_text


ROOT = Path(__file__).resolve().parents[4]


class _ShapeTrap:
    Name = "HeavyAssemblyShape"
    Label = "Heavy Assembly Shape"
    TypeId = "Part::Feature"
    PropertiesList: list[str] = []

    @property
    def Shape(self):
        raise AssertionError("The editor program index must never access Shape")


class _Document:
    Name = "EditorIndex"
    Uid = "editor-index-document"
    Objects = [_ShapeTrap()]


class _Service:
    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def active_workbench_name() -> str:
        return "AssemblyWorkbench"

    @staticmethod
    def project_scope_snapshot() -> dict[str, str]:
        return {"root": ""}

    @staticmethod
    def _active_document() -> _Document:
        return _Document()


def _finish_delete_fixture(pack, trash: Path, program_id: str) -> dict:
    return runtime.finish_delete(
        {
            "trash_directory": str(trash),
            "program_id": program_id,
            "pack": pack,
            "arguments": {"reason": "Deleted from Model Code Editor"},
        },
        {"deleted_objects": []},
    )


def test_editor_program_index_never_captures_domain_geometry() -> None:
    snapshot = domains.domain_program_index_snapshot(_Service(), "assembly")
    assert snapshot["native_programs"] == []
    assert "assembly_component_shapes" not in snapshot
    assert "part_document_shapes" not in snapshot
    completed = domains.complete_domain_program_index(snapshot)
    assert completed["ok"] is True
    assert completed["programs"] == []
    assert "component_candidates" not in completed


def test_editor_is_vibescript_only_with_three_human_actions() -> None:
    source = (ROOT / "src/Mod/VibeCAD/VibeCADScriptedEditor.py").read_text(encoding="utf-8")
    session = (ROOT / "src/Mod/VibeCAD/VibeCADSession.py").read_text(encoding="utf-8")
    assert "domain_program_index_snapshot(" in source
    assert "domain_context_snapshot(" not in source
    assert ".timer.start()" not in source
    assert "dock.setMinimumWidth" not in source
    assert "dock.setMinimumHeight" not in source
    assert "widget.setMinimumWidth" not in source
    assert "widget.setMinimumHeight" not in source
    assert '"VibeScriptedContentSplitter"' in source
    assert '("New", "VibeScriptedNew"' in source
    assert '"Save",' in source
    assert '"VibeScriptedSave"' in source
    assert '"Build", "VibeScriptedRender"' in source
    assert 'QtWidgets.QToolButton(selector_row)' in source
    assert 'setObjectName("VibeScriptedDelete")' in source
    assert '"vibescript_program_deleted"' in source
    assert 'f"vibescript.{self.domain}.delete_program"' in source
    assert "VibeScriptedAccept" not in source
    assert "VibeScriptedRevert" not in source
    assert "VibeScriptedImport" not in source
    assert "VibeScriptedExport" not in source
    assert "QFileSystemWatcher" not in source
    assert 'SCRIPTED_ENGINES = {"vibescript"}' in source
    assert "self._start_vibescript_apply()" in source
    assert "self._adopt_failed_vibescript_revision(result)" in source
    assert 'captured["allow_unchanged_revision"] = True' in session
    assert "itemDoubleClicked.connect(" in source
    assert 'setObjectName("VibeScriptedDiagnosticDetail")' in source
    assert 'setObjectName("VibeScriptedDiagnosticDetailText")' in source


def test_editor_formats_complete_build_error_details() -> None:
    payload = {
        "error": "VibeScript domain execution was cancelled.",
        "failure_code": "RUN_CANCELLED",
        "failure_stage": "external_process",
        "observed": {
            "cancelled_by": "host",
            "termination_reason": "host_cancellation_request",
            "returncode": -15,
        },
    }
    detail = _diagnostic_detail_text(payload)
    assert detail.startswith("VibeScript domain execution was cancelled.")
    assert "Failure code: RUN_CANCELLED" in detail
    assert "Failure stage: external_process" in detail
    assert '"cancelled_by": "host"' in detail
    assert '"termination_reason": "host_cancellation_request"' in detail


def test_same_document_refresh_cannot_cancel_an_active_editor_job(
    monkeypatch,
) -> None:
    document = type("Document", (), {"Name": "Model", "Uid": "document-1"})()
    service = type(
        "Service",
        (),
        {
            "modeling_engine": staticmethod(lambda: "vibescript"),
            "active_workbench_name": staticmethod(lambda: "PartDesignWorkbench"),
            "_active_document": staticmethod(lambda: document),
        },
    )()
    resolution = type(
        "Resolution",
        (),
        {"domain": "partdesign", "available": True},
    )()
    monkeypatch.setattr(editor, "get_service", lambda: service)
    monkeypatch.setattr(
        editor,
        "resolve_modeling_surface",
        lambda _workbench, _engine: resolution,
    )

    controller = object.__new__(editor.ScriptedEditorController)
    controller.editor_active = True
    controller.engine = "vibescript"
    controller.domain = "partdesign"
    controller.document_uid = "document-1"
    controller.busy = True
    refreshes: list[str] = []
    controller._start_vibescript_model_refresh = refreshes.append

    controller.refresh("program-1")

    assert refreshes == []


def test_editor_delete_lifecycle_accepts_a_failed_source_only_program(
    tmp_path: Path,
) -> None:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    program_id = "1" * 32
    source = "result = {'Body': api.box(1, 2, 3)}"
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    expected_outputs = [{"name": "Body", "type": "solid"}]
    revision = domains.program_revision(
        domain=pack.domain,
        source=source,
        input_schema=input_schema,
        inputs={},
        expected_outputs=expected_outputs,
    )
    directory = tmp_path / "vibescript" / pack.domain / program_id
    directory.mkdir(parents=True)
    (directory / "program.json").write_text(
        json.dumps(
            {
                "schema": domains.PROGRAM_SCHEMA,
                "version": domains.PROGRAM_VERSION,
                "program_id": program_id,
                "domain": pack.domain,
                "workbench": pack.workbench,
                "label": "Failed source only",
                "source": source,
                "input_schema": input_schema,
                "inputs": {},
                "expected_outputs": expected_outputs,
                "working_revision": revision,
                "accepted_revision": "",
                "accepted_contract": None,
                "live_outputs": {},
                "latest_candidate": {
                    "status": "failed",
                    "revision": revision,
                    "failure": {"error": "Candidate did not build."},
                },
            }
        ),
        encoding="utf-8",
    )
    prepared = runtime.prepare_delete(
        {
            "pack": pack,
            "operation": "delete_program",
            "tool_name": "vibescript.partdesign.delete_program",
            "arguments": {
                "program_id": program_id,
                "expected_revision": revision,
                "reason": "Deleted from Model Code Editor",
            },
            "project_root": str(tmp_path),
            "document_program": {},
            "live_programs": [],
        }
    )
    assert not directory.exists()
    assert Path(prepared["trash_directory"]).is_dir()
    deleted = runtime.finish_delete(prepared, {"deleted_objects": []})
    assert deleted == {
        "ok": True,
        "program_id": program_id,
        "domain": "partdesign",
        "source_deleted": True,
        "deleted_objects": [],
        "cad_objects_removed": 0,
        "reason": "Deleted from Model Code Editor",
        "artifacts_deleted": True,
    }
    assert not Path(prepared["trash_directory"]).exists()


def test_editor_delete_lifecycle_tolerates_a_disappearing_trash_child(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    trash = tmp_path / ".trash" / f"{'2' * 32}-pending"
    outputs = trash / "attempts" / "attempt-1" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "partdesign-native-history.json").write_text(
        "{}",
        encoding="utf-8",
    )
    real_rmtree = runtime.shutil.rmtree
    cleanup_calls: list[str] = []

    def racing_rmtree(path, *args, **kwargs) -> None:
        cleanup_calls.append(str(path))
        if len(cleanup_calls) == 1:
            missing = outputs / "partdesign-native-history.json"
            missing.unlink()
            raise FileNotFoundError(3, "The system cannot find the path specified", missing)
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runtime.shutil, "rmtree", racing_rmtree)

    deleted = _finish_delete_fixture(pack, trash, "2" * 32)

    assert deleted["artifacts_deleted"] is True
    assert len(cleanup_calls) == 2
    assert all(call.endswith(str(trash)) for call in cleanup_calls)
    assert not trash.exists()


def test_editor_delete_lifecycle_uses_extended_windows_cleanup_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if runtime.os.name != "nt":
        pytest.skip("Windows extended-length path behavior")
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    trash = tmp_path / ".trash" / f"{'5' * 32}-pending"
    trash.mkdir(parents=True)
    long_child = (
        trash
        / "attempts"
        / ("6" * 96)
        / "outputs"
        / "partdesign-native-history.json"
    )
    assert len(str(long_child)) > 260
    real_rmtree = runtime.shutil.rmtree
    cleanup_calls: list[str] = []

    def long_path_sensitive_rmtree(path, *args, **kwargs) -> None:
        cleanup_calls.append(str(path))
        if not str(path).startswith("\\\\?\\"):
            raise FileNotFoundError(3, "The system cannot find the path specified", long_child)
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runtime.shutil, "rmtree", long_path_sensitive_rmtree)

    deleted = _finish_delete_fixture(pack, trash, "5" * 32)

    assert deleted["artifacts_deleted"] is True
    assert cleanup_calls == [f"\\\\?\\{trash}"]
    assert not trash.exists()


def test_editor_delete_lifecycle_retries_a_transient_nonempty_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    trash = tmp_path / ".trash" / f"{'4' * 32}-pending"
    trash.mkdir(parents=True)
    real_rmtree = runtime.shutil.rmtree
    cleanup_calls: list[str] = []

    def racing_rmtree(path, *args, **kwargs) -> None:
        cleanup_calls.append(str(path))
        if len(cleanup_calls) == 1:
            raise OSError(errno.ENOTEMPTY, "The directory is not empty", path)
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runtime.shutil, "rmtree", racing_rmtree)

    deleted = _finish_delete_fixture(pack, trash, "4" * 32)

    assert deleted["artifacts_deleted"] is True
    assert len(cleanup_calls) == 2
    assert all(call.endswith(str(trash)) for call in cleanup_calls)
    assert not trash.exists()


def test_editor_delete_lifecycle_preserves_real_cleanup_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pack = domains.get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None
    trash = tmp_path / ".trash" / f"{'3' * 32}-pending"
    trash.mkdir(parents=True)
    denied = PermissionError(13, "Access is denied", trash / "locked.json")

    def denied_rmtree(path, *args, **kwargs) -> None:
        onerror = kwargs["onerror"]
        onerror(
            Path.unlink,
            str(Path(path) / "locked.json"),
            (PermissionError, denied, None),
        )

    monkeypatch.setattr(runtime.shutil, "rmtree", denied_rmtree)

    try:
        _finish_delete_fixture(pack, trash, "3" * 32)
    except PermissionError as exc:
        assert exc is denied
    else:
        raise AssertionError("A real artifact-cleanup failure was hidden")
    assert trash.exists()
