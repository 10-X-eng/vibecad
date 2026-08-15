# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Pocket Shape."""

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
import Path.Op.FeatureExtension as FeatureExtensions
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
        model = document.addObject("Part::Feature", "PocketGateModel")
        model.Label = "Pocket gate model"
        model.Shape = Part.makeBox(40.0, 30.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Pocket gate model", create_model)

    def create_job():
        job = PathJob.Create("PocketJob", [model], templateFile=None)
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

    return model, _commit(document, "Create Pocket gate Job", create_job)


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


def _top_face_and_edge(model) -> tuple[str, str]:
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for face_index, face in enumerate(model.Shape.Faces, start=1):
        if all(abs(float(vertex.Point.z) - maximum_z) <= 1e-9 for vertex in face.Vertexes):
            for edge_index, edge in enumerate(model.Shape.Edges, start=1):
                if any(edge.isSame(candidate) for candidate in face.OuterWire.Edges):
                    return f"Face{face_index}", f"Edge{edge_index}"
    raise AssertionError("The gate model has no exact top face and boundary edge")


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("pocket_shape",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "tool_controller",
        "subelements",
        "stepover_percent",
        "finish_step_mm",
        "default_length_mm",
        "extend_corners",
    ):
        assert field in encoded
    assert "entire_job" not in encoded
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


def _arguments(model, job, face_name: str, edge_name: str) -> dict:
    state = job_state(job)
    controller = state["tools"][0]
    job_model = next(
        item for item in state["models"] if item["object_name"] == model.Name
    )
    model_target = _target(job_model)
    return {
        "operation": "pocket_shape",
        "label": "Native top Pocket Shape",
        "job": _target(state),
        "tool_controller": _target(controller),
        "geometry": {
            "kind": "subelements",
            "items": [
                {
                    "model": model_target,
                    "subelements": [face_name],
                }
            ],
        },
        "pocket": {
            "cut_mode": "climb",
            "pattern": {"kind": "line", "angle_degrees": 30.0},
            "stepover_percent": 45,
            "material_allowance_mm": 0.2,
            "ignore_holes": False,
            "minimize_travel": False,
            "rest_machining": False,
        },
        "depths": {
            "start_depth_mm": 10.0,
            "final_depth_mm": 2.0,
            "step_down_mm": 2.0,
            "finish_step_mm": 0.5,
        },
        "heights": {
            "safe_height_mm": 12.0,
            "clearance_height_mm": 15.0,
        },
        "extensions": {
            "kind": "explicit",
            "default_length_mm": 3.0,
            "extend_corners": False,
            "items": [
                {
                    "model": model_target,
                    "feature": face_name,
                    "edges": [edge_name],
                }
            ],
        },
        "coolant": "mist",
    }


def _assert_pocket_graph(
    document,
    job,
    operation,
    model,
    face_name: str,
    edge_name: str,
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
    assert tuple(operation.Base) == ((job.Model.Group[0], (face_name,)),)
    assert job.Proxy.baseObject(job, operation.Base[0][0]) is model
    assert operation.Label == "Native top Pocket Shape"
    assert operation.CutMode == "Climb"
    assert operation.ClearingPattern == "Line"
    assert round(float(operation.Angle), 9) == 30.0
    assert int(operation.StepOver) == 45
    assert round(operation.ExtraOffset.getValueAs("mm"), 9) == 0.2
    assert operation.UseOutline is False
    assert operation.MinTravel is False
    assert operation.UseRestMachining is False
    assert operation.UseStartPoint is False
    assert operation.StartAt == "Center"
    assert operation.SortingMode == "Automatic"
    assert operation.ForceMaxStepOver is False
    assert operation.SplitArcs is False
    assert operation.CoolantMode == "Mist"
    assert round(operation.FinishDepth.getValueAs("mm"), 9) == 0.5
    assert round(operation.ExtensionLengthDefault.getValueAs("mm"), 9) == 3.0
    assert operation.ExtensionCorners is False
    assert FeatureExtensions.readObjExtensionFeature(operation) == [
        (job.Model.Group[0].Name, face_name, edge_name)
    ]
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-pocket-")
        save_path = Path(temporary.name) / "native-manufacture-pocket.FCStd"
        document = App.newDocument("NativeManufacturePocketGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Pocket_Shape"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            "pocket_shape",
            "ExactCamJobPocketGeometryControllerExtensionsAndParameters",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        face_name, edge_name = _top_face_and_edge(model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        arguments = _arguments(model, job, face_name, edge_name)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-pocket-shape-gui")

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
                f"native-manufacture-pocket-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        invalid = json.loads(json.dumps(arguments))
        invalid["pocket"]["minimize_travel"] = True
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "start point" in invalid_result["error"].lower()
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert int(document.UndoCount) == undo_before

        result = call(arguments)
        _events(12)
        operation_name = result["pocket_shape"]["object_name"]
        operation = document.getObject(operation_name)
        assert operation is not None
        _assert_pocket_graph(
            document,
            job,
            operation,
            model,
            face_name,
            edge_name,
        )
        assert result["pocket_shape"]["geometry"] == {
            "kind": "subelements",
            "items": [{"object_name": model.Name, "subelements": [face_name]}],
        }
        assert result["pocket_shape"]["extensions"] == {
            "kind": "explicit",
            "count": 1,
            "default_length_mm": 3.0,
            "extend_corners": False,
            "items": [
                {
                    "object_name": model.Name,
                    "feature": face_name,
                    "edges": [edge_name],
                }
            ],
        }
        assert result["pocket_shape"]["cutting_command_count"] >= 1
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
        model = document.getObject("PocketGateModel")
        job = document.getObject("PocketJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_pocket_graph(
            document,
            job,
            operation,
            model,
            face_name,
            edge_name,
        )
        assert operation_state(operation)["state_sha256"] == created_state["state_sha256"]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("PocketGateModel")
        job = document.getObject("PocketJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_pocket_graph(
            document,
            job,
            operation,
            model,
            face_name,
            edge_name,
            diagnostics_required=False,
        )
        assert operation_state(operation)["state_sha256"] == created_state["state_sha256"]

        print(
            "VIBECAD_NATIVE_MANUFACTURE_POCKET_SHAPE_GUI_OK "
            "exact_targets=true geometry=true extensions=true parameters=true "
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
