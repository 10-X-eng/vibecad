# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for the optional Native CAM Waterline action."""

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
import Path.Preferences as PathPreferences
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureOperationSupport import shape_sha256
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeManufactureOperationRuntime import NativeManufactureOperationRuntime
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
    assert "CAM_Waterline" in surface.command_ids
    return controller, surface


def _create_fixture(document):
    def create_model():
        model = document.addObject("Part::Feature", "WaterlineGateModel")
        model.Label = "Waterline gate model"
        base = Part.makeBox(40.0, 30.0, 5.0)
        box_boss = Part.makeBox(12.0, 12.0, 5.0, App.Vector(6.0, 6.0, 5.0))
        round_boss = Part.makeCylinder(5.0, 4.0, App.Vector(29.0, 20.0, 5.0))
        model.Shape = base.fuse((box_boss, round_boss))
        assert model.Shape.isValid() and len(model.Shape.Solids) == 1
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Waterline gate model", create_model)

    def create_job():
        job = PathJob.Create("WaterlineJob", [model], templateFile=None)
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

    job = _commit(document, "Create Waterline gate Job", create_job)
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
    algorithm: dict,
    layers: dict,
    cut_mode: str,
    depth_offset_mm: float,
    boundary_enforcement: bool,
    cut_internal_features: bool,
    multiple_features: str,
    reverse_pass_order: bool,
    optimize_stepover_transitions: bool,
    gap_threshold_mm: float,
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
        "operation": "waterline",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": exact_geometry,
        "waterline": {
            "algorithm": algorithm,
            "cut_mode": cut_mode,
            "layers": layers,
            "depth_offset_mm": depth_offset_mm,
            "geometry_handling": {
                "boundary_enforcement": boundary_enforcement,
                "internal_features": {
                    "cut": cut_internal_features,
                    "adjustment_mm": 0.0,
                },
                "multiple_features": multiple_features,
            },
            "reverse_pass_order": reverse_pass_order,
            "optimization": {
                "stepover_transitions": optimize_stepover_transitions,
                "gap_threshold_mm": gap_threshold_mm,
            },
            "start": start,
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
    schema = definition.provider_schema(("waterline",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        '"const":"waterline"',
        '"const":"entire_job"',
        '"const":"faces"',
        '"const":"drop_cutter"',
        '"const":"adaptive"',
        '"const":"experimental"',
        '"const":"waterline_only"',
        '"const":"every_layer"',
        '"const":"final_layer"',
        '"const":"single_pass"',
        '"const":"multi_pass"',
        '"minimum_sample_interval_mm"',
        '"ignore_outer_above_mm"',
        '"avoid_last_face_count"',
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


def _mm(operation, property_name: str) -> float:
    return round(float(getattr(operation, property_name).getValueAs("mm")), 7)


def _assert_operation(
    document,
    job,
    operation,
    *,
    controller,
    base,
    label: str,
    algorithm: str,
    bounds: str,
    pattern: str,
    clear_last_layer: str,
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
    assert operation.Algorithm == algorithm
    assert operation.BoundBox == bounds
    assert operation.CutPattern == pattern
    assert operation.ClearLastLayer == clear_last_layer
    assert operation.LayerMode == layer_mode
    assert _mm(operation, "StepDown") == step_down_mm
    assert _mm(operation, "StartDepth") == start_depth_mm
    assert _mm(operation, "FinalDepth") == final_depth_mm
    assert _mm(operation, "SafeHeight") == start_depth_mm + 2.0
    assert _mm(operation, "ClearanceHeight") == start_depth_mm + 4.0
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


def _assert_regenerated_state(
    before: dict,
    after: dict,
    *,
    compare_path_length: bool = True,
) -> None:
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
    assert abs(after_count - before_count) <= max(int(before_count * 0.05), 8), (
        before_count,
        after_count,
    )
    before_length = float(before["path_length_mm"])
    after_length = float(after["path_length_mm"])
    if compare_path_length:
        assert abs(after_length - before_length) <= max(before_length * 0.05, 0.01), (
            before_length,
            after_length,
        )
    before_bounds = before.get("bounds")
    after_bounds = after.get("bounds")
    assert (before_bounds is None) == (after_bounds is None)
    if before_bounds is not None and after_bounds is not None:
        for limit in ("minimum_mm", "maximum_mm"):
            assert all(
                abs(float(actual) - float(expected)) <= 1.0e-5
                for expected, actual in zip(
                    before_bounds[limit], after_bounds[limit], strict=True
                )
            ), (before_bounds, after_bounds)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    prior_advanced_ocl = PathPreferences.advancedOCLFeaturesEnabled()
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-waterline-")
        save_path = Path(temporary.name) / "native-manufacture-waterline.FCStd"
        document = App.newDocument("NativeManufactureWaterlineGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Waterline"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.waterline",
            "waterline",
            "ExactCamJobWaterlineFacesControllerAlgorithmAndParameters",
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
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-waterline-gui")

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
                f"native-manufacture-waterline-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, floor_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        adaptive_arguments = _arguments(
            job,
            controller,
            model,
            label="Native selected-face Waterline",
            geometry={
                "kind": "faces",
                "items": [{"faces": [floor_face, box_top_face]}],
                "avoid_last_face_count": 1,
                "avoid_internal_features": True,
            },
            algorithm={
                "kind": "adaptive",
                "sample_interval_mm": 2.0,
                "minimum_sample_interval_mm": 0.5,
                "optimize_linear_paths": True,
                "mesh_deflection_mm": 0.1,
            },
            layers={"kind": "multi_pass", "step_down_mm": 2.5},
            cut_mode="conventional",
            depth_offset_mm=0.0,
            boundary_enforcement=True,
            cut_internal_features=True,
            multiple_features="collectively",
            reverse_pass_order=False,
            optimize_stepover_transitions=False,
            gap_threshold_mm=0.005,
            start={
                "kind": "point",
                "point_mm": {"x_mm": 2.0, "y_mm": 2.0, "z_mm": source_top},
            },
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="mist",
        )

        stale_job = json.loads(json.dumps(adaptive_arguments))
        stale_job["job"]["expected_state_sha256"] = "0" * 64
        assert call(stale_job, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )
        stale_model = json.loads(json.dumps(adaptive_arguments))
        stale_model["geometry"]["items"][0]["model"]["expected_state_sha256"] = (
            "0" * 64
        )
        assert call(stale_model, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )
        stale_controller = json.loads(json.dumps(adaptive_arguments))
        stale_controller["tool_controller"]["expected_state_sha256"] = "0" * 64
        assert call(stale_controller, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        invalid_adaptive = json.loads(json.dumps(adaptive_arguments))
        invalid_adaptive["waterline"]["algorithm"] = {
            "kind": "adaptive",
            "sample_interval_mm": 1.0,
            "minimum_sample_interval_mm": 2.0,
            "optimize_linear_paths": True,
            "mesh_deflection_mm": 0.1,
        }
        assert call(invalid_adaptive, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        invalid_experimental = json.loads(json.dumps(adaptive_arguments))
        invalid_experimental["waterline"]["algorithm"] = {
            "kind": "experimental",
            "bounds": "model",
            "clearing": {"kind": "waterline_only"},
            "boundary_adjustment_mm": 0.0,
            "ignore_outer_above_mm": source_top + 1.0,
        }
        assert call(invalid_experimental, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        unsupported_face_algorithm = json.loads(json.dumps(adaptive_arguments))
        unsupported_face_algorithm["waterline"]["algorithm"] = {
            "kind": "drop_cutter",
            "bounds": "model",
            "sample_interval_mm": 2.0,
            "mesh_deflection_mm": 0.1,
        }
        unsupported_result = call(unsupported_face_algorithm, succeeds=False)
        assert unsupported_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "supported only" in unsupported_result["error"]
        assert "entire_job" in unsupported_result["error"]

        invalid_avoidance = json.loads(json.dumps(adaptive_arguments))
        invalid_avoidance["geometry"]["avoid_last_face_count"] = 2
        assert call(invalid_avoidance, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        below_stock = json.loads(json.dumps(adaptive_arguments))
        below_stock["depths"]["final_depth_mm"] = stock_bottom
        below_stock["waterline"]["depth_offset_mm"] = -1.0
        assert call(below_stock, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        excessive = json.loads(json.dumps(adaptive_arguments))
        excessive["waterline"]["algorithm"]["sample_interval_mm"] = 0.001
        excessive["waterline"]["algorithm"]["minimum_sample_interval_mm"] = 0.001
        excessive["waterline"]["layers"] = {
            "kind": "multi_pass",
            "step_down_mm": 0.01,
        }
        assert call(excessive, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        )

        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert _selection() == selection_before

        adaptive_result = call(adaptive_arguments)
        _events(12)
        adaptive_name = adaptive_result["waterline"]["object_name"]
        adaptive_operation = document.getObject(adaptive_name)
        assert adaptive_operation is not None
        _assert_operation(
            document,
            job,
            adaptive_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Waterline",
            algorithm="OCL Adaptive",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Mist",
        )
        assert adaptive_operation.AvoidLastX_Faces == 1
        assert adaptive_result["waterline"]["target_mode"] == "faces"
        assert adaptive_result["waterline"]["face_count"] == 2
        assert adaptive_result["waterline"]["cutting_face_count"] == 1
        assert adaptive_result["waterline"]["avoided_face_count"] == 1
        assert adaptive_result["waterline"]["algorithm"] == "adaptive"
        assert adaptive_result["waterline"]["estimated_processing_cells"] > 0
        assert adaptive_result["assistant_undo_available"] is True
        adaptive_state = operation_state(adaptive_operation)

        drop_arguments = _arguments(
            job,
            controller,
            model,
            label="Native drop-cutter Waterline",
            geometry={"kind": "entire_job"},
            algorithm={
                "kind": "drop_cutter",
                "bounds": "model",
                "sample_interval_mm": 2.0,
                "mesh_deflection_mm": 0.1,
            },
            layers={"kind": "multi_pass", "step_down_mm": 2.5},
            cut_mode="climb",
            depth_offset_mm=0.0,
            boundary_enforcement=True,
            cut_internal_features=True,
            multiple_features="collectively",
            reverse_pass_order=True,
            optimize_stepover_transitions=True,
            gap_threshold_mm=0.01,
            start={"kind": "automatic"},
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="flood",
        )
        drop_result = call(drop_arguments)
        _events(12)
        drop_name = drop_result["waterline"]["object_name"]
        drop_operation = document.getObject(drop_name)
        assert drop_operation is not None
        _assert_operation(
            document,
            job,
            drop_operation,
            controller=controller,
            base=(),
            label="Native drop-cutter Waterline",
            algorithm="OCL Dropcutter",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Flood",
        )
        assert drop_result["waterline"]["target_mode"] == "entire_job"
        assert drop_result["waterline"]["algorithm"] == "drop_cutter"
        drop_state = operation_state(drop_operation)
        _assert_regenerated_state(
            adaptive_state,
            operation_state(adaptive_operation),
            compare_path_length=False,
        )

        experimental_arguments = _arguments(
            job,
            controller,
            model,
            label="Native experimental Waterline",
            geometry={"kind": "entire_job"},
            algorithm={
                "kind": "experimental",
                "bounds": "stock",
                "clearing": {
                    "kind": "final_layer",
                    "pattern": {"kind": "offset"},
                    "stepover_percent": 55,
                },
                "boundary_adjustment_mm": 0.0,
                "ignore_outer_above_mm": source_top,
            },
            layers={"kind": "multi_pass", "step_down_mm": 5.0},
            cut_mode="conventional",
            depth_offset_mm=0.0,
            boundary_enforcement=True,
            cut_internal_features=True,
            multiple_features="individually",
            reverse_pass_order=False,
            optimize_stepover_transitions=False,
            gap_threshold_mm=0.005,
            start={
                "kind": "point",
                "point_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": source_top},
            },
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="none",
        )
        experimental_result = call(experimental_arguments)
        _events(12)
        experimental_name = experimental_result["waterline"]["object_name"]
        experimental_operation = document.getObject(experimental_name)
        assert experimental_operation is not None
        _assert_operation(
            document,
            job,
            experimental_operation,
            controller=controller,
            base=(),
            label="Native experimental Waterline",
            algorithm="Experimental",
            bounds="Stock",
            pattern="None",
            clear_last_layer="Offset",
            layer_mode="Multi-pass",
            step_down_mm=5.0,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="None",
        )
        assert experimental_result["waterline"]["algorithm"] == "experimental"
        assert experimental_operation.CutPattern == "None"
        assert experimental_operation.ClearLastLayer == "Offset"
        assert len(job.Operations.Group) == len(initial_operations) + 3
        assert int(document.UndoCount) == undo_before + 3
        assert state_store.current_revision(context.document_uid) == revision_before + 3
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert shape_sha256(model.Shape, model.Name) == source_hash
        assert bool(model.ViewObject.Visibility) is source_visibility
        experimental_state = operation_state(experimental_operation)
        _assert_regenerated_state(drop_state, operation_state(drop_operation))
        _assert_regenerated_state(
            adaptive_state,
            operation_state(adaptive_operation),
            compare_path_length=False,
        )

        document.undo()
        _events(12)
        assert document.getObject(experimental_name) is None
        assert document.getObject(drop_name) is not None
        document.undo()
        _events(12)
        assert document.getObject(drop_name) is None
        assert document.getObject(adaptive_name) is not None
        document.undo()
        _events(12)
        assert document.getObject(adaptive_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        document.redo()
        _events(12)
        assert document.recompute(None, True, True) is not False
        _events(12)
        model = document.getObject("WaterlineGateModel")
        job = document.getObject("WaterlineJob")
        controller = document.getObject(controller.Name)
        drop_operation = document.getObject(drop_name)
        adaptive_operation = document.getObject(adaptive_name)
        experimental_operation = document.getObject(experimental_name)
        assert all(
            item is not None
            for item in (
                model,
                job,
                controller,
                drop_operation,
                adaptive_operation,
                experimental_operation,
            )
        )
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            drop_operation,
            controller=controller,
            base=(),
            label="Native drop-cutter Waterline",
            algorithm="OCL Dropcutter",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Flood",
        )
        _assert_operation(
            document,
            job,
            adaptive_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Waterline",
            algorithm="OCL Adaptive",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Mist",
        )
        _assert_operation(
            document,
            job,
            experimental_operation,
            controller=controller,
            base=(),
            label="Native experimental Waterline",
            algorithm="Experimental",
            bounds="Stock",
            pattern="None",
            clear_last_layer="Offset",
            layer_mode="Multi-pass",
            step_down_mm=5.0,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="None",
        )
        drop_redo_state = operation_state(drop_operation)
        adaptive_redo_state = operation_state(adaptive_operation)
        experimental_redo_state = operation_state(experimental_operation)
        _assert_regenerated_state(drop_state, drop_redo_state)
        _assert_regenerated_state(
            adaptive_state,
            adaptive_redo_state,
            compare_path_length=False,
        )
        _assert_regenerated_state(experimental_state, experimental_redo_state)
        assert shape_sha256(model.Shape, model.Name) == source_hash

        controller_name = controller.Name
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("WaterlineGateModel")
        job = document.getObject("WaterlineJob")
        controller = document.getObject(controller_name)
        drop_operation = document.getObject(drop_name)
        adaptive_operation = document.getObject(adaptive_name)
        experimental_operation = document.getObject(experimental_name)
        assert all(
            item is not None
            for item in (
                model,
                job,
                controller,
                drop_operation,
                adaptive_operation,
                experimental_operation,
            )
        )
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            drop_operation,
            controller=controller,
            base=(),
            label="Native drop-cutter Waterline",
            algorithm="OCL Dropcutter",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
            layer_mode="Multi-pass",
            step_down_mm=2.5,
            start_depth_mm=source_top,
            final_depth_mm=5.0,
            coolant="Flood",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            adaptive_operation,
            controller=controller,
            base=((resource, (floor_face, box_top_face)),),
            label="Native selected-face Waterline",
            algorithm="OCL Adaptive",
            bounds="BaseBoundBox",
            pattern="None",
            clear_last_layer="Off",
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
            experimental_operation,
            controller=controller,
            base=(),
            label="Native experimental Waterline",
            algorithm="Experimental",
            bounds="Stock",
            pattern="None",
            clear_last_layer="Offset",
            layer_mode="Multi-pass",
            step_down_mm=5.0,
            start_depth_mm=source_top,
            final_depth_mm=stock_bottom,
            coolant="None",
            diagnostics=False,
        )
        _assert_regenerated_state(drop_redo_state, operation_state(drop_operation))
        _assert_regenerated_state(
            adaptive_redo_state,
            operation_state(adaptive_operation),
            compare_path_length=False,
        )
        _assert_regenerated_state(
            experimental_redo_state,
            operation_state(experimental_operation),
        )
        assert _shape_signature(model.Shape) == source_signature

        print(
            "VIBECAD_NATIVE_MANUFACTURE_WATERLINE_GUI_OK "
            "exact_targets=true faces=true avoidance=true entire_job=true "
            "algorithms=true clearing=true parameters=true bounded_work=true "
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
