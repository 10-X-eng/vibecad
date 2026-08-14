# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Mill Facing."""

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
        model = document.addObject("Part::Feature", "FacingGateModel")
        model.Label = "Facing gate model"
        model.Shape = Part.makeBox(48.0, 32.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Facing gate model", create_model)

    def create_job():
        job = PathJob.Create("FacingJob", [model], templateFile=None)
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

    return model, _commit(document, "Create Facing gate Job", create_job)


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
    schema = definition.provider_schema(("mill_facing",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "tool_controller",
        "cut_mode",
        "pattern",
        "angle_degrees",
        "reverse",
        "stepover_percent",
        "axial_stock_to_leave_mm",
        "pass_extension_mm",
        "stock_extension_mm",
        "linking",
        "collision_clearance_mm",
    ):
        assert field in encoded
    assert "geometry" not in schema["parameters"]["oneOf"][0]["properties"]
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


def _arguments(model, job) -> dict:
    state = job_state(job)
    controller = state["tools"][0]
    stock_top = round(float(job.Stock.Shape.BoundBox.ZMax), 9)
    model_top = round(float(model.Shape.BoundBox.ZMax), 9)
    assert stock_top > model_top
    return {
        "operation": "mill_facing",
        "label": "Native stock facing",
        "job": _target(state),
        "tool_controller": _target(controller),
        "facing": {
            "cut_mode": "conventional",
            "pattern": "directional",
            "angle_degrees": 25.0,
            "reverse": True,
            "stepover_percent": 40,
            "axial_stock_to_leave_mm": 0.2,
            "pass_extension_mm": 3.0,
            "stock_extension_mm": 1.0,
        },
        "depths": {
            "start_depth_mm": stock_top,
            "final_depth_mm": model_top,
            "step_down_mm": 0.5,
        },
        "heights": {
            "safe_height_mm": stock_top + 2.0,
            "clearance_height_mm": stock_top + 5.0,
        },
        "linking": {
            "strategy": "tool_diameter",
            "collision_clearance_mm": 0.5,
        },
        "coolant": "flood",
    }


def _assert_facing_graph(
    document,
    job,
    operation,
    model,
    *,
    diagnostics_required: bool = True,
) -> None:
    assert operation is job.Operations.Group[-1]
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController in tuple(job.Tools.Group)
    assert operation.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
    if hasattr(operation.ViewObject.Proxy, "deleteOnReject"):
        assert operation.ViewObject.Proxy.deleteOnReject is False
    assert tuple(getattr(operation, "Base", ()) or ()) == ()
    assert job.Proxy.baseObject(job, job.Model.Group[0]) is model
    assert operation.Label == "Native stock facing"
    assert operation.CutMode == "Conventional"
    assert operation.ClearingPattern == "Directional"
    assert round(float(operation.Angle.Value), 9) == 25.0
    assert operation.Reverse is True
    assert int(operation.StepOver) == 40
    assert round(operation.AxialStockToLeave.getValueAs("mm"), 9) == 0.2
    assert round(operation.PassExtension.getValueAs("mm"), 9) == 3.0
    assert round(operation.StockExtension.getValueAs("mm"), 9) == 1.0
    assert operation.CollisionAvoidanceStrategy == "Tool Diameter"
    assert round(operation.CollisionClearance.getValueAs("mm"), 9) == 0.5
    assert operation.CoolantMode == "Flood"
    assert tuple(document.VibeCADTimeline.Operations)[-1] is operation
    commands = tuple(operation.Path.Commands)
    assert any(command.Name in {"G1", "G2", "G3"} for command in commands)
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-facing-")
        save_path = Path(temporary.name) / "native-manufacture-facing.FCStd"
        document = App.newDocument("NativeManufactureFacingGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_MillFacing"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            "mill_facing",
            "ExactCamJobStockControllerAndFacingParameters",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        arguments = _arguments(model, job)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-mill-facing-gui")

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
                f"native-manufacture-facing-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(arguments))
        stale["job"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        invalid = json.loads(json.dumps(arguments))
        invalid["facing"]["axial_stock_to_leave_mm"] = (
            invalid["depths"]["start_depth_mm"] - invalid["depths"]["final_depth_mm"]
        )
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "plus axial_stock_to_leave_mm" in invalid_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert int(document.UndoCount) == undo_before

        result = call(arguments)
        _events(12)
        operation_name = result["mill_facing"]["object_name"]
        operation = document.getObject(operation_name)
        assert operation is not None
        _assert_facing_graph(document, job, operation, model)
        assert result["mill_facing"]["geometry"] == {
            "kind": "entire_job",
            "model_names": [model.Name],
        }
        assert result["mill_facing"]["stock"]["object_name"] == job.Stock.Name
        assert len(result["mill_facing"]["stock"]["shape_sha256"]) == 64
        assert result["mill_facing"]["parameters"] == {
            "facing": arguments["facing"],
            "depths": arguments["depths"],
            "heights": arguments["heights"],
            "linking": arguments["linking"],
            "coolant": arguments["coolant"],
        }
        assert result["mill_facing"]["cutting_command_count"] >= 1
        assert result["job"]["operation_count"] == len(initial_operations) + 1
        assert [item["object_name"] for item in result["receipt"]["created"]] == [
            operation_name
        ]
        assert result["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 1
        assert state_store.current_revision(context.document_uid) == revision_before + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        created_state = operation_state(operation)

        document.undo()
        _events(12)
        assert document.getObject(operation_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        model = document.getObject("FacingGateModel")
        job = document.getObject("FacingJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_facing_graph(document, job, operation, model)
        assert (
            operation_state(operation)["state_sha256"] == created_state["state_sha256"]
        )

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("FacingGateModel")
        job = document.getObject("FacingJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_facing_graph(
            document,
            job,
            operation,
            model,
            diagnostics_required=False,
        )
        assert (
            operation_state(operation)["state_sha256"] == created_state["state_sha256"]
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_MILL_FACING_GUI_OK "
            "exact_targets=true stock=true parameters=true linking=true "
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
