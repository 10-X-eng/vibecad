# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Slot."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeManufactureOperationRuntime import NativeManufactureOperationRuntime
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


def _commit(document, label: str, action):
    document.openTransaction(label)
    transaction = int(document.getBookedTransactionID())
    assert transaction
    try:
        value = action()
        assert document.recompute(None, True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return value


def _create_model_and_job(document):
    def create_model():
        plate = document.addObject("Part::Feature", "SlotGatePlate")
        plate.Label = "Slot gate plate"
        plate.Shape = Part.makeBox(60.0, 40.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(plate, (), ())
        guide = document.addObject("Part::Feature", "SlotGateModel")
        guide.Label = "Slot gate guide geometry"
        guide.Shape = Part.makeCompound(
            [
                Part.makeLine(App.Vector(10.0, 20.0, 8.0), App.Vector(50.0, 20.0, 8.0)),
                Part.makeLine(App.Vector(5.0, 5.0, 0.0), App.Vector(5.0, 5.0, 8.0)),
            ]
        )
        document.publishProvisionalTimelineOperationBlock(guide, (), ())
        return plate, guide

    plate, model = _commit(document, "Create Slot gate model", create_model)

    def create_job():
        job = PathJob.Create("SlotJob", [plate, model], templateFile=None)
        provider = PathJobGui.ViewProvider(job.ViewObject)
        job.ViewObject.Proxy = provider
        job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
        provider.setupEditVisibility(job)
        try:
            provider.syncTimelineReplacedInputs(job)
        finally:
            provider.resetEditVisibility(job)
        provider.applyAcceptedReplacementVisibilityTransition(job)
        provider.deleteOnReject = False
        return job

    return model, _commit(document, "Create Slot gate Job", create_job)


def _edge_names(model) -> tuple[str, str]:
    top_long = []
    vertical = []
    for index, edge in enumerate(model.Shape.Edges, start=1):
        bounds = edge.BoundBox
        name = f"Edge{index}"
        if (
            abs(float(bounds.XLength) - 40.0) <= 1.0e-7
            and abs(float(bounds.ZMin) - 8.0) <= 1.0e-7
            and abs(float(bounds.ZMax) - 8.0) <= 1.0e-7
        ):
            top_long.append(name)
        if abs(float(bounds.ZLength) - 8.0) <= 1.0e-7:
            vertical.append(name)
    assert len(top_long) == 1, top_long
    assert len(vertical) == 1, vertical
    return top_long[0], vertical[0]


def _job_resource(job, source):
    matches = tuple(
        resource
        for resource in job.Model.Group
        if job.Proxy.baseObject(job, resource) is source
    )
    assert len(matches) == 1, matches
    return matches[0]


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("slot",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "custom_points",
        "single_edge",
        "single_horizontal_face",
        "single_vertical_face",
        "two_vertices",
        "two_edges",
        "two_vertical_faces",
        "extend_start_mm",
        "extend_end_mm",
        "layer_mode",
        "reverse_direction",
        "start_depth_mm",
        "clearance_height_mm",
    ):
        assert field in encoded
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


def _common_arguments(job, *, label: str, slot: dict) -> dict:
    state = job_state(job)
    return {
        "operation": "slot",
        "label": label,
        "job": _target(state),
        "tool_controller": _target(state["tools"][0]),
        "slot": slot,
        "depths": {
            "start_depth_mm": 8.0,
            "final_depth_mm": 5.0,
            "step_down_mm": 1.0,
        },
        "heights": {
            "safe_height_mm": 10.0,
            "clearance_height_mm": 12.0,
        },
        "coolant": "mist",
    }


def _feature_arguments(model, job, edge_name: str) -> dict:
    state = job_state(job)
    job_model = next(
        item for item in state["models"] if item["object_name"] == model.Name
    )
    return {
        "operation": "slot",
        "label": "Native edge Slot",
        "job": _target(state),
        "tool_controller": _target(state["tools"][0]),
        "slot": {
            "path": {
                "kind": "single_edge",
                "model": _target(job_model),
                "edge": edge_name,
                "orientation": "start_to_end",
            },
            "extend_start_mm": 1.0,
            "extend_end_mm": 2.0,
            "layer_mode": "directional",
            "reverse_direction": True,
        },
        "depths": {
            "start_depth_mm": 8.0,
            "final_depth_mm": 5.0,
            "step_down_mm": 1.0,
        },
        "heights": {
            "safe_height_mm": 10.0,
            "clearance_height_mm": 12.0,
        },
        "coolant": "flood",
    }


def _custom_arguments(job) -> dict:
    return _common_arguments(
        job,
        label="Native custom Slot",
        slot={
            "path": {
                "kind": "custom_points",
                "start_point_mm": {"x_mm": 10.0, "y_mm": 20.0, "z_mm": 8.0},
                "end_point_mm": {"x_mm": 50.0, "y_mm": 20.0, "z_mm": 8.0},
            },
            "extend_start_mm": 0.5,
            "extend_end_mm": 0.5,
            "layer_mode": "bidirectional",
            "reverse_direction": False,
        },
    )


def _assert_slot_graph(
    document,
    job,
    operation,
    *,
    label: str,
    expected_base: tuple,
    path_kind: str,
    diagnostics_required: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController in tuple(job.Tools.Group)
    assert operation.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
    if hasattr(operation.ViewObject.Proxy, "deleteOnReject"):
        assert operation.ViewObject.Proxy.deleteOnReject is False
    assert tuple(operation.Base) == expected_base
    assert operation.Label == label
    assert round(operation.StartDepth.getValueAs("mm"), 9) == 8.0
    assert round(operation.FinalDepth.getValueAs("mm"), 9) == 5.0
    assert round(operation.StepDown.getValueAs("mm"), 9) == 1.0
    assert round(operation.SafeHeight.getValueAs("mm"), 9) == 10.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 9) == 12.0
    assert round(operation.ExtendRadius.getValueAs("mm"), 9) == 0.0
    assert operation.ShowTempObjects is False
    assert operation.UseStartPoint is False
    assert tuple(round(value, 9) for value in operation.StartPoint) == (0.0, 0.0, 0.0)
    first = operation.CustomPoint1
    second = operation.CustomPoint2
    assert abs(float(first.x) - float(second.x)) + abs(float(first.y) - float(second.y)) > 0.1
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    commands = tuple(operation.Path.Commands)
    assert any(command.Name in {"G1", "G2", "G3"} for command in commands)
    if path_kind == "single_edge":
        assert operation.Reference1 == "Long Edge"
        assert operation.PathOrientation == "Start to End"
        assert operation.CutPattern == "Directional"
        assert operation.ReverseDirection is True
        assert round(operation.ExtendPathStart.getValueAs("mm"), 9) == 1.0
        assert round(operation.ExtendPathEnd.getValueAs("mm"), 9) == 2.0
        assert operation.CoolantMode == "Flood"
    else:
        assert operation.PathOrientation == "Start to End"
        assert operation.CutPattern == "Bidirectional"
        assert operation.ReverseDirection is False
        assert round(operation.ExtendPathStart.getValueAs("mm"), 9) == 0.5
        assert round(operation.ExtendPathEnd.getValueAs("mm"), 9) == 0.5
        assert operation.CoolantMode == "Mist"
    if diagnostics_required:
        diagnostics = operation.Proxy.getGenerationDiagnostics(operation)
        assert diagnostics["status"] == "succeeded", diagnostics
        assert diagnostics["stage"] == "complete", diagnostics
        assert diagnostics["error"] is None, diagnostics


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-slot-")
        save_path = Path(temporary.name) / "native-manufacture-slot.FCStd"
        document = App.newDocument("NativeManufactureSlotGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Slot"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.slot",
            "slot",
            "ExactCamJobSlotPathControllerAndParameters",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        edge_name, vertical_edge = _edge_names(model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        feature_arguments = _feature_arguments(model, job, edge_name)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-slot-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        runtimes = build_native_runtime_bindings(context, turn.tool_names)
        runtimes[MANUFACTURE_OPERATION_CAPABILITY_NAME] = (
            NativeManufactureOperationRuntime(context)
        )
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=runtimes,
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
                f"native-manufacture-slot-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, edge_name)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(feature_arguments))
        stale["slot"]["path"]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        invalid = json.loads(json.dumps(feature_arguments))
        invalid["slot"]["path"]["edge"] = vertical_edge
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "horizontal Edge" in invalid_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert int(document.UndoCount) == undo_before

        feature_result = call(feature_arguments)
        _events(12)
        feature_name = feature_result["slot"]["object_name"]
        feature_slot = document.getObject(feature_name)
        assert feature_slot is not None
        expected_feature_base = ((_job_resource(job, model), (edge_name,)),)
        _assert_slot_graph(
            document,
            job,
            feature_slot,
            label="Native edge Slot",
            expected_base=expected_feature_base,
            path_kind="single_edge",
        )
        assert feature_result["slot"]["geometry"] == {
            "kind": "subelements",
            "items": [{"object_name": model.Name, "subelements": [edge_name]}],
        }
        assert feature_result["slot"]["parameters"]["slot"] == {
            "path": {"kind": "single_edge", "orientation": "start_to_end"},
            "extend_start_mm": 1.0,
            "extend_end_mm": 2.0,
            "layer_mode": "directional",
            "reverse_direction": True,
        }
        assert feature_result["slot"]["path_kind"] == "single_edge"
        assert feature_result["slot"]["tool_diameter_mm"] > 0.0
        assert feature_result["slot"]["cutting_command_count"] >= 1
        assert _selection() == selection_before
        feature_state = operation_state(feature_slot)

        custom_arguments = _custom_arguments(job)
        custom_result = call(custom_arguments)
        _events(12)
        custom_name = custom_result["slot"]["object_name"]
        custom_slot = document.getObject(custom_name)
        assert custom_slot is not None
        _assert_slot_graph(
            document,
            job,
            custom_slot,
            label="Native custom Slot",
            expected_base=(),
            path_kind="custom_points",
        )
        assert custom_result["slot"]["geometry"] == {"kind": "custom_points"}
        assert custom_result["slot"]["parameters"]["slot"] == custom_arguments["slot"]
        assert custom_result["slot"]["path_kind"] == "custom_points"
        assert custom_result["slot"]["cutting_command_count"] >= 1
        assert custom_result["job"]["operation_count"] == len(initial_operations) + 2
        assert [item["object_name"] for item in custom_result["receipt"]["created"]] == [
            custom_name
        ]
        assert custom_result["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == revision_before + 2
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        custom_state = operation_state(custom_slot)

        document.undo()
        _events(12)
        assert document.getObject(custom_name) is None
        assert document.getObject(feature_name) is not None
        document.undo()
        _events(12)
        assert document.getObject(feature_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        model = document.getObject("SlotGateModel")
        job = document.getObject("SlotJob")
        feature_slot = document.getObject(feature_name)
        custom_slot = document.getObject(custom_name)
        assert all(value is not None for value in (model, job, feature_slot, custom_slot))
        _assert_slot_graph(
            document,
            job,
            feature_slot,
            label="Native edge Slot",
            expected_base=((_job_resource(job, model), (edge_name,)),),
            path_kind="single_edge",
        )
        _assert_slot_graph(
            document,
            job,
            custom_slot,
            label="Native custom Slot",
            expected_base=(),
            path_kind="custom_points",
        )
        assert operation_state(feature_slot)["state_sha256"] == feature_state["state_sha256"]
        restored_custom_state = operation_state(custom_slot)
        assert restored_custom_state["state_sha256"] == custom_state["state_sha256"], {
            key: (custom_state.get(key), restored_custom_state.get(key))
            for key in set(custom_state) | set(restored_custom_state)
            if custom_state.get(key) != restored_custom_state.get(key)
        }

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("SlotGateModel")
        job = document.getObject("SlotJob")
        feature_slot = document.getObject(feature_name)
        custom_slot = document.getObject(custom_name)
        assert all(value is not None for value in (model, job, feature_slot, custom_slot))
        _assert_slot_graph(
            document,
            job,
            feature_slot,
            label="Native edge Slot",
            expected_base=((_job_resource(job, model), (edge_name,)),),
            path_kind="single_edge",
            diagnostics_required=False,
        )
        _assert_slot_graph(
            document,
            job,
            custom_slot,
            label="Native custom Slot",
            expected_base=(),
            path_kind="custom_points",
            diagnostics_required=False,
        )
        assert operation_state(feature_slot)["state_sha256"] == feature_state["state_sha256"]
        reopened_custom_state = operation_state(custom_slot)
        assert reopened_custom_state["state_sha256"] == custom_state["state_sha256"], {
            key: (custom_state.get(key), reopened_custom_state.get(key))
            for key in set(custom_state) | set(reopened_custom_state)
            if custom_state.get(key) != reopened_custom_state.get(key)
        }

        print(
            "VIBECAD_NATIVE_MANUFACTURE_SLOT_GUI_OK "
            "exact_targets=true feature_path=true custom_points=true parameters=true "
            "toolpath=true history=true rollback=true undo=true redo=true reopen=true",
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
