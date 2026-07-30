# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression contracts for the lightweight, native model code editor."""

from __future__ import annotations

from pathlib import Path

import VibeCADVibeScriptDomains as domains


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
    assert "VibeScriptedAccept" not in source
    assert "VibeScriptedRevert" not in source
    assert "VibeScriptedImport" not in source
    assert "VibeScriptedExport" not in source
    assert "QFileSystemWatcher" not in source
    assert 'SCRIPTED_ENGINES = {"vibescript"}' in source
    assert "self._start_vibescript_apply()" in source
    assert "self._adopt_failed_vibescript_revision(result)" in source
    assert 'captured["allow_unchanged_revision"] = True' in session
