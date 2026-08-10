# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI gate for exact Native Assembly component-joint reading."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import UtilsAssembly
from VibeCADNativeAssemblyDiagnosisBindings import (
    ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeTargets import read_current_selection
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_assembly_diagnosis_gui_support import (
    active_assemble_surface,
    assembly_summary,
    create_cylindrical_joint,
    create_fixed_joint,
    create_slider_joint,
    dispatcher,
    joint_group,
    process_events,
    select_assemble_ribbon,
)


_OPERATION = "select_joints_of_component"


def _arguments(
    summary: dict,
    component_name: str,
    *,
    offset: int = 0,
    limit: int = 16,
) -> dict:
    graph = summary["component_joint_state"]
    assert graph["available"] is True
    return {
        "operation": _OPERATION,
        "assembly": {"object_name": summary["object_name"]},
        "component": {"object_name": component_name},
        "expected_joint_graph_state_sha256": graph["state_sha256"],
        "expected_component_count": graph["component_count"],
        "expected_joint_count": graph["joint_count"],
        "offset": offset,
        "limit": limit,
    }


def _select_component(assembly, component) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(assembly, f"{component.Name}.")
    process_events(8)


def _human_component_joints(assembly, component) -> list[str]:
    _select_component(assembly, component)
    Gui.runCommand("Assembly_SelectJointsOfComponent")
    process_events(12)
    return [obj.Name for obj in Gui.Selection.getSelection()]


def _native_joint_names(*results: dict) -> list[str]:
    return [
        item["joint"]["object_name"]
        for result in results
        for item in result["joints"]
    ]


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-component-joints-"
        )
        path = Path(temporary.name) / "native-assembly-component-joints.FCStd"
        document = App.newDocument("NativeAssemblyComponentJointsGate")
        document.UndoMode = 1
        sources = []
        for index in range(5):
            source = document.addObject("Part::Box", f"ComponentJointSource{index}")
            source.Length = 10.0 + index
            source.Width = 9.0
            source.Height = 8.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        document.openTransaction("Prepare component-joint Assembly")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"JointComponent{index}")
            component.LinkedObject = source
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        group = joint_group(assembly)
        joints = (
            create_fixed_joint(group, components[0], components[1], "BaseJoint"),
            create_cylindrical_joint(
                group,
                components[1],
                components[2],
                "DrivenJoint",
            ),
            create_slider_joint(
                group,
                components[1],
                components[3],
                "GuideJoint",
            ),
        )
        suppressed = create_fixed_joint(
            group,
            components[1],
            components[4],
            "SuppressedJoint",
        )
        suppressed.Suppressed = True
        document.recompute()
        document.commitTransaction()
        process_events(16)
        assert [joint.Name for joint in assembly.Joints] == [
            joint.Name for joint in joints
        ]
        document.clearUndos()

        human_selected = _human_component_joints(assembly, components[1])
        expected = [joint.Name for joint in joints]
        assert human_selected == expected
        _select_component(assembly, components[1])

        controller, surface = active_assemble_surface()
        assert "Assembly_SelectJointsOfComponent" in surface.command_ids
        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_definition_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.missing_implementation_names
        )
        assert (
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME
            not in production.incomplete_definition_names
        )
        definition = registry.definition(ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME)
        assert definition is not None
        variant = next(
            item for item in definition.variants if item.operation == _OPERATION
        )
        assert variant.action_ids == frozenset(
            {"Assembly_SelectJointsOfComponent"}
        )

        native = dispatcher(
            document,
            surface,
            registry,
            controller,
            "native-assembly-component-joints-gui",
            (_OPERATION,),
        )
        current = native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-component-joints-state",
        )
        assert current["ok"] is True, current
        summary = assembly_summary(current, assembly.Name)
        graph = summary["component_joint_state"]
        assert graph["available"] is True
        assert graph["component_count"] == 5
        assert graph["joint_count"] == 3

        before_objects = tuple(document.Objects)
        before_placements = tuple(App.Placement(item.Placement) for item in components)
        before_selection = read_current_selection(document)
        before_undo = int(document.UndoCount)
        stale_arguments = _arguments(summary, components[1].Name)
        stale_arguments["expected_joint_graph_state_sha256"] = "0" * 64
        stale = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(stale_arguments, separators=(",", ":")),
            "assembly-component-joints-stale",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_ASSEMBLY_COMPONENT_JOINTS_FAILED"
        wrong_count_arguments = _arguments(summary, components[1].Name)
        wrong_count_arguments["expected_joint_count"] = 2
        wrong_count = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(wrong_count_arguments, separators=(",", ":")),
            "assembly-component-joints-wrong-count",
        )
        assert wrong_count["ok"] is False, wrong_count
        wrong_target = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(summary, sources[0].Name),
                separators=(",", ":"),
            ),
            "assembly-component-joints-wrong-target",
        )
        assert wrong_target["ok"] is False, wrong_target

        first_encoded = json.dumps(
            _arguments(summary, components[1].Name, limit=2),
            separators=(",", ":"),
        )
        first = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-component-joints-first",
        )
        second = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(summary, components[1].Name, offset=2, limit=2),
                separators=(",", ":"),
            ),
            "assembly-component-joints-second",
        )
        assert first["ok"] is True, first
        assert second["ok"] is True, second
        assert first["joint_graph_state_sha256"] == graph["state_sha256"]
        assert first["component"]["object_name"] == components[1].Name
        assert first["component_count"] == 5
        assert first["joint_count"] == 3
        assert first["component_joint_count"] == 3
        assert first["returned_count"] == 2
        assert first["next_offset"] == 2
        assert second["returned_count"] == 1
        assert "next_offset" not in second
        assert _native_joint_names(first, second) == human_selected
        assert [item["component_side"] for item in first["joints"]] == [
            "second",
            "first",
        ]
        assert second["joints"][0]["component_side"] == "first"
        assert (
            first["joints"][0]["other_component"]["object_name"]
            == components[0].Name
        )
        assert first["joints"][0]["first"]["component"]["object_name"] == (
            components[0].Name
        )
        assert first["joints"][0]["second"]["component"]["object_name"] == (
            components[1].Name
        )

        empty = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(summary, components[4].Name, limit=1),
                separators=(",", ":"),
            ),
            "assembly-component-joints-empty",
        )
        assert empty["ok"] is True, empty
        assert empty["component_joint_count"] == 0
        assert empty["returned_count"] == 0
        assert empty["joints"] == []
        replay = native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            first_encoded,
            "assembly-component-joints-first",
        )
        assert replay == first
        assert tuple(document.Objects) == before_objects
        assert all(
            item.Placement.isSame(before, 1.0e-9)
            for item, before in zip(components, before_placements)
        )
        assert read_current_selection(document) == before_selection
        assert int(document.UndoCount) == before_undo == 0
        assert not document.HasPendingTransaction
        assert int(document.getBookedTransactionID()) == 0
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        assembly_name = assembly.Name
        component_names = tuple(component.Name for component in components)
        joint_names = tuple(joint.Name for joint in joints)
        suppressed_name = suppressed.Name
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        process_events(24)
        assembly = document.getObject(assembly_name)
        components = tuple(document.getObject(name) for name in component_names)
        joints = tuple(document.getObject(name) for name in joint_names)
        suppressed = document.getObject(suppressed_name)
        assert assembly is not None
        assert all(component is not None for component in components)
        assert all(joint is not None for joint in joints)
        assert suppressed is not None and suppressed.Suppressed is True
        assert Gui.activeDocument().setEdit(assembly.Name)
        process_events(20)
        assert _human_component_joints(assembly, components[1]) == expected
        _select_component(assembly, components[1])
        document.clearUndos()

        main_window = Gui.getMainWindow()
        select_assemble_ribbon(main_window)
        reopened_surface = read_active_ribbon_surface(controller)
        reopened_native = dispatcher(
            document,
            reopened_surface,
            registry,
            controller,
            "native-assembly-component-joints-reopen",
            (_OPERATION,),
        )
        reopened_state = reopened_native.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-component-joints-reopened-state",
        )
        assert reopened_state["ok"] is True, reopened_state
        reopened_summary = assembly_summary(reopened_state, assembly.Name)
        reopened_graph = reopened_summary["component_joint_state"]
        assert reopened_graph["available"] is True
        assert reopened_graph["component_count"] == 5
        assert reopened_graph["joint_count"] == 3
        reopened_result = reopened_native.call(
            ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
            json.dumps(
                _arguments(reopened_summary, components[1].Name),
                separators=(",", ":"),
            ),
            "assembly-component-joints-reopened-read",
        )
        assert reopened_result["ok"] is True, reopened_result
        assert _native_joint_names(reopened_result) == expected
        assert read_current_selection(document)["selected_count"] == 1
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        print(
            "VIBECAD_NATIVE_ASSEMBLY_COMPONENT_JOINTS_GUI_OK "
            "components=5 joints=3 attached=3 suppressed_excluded=true "
            "human_match=true exact_sides=true pagination=true empty=true "
            "stale_noop=true selection=true transactions=0 reopen=true "
            "diagnose_complete=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None:
            try:
                Gui.activeDocument().resetEdit()
            except (AttributeError, RuntimeError):
                pass
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
