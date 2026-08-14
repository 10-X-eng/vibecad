# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for one exact Native Assembly Distance joint."""

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
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointConnectors import placement_summary
from VibeCADNativeAssemblyJointSchema import assembly_joint_capability_definition
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


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
            joint_definition.provider_schema(("create_distance",)),
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


def _placement(x: float, y: float, z: float, axis: tuple[float, float, float], angle: float) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": axis[0], "y": axis[1], "z": axis[2]},
            "angle_degrees": angle,
        },
    }


ARM_POINT_OFFSET = _placement(1.0, 0.0, 2.0, (1.0, 0.0, 0.0), 15.0)
BASE_PLANE_OFFSET = _placement(0.0, -1.0, 0.0, (0.0, 0.0, 1.0), 20.0)


def _connector(component, element: str, offset: dict) -> dict:
    return {
        "component": {"object_name": component.Name},
        "element_path": element,
        "anchor_path": element,
        "offset": offset,
        "expected_component_placement": placement_summary(component.Placement),
    }


def _arguments(assembly, components, *, expected_joint_count: int) -> dict:
    return {
        "operation": "create_distance",
        "assembly": {"object_name": assembly.Name},
        "first": _connector(components[1], "Vertex1", ARM_POINT_OFFSET),
        "second": _connector(components[0], "Face6", BASE_PLANE_OFFSET),
        "label": "Native Arm Height",
        "reverse": True,
        "distance_mm": 18.0,
        "expected_distance_mode": "point_plane",
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": expected_joint_count,
        "expected_solve_on_creation": True,
    }


def _assert_offset(actual: dict, expected: dict) -> None:
    for coordinate in ("x", "y", "z"):
        assert abs(actual["origin_mm"][coordinate] - expected["origin_mm"][coordinate]) < 1e-9
        assert abs(actual["rotation"]["axis"][coordinate] - expected["rotation"]["axis"][coordinate]) < 1e-9
    assert abs(actual["rotation"]["angle_degrees"] - expected["rotation"]["angle_degrees"]) < 1e-9


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
            prefix="vibecad-native-assembly-distance-joint-"
        )
        path = Path(temporary.name) / "native-assembly-distance-joint.FCStd"
        document = App.newDocument("NativeAssemblyDistanceJointGate")
        document.UndoMode = 1
        sources = []
        for index in range(2):
            source = document.addObject("Part::Box", f"DistanceSource{index + 1}")
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

        document.openTransaction("Prepare Distance-joint fixture")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject(
                "App::Link",
                f"DistanceComponent{index + 1}",
            )
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

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateJointDistance" in surface.command_ids
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is True, production.summary()
        assert ASSEMBLY_JOINT_CAPABILITY_NAME not in production.missing_definition_names
        assert ASSEMBLY_JOINT_CAPABILITY_NAME not in production.missing_implementation_names
        assert ASSEMBLY_JOINT_CAPABILITY_NAME not in production.incomplete_definition_names

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-distance-joint-gui")

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
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
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
            "state.read",
            '{"operation":"active"}',
            "assembly-distance-state-1",
        )
        assert initial["ok"] is True, initial
        active_summary = initial["domain"]["assemblies"][0]
        assert active_summary["counts"] == {
            "components": 2,
            "joints": 0,
            "grounded": 1,
        }

        before_invalid = tuple(document.Objects)
        invalid = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            json.dumps(_arguments(assembly, components, expected_joint_count=1)),
            "assembly-distance-stale",
        )
        assert invalid["ok"] is False, invalid
        assert invalid["error_code"] == "NATIVE_ASSEMBLY_DISTANCE_JOINT_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        arguments = _arguments(assembly, components, expected_joint_count=0)
        encoded = json.dumps(arguments, separators=(",", ":"))
        result = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-distance-create",
        )
        assert result["ok"] is True, result
        joint_name = result["joint"]["object_name"]
        assert result["joint_type"] == "Distance"
        assert result["distance_mm"] == 18.0
        assert result["distance_mode"] == "point_plane"
        assert result["reverse"] is True
        assert result["joint_count"] == 1
        assert result["grounded_count"] == 1
        assert result["solver"]["solver_status"] == 0
        assert len(result["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1
        assert not Gui.Selection.getSelection()
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        joint = document.getObject(joint_name)
        assert joint.JointType == "Distance"
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert joint.Distance.Value == 18.0
        assert joint.Reference1[0] is components[0]
        assert joint.Reference1[1] == ["Face6", "Face6"]
        assert joint.Reference2[0] is components[1]
        assert joint.Reference2[1] == ["Vertex1", "Vertex1"]
        _assert_offset(placement_summary(joint.Offset1), BASE_PLANE_OFFSET)
        _assert_offset(placement_summary(joint.Offset2), ARM_POINT_OFFSET)

        replay = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-distance-create",
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
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)

        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        components = [document.getObject(name) for name in component_names]
        joint = document.getObject(joint_name)
        assert joint in _regular_joints(assembly)
        assert joint.JointType == "Distance"
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert joint.Distance.Value == 18.0
        assert joint.Reference1[0] is components[0]
        assert joint.Reference2[0] is components[1]
        _assert_offset(placement_summary(joint.Offset1), BASE_PLANE_OFFSET)
        _assert_offset(placement_summary(joint.Offset2), ARM_POINT_OFFSET)

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
        assert joint_summary["joint_type"] == "Distance"
        assert joint_summary["distance_mm"] == 18.0
        assert joint_summary["distance_mode"] == "point_plane"
        assert "linear_limits" not in joint_summary
        assert "angular_limits" not in joint_summary
        assert joint_summary["first"]["component"]["object_name"] == components[0].Name
        assert joint_summary["second"]["component"]["object_name"] == components[1].Name
        _assert_offset(joint_summary["first"]["offset"], BASE_PLANE_OFFSET)
        _assert_offset(joint_summary["second"]["offset"], ARM_POINT_OFFSET)

        print(
            "VIBECAD_NATIVE_ASSEMBLY_DISTANCE_JOINT_GUI_OK "
            "components=2 joints=1 mode=point_plane canonicalized=true "
            "distance_mm=18 reverse=true transactions=1 reopen=true",
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
