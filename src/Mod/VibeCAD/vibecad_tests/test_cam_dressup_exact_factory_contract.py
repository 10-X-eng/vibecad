# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for shipped CAM dress-up output identity."""

from __future__ import annotations

import ast
from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[4]
_SHIPPED_DRESSUPS = {
    "CAM_DressupAxisMap": (
        "src/Mod/CAM/Path/Dressup/Gui/AxisMap.py",
        "CommandPathDressup",
    ),
    "CAM_DressupDragKnife": (
        "src/Mod/CAM/Path/Dressup/Gui/Dragknife.py",
        "CommandDressupDragknife",
    ),
    "CAM_DressupZCorrect": (
        "src/Mod/CAM/Path/Dressup/Gui/ZCorrect.py",
        "CommandPathDressup",
    ),
}


def _source(relative_path: str) -> str:
    return (_REPOSITORY / relative_path).read_text(encoding="utf-8")


def _method_source(
    relative_path: str,
    class_name: str,
    method_name: str,
) -> str:
    source = _source(relative_path)
    tree = ast.parse(source)
    command_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in command_class.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    segment = ast.get_source_segment(source, method)
    assert segment is not None
    return segment


def test_audited_dressups_are_in_the_shipped_ribbon_inventory():
    init_gui = _source("src/Mod/CAM/InitGui.py")
    gui_startup = _source("src/Mod/CAM/Path/GuiInit.py")

    for command, (relative_path, _class_name) in _SHIPPED_DRESSUPS.items():
        assert repr(command) in init_gui or f'"{command}"' in init_gui
        module_name = Path(relative_path).stem
        assert (
            f"from Path.Dressup.Gui import {module_name}" in gui_startup
        )


def test_shipped_dressups_bind_and_validate_the_exact_factory_return():
    for _command, (relative_path, class_name) in _SHIPPED_DRESSUPS.items():
        source = _source(relative_path)
        activated = _method_source(
            relative_path,
            class_name,
            "Activated",
        )

        assert "def createDressupFeature(document):" in source
        assert 'document.addObject(' in source
        assert "return result" in source
        assert "FreeCADGui.runDocumentObjectCommand(" in activated
        assert '"Path::FeaturePython"' in activated
        assert "result_name = str(result.Name)" in activated
        assert "result_id = int(result.ID)" in activated
        assert "document.getObject(result_name) is not result" in source
        assert "document.getObject(result_id) is not result" in source
        assert "PathUtil.timelineParentJob(result) is not job" in source
        assert (
            "isProvisionallyEnrolledInTimelineByCurrentTransaction"
            in source
        )
        assert "begin_task_launch(" in activated
        assert "launch.require_claimed()" in activated
        assert "launch.abort()" in activated
        assert "markTimelineReplacedInputs(" in activated

        assert "_cam_doc" not in activated
        assert "doCommandEval(" not in activated
        assert ".addObject(" not in activated
        assert "\n            obj =" not in activated


def test_legacy_cam_copy_is_not_a_shipped_or_startup_registered_command():
    """The old CAM_Copy module remains import compatibility, not ribbon UI."""

    init_gui = _source("src/Mod/CAM/InitGui.py")
    gui_startup = _source("src/Mod/CAM/Path/GuiInit.py")
    copy_source = _source("src/Mod/CAM/Path/Op/Gui/Copy.py")

    assert '"CAM_Copy"' not in init_gui
    assert "from Path.Op.Gui import Copy" not in gui_startup
    assert 'FreeCADGui.addCommand("CAM_Copy"' in copy_source

    # The shipped replacement is a different command with exact timeline-copy
    # semantics. Keeping the legacy module untouched preserves direct-import
    # callers without exposing its old console-global implementation.
    assert '"CAM_OperationCopy"' in init_gui
