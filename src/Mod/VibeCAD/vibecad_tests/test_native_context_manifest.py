# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from VibeCADNativeActionManifest import KNOWN_ACTIONS_BY_SURFACE
from VibeCADNativeContextManifest import (
    NATIVE_CONTEXT_ACTIONS,
    NativeContextManifestError,
    context_actions_for_surface,
    provider_context_actions_for_surface,
)


MOD_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CONTEXT_ACTION_IDS = {
    "AssemblyContextToggleActive",
    "AssemblyContextMakeFlexible",
    "AssemblyContextMakeRigid",
    "Assembly_LinkSelectLinked",
    "AssemblyContextPlaySimulation",
    "AssemblySimulationSeek",
    "AssemblySimulationStep",
    "AssemblySimulationPlay",
    "AssemblySimulationPause",
    "AssemblySimulationClose",
    "CAM_ExportTemplate",
    "CAM_SetStartPoint",
    "CAM_ToolBitSave",
    "CAM_ToolBitSaveAs",
    "TechDrawContextEditBalloon",
    "TechDrawContextEditDimension",
    "TechDrawContextShowDrawing",
    "TechDrawContextToggleKeepUpdated",
    "TechDrawContextToggleFrames",
    "TechDrawContextToggleGrid",
    "TechDrawContextExportSVG",
    "TechDrawContextExportDXF",
    "TechDrawContextExportPDF",
    "TechDrawContextPrintAll",
    "InspectionContextAnnotation",
    "InspectionContextLeaveInfoMode",
}


def _cpp_context_ids(directory: Path, prefixes: tuple[str, ...]) -> set[str]:
    pattern = re.compile(r'setObjectName\(QStringLiteral\("([^"]+)"\)\)')
    return {
        action_id
        for path in directory.rglob("*.cpp")
        for action_id in pattern.findall(path.read_text(encoding="utf-8"))
        if action_id.startswith(prefixes)
    }


def _cam_workbench_context_commands() -> set[str]:
    source = (MOD_ROOT / "CAM" / "InitGui.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    context_method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "ContextMenu"
    )
    return {
        value.value
        for value in ast.walk(context_method)
        if isinstance(value, ast.Constant)
        and isinstance(value.value, str)
        and value.value.startswith("CAM_")
    }


def test_context_inventory_is_complete_unique_and_small() -> None:
    assert {action.action_id for action in NATIVE_CONTEXT_ACTIONS} == (
        EXPECTED_CONTEXT_ACTION_IDS
    )
    assert len(NATIVE_CONTEXT_ACTIONS) == 26
    assert len({action.action_id for action in NATIVE_CONTEXT_ACTIONS}) == 26
    assert sum(action.classification.human_only for action in NATIVE_CONTEXT_ACTIONS) == 5
    assert all(action.exact_target_type for action in NATIVE_CONTEXT_ACTIONS)


def test_surface_filtering_never_leaks_context_actions() -> None:
    assert len(context_actions_for_surface("drawing")) == 12
    assert len(provider_context_actions_for_surface("drawing")) == 8
    assert len(context_actions_for_surface("assemble")) == 12
    assert len(provider_context_actions_for_surface("assemble")) == 9
    assert len(context_actions_for_surface("manufacture")) == 6
    assert len(provider_context_actions_for_surface("manufacture")) == 4
    assert len(context_actions_for_surface("model")) == 2
    assert provider_context_actions_for_surface("model") == ()
    assert context_actions_for_surface("sketch.edit") == ()


@pytest.mark.parametrize("surface_id", ("unavailable", "DraftWorkbench", ""))
def test_unknown_or_unavailable_surface_fails_closed(surface_id: str) -> None:
    with pytest.raises(NativeContextManifestError, match="Unknown Native surface"):
        context_actions_for_surface(surface_id)


def test_human_only_actions_cannot_be_misrepresented_as_provider_operations() -> None:
    human_only = [
        action for action in NATIVE_CONTEXT_ACTIONS if action.classification.human_only
    ]
    assert all(action.operation_variant is None for action in human_only)
    assert all(action.transaction_behavior == "human" for action in human_only)
    assert all(action.implementation_status == "human_only" for action in human_only)
    assert all(action.classification.interactive for action in human_only)


