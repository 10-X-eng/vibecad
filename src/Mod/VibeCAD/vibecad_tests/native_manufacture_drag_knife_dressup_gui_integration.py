# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Drag Knife replacements."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Dressup.Gui.Dragknife as DragknifeGui
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)

from VibeCADNativeManufactureState import (
    copy_configuration_state,
    job_state,
    operation_reference_state,
    persistent_resource_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["drag_knife_dressup"]


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture", surface.surface_id
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> dict[str, bool]:
    return {
        obj.Name: bool(obj.ViewObject.Visibility)
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    }


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(
    job,
    base,
    *,
    label="Native CAM Drag Knife",
    corner_filter_angle_degrees=20.0,
    blade_offset_mm=2.0,
    pivot_height_mm=4.0,
) -> dict:
    return {
        "operation": "drag_knife_dressup",
        "label": label,
        "job": _target(job_state(job)),
        "base_operation": _target(operation_reference_state(base)),
        "corner_filter_angle_degrees": corner_filter_angle_degrees,
        "blade_offset_mm": blade_offset_mm,
        "pivot_height_mm": pivot_height_mm,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("drag_knife_dressup",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operation",
        "expected_state_sha256",
        "corner_filter_angle_degrees",
        "blade_offset_mm",
        "pivot_height_mm",
    ):
        assert field in encoded
    variant = schema["parameters"]["oneOf"][0]["properties"]
    assert variant["corner_filter_angle_degrees"]["minimum"] == 0.0
    assert variant["corner_filter_angle_degrees"]["maximum"] == 180.0
    assert variant["blade_offset_mm"]["exclusiveMinimum"] == 0.0
    assert variant["blade_offset_mm"]["maximum"] == 100.0
    assert variant["pivot_height_mm"]["minimum"] == 0.0
    assert variant["pivot_height_mm"]["maximum"] == 100.0
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _custom(job, controller, name, gcode):
    operation = PathCustom.Create(name, parentJob=job)
    operation.Label = "Drag Knife gate source"
    operation.ToolController = controller
    operation.CoolantMode = "None"
    operation.Gcode = list(gcode)
    return operation


def _line_path(*, depth=-1.0):
    return (
        "G0 X0 Y0 Z8",
        f"G1 Z{depth} F80",
        f"G1 X10 Y0 Z{depth} F120",
        f"G1 X10 Y10 Z{depth} F120",
        f"G1 X0 Y10 Z{depth} F120",
        "G0 Z8",
    )


def _arc_path():
    return (
        "G0 X0 Y0 Z8",
        "G1 Z-1 F80",
        "G1 X10 Y0 Z-1 F120",
        "G3 X20 Y10 Z-1 I10 J0 F120",
        "G1 X20 Y20 Z-1 F120",
        "G0 Z8",
    )


def _zero_radius_arc_path():
    return (
        "G0 X0 Y0 Z8",
        "G1 Z-1 F80",
        "G1 X10 Y0 Z-1 F120",
        "G2 X20 Y0 Z-1 I0 J0 F120",
        "G1 X30 Y0 Z-1 F120",
        "G0 Z8",
    )


def _create_fixture(document):
    model = document.addObject("Part::Feature", "DragKnifeGateModel")
    model.Label = "Drag Knife gate model"
    model.Shape = Part.makeBox(30.0, 20.0, 4.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]
    line = _custom(job, controller, "DragKnifeLine", _line_path())
    arc = _custom(job, controller, "DragKnifeArc", _arc_path())
    filtered = _custom(job, controller, "DragKnifeFiltered", _line_path())
    no_compensation = _custom(
        job,
        controller,
        "DragKnifeNoCompensation",
        ("G0 X0 Y0 Z8", "G1 Z-1 F80", "G1 X10 Y0 Z-1 F120"),
    )
    unsafe_depth = _custom(
        job,
        controller,
        "DragKnifeUnsafeDepth",
        _line_path(depth=5.0),
    )
    zero_radius = _custom(
        job,
        controller,
        "DragKnifeZeroRadius",
        _zero_radius_arc_path(),
    )
    assert document.recompute(None, True, True) is not False
    center = App.Vector(2.0, -3.0, 7.0)
    job_path = job.Path
    job_path.Center = center
    job.Path = job_path
    sources = (line, arc, filtered, no_compensation, unsafe_depth, zero_radius)
    for source in sources:
        source_path = source.Path
        source_path.Center = center
        source.Path = source_path
    assert tuple(job.Operations.Group) == sources
    assert all(source.isValid() and source.Path.Size for source in sources)
    return model, job, controller, sources


def _move_timeline_to(document, position: int) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None and 0 <= position <= len(timeline.Operations)
    window = Gui.getMainWindow()
    end = window.findChild(QtWidgets.QToolButton, "VibeCADFeatureTimelineEnd")
    previous = window.findChild(
        QtWidgets.QToolButton,
        "VibeCADFeatureTimelinePrevious",
    )
    assert end is not None and previous is not None
    end.click()
    _events(8)
    while int(timeline.Position) > position:
        previous.click()
        _events(4)
    assert int(timeline.Position) == position


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drag-knife-")
        save_path = Path(temporary.name) / "native-manufacture-drag-knife.FCStd"
        document = App.newDocument("NativeManufactureDragKnifeGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_DressupDragKnife"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "drag_knife_dressup",
            "ExactCamJobOperationAndDragKnifeCompensation",
            True,
            False,
        )

        model, job, controller, sources = _create_fixture(document)
        line, arc, filtered, no_compensation, unsafe_depth, zero_radius = sources
        document.clearUndos()
        source_states = {
            source.Name: (
                copy_configuration_state(source, {}),
                persistent_resource_state(source)["path_sha256"],
            )
            for source in sources
        }
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
        initial_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-drag-knife-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                controller_widget
            ).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-drag-knife-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)

        stale = _arguments(job, line)
        stale["base_operation"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        zero_offset = call(
            _arguments(job, line, blade_offset_mm=0.0),
            succeeds=False,
        )
        assert zero_offset["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        with patch(
            "VibeCADNativeManufactureDressupDragKnife.MAX_DRAG_KNIFE_INPUT_COMMANDS",
            3,
        ):
            workload = call(_arguments(job, line), succeeds=False)
        assert workload["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        unsafe = call(
            _arguments(job, unsafe_depth, pivot_height_mm=4.0),
            succeeds=False,
        )
        assert unsafe["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert unsafe["repair"]["highest_compensated_depth_mm"] == 5.0

        malformed = call(_arguments(job, zero_radius), succeeds=False)
        assert malformed["error_code"] == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        assert malformed["repair"]["native_error_type"] == "ValueError"

        no_op = call(_arguments(job, no_compensation), succeeds=False)
        assert no_op["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert no_op["repair"]["corner_action_count"] == 0
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group

        first_payload = _arguments(
            job,
            line,
            label="Native Drag Knife Line Compensation",
        )
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_created_drag_knife_dressup",
            side_effect=RuntimeError("forced Drag Knife postcondition failure"),
        ):
            failed = call(first_payload, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert tuple(document.Objects) == initial_objects
        assert tuple(job.Operations.Group) == initial_group
        assert initial_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        assert int(document.UndoCount) == 0

        line_result = call(first_payload)
        _events(16)
        line_output = document.getObject(line_result["object_name"])
        line_output_name = str(line_output.Name)
        assert isinstance(line_output.Proxy, DragknifeGui.ObjectDressup)
        assert isinstance(line_output.ViewObject.Proxy, DragknifeGui.ViewProviderDressup)
        assert line_output.Base is line
        assert line_result["corner_candidate_count"] == 2
        assert line_result["corner_action_count"] == 2
        assert line_result["line_extension_count"] == 3
        assert line_result["line_twist_count"] == 2
        assert line_result["arc_extension_count"] == 0
        assert line_result["arc_twist_count"] == 0
        assert line_result["corner_action_depths_mm"] == [-1.0]
        assert line_result["path_center_mm"] == [2.0, -3.0, 7.0]
        assert len(line_result["receipt"]["created"]) == 1
        assert len(line_result["receipt"]["replaced"]) == 1
        assert line_result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(line_output_name) is None
        assert tuple(job.Operations.Group) == initial_group
        assert line.ViewObject.Visibility
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        controller = document.getObject(controller.Name)
        model = document.getObject(model.Name)
        sources = tuple(document.getObject(source.Name) for source in sources)
        line, arc, filtered, no_compensation, unsafe_depth, zero_radius = sources
        line_output = document.getObject(line_output_name)
        assert line_output.Base is line

        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-drag-knife-gui-after-redo")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        arc_result = call(
            _arguments(
                job,
                arc,
                label="Native Drag Knife Arc Compensation",
                corner_filter_angle_degrees=5.0,
                blade_offset_mm=1.5,
                pivot_height_mm=6.0,
            )
        )
        arc_output = document.getObject(arc_result["object_name"])
        assert arc_output.Base is arc
        assert arc_result["corner_action_count"] == 2, arc_result
        assert arc_result["line_extension_count"] >= 1
        assert arc_result["arc_extension_count"] >= 1
        assert arc_result["line_twist_count"] >= 1
        assert arc_result["arc_twist_count"] >= 1
        assert arc_result["blade_offset_mm"] == 1.5
        assert arc_result["pivot_height_mm"] == 6.0

        timeline = document.getObject("VibeCADTimeline")
        future_index = tuple(timeline.Operations).index(no_compensation)
        _move_timeline_to(document, future_index)
        marker_before = int(timeline.Position)
        turn = _turn(surface, registry)
        frozen = turn.surface
        ledger.begin_run("native-manufacture-drag-knife-gui-at-marker")
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        filtered_result = call(
            _arguments(
                job,
                filtered,
                label="Native Drag Knife Filtered Corners",
                corner_filter_angle_degrees=100.0,
                blade_offset_mm=3.0,
                pivot_height_mm=5.0,
            )
        )
        filtered_output = document.getObject(filtered_result["object_name"])
        assert filtered_result["corner_candidate_count"] == 2
        assert filtered_result["corner_action_count"] == 0
        assert filtered_result["line_extension_count"] == 1
        assert filtered_result["line_twist_count"] == 0
        assert int(timeline.Position) == marker_before + 1
        assert tuple(timeline.Operations)[marker_before] is filtered_output
        assert tuple(timeline.Operations)[marker_before + 1] is no_compensation

        end = Gui.getMainWindow().findChild(
            QtWidgets.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        assert end is not None
        end.click()
        _events(12)

        outputs = (line_output, arc_output, filtered_output)
        successful_sources = (line, arc, filtered)
        for source in sources:
            configuration, path_sha256 = source_states[source.Name]
            actual_configuration = copy_configuration_state(source, {})
            assert actual_configuration == configuration, (
                source.Name,
                configuration,
                actual_configuration,
            )
            assert persistent_resource_state(source)["path_sha256"] == path_sha256
            assert tuple(source.Path.Center) == (2.0, -3.0, 7.0)
        assert all(not source.ViewObject.Visibility for source in successful_sources)
        assert all(output.ViewObject.Visibility for output in outputs)
        assert _selection() == selection_before
        for name, visible in visibility_before.items():
            if name not in {source.Name for source in successful_sources}:
                assert bool(document.getObject(name).ViewObject.Visibility) is visible

        job_name = str(job.Name)
        output_names = tuple(str(output.Name) for output in outputs)
        source_names = tuple(str(source.Name) for source in successful_sources)
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = None
        _events(20)
        document = App.openDocument(str(save_path))
        _events(24)
        job = document.getObject(job_name)
        reopened_outputs = tuple(document.getObject(name) for name in output_names)
        reopened_sources = tuple(document.getObject(name) for name in source_names)
        assert all(
            isinstance(output.Proxy, DragknifeGui.ObjectDressup)
            and isinstance(output.ViewObject.Proxy, DragknifeGui.ViewProviderDressup)
            and output.Base is source
            and output in job.Operations.Group
            for output, source in zip(reopened_outputs, reopened_sources)
        )
        assert all(not source.ViewObject.Visibility for source in reopened_sources)
        assert all(output.ViewObject.Visibility for output in reopened_outputs)
        assert all(
            tuple(output.VibeCADTimelineReplacedInputs) == (source,)
            for output, source in zip(reopened_outputs, reopened_sources)
        )
        reopened_center = tuple(job.Path.Center)
        assert all(
            tuple(output.Path.Center) == reopened_center
            for output in reopened_outputs
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_DRAG_KNIFE_DRESSUP_GUI_OK "
            "exact_job=true exact_base=true stale=true invalid_offset=true "
            "workload_guard=true unsafe_pivot=true malformed_arc=true no_op=true "
            "rollback=true line=true arc=true filter=true rotary_center=true "
            "source_preserved=true replacement=true history=true marker=true receipt=true "
            "selection=true visibility=true undo=true redo=true reopen=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        if application is not None:
            application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
