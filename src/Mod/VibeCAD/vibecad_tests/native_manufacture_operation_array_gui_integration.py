# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact parametric CAM Array operations."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureState import (
    candidate_model_state,
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


def _visibility(document) -> tuple:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _array_arguments(job, bases, pattern, *, label="Native CAM Array", reverse=False, jitter=None):
    return {
        "operation": "array",
        "label": label,
        "job": _target(job_state(job)),
        "base_operations": [
            _target(operation_reference_state(operation)) for operation in bases
        ],
        "pattern": pattern,
        "reverse_direction": reverse,
        "jitter": jitter if jitter is not None else {"enabled": False},
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("array",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "base_operations",
        "expected_state_sha256",
        "linear_1d",
        "linear_2d",
        "polar",
        "points",
        "reverse_direction",
        "maximum_offset_mm",
        "maximum_rotation_degrees",
    ):
        assert field in encoded
    assert '"maxItems":64' in encoded
    assert '"maxItems":32' in encoded
    assert '"maximum":2147483647' in encoded
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_OPERATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _create_fixture(document):
    model = document.addObject("Part::Feature", "ArrayGateModel")
    model.Label = "Operation Array gate model"
    model.Shape = Part.makeBox(24.0, 18.0, 8.0)
    point_source = document.addObject("Part::Feature", "ArrayGatePoints")
    point_source.Label = "Operation Array exact points"
    point_source.Shape = Part.makeCompound(
        [
            Part.Vertex(App.Vector(30.0, 0.0, 0.0)),
            Part.Vertex(App.Vector(0.0, 30.0, 0.0)),
        ]
    )
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    controller = job.Tools.Group[0]

    base = PathCustom.Create("ArrayGateBase", parentJob=job)
    job.Proxy.addOperation(base)
    base.Label = "Array gate source path"
    base.ToolController = controller
    base.CoolantMode = "None"
    base.Gcode = [
        "G0 X10 Y0 Z5",
        "G0 X10 Y0 Z0",
        "G1 X15 Y0 Z0",
        "G1 X15 Y5 Z0",
        "G1 X10 Y5 Z0",
        "G1 X10 Y0 Z0",
    ]
    assert document.recompute(None, True, True) is not False
    assert tuple(job.Operations.Group) == (base,)
    assert base.isValid() and base.Path.Size >= 6, {
        "state": list(base.State),
        "path_size": int(base.Path.Size),
        "gcode": list(base.Gcode),
        "tool_controller": str(getattr(getattr(base, "ToolController", None), "Name", "")),
        "job_operations": [value.Name for value in job.Operations.Group],
    }
    return model, point_source, job, controller, base


def _xy(command) -> tuple[float, float]:
    return round(float(command.x), 6), round(float(command.y), 6)


def _repeat_starts(operation, block_size: int) -> list[tuple[float, float]]:
    commands = tuple(operation.Path.Commands)
    result = []
    for block_start in range(0, len(commands), block_size):
        block = commands[block_start : block_start + block_size]
        positioned = next(
            command
            for command in block
            if command.x is not None and command.y is not None
        )
        result.append(_xy(positioned))
    return result


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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-array-")
        save_path = Path(temporary.name) / "native-manufacture-array.FCStd"
        document = App.newDocument("NativeManufactureArrayGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Array"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            "manufacture.array",
            "array",
            "ExactCamJobBaseToolpathsArrayPatternAndPointSources",
            True,
            False,
        ), actual_plan

        model, point_source, job, tool_controller, base = _create_fixture(document)
        document.clearUndos()
        source_state = persistent_resource_state(base)
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
        ledger.begin_run("native-manufacture-operation-array-gui")

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
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-array-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)
        linear_pattern = {
            "kind": "linear_1d",
            "copies": 2,
            "offset_mm": {"x_mm": 20.0, "y_mm": 0.0, "z_mm": 0.0},
        }

        stale = _array_arguments(job, (base,), linear_pattern)
        stale["base_operations"][0]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(document.Objects) == initial_objects
        assert int(document.UndoCount) == 0

        oversized = _array_arguments(
            job,
            (base,),
            {
                "kind": "linear_1d",
                "copies": 99999,
                "offset_mm": {"x_mm": 1.0, "y_mm": 0.0, "z_mm": 0.0},
            },
        )
        oversized_result = call(oversized, succeeds=False)
        assert oversized_result["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        assert int(document.UndoCount) == 0

        linear_payload = _array_arguments(job, (base,), linear_pattern)
        with patch(
            "VibeCADNativeManufactureOperationRuntime.verify_created_array",
            side_effect=RuntimeError("forced Array postcondition failure"),
        ):
            failed = call(linear_payload, succeeds=False)
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

        linear = call(linear_payload)
        _events(16)
        linear_object = document.getObject(linear["object_name"])
        linear_name = str(linear_object.Name)
        assert linear["pattern"]["repeat_count"] == 2
        assert linear["command_count"] == base.Path.Size * 2
        assert tuple(linear_object.Base) == (base,)
        assert linear_object.ToolController is tool_controller
        assert _repeat_starts(linear_object, base.Path.Size) == [
            (30.0, 0.0),
            (50.0, 0.0),
        ]
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before
        assert persistent_resource_state(base) == source_state
        assert linear["assistant_undo_available"] is True
        assert len(linear["receipt"]["created"]) == 1
        assert int(document.UndoCount) == 1

        document.undo()
        _events(16)
        assert document.getObject(linear_name) is None
        assert tuple(job.Operations.Group) == initial_group
        document.redo()
        _events(16)
        job = document.getObject(job.Name)
        base = document.getObject(base.Name)
        linear_object = document.getObject(linear_name)
        assert tuple(linear_object.Base) == (base,)

        linear_2d = call(
            _array_arguments(
                job,
                (base,),
                {
                    "kind": "linear_2d",
                    "copies_x": 1,
                    "copies_y": 1,
                    "offset_mm": {"x_mm": 20.0, "y_mm": 15.0, "z_mm": 0.0},
                    "first_direction": "x",
                },
                label="Native CAM 2D Array",
                reverse=True,
            )
        )
        linear_2d_object = document.getObject(linear_2d["object_name"])
        assert linear_2d["pattern"]["repeat_count"] == 3
        starts_2d = set(_repeat_starts(linear_2d_object, base.Path.Size))
        assert starts_2d == {(30.0, 0.0), (30.0, 15.0), (10.0, 15.0)}

        polar = call(
            _array_arguments(
                job,
                (base,),
                {
                    "kind": "polar",
                    "copies": 3,
                    "total_angle_degrees": 360.0,
                    "centre_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                },
                label="Native CAM Polar Array",
            )
        )
        polar_object = document.getObject(polar["object_name"])
        polar_starts = _repeat_starts(polar_object, base.Path.Size)
        assert polar_starts == [(0.0, 10.0), (-10.0, 0.0), (0.0, -10.0)]

        point_target = _target(candidate_model_state(point_source))
        points = call(
            _array_arguments(
                job,
                (base,),
                {
                    "kind": "points",
                    "sources": [{"model": point_target, "subelements": []}],
                    "origin": {"kind": "global"},
                    "sorting": "manual",
                },
                label="Native CAM Points Array",
            )
        )
        points_object = document.getObject(points["object_name"])
        point_starts = _repeat_starts(points_object, base.Path.Size)
        assert point_starts == [(40.0, 0.0), (10.0, 30.0)]
        assert points["pattern"]["repeat_count"] == 2

        jitter_payload = _array_arguments(
            job,
            (base,),
            linear_pattern,
            label="Native CAM Seeded Array A",
            jitter={
                "enabled": True,
                "seed": 12345,
                "maximum_offset_mm": {"x_mm": 1.0, "y_mm": 2.0, "z_mm": 0.0},
                "maximum_rotation_degrees": 4.0,
            },
        )
        jitter_a = call(jitter_payload)
        jitter_payload = _array_arguments(
            job,
            (base,),
            linear_pattern,
            label="Native CAM Seeded Array B",
            jitter=jitter_payload["jitter"],
        )
        jitter_b = call(jitter_payload)
        assert jitter_a["path_sha256"] == jitter_b["path_sha256"]

        timeline = document.VibeCADTimeline
        marker = list(timeline.Operations).index(base) + 1
        _move_timeline_to(document, marker)
        timeline_before_marker = (
            tuple(timeline.Operations),
            tuple(bool(value) for value in timeline.VisibilityAtEnd),
            tuple(bool(value) for value in timeline.SuppressionAtEnd),
        )
        marker_array = call(
            _array_arguments(
                job,
                (base,),
                linear_pattern,
                label="Native CAM Marker Array",
            )
        )
        marker_object = document.getObject(marker_array["object_name"])
        assert tuple(timeline.Operations) == (
            *timeline_before_marker[0][:marker],
            marker_object,
            *timeline_before_marker[0][marker:],
        )
        for old_index in range(len(timeline_before_marker[0])):
            new_index = old_index if old_index < marker else old_index + 1
            assert bool(timeline.VisibilityAtEnd[new_index]) is timeline_before_marker[1][old_index]
            assert bool(timeline.SuppressionAtEnd[new_index]) is timeline_before_marker[2][old_index]
        _move_timeline_to(document, len(timeline.Operations))

        assert persistent_resource_state(base) == source_state
        assert _selection() == selection_before
        assert _visibility(document)[: len(visibility_before)] == visibility_before
        assert not Gui.Control.activeDialog()

        document_name = str(document.Name)
        job_name = str(job.Name)
        base_name = str(base.Name)
        output_names = tuple(
            result["object_name"]
            for result in (
                linear,
                linear_2d,
                polar,
                points,
                jitter_a,
                jitter_b,
                marker_array,
            )
        )
        document.saveAs(str(save_path))
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _events(24)
        job = document.getObject(job_name)
        base = document.getObject(base_name)
        outputs = tuple(document.getObject(name) for name in output_names)
        assert all(outputs)
        assert all(tuple(output.Base) == (base,) for output in outputs)
        assert all(output.isValid() and output.Path.Size for output in outputs)
        assert all(output in job.Operations.Group for output in outputs)
        assert all(str(output.VibeCADTimelineRole) == "operation" for output in outputs)

        print(
            "VIBECAD_NATIVE_MANUFACTURE_OPERATION_ARRAY_GUI_OK "
            "exact_job=true exact_bases=true stale=true bounded_work=true "
            "rollback=true linear_1d=true linear_2d=true polar=true points=true "
            "seeded_jitter=true source_preserved=true history=true receipt=true "
            "marker=true selection=true visibility=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
