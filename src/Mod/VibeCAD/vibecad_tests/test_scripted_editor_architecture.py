# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression contracts for the lightweight, native model code editor."""

from __future__ import annotations

from pathlib import Path

import VibeCADBuild123d as build123d
import VibeCADOpenSCAD as openscad
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


class _DirectEngineModel:
    Name = "DirectModel"
    Label = "Direct model"
    TypeId = "App::Part"
    PropertiesList = list(
        {
            build123d.PROP_MODEL_ID,
            build123d.PROP_SOURCE,
            build123d.PROP_REVISION,
            openscad.PROP_MODEL_ID,
        }
    )

    @property
    def Shape(self):
        raise AssertionError("A selector index must never access model geometry")

    def __getattr__(self, name: str):
        values = {
            build123d.PROP_MODEL_ID: "a" * 32,
            build123d.PROP_SOURCE: "result = {}\n",
            build123d.PROP_REVISION: "build-revision",
            build123d.PROP_RUNTIME_VERSION: "test",
            build123d.PROP_OUTPUTS: "{}",
            build123d.PROP_INPUTS: "{}",
            build123d.PROP_PARAMETERS: "{}",
            openscad.PROP_MODEL_ID: "b" * 32,
            openscad.PROP_SOURCE: "cube(1);\n",
            openscad.PROP_REVISION: "openscad-revision",
            openscad.PROP_PARAMETERS: "{}",
            openscad.PROP_OUTPUTS: "{}",
            openscad.PROP_CONVERSION_MODE: "exact_brep",
            openscad.PROP_FIDELITY: "exact_brep",
        }
        return values.get(name, "")


class _DirectDocument:
    Objects = [_DirectEngineModel()]


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


def test_direct_engine_editor_indexes_never_capture_geometry() -> None:
    build_index = build123d.editor_model_index_snapshot(_DirectDocument())
    scad_index = openscad.editor_model_index_snapshot(_DirectDocument())
    assert len(build_index["native_models"]) == 1
    assert len(scad_index["native_models"]) == 1
    assert "shape" not in build_index["native_models"][0]
    assert "shape" not in scad_index["native_models"][0]


def test_editor_uses_explicit_builds_and_native_resizing() -> None:
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
    assert '"Save",' in source
    assert '"VibeScriptedSave"' in source
    assert '"Build", "VibeScriptedRender"' in source
    assert '"Apply", "VibeScriptedAccept"' in source
    assert 'self.button("VibeScriptedSave").setVisible(self.engine == "vibescript")' in source
    assert 'setVisible(self.engine != "vibescript")' in source
    assert 'self.button("VibeScriptedRevert").setVisible(self.engine != "vibescript")' in source
    assert "self._start_vibescript_apply()" in source
    assert "self._adopt_failed_vibescript_revision(result)" in source
    assert 'captured["allow_unchanged_revision"] = True' in session
