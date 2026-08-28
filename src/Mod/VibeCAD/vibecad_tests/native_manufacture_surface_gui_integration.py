# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for the optional Native CAM Surface action."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import Path.Preferences as PathPreferences
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureOperationSupport import shape_sha256
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_TOLERANCE = 1.0e-7


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _await_job(manager: NativeBackgroundManager, job_id: str) -> dict:
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            assert snapshot.phase == "completed", snapshot
            assert snapshot.result is not None
            return snapshot.result
        time.sleep(0.01)
    raise AssertionError("The isolated Surface path gate timed out")


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


def _surface():
    PathPreferences.preferences().SetBool(
        PathPreferences.EnableAdvancedOCLFeatures,
        True,
    )
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    assert "CAM_Surface" in surface.command_ids
    return controller, surface


def _create_fixture(document):
    def create_model():
        model = document.addObject("Part::Feature", "SurfaceGateModel")
        model.Label = "Surface gate model"
        base = Part.makeBox(40.0, 30.0, 5.0)
        box_boss = Part.makeBox(12.0, 12.0, 5.0, App.Vector(6.0, 6.0, 5.0))
        round_boss = Part.makeCylinder(5.0, 4.0, App.Vector(29.0, 20.0, 5.0))
        model.Shape = base.fuse((box_boss, round_boss))
        assert model.Shape.isValid() and len(model.Shape.Solids) == 1
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Surface gate model", create_model)

    def create_job():
        job = PathJob.Create("SurfaceJob", [model], templateFile=None)
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

    job = _commit(document, "Create Surface gate Job", create_job)
    return model, job, job.Tools.Group[0]