def test_provider_actions_have_exact_variants_and_transaction_classification() -> None:
    provider_actions = {
        action.action_id: action
        for action in NATIVE_CONTEXT_ACTIONS
        if not action.classification.human_only
    }
    assert provider_actions["AssemblyContextMakeFlexible"].operation_variant == (
        "make_flexible"
    )
    assert provider_actions["AssemblyContextMakeRigid"].operation_variant == "make_rigid"
    assert provider_actions["Assembly_LinkSelectLinked"].operation_variant == (
        "linked_source"
    )
    assert provider_actions["Assembly_LinkSelectLinked"].classification.read
    assert provider_actions["Assembly_LinkSelectLinked"].transaction_behavior == "none"
    assert provider_actions["Assembly_LinkSelectLinked"].source_command_id == (
        "Assembly_LinkSelectLinked"
    )
    assert provider_actions["AssemblyContextPlaySimulation"].operation_variant == (
        "open"
    )
    assert provider_actions["AssemblyContextPlaySimulation"].source_command_id == (
        "Assembly_EditHistoryOperation"
    )
    assert all(
        provider_actions[action_id].classification.view
        and provider_actions[action_id].classification.interactive
        and provider_actions[action_id].transaction_behavior == "presentation"
        for action_id in {
            "AssemblyContextPlaySimulation",
            "AssemblySimulationSeek",
            "AssemblySimulationStep",
            "AssemblySimulationPlay",
            "AssemblySimulationPause",
            "AssemblySimulationClose",
        }
    )
    assert provider_actions["TechDrawContextToggleKeepUpdated"].classification.mutation
    assert provider_actions["TechDrawContextToggleKeepUpdated"].transaction_behavior == (
        "document"
    )
    assert provider_actions["TechDrawContextToggleGrid"].classification.view
    assert provider_actions["TechDrawContextToggleGrid"].transaction_behavior == (
        "presentation"
    )
    assert all(
        action.background_required
        for action_id, action in provider_actions.items()
        if action_id.startswith("TechDrawContextExport")
        or action_id == "TechDrawContextPrintAll"
    )


def test_cpp_context_action_ids_match_the_inventory_exactly() -> None:
    assembly_ids = _cpp_context_ids(MOD_ROOT / "Assembly" / "Gui", ("AssemblyContext",))
    drawing_ids = _cpp_context_ids(MOD_ROOT / "TechDraw" / "Gui", ("TechDrawContext",))
    inspection_ids = _cpp_context_ids(
        MOD_ROOT / "Inspection" / "Gui", ("InspectionContext",)
    )

    assert assembly_ids == {
        "AssemblyContextToggleActive",
        "AssemblyContextMakeFlexible",
        "AssemblyContextMakeRigid",
    }
    assert drawing_ids == {
        action_id
        for action_id in EXPECTED_CONTEXT_ACTION_IDS
        if action_id.startswith("TechDrawContext")
    }
    assert inspection_ids == {
        "InspectionContextAnnotation",
        "InspectionContextLeaveInfoMode",
    }


def test_cam_context_only_commands_match_the_inventory_exactly() -> None:
    context_commands = _cam_workbench_context_commands()
    default_ribbon_commands = set(KNOWN_ACTIONS_BY_SURFACE["manufacture"])

    assert context_commands - default_ribbon_commands == {
        "CAM_ExportTemplate",
        "CAM_SetStartPoint",
        "CAM_ToolBitSave",
        "CAM_ToolBitSaveAs",
    }
    assert context_commands <= default_ribbon_commands | {
        action.action_id
        for action in NATIVE_CONTEXT_ACTIONS
        if action.surface_ids == ("manufacture",)
    }


def test_current_vibecad_fastener_workflow_has_no_hidden_context_only_action() -> None:
    fastener_actions = {
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
        "VibeCAD_CreateMatchingFastenerHole",
        "VibeCAD_AttachStandardFastener",
    }
    assert fastener_actions <= set(KNOWN_ACTIONS_BY_SURFACE["model"])
    assert fastener_actions & set(KNOWN_ACTIONS_BY_SURFACE["assemble"]) == {
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
    }
    source = "\n".join(
        (MOD_ROOT / "VibeCAD" / filename).read_text(encoding="utf-8")
        for filename in ("VibeCADFasteners.py", "VibeCADFastenersGui.py")
    )
    assert "appendContextMenu" not in source


def test_context_manifest_exposes_no_activation_or_command_dispatch_api() -> None:
    import VibeCADNativeContextManifest as module

    public_names = {name for name in vars(module) if not name.startswith("_")}
    forbidden_fragments = ("activate", "switch", "dispatch", "run_command")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden_fragments
    )
