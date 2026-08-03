# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression contracts for the lightweight, native model code editor."""

from __future__ import annotations

import json
from pathlib import Path

import VibeCADVibeScriptDomains as domains
import VibeCADVibeScriptDomainRuntime as runtime


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