def _horizontal_face(model, z_mm: float, minimum_area: float) -> str:
    matches = [
        f"Face{index}"
        for index, face in enumerate(model.Shape.Faces, start=1)
        if face.BoundBox.ZLength <= _TOLERANCE
        and abs(float(face.BoundBox.ZMin) - z_mm) <= _TOLERANCE
        and float(face.Area) >= minimum_area
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _shape_signature(shape) -> tuple:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        bool(shape.isValid()),
        len(shape.Solids),
        len(shape.Shells),
        len(shape.Faces),
        len(shape.Wires),
        len(shape.Edges),
        len(shape.Vertexes),
        round(float(shape.Volume), 7),
        round(float(shape.Area), 7),
        tuple(
            round(float(getattr(bounds, name)), 7)
            for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
        ),
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _model_target(state: dict, model) -> dict:
    return _target(
        next(item for item in state["models"] if item["object_name"] == model.Name)
    )


def _controller_target(state: dict, controller) -> dict:
    return _target(
        next(item for item in state["tools"] if item["object_name"] == controller.Name)
    )


def _resource(job, model):
    matches = tuple(
        item for item in job.Model.Group if job.Proxy.baseObject(job, item) is model
    )
    assert len(matches) == 1, matches
    return matches[0]


def _arguments(
    job,
    controller,
    model,
    *,
    label: str,
    geometry: dict,
    pattern: dict,
    layers: dict,
    bounds: str,
    cut_mode: str,
    stepover_percent: int,
    sample_interval_mm: float,
    profile_edges: str,
    boundary_enforcement: bool,
    multiple_features: str,
    reverse_pass_order: bool,
    start: dict,
    start_depth_mm: float,
    final_depth_mm: float,
    coolant: str,
) -> dict:
    state = job_state(job)
    exact_geometry = json.loads(json.dumps(geometry))
    for item in exact_geometry.get("items", []):
        item["model"] = _model_target(state, model)
    return {
        "operation": "surface",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": exact_geometry,
        "surface": {
            "bounds": bounds,
            "cut_mode": cut_mode,
            "pattern": pattern,
            "layers": layers,
            "stepover_percent": stepover_percent,
            "depth_offset_mm": 0.0,
            "sample_interval_mm": sample_interval_mm,
            "profile_edges": profile_edges,
            "boundary": {"enforce": boundary_enforcement, "adjustment_mm": 0.0},
            "internal_features": {"cut": True, "adjustment_mm": 0.0},
            "multiple_features": multiple_features,
            "reverse_pass_order": reverse_pass_order,
            "optimization": {
                "linear_paths": True,
                "stepover_transitions": False,
                "gap_threshold_mm": 0.005,
            },
            "start": start,
            "mesh_deflection_mm": 0.1,
        },
        "depths": {
            "start_depth_mm": start_depth_mm,
            "final_depth_mm": final_depth_mm,
        },
        "heights": {
            "safe_height_mm": start_depth_mm + 2.0,
            "clearance_height_mm": start_depth_mm + 4.0,
        },
        "coolant": coolant,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("surface",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        '"const":"surface"',
        '"const":"entire_job"',
        '"const":"faces"',
        '"const":"single_pass"',
        '"const":"multi_pass"',
        '"circular"',
        '"const":"spiral"',
        '"avoid_last_face_count"',
        '"sample_interval_mm"',
        '"mesh_deflection_mm"',
    ):
        assert field in encoded, field
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


def _assert_operation(
    document,
    job,
    operation,
    *,
    controller,
    base,
    label: str,
    pattern: str,
    layer_mode: str,
    step_down_mm: float,
    start_depth_mm: float,
    final_depth_mm: float,
    coolant: str,
    diagnostics: bool = True,
) -> None:
    assert operation in job.Operations.Group
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController is controller
    assert tuple(operation.Base) == base
    assert operation.Label == label
    assert operation.ScanType == "Planar"
    assert operation.CutPattern == pattern
    assert operation.LayerMode == layer_mode
    assert round(operation.StepDown.getValueAs("mm"), 7) == step_down_mm
    assert round(operation.StartDepth.getValueAs("mm"), 7) == start_depth_mm
    assert round(operation.FinalDepth.getValueAs("mm"), 7) == final_depth_mm
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == start_depth_mm + 2.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == start_depth_mm + 4.0
    assert operation.CoolantMode == coolant
    assert operation.ShowTempObjects is False
    assert tuple(round(float(value), 7) for value in operation.Workplane) == (
        0.0,
        0.0,
        1.0,
    )
    expression_paths = {
        str(path).lstrip(".") for path, _expression in tuple(operation.ExpressionEngine)
    }
    for property_name in (
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    ):
        assert property_name not in expression_paths
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    cutting = tuple(
        command for command in operation.Path.Commands if command.Name in {"G1", "G2", "G3"}
    )
    assert cutting
    if diagnostics:
        facts = operation.Proxy.getGenerationDiagnostics(operation)
        assert facts["status"] == "succeeded", facts
        assert facts["stage"] == "complete", facts
        assert facts["error"] is None, facts


def _assert_regenerated_state(before: dict, after: dict) -> None:
    for field in (
        "document_uid",
        "object_name",
        "type_id",
        "label",
        "active",
        "settings_sha256",
        "tool_controller",
        "placement",
    ):
        assert after[field] == before[field], (field, before[field], after[field])
    before_count = int(before["command_count"])
    after_count = int(after["command_count"])
    assert before_count > 0 and after_count > 0
    assert abs(after_count - before_count) <= max(int(before_count * 0.05), 6)
    before_length = float(before["path_length_mm"])
    after_length = float(after["path_length_mm"])
    assert abs(after_length - before_length) <= max(before_length * 0.05, 0.01)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    prior_advanced_ocl = PathPreferences.advancedOCLFeaturesEnabled()
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-surface-")
        save_path = Path(temporary.name) / "native-manufacture-surface.FCStd"
        document = App.newDocument("NativeManufactureSurfaceGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Surface"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.surface",
            "surface",
            "ExactCamJobSurfaceFacesControllerAndParameters",
            True,
            False,
        )

        model, job, controller = _create_fixture(document)
        resource = _resource(job, model)
        source_top = round(float(job.Stock.Shape.BoundBox.ZMax), 7)
        stock_bottom = round(float(job.Stock.Shape.BoundBox.ZMin), 7)
        floor_face = _horizontal_face(model, 5.0, 800.0)
        box_top_face = _horizontal_face(model, 10.0, 100.0)
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        source_hash = shape_sha256(model.Shape, model.Name)
        source_signature = _shape_signature(model.Shape)
        source_visibility = bool(model.ViewObject.Visibility)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        background = service.native_background_manager()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-surface-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, ribbon_controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=undo_ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                ribbon_controller
            ).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=background,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
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
                f"native-manufacture-surface-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            if succeeds and response.get("next", {}).get("tool") == "native.job":
                assert response["job"]["resource_scope"] == (
                    f"manufacture:{payload['job']['object_name']}"
                )
                active_jobs = service.native_active_snapshot()["domain"][
                    "background_jobs"
                ]
                assert any(
                    item["job_id"] == response["job"]["job_id"]
                    and item["resource_scope"] == response["job"]["resource_scope"]
                    and item["terminal"] is False
                    for item in active_jobs
                )
                return _await_job(background, response["job"]["job_id"])
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, floor_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        face_arguments = _arguments(
            job,
            controller,
            model,
            label="Native selected-face Surface",
            geometry={
                "kind": "faces",
                "items": [
                    {"faces": [floor_face, box_top_face]},
                ],
                "avoid_last_face_count": 1,
                "avoid_internal_features": True,
            },
            pattern={"kind": "line", "angle_degrees": 20.0},
            layers={"kind": "multi_pass", "step_down_mm": 2.5},
            bounds="model",
            cut_mode="conventional",
            stepover_percent=55,
            sample_interval_mm=1.5,
            profile_edges="none",
            boundary_enforcement=True,
            multiple_features="collectively",
            reverse_pass_order=False,
            start={
                "kind": "point",
                "point_mm": {"x_mm": 2.0, "y_mm": 2.0, "z_mm": source_top},
            },
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="mist",
        )

        stale = json.loads(json.dumps(face_arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid_avoidance = json.loads(json.dumps(face_arguments))
        invalid_avoidance["geometry"]["avoid_last_face_count"] = 2
        assert call(invalid_avoidance, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        below_stock = json.loads(json.dumps(face_arguments))
        below_stock["depths"]["final_depth_mm"] = stock_bottom - 1.0
        assert call(below_stock, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        excessive = json.loads(json.dumps(face_arguments))
        excessive["surface"]["sample_interval_mm"] = 0.001
        excessive["surface"]["stepover_percent"] = 1
        assert call(excessive, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        )

        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert _selection() == selection_before

        face_result = call(face_arguments)
        _events(12)
        face_name = face_result["surface"]["object_name"]
        face_operation = document.getObject(face_name)
        assert face_operation is not None
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Surface",
            pattern="Line",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Mist",
        )
        assert face_operation.AvoidLastX_Faces == 1
        assert face_result["surface"]["target_mode"] == "faces"
        assert face_result["surface"]["face_count"] == 2
        assert face_result["surface"]["cutting_face_count"] == 1
        assert face_result["surface"]["avoided_face_count"] == 1
        assert face_result["surface"]["estimated_drop_cutter_points"] > 0
        assert face_result["assistant_undo_available"] is True
        face_state = operation_state(face_operation)

        whole_arguments = _arguments(
            job,
            controller,
            model,
            label="Native whole-model Surface",
            geometry={"kind": "entire_job"},
            pattern={
                "kind": "circular",
                "center": {"kind": "bounding_box_center"},
                "emit_arcs": True,
            },
            layers={"kind": "single_pass"},
            bounds="model",
            cut_mode="climb",
            stepover_percent=65,
            sample_interval_mm=2.0,
            profile_edges="last",
            boundary_enforcement=True,
            multiple_features="collectively",
            reverse_pass_order=True,
            start={"kind": "automatic"},
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="flood",
        )
        whole_result = call(whole_arguments)
        _events(12)
        whole_name = whole_result["surface"]["object_name"]
        whole_operation = document.getObject(whole_name)
        assert whole_operation is not None
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            base=(),
            label="Native whole-model Surface",
            pattern="Circular",
            layer_mode="Single-pass",
            step_down_mm=source_top - stock_bottom,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="Flood",
        )
        assert whole_operation.CircularUseG2G3 is True
        assert whole_result["surface"]["target_mode"] == "entire_job"
        assert whole_result["surface"]["face_count"] == 0
        assert len(job.Operations.Group) == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == revision_before + 2
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert shape_sha256(model.Shape, model.Name) == source_hash
        assert bool(model.ViewObject.Visibility) is source_visibility
        whole_state = operation_state(whole_operation)
        _assert_regenerated_state(face_state, operation_state(face_operation))

        document.undo()
        _events(12)
        assert document.getObject(whole_name) is None
        assert document.getObject(face_name) is not None
        document.undo()
        _events(12)
        assert document.getObject(face_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        assert document.recompute(None, True, True) is not False
        _events(12)
        model = document.getObject("SurfaceGateModel")
        job = document.getObject("SurfaceJob")
        face_operation = document.getObject(face_name)
        whole_operation = document.getObject(whole_name)
        assert all(item is not None for item in (model, job, face_operation, whole_operation))
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Surface",
            pattern="Line",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Mist",
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            base=(),
            label="Native whole-model Surface",
            pattern="Circular",
            layer_mode="Single-pass",
            step_down_mm=source_top - stock_bottom,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="Flood",
        )
        face_redo_state = operation_state(face_operation)
        whole_redo_state = operation_state(whole_operation)
        _assert_regenerated_state(face_state, face_redo_state)
        _assert_regenerated_state(whole_state, whole_redo_state)
        assert shape_sha256(model.Shape, model.Name) == source_hash

        controller_name = controller.Name
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("SurfaceGateModel")
        job = document.getObject("SurfaceJob")
        controller = document.getObject(controller_name)
        face_operation = document.getObject(face_name)
        whole_operation = document.getObject(whole_name)
        assert all(
            item is not None
            for item in (model, job, controller, face_operation, whole_operation)
        )
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Surface",
            pattern="Line",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Mist",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            base=(),
            label="Native whole-model Surface",
            pattern="Circular",
            layer_mode="Single-pass",
            step_down_mm=source_top - stock_bottom,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="Flood",
            diagnostics=False,
        )
        _assert_regenerated_state(face_redo_state, operation_state(face_operation))
        _assert_regenerated_state(whole_redo_state, operation_state(whole_operation))
        assert _shape_signature(model.Shape) == source_signature

        print(
            "VIBECAD_NATIVE_MANUFACTURE_SURFACE_GUI_OK "
            "exact_targets=true faces=true avoidance=true entire_job=true "
            "patterns=true layers=true quality=true bounded_work=true "
            "toolpath=true history=true rollback=true sources_preserved=true "
            "undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        PathPreferences.preferences().SetBool(
            PathPreferences.EnableAdvancedOCLFeatures,
            prior_advanced_ocl,
        )
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
