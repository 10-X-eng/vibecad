# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for one exact Native Assembly Screw joint."""

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
from VibeCADNativeAssemblyScrewJoint import screw_dependency_summary
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


THREAD_PITCH_MM = -2.0
RELATIVE_ADVANCE_MM_PER_REVOLUTION = THREAD_PITCH_MM
SLIDER_TRAVEL_MM_PER_SCREW_REVOLUTION = -THREAD_PITCH_MM


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
            joint_definition.provider_schema(("create_screw",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def _joint_group(assembly):
    groups = [
        child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
    ]
    assert len(groups) == 1
    return groups[0]


def _regular_joints(assembly):
    return [
        joint
        for joint in _joint_group(assembly).Group
        if hasattr(joint, "JointType")
        and UtilsAssembly.isTimelineOperationActive(joint)
    ]


def _placement() -> dict:
    return {
        "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": 0.0,
        },
    }


IDENTITY_OFFSET = _placement()


def _reference(component):
    return [component, ["Face6", "Face6"]]


def _connector(component) -> dict:
    return {
        "component": {"object_name": component.Name},
        "element_path": "Face6",
        "anchor_path": "Face6",
        "offset": IDENTITY_OFFSET,
        "expected_component_placement": placement_summary(component.Placement),
    }


def _create_prerequisite_joint(
    joint_group,
    *,
    type_index: int,
    label: str,
    first,
    second,
):
    joint = joint_group.newObject("App::FeaturePython", "Joint")
    joint.Label = label
    JointObject.Joint(joint, type_index)
    JointObject.ensureViewProviderJoint(joint)
    joint.Offset1 = part_placement_from_mapping(IDENTITY_OFFSET)
    joint.Offset2 = part_placement_from_mapping(IDENTITY_OFFSET)
    joint.Proxy.setJointConnectors(
        joint,
        [_reference(first), _reference(second)],
    )
    return joint


def _arguments(
    assembly,
    slider_component,
    screw_component,
    slider,
    revolute,
    *,
    expected_joint_count: int,
) -> dict:
    return {
        "operation": "create_screw",
        "assembly": {"object_name": assembly.Name},
        "slider_connector": _connector(slider_component),
        "screw_connector": _connector(screw_component),
        "slider_joint": {"object_name": slider.Name},
        "screw_revolute_joint": {"object_name": revolute.Name},
        "label": "Native Lead Screw Coupling",
        "thread_pitch_mm": THREAD_PITCH_MM,
        "expected_component_count": 3,
        "expected_grounded_count": 1,
        "expected_joint_count": expected_joint_count,
        "expected_solve_on_creation": True,
    }


def _assert_identity_offset(actual: dict) -> None:
    expected_axis = {"x": 0.0, "y": 0.0, "z": 1.0}
    for coordinate in ("x", "y", "z"):
        assert abs(actual["origin_mm"][coordinate]) < 1.0e-9
        assert (
            abs(actual["rotation"]["axis"][coordinate] - expected_axis[coordinate])
            < 1.0e-9
        )
    assert abs(actual["rotation"]["angle_degrees"]) < 1.0e-9


def _assert_dependency_graph(joint, slider, revolute) -> None:
    assert joint.Reference1[0] is slider.Reference2[0]
    assert joint.Reference1[1] == slider.Reference2[1]
    assert joint.Offset1.isSame(slider.Offset2, 1.0e-9)
    assert joint.Reference2[0] is revolute.Reference2[0]
    assert joint.Reference2[1] == revolute.Reference2[1]
    assert joint.Offset2.isSame(revolute.Offset2, 1.0e-9)
    assembly = UtilsAssembly.findOwningAssembly(joint)
    dependency = screw_dependency_summary(joint, tuple(_regular_joints(assembly)))
    assert dependency is not None
    assert dependency["slider_joint"]["object_name"] == slider.Name
    assert dependency["screw_revolute_joint"]["object_name"] == revolute.Name
    assert dependency["axes_collinear"] is True


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
            prefix="vibecad-native-assembly-screw-joint-"
        )
        path = Path(temporary.name) / "native-assembly-screw-joint.FCStd"
        document = App.newDocument("NativeAssemblyScrewJointGate")
        document.UndoMode = 1
        sources = []
        for index in range(3):
            source = document.addObject("Part::Box", f"ScrewSource{index + 1}")
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

        document.openTransaction("Prepare Screw prerequisites")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject(
                "App::Link",
                ("Base", "Slider", "Screw")[index],
            )
            component.LinkedObject = source
            component.Placement.Base.x = float(index * 35)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        base, slider_component, screw_component = components
        ground = CommandCreateJoint.createGroundedJointFeature(base, assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        group = _joint_group(assembly)
        slider = _create_prerequisite_joint(
            group,
            type_index=3,
            label="Slider Prerequisite",
            first=base,
            second=slider_component,
        )
        revolute = _create_prerequisite_joint(
            group,
            type_index=1,
            label="Screw Revolute Prerequisite",
            first=base,
            second=screw_component,
        )
        document.recompute()
        assembly.solve()
        document.recompute()
        document.commitTransaction()
        _process_events(20)
        assert slider.JointType == "Slider"
        assert revolute.JointType == "Revolute"
        assert len(_regular_joints(assembly)) == 2
        document.clearUndos()
        Gui.Selection.clearSelection()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateJointScrew" in surface.command_ids
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
        ledger.begin_run("native-assembly-screw-joint-gui")

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
            "assembly-screw-state-1",
        )
        assert initial["ok"] is True, initial
        assert initial["domain"]["assemblies"][0]["counts"] == {
            "components": 3,
            "joints": 2,
            "grounded": 1,
        }

        before_invalid = tuple(document.Objects)
        invalid = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            json.dumps(
                _arguments(
                    assembly,
                    slider_component,
                    screw_component,
                    slider,
                    revolute,
                    expected_joint_count=1,
                )
            ),
            "assembly-screw-stale",
        )
        assert invalid["ok"] is False, invalid
        assert invalid["error_code"] == "NATIVE_ASSEMBLY_SCREW_JOINT_FAILED"
        assert tuple(document.Objects) == before_invalid
        assert int(document.UndoCount) == 0

        arguments = _arguments(
            assembly,
            slider_component,
            screw_component,
            slider,
            revolute,
            expected_joint_count=2,
        )
        encoded = json.dumps(arguments, separators=(",", ":"))
        result = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-screw-create",
        )
        assert result["ok"] is True, result
        joint_name = result["joint"]["object_name"]
        assert result["joint_type"] == "Screw"
        assert result["thread_pitch_mm"] == THREAD_PITCH_MM
        assert (
            result["relative_axial_advance_mm_per_revolution"]
            == RELATIVE_ADVANCE_MM_PER_REVOLUTION
        )
        assert (
            result["slider_travel_mm_per_screw_revolution"]
            == SLIDER_TRAVEL_MM_PER_SCREW_REVOLUTION
        )
        assert result["slider_joint"]["object_name"] == slider.Name
        assert result["screw_revolute_joint"]["object_name"] == revolute.Name
        assert result["axes_collinear"] is True
        assert result["joint_count"] == 3
        assert result["grounded_count"] == 1
        assert result["solver"]["solver_status"] == 0
        assert "connectors" not in result
        assert "reverse" not in result
        assert "properties" not in result
        assert len(result["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1
        assert not Gui.Selection.getSelection()
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        joint = document.getObject(joint_name)
        assert joint.JointType == "Screw"
        assert joint.Distance.Value == THREAD_PITCH_MM
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        _assert_identity_offset(placement_summary(joint.Offset1))
        _assert_identity_offset(placement_summary(joint.Offset2))
        _assert_dependency_graph(joint, slider, revolute)

        replay = dispatcher.call(
            ASSEMBLY_JOINT_CAPABILITY_NAME,
            encoded,
            "assembly-screw-create",
        )
        assert replay == result
        assert int(document.UndoCount) == 1
        assert len(_regular_joints(assembly)) == 3

        assembly_name = assembly.Name
        component_names = [component.Name for component in components]
        slider_name = slider.Name
        revolute_name = revolute.Name
        document.undo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        assert document.getObject(joint_name) is None
        assert len(_regular_joints(assembly)) == 2
        assert document.getObject(slider_name) in _regular_joints(assembly)
        assert document.getObject(revolute_name) in _regular_joints(assembly)
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly_name)
        joint = document.getObject(joint_name)
        slider = document.getObject(slider_name)
        revolute = document.getObject(revolute_name)
        assert joint in _regular_joints(assembly)
        assert joint.Distance.Value == THREAD_PITCH_MM
        _assert_dependency_graph(joint, slider, revolute)

        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        joint = document.getObject(joint_name)
        slider = document.getObject(slider_name)
        revolute = document.getObject(revolute_name)
        assert joint in _regular_joints(assembly)
        assert joint.JointType == "Screw"
        assert joint.Distance.Value == THREAD_PITCH_MM
        assert isinstance(joint.Proxy, JointObject.Joint)
        assert isinstance(joint.ViewObject.Proxy, JointObject.ViewProviderJoint)
        assert joint.Reference1[0].Name in component_names
        assert joint.Reference2[0].Name in component_names
        _assert_identity_offset(placement_summary(joint.Offset1))
        _assert_identity_offset(placement_summary(joint.Offset2))
        _assert_dependency_graph(joint, slider, revolute)

        reopened = build_assembly_snapshot(document)
        summary = next(
            item
            for item in reopened["assemblies"]
            if item["object_name"] == assembly_name
        )
        assert summary["counts"] == {
            "components": 3,
            "joints": 3,
            "grounded": 1,
        }
        joint_summary = next(
            item for item in summary["joints"] if item["object_name"] == joint_name
        )
        assert joint_summary["joint_type"] == "Screw"
        assert joint_summary["thread_pitch_mm"] == THREAD_PITCH_MM
        assert (
            joint_summary["relative_axial_advance_mm_per_revolution"]
            == RELATIVE_ADVANCE_MM_PER_REVOLUTION
        )
        assert (
            joint_summary["slider_travel_mm_per_screw_revolution"]
            == SLIDER_TRAVEL_MM_PER_SCREW_REVOLUTION
        )
        assert joint_summary["prerequisites_resolved"] is True
        assert joint_summary["slider_joint"]["object_name"] == slider_name
        assert joint_summary["screw_revolute_joint"]["object_name"] == revolute_name
        assert joint_summary["axes_collinear"] is True
        assert "angular_limits" not in joint_summary
        assert "linear_limits" not in joint_summary
        assert "distance_mm" not in joint_summary
        _assert_identity_offset(joint_summary["first"]["offset"])
        _assert_identity_offset(joint_summary["second"]["offset"])

        print(
            "VIBECAD_NATIVE_ASSEMBLY_SCREW_JOINT_GUI_OK "
            "components=3 joints=3 prerequisites=true thread_pitch_mm=-2 "
            "slider_travel_mm_per_revolution=2 axes_collinear=true "
            "transactions=1 reopen=true",
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
