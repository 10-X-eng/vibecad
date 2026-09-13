# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Adaptive."""

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
from VibeCADNativeManufactureFocusedOperationSchema import (
    MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES,
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES["adaptive"]


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
        model = document.addObject("Part::Feature", "AdaptiveGateModel")
        model.Label = "Adaptive gate model"
        base = Part.makeBox(60.0, 50.0, 5.0)
        boss = Part.makeBox(30.0, 20.0, 5.0, App.Vector(15.0, 15.0, 5.0))
        model.Shape = base.fuse(boss)
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Adaptive gate model", create_model)

    def create_job():
        job = PathJob.Create("AdaptiveJob", [model], templateFile=None)
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

    return model, _commit(document, "Create Adaptive gate Job", create_job)


def _boss_top_face_and_edge(model) -> tuple[str, str]:
    candidates = []
    for index, face in enumerate(model.Shape.Faces, start=1):
        bounds = face.BoundBox
        if abs(float(bounds.ZMin) - 10.0) > 1.0e-7 or abs(
            float(bounds.ZMax) - 10.0
        ) > 1.0e-7:
            continue
        u_min, u_max, v_min, v_max = face.ParameterRange
        normal = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        if float(normal.z) > 0.9:
            candidates.append((index, face))
    assert len(candidates) == 1, candidates
    face_index, face = candidates[0]
    edge_names = []
    for edge_index, edge in enumerate(model.Shape.Edges, start=1):
        if any(edge.isSame(candidate) for candidate in face.OuterWire.Edges):
            edge_names.append(f"Edge{edge_index}")
    assert len(edge_names) == 4, edge_names
    return f"Face{face_index}", edge_names[0]


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
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("adaptive",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in ("job", "tool_controller", "geometry"):
        assert field in encoded
    for field in ("tolerance_mm", "depths", "heights", "extensions"):
        assert field not in encoded
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


def _arguments(model, job, face_name: str, edge_name: str) -> dict:
    state = job_state(job)
    controller = state["tools"][0]
    job_model = next(
        item for item in state["models"] if item["object_name"] == model.Name
    )
    model_target = _target(job_model)
    return {
        "job": _target(state),
        "tool_controller": _target(controller),
        "geometry": [
            {
                "model": model_target,
                "subelements": [face_name],
            }
        ],
    }


def _assert_adaptive_graph(
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
    assert operation.Label.startswith("Adaptive")
    assert operation.Side == "Inside"
    assert operation.OperationType == "Clearing"
    assert round(float(operation.Tolerance), 9) == 0.1
    assert round(float(operation.StepOverPercent), 9) == 20.0
    assert round(operation.LiftDistance.getValueAs("mm"), 9) == 0.0
    assert round(float(operation.KeepToolDownRatio.Value), 9) == 3.0
    assert round(operation.StockToLeave.getValueAs("mm"), 9) == 0.0
    assert operation.ForceInsideOut is False
    assert operation.FinishingProfile is True
    assert operation.UseOutline is False
    assert operation.UseRestMachining is False
    assert round(float(operation.HelixMaxRampAngle.Value), 9) == 5.0
    assert round(float(operation.HelixConeAngle.Value), 9) == 0.0
    assert int(operation.HelixMaxDiameterPercent) == 100
    assert int(operation.HelixMinDiameterPercent) == 10
    start = operation.StartDepth.getValueAs("mm")
    final = operation.FinalDepth.getValueAs("mm")
    safe = operation.SafeHeight.getValueAs("mm")
    clearance = operation.ClearanceHeight.getValueAs("mm")
    assert final < start <= safe <= clearance
    assert operation.StepDown.getValueAs("mm") > 0.0
    assert operation.CoolantMode == "None"
    assert operation.ModelAwareExperiment is False
    assert operation.OrderCutsByRegion is False
    assert round(operation.ZStockToLeave.getValueAs("mm"), 9) == 0.0
    assert list(operation.Locations) == []
    assert tuple(round(value, 9) for value in operation.Workplane) == (0.0, 0.0, 1.0)
    assert isinstance(operation.AdaptiveInputState, dict)
    assert isinstance(operation.AdaptiveOutputState, list)
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-adaptive-")
        save_path = Path(temporary.name) / "native-manufacture-adaptive.FCStd"
        document = App.newDocument("NativeManufactureAdaptiveGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Adaptive"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.adaptive",
            "adaptive",
            "ExactCamJobAdaptiveRegionsAndController",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        face_name, edge_name = _boss_top_face_and_edge(model)
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
        ledger.begin_run("native-manufacture-adaptive-gui")

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
        runtimes[CAPABILITY_NAME] = NativeManufactureOperationRuntime(context)
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
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-adaptive-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, face_name)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(arguments))
        stale["geometry"][0]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        invalid = json.loads(json.dumps(arguments))
        invalid["geometry"][0]["subelements"] = [edge_name]
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "closed horizontal wires" in invalid_result["error"]
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert int(document.UndoCount) == undo_before

        result = call(arguments)
        _events(12)
        operation_name = result["adaptive"]["object_name"]
        operation = document.getObject(operation_name)
        assert operation is not None
        _assert_adaptive_graph(
            document,
            job,
            operation,
            model,
            face_name,
            edge_name,
        )
        assert result["adaptive"]["geometry"] == {
            "kind": "subelements",
            "items": [
                {
                    "object_name": model.Name,
                    "subelements": [face_name],
                }
            ],
        }
        assert result["adaptive"]["parameters"]["source"] == "setup_defaults"
        assert result["adaptive"]["parameters"]["cut_region"] == "Inside"
        assert result["adaptive"]["parameters"]["operation_type"] == "Clearing"
        assert result["adaptive"]["engine"] == "libarea.Adaptive2d"
        assert result["adaptive"]["tool_diameter_mm"] > 0.0
        assert result["adaptive"]["stock"]["object_name"] == job.Stock.Name
        assert result["adaptive"]["cutting_command_count"] >= 1
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
        model = document.getObject("AdaptiveGateModel")
        job = document.getObject("AdaptiveJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_adaptive_graph(
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
        model = document.getObject("AdaptiveGateModel")
        job = document.getObject("AdaptiveJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_adaptive_graph(
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
            "VIBECAD_NATIVE_MANUFACTURE_ADAPTIVE_GUI_OK "
            "exact_targets=true regions=true setup_defaults=true "
            "toolpath=true history=true rollback=true undo=true "
            "redo=true reopen=true",
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
