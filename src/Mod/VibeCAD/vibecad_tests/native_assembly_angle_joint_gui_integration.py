# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for one exact Native Assembly Angle joint."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import CommandCreateJoint
import JointObject
import Preferences
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyAngleJoint import (
    angle_axes_satisfied,
    measured_axis_angle_degrees,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointConnectors import placement_summary
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativePartPrimitives import part_placement_from_mapping
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


TARGET_ANGLE_DEGREES = 60.0
ANGLE_TOLERANCE_DEGREES = 1.0e-4


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> None:
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert tabs is not None
    index = next(
        (
            candidate
            for candidate in range(tabs.count())
            if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
        ),
        -1,
    )
    assert index >= 0
    tabs.setCurrentIndex(index)
    _process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state_definition = registry.definition("state.read")
    assert state_definition is not None
    joint_definition = assembly_joint_capability_definition()
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_JOINT_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            joint_definition.provider_schema(("create_angle",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joint_group(assembly):
    groups = [child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"]
    assert len(groups) == 1
    return groups[0]


def _regular_joints(assembly):
    return [
        joint
        for joint in _joint_group(assembly).Group
        if hasattr(joint, "JointType")
        and UtilsAssembly.isTimelineOperationActive(joint)
    ]


def _placement(
    x: float,
    y: float,
    z: float,
    axis: tuple[float, float, float],
    angle: float,
) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": axis[0], "y": axis[1], "z": axis[2]},
            "angle_degrees": angle,
        },
    }


# Yaw-only offsets retain parallel initial connector Z axes while proving both
# complete attachment placements survive every lifecycle transition.
FIRST_OFFSET = _placement(1.0, -2.0, 3.0, (0.0, 0.0, 1.0), 15.0)
SECOND_OFFSET = _placement(-4.0, 5.0, -6.0, (0.0, 0.0, 1.0), -25.0)


def _connector(component, offset: dict) -> dict:
    return {
        "component": {"object_name": component.Name},
        "element_path": "Face6",
        "anchor_path": "Face6",
        "offset": offset,
        "expected_component_placement": placement_summary(component.Placement),
    }


def _arguments(assembly, components, *, expected_joint_count: int) -> dict:
    return {
        "operation": "create_angle",
        "assembly": {"object_name": assembly.Name},
        "first": _connector(components[0], FIRST_OFFSET),
        "second": _connector(components[1], SECOND_OFFSET),
        "label": "Native Base-Arm Sixty Degree Angle",
        "angle_degrees": TARGET_ANGLE_DEGREES,
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": expected_joint_count,
        "expected_solve_on_creation": True,
    }


def _assert_offset(actual: dict, expected: dict) -> None:
    for coordinate in ("x", "y", "z"):
        assert (
            abs(actual["origin_mm"][coordinate] - expected["origin_mm"][coordinate])
            < 1.0e-9
        )
        assert (
            abs(
                actual["rotation"]["axis"][coordinate]
                - expected["rotation"]["axis"][coordinate]
            )
            < 1.0e-9
        )
    assert (
        abs(
            actual["rotation"]["angle_degrees"]
            - expected["rotation"]["angle_degrees"]
        )
        < 1.0e-9
    )


def _global_connector_placement(component, offset: dict):
    reference = [component, ["Face6", "Face6"]]
    local = UtilsAssembly.findPlacement(reference, False)
    local = local * part_placement_from_mapping(offset)
    return UtilsAssembly.getJcsGlobalPlc(local, reference)


def _assert_measured_angle(joint) -> None:
    measured = measured_axis_angle_degrees(joint)
    assert measured is not None
    assert abs(measured - TARGET_ANGLE_DEGREES) < ANGLE_TOLERANCE_DEGREES
    assert angle_axes_satisfied(joint, TARGET_ANGLE_DEGREES)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = Preferences.preferences()
    prior_solve_preference = preferences.GetBool("SolveInJointCreation", True)
    exit_code = 1
    try:
        preferences.SetBool("SolveInJointCreation", True)
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-angle-joint-"
        )
        path = Path(temporary.name) / "native-assembly-angle-joint.FCStd"
        document = App.newDocument("NativeAssemblyAngleJointGate")
        document.UndoMode = 1
        sources = []
        for index in range(2):
            source = document.addObject("Part::Box", f"AngleSource{index + 1}")
            source.Length = 12.0
            source.Width = 10.0
            source.Height = 8.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.openTransaction("Prepare Angle-joint fixture")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"AngleComponent{index + 1}")
            component.LinkedObject = source
            component.Placement.Base.x = float(index * 35)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        ground = CommandCreateJoint.createGroundedJointFeature(components[0], assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        document.recompute()
        document.commitTransaction()
        _process_events(16)
        document.clearUndos()
        Gui.Selection.clearSelection()

        first_initial = _global_connector_placement(components[0], FIRST_OFFSET)
        second_initial = _global_connector_placement(components[1], SECOND_OFFSET)
        assert UtilsAssembly.arePlacementZParallel(first_initial, second_initial)

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateJointAngle" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is False
        assert ASSEMBLY_JOINT_CAPABILITY_NAME in production.incomplete_definition_names

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-angle-joint-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: Gui.Control.activeDialog() is not None,
        )
        turn = _focused_turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        initial = dispatcher.call(
            "state.read", '{"operation":"active"}', "assembly-angle-state-1"
        )
        assert initial["ok"] is True, initial
        assert initial["domain"]["assemblies"][0]["counts"] == {
            "components": 2,
            "joints": 0,
            "grounded": 1,
        }

        before_invalid = tuple(document.Objects)
        invalid = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            json.dumps(_arguments(assembly, components, expected_joint_count=1)),
            "assembly-angle-stale",
        )
        assert invalid["ok"] is False, invalid
        assert invalid["error_code"] == "NATIVE_ASSEMBLY_ANGLE_JOINT_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        arguments = _arguments(assembly, components, expected_joint_count=0)
        encoded = json.dumps(arguments, separators=(",", ":"))
        result = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-angle-create",
        )
        assert result["ok"] is True, result
        joint_name = result["joint"]["object_name"]
        assert result["joint_type"] == "Angle"
        assert result["angle_degrees"] == TARGET_ANGLE_DEGREES
        assert result["angle_relation"] == "axis_dot_cosine"
        assert result["angle_satisfied"] is True
        assert (
            abs(result["measured_axis_angle_degrees"] - TARGET_ANGLE_DEGREES)
            < ANGLE_TOLERANCE_DEGREES
        )
        assert result["joint_count"] == 1
        assert result["grounded_count"] == 1
        assert result["solver"]["solver_status"] == 0
        assert "reverse" not in result
        assert "properties" not in result
        assert any(
            item["component"]["object_name"] == components[1].Name
            for item in result["moved_components"]
        )
        assert len(result["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1
        assert not Gui.Selection.getSelection()
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        joint = document.getObject(joint_name)
        assert joint.JointType == "Angle"
        assert joint.Angle.Value == TARGET_ANGLE_DEGREES
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert joint.Reference1[1] == ["Face6", "Face6"]
        assert joint.Reference2[1] == ["Face6", "Face6"]
        _assert_offset(placement_summary(joint.Offset1), FIRST_OFFSET)
        _assert_offset(placement_summary(joint.Offset2), SECOND_OFFSET)
        _assert_measured_angle(joint)

        replay = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-angle-create",
        )
        assert replay == result
        assert int(document.UndoCount) == 1
        assert len(_regular_joints(assembly)) == 1

        assembly_name = assembly.Name
        component_names = [component.Name for component in components]
        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        assert document.getObject(joint_name) is None
        assert not _regular_joints(assembly)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        joint = document.getObject(joint_name)
        assert joint in _regular_joints(assembly)
        assert joint.Angle.Value == TARGET_ANGLE_DEGREES
        _assert_measured_angle(joint)

        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        joint = document.getObject(joint_name)
        assert joint in _regular_joints(assembly)
        assert joint.JointType == "Angle"
        assert joint.Angle.Value == TARGET_ANGLE_DEGREES
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert joint.Reference1[0].Name in component_names
        assert joint.Reference2[0].Name in component_names
        _assert_offset(placement_summary(joint.Offset1), FIRST_OFFSET)
        _assert_offset(placement_summary(joint.Offset2), SECOND_OFFSET)
        _assert_measured_angle(joint)

        reopened = build_assembly_snapshot(document)
        summary = next(
            item for item in reopened["assemblies"] if item["object_name"] == assembly_name
        )
        assert summary["counts"] == {
            "components": 2,
            "joints": 1,
            "grounded": 1,
        }
        joint_summary = summary["joints"][0]
        assert joint_summary["joint_type"] == "Angle"
        assert joint_summary["angle_degrees"] == TARGET_ANGLE_DEGREES
        assert joint_summary["angle_relation"] == "axis_dot_cosine"
        assert joint_summary["angle_satisfied"] is True
        assert (
            abs(
                joint_summary["measured_axis_angle_degrees"]
                - TARGET_ANGLE_DEGREES
            )
            < ANGLE_TOLERANCE_DEGREES
        )
        assert "axes_parallel" not in joint_summary
        assert "axes_perpendicular" not in joint_summary
        assert "angular_limits" not in joint_summary
        assert "linear_limits" not in joint_summary
        assert "distance_mm" not in joint_summary
        _assert_offset(joint_summary["first"]["offset"], FIRST_OFFSET)
        _assert_offset(joint_summary["second"]["offset"], SECOND_OFFSET)

        print(
            "VIBECAD_NATIVE_ASSEMBLY_ANGLE_JOINT_GUI_OK "
            "components=2 joints=1 initial_parallel=true angle_degrees=60 "
            "angle_satisfied=true offsets=true transactions=1 reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        preferences.SetBool("SolveInJointCreation", prior_solve_preference)
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
