# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for the VibeScript model-tree presentation."""

from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3]
GUI_ROOT = SOURCE_ROOT / "Gui"
PARTDESIGN_GUI_ROOT = SOURCE_ROOT / "Mod" / "PartDesign" / "Gui"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_projection_uses_exact_persisted_program_ownership() -> None:
    source = _source(GUI_ROOT / "ModelTreeBrowser.cpp")

    assert '"PartDesign::DesignScriptOperation"' in source
    assert '"ProgramObjectName"' in source
    assert '"ProgramId"' in source
    assert '"VibeCADScriptedRole"' in source
    assert '"model"' in source
    assert "ownership.component =" in source


def test_vibescript_component_has_clear_primary_tree_roles() -> None:
    source = _source(GUI_ROOT / "Tree.cpp")

    assert 'TreeWidget::tr("Design History")' in source
    assert 'TreeWidget::tr("Published Outputs")' in source
    assert 'TreeWidget::tr("Bodies")' in source
    assert 'Projection::isVibeScriptProgram(' in source
    assert 'vibeScriptProgram ? "design-history" : "operations"' not in source
    assert (
        '"design-history",\n                TreeWidget::tr("Design History"),'
        in source
    )
    assert "return vibeScriptProgram || !entry.bodyRepresentation;" in source
    assert "firstBuild && vibeScriptProgram" in source
    assert (
        "logicalParent,\n            entry.publishedImplementation\n        );"
        in source
    )


def test_script_history_view_provider_is_non_rendering_and_lists_outputs() -> None:
    header = _source(PARTDESIGN_GUI_ROOT / "ViewProviderDesignScriptOperation.h")
    source = _source(PARTDESIGN_GUI_ROOT / "ViewProviderDesignScriptOperation.cpp")
    app_source = _source(PARTDESIGN_GUI_ROOT / "AppPartDesignGui.cpp")
    cmake_source = _source(PARTDESIGN_GUI_ROOT / "CMakeLists.txt")
    design_header = _source(
        SOURCE_ROOT / "Mod" / "PartDesign" / "App" / "DesignFeature.h"
    )

    assert "Gui::TreeViewDetailProvider" in header
    assert "getTreeViewDetails() const override" in header
    assert "NoToggleVisibility" in source
    assert "ProgramOutputKeys" in source
    assert "ProgramOutputTypes" in source
    assert '"Produces "' in source
    assert "ViewProviderDesignScriptOperation::init()" in app_source
    assert "ViewProviderDesignScriptOperation.cpp" in cmake_source
    assert "ViewProviderDesignScriptOperation.h" in cmake_source
    assert 'return "PartDesignGui::ViewProviderDesignScriptOperation"' in design_header
