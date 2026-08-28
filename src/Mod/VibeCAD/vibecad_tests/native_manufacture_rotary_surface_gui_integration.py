# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Rotary Surface."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

from Machine.models.machine import Machine, MachineFactory, WrapStrategy
import Path.Base.Util as PathUtil
import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import Path.Main.Stock as PathStock
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


_TOLERANCE = 1.0e-6


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
    preferences = PathPreferences.preferences()
    preferences.SetBool(PathPreferences.EnableAdvancedOCLFeatures, True)
    preferences.SetBool(PathPreferences.EnableExperimentalFeatures, True)
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    assert "CAM_RotarySurface" in surface.command_ids
    return controller, surface


def _save_machine(*, wrap_strategy: WrapStrategy, limits=(-10_000.0, 10_000.0)):
    machine = Machine.create_4axis_A_config(a_limits=limits)
    machine.name = "Native Rotary Surface Gate A"
    machine.rotary_axes["A"].wrap_strategy = wrap_strategy
    MachineFactory.save_configuration(machine, "Native_Rotary_Surface_Gate_A.fcm")
    return machine


def _octagonal_prism(length_mm: float, radius_mm: float):
    points = [
        App.Vector(
            -length_mm * 0.5,
            radius_mm * math.cos(index * math.pi / 4.0),
            radius_mm * math.sin(index * math.pi / 4.0),
        )
        for index in range(8)
    ]
    wire = Part.makePolygon((*points, points[0]))
    return Part.Face(wire).extrude(App.Vector(length_mm, 0.0, 0.0))


def _positive_y_side_face(model) -> str:
    candidates = [
        (index, face)
        for index, face in enumerate(model.Shape.Faces, start=1)
        if float(face.BoundBox.XLength) > 39.0
    ]
    assert len(candidates) == 8, candidates
    index, _face = max(candidates, key=lambda item: float(item[1].BoundBox.Center.y))
    return f"Face{index}"


def _create_fixture(document):
    def create_model():
        model = document.addObject("Part::Feature", "RotarySurfaceGateModel")
        model.Label = "Rotary Surface gate model"
        model.Shape = _octagonal_prism(40.0, 10.0)
        assert model.Shape.isValid() and len(model.Shape.Solids) == 1
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Rotary Surface gate model", create_model)

    def create_job():
        job = PathJob.Create("RotarySurfaceJob", [model], templateFile=None)
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

    job = _commit(document, "Create Rotary Surface gate Job", create_job)

    def configure_job():
        previous_stock = job.Stock
        token = PathUtil.stageTimelineDirectResourceReplacement(job, previous_stock)
        document.removeObject(previous_stock.Name)
        placement = App.Placement(
            App.Vector(-20.0, 0.0, 0.0),
            App.Rotation(App.Vector(0.0, 1.0, 0.0), 90.0),
        )
        stock = PathStock.CreateCylinder(
            job,
            radius=12.5,
            height=40.0,
            placement=placement,
        )
        job.Stock = stock
        PathStock.ApplyStockViewDefaults(stock)
        PathUtil.finalizeTimelineDirectResourceReplacement(job, token, stock)
        job.Machine = "Native Rotary Surface Gate A"
        return stock

    _commit(document, "Configure Rotary Surface gate Job", configure_job)
    # A one-for-one resource replacement inherits the accepted display state
    # of the resource it replaces.  Mirror the human Job editor's cleanup by
    # restoring that persistent state after its provisional edit transaction.
    job.Stock.ViewObject.Visibility = True
    timeline = document.VibeCADTimeline
    stock_index = list(timeline.Operations).index(job.Stock)
    job_index = list(timeline.Operations).index(job)
    assert bool(timeline.VisibilityAtEnd[stock_index]) is bool(
        job.Stock.ViewObject.Visibility
    ), (
        "stock_accepted",
        bool(timeline.VisibilityAtEnd[stock_index]),
        "stock_app",
        bool(job.Stock.Visibility),
        "stock_view",
        bool(job.Stock.ViewObject.Visibility),
        "job_accepted",
        bool(timeline.VisibilityAtEnd[job_index]),
        "job_app",
        bool(job.Visibility),
        "job_view",
        bool(job.ViewObject.Visibility),
        "transaction",
        int(document.getBookedTransactionID()),
    )
    return model, job, job.Tools.Group[0]


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
        len(shape.Faces),
        len(shape.Edges),
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
    cut_mode: str = "climb",
    axial_window: dict | None = None,
    angular_resolution_degrees: float = 15.0,
    radial_stock_to_leave_mm: float = 0.2,
    layers: dict | None = None,
    feed_mode: str = "axial_only",
    maximum_feed: float = 2400.0,
    linear_deflection_mm: float = 0.2,
    angular_deflection_radians: float = 0.35,
    safe_height_mm: float = 22.0,
    clearance_height_mm: float = 18.0,
    coolant: str = "none",
) -> dict:
    state = job_state(job)
    assert state["machine"]["available"] is True
    assert state["machine"]["rotary_axes"][0]["command_letter"] == "A"
    exact_geometry = json.loads(json.dumps(geometry))
    for item in exact_geometry.get("items", []):
        item["model"] = _model_target(state, model)
    return {
        "operation": "rotary_surface",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": exact_geometry,
        "rotary_surface": {
            "pattern": pattern,
            "cut_mode": cut_mode,
            "axial_window": axial_window or {"kind": "stock"},
            "angular_resolution_degrees": angular_resolution_degrees,
            "radial_stock_to_leave_mm": radial_stock_to_leave_mm,
            "layers": layers or {"kind": "single_pass"},
            "feed_mode": feed_mode,
            "maximum_effective_feed_mm_per_min": maximum_feed,
            "mesh": {
                "linear_deflection_mm": linear_deflection_mm,
                "angular_deflection_radians": angular_deflection_radians,
            },
        },
        "heights": {
            "safe_height_mm": safe_height_mm,
            "clearance_height_mm": clearance_height_mm,
        },
        "coolant": coolant,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("rotary_surface",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        '"const":"rotary_surface"',
        '"const":"entire_job"',
        '"const":"faces"',
        '"const":"spiral"',
        '"const":"parallel"',
        '"const":"rings"',
        '"const":"stock"',
        '"const":"explicit"',
        '"const":"single_pass"',
        '"const":"multi_pass"',
        '"axial_pitch_mm"',
        '"surface_stepover_mm"',
        '"axial_spacing_mm"',
        '"maximum_effective_feed_mm_per_min"',
        '"angular_deflection_radians"',
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
    pattern: str,
    cut_mode: str,
    step_over_mm: float,
    step_down_mm: float,
    feed_mode: str,
    coolant: str,
    diagnostics: bool = True,
) -> None:
    assert operation in job.Operations.Group
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController is controller
    assert tuple(operation.Base) == base
    assert operation.Label == label
    assert operation.CutPattern == pattern
    assert operation.CutMode == cut_mode
    assert _mm(operation, "StepOver") == step_over_mm
    assert _mm(operation, "StepDown") == step_down_mm
    assert operation.FeedMode == feed_mode
    assert operation.CoolantMode == coolant
    assert _mm(operation, "SafeHeight") == 22.0
    assert _mm(operation, "ClearanceHeight") == 18.0
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    expressions = {
        str(path).lstrip(".") for path, _expression in tuple(operation.ExpressionEngine)
    }
    assert not {"StepDown", "SafeHeight", "ClearanceHeight"}.intersection(expressions)
    cutting = tuple(
        command for command in operation.Path.Commands if command.Name in {"G1", "G2", "G3"}
    )
    assert cutting
    assert all(
        all(name in command.Parameters for name in ("X", "Y", "Z", "A"))
        for command in cutting
    )
    rotary = [float(command.Parameters["A"]) for command in operation.Path.Commands if "A" in command.Parameters]
    assert rotary and min(rotary) >= -_TOLERANCE and max(rotary) < 360.0 + _TOLERANCE
    if diagnostics:
        facts = operation.Proxy.getGenerationDiagnostics(operation)
        assert facts["status"] == "succeeded", facts
        assert facts["stage"] == "complete", facts
        assert facts["error"] is None, facts


def _assert_regenerated(before: dict, after: dict) -> None:
    for field in (
        "document_uid",
        "object_name",
        "type_id",
        "label",
        "active",
        "settings_sha256",
        "tool_controller",
        "placement",
        "command_count",
    ):
        assert after[field] == before[field], (field, before[field], after[field])
    assert before["command_count"] > 0
    for limit in ("minimum_mm", "maximum_mm"):
        assert all(
            abs(float(actual) - float(expected)) <= 1.0e-5
            for expected, actual in zip(
                before["bounds"][limit], after["bounds"][limit], strict=True
            )
        )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    previous_machine_directory = MachineFactory._config_dir
    preferences = PathPreferences.preferences()
    prior_advanced = PathPreferences.advancedOCLFeaturesEnabled()
    prior_experimental = PathPreferences.experimentalFeaturesEnabled()
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-rotary-")
        MachineFactory.set_config_directory(Path(temporary.name) / "Machines")
        _save_machine(wrap_strategy=WrapStrategy.MODULO)
        save_path = Path(temporary.name) / "native-manufacture-rotary-surface.FCStd"
        document = App.newDocument("NativeManufactureRotarySurfaceGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_RotarySurface"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.rotary_surface",
            "rotary_surface",
            "ExactCamJobMachineCylinderRotaryFacesControllerAndParameters",
            True,
            False,
        )

        model, job, controller = _create_fixture(document)
        selected_face = _positive_y_side_face(model)
        resource = _resource(job, model)
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
        undo_ledger.begin_run("native-manufacture-rotary-surface-gui")

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
                f"native-manufacture-rotary-surface-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, selected_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        face_arguments = _arguments(
            job,
            controller,
            model,
            label="Native selected-face Rotary Surface",
            geometry={"kind": "faces", "items": [{"faces": [selected_face]}]},
            pattern={
                "kind": "parallel",
                "surface_stepover_mm": 4.0,
                "start_angle_degrees": 0.0,
                "sweep_degrees": 360.0,
            },
            feed_mode="surface_speed",
            coolant="mist",
        )

        stale_job = json.loads(json.dumps(face_arguments))
        stale_job["job"]["expected_state_sha256"] = "0" * 64
        assert call(stale_job, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        stale_model = json.loads(json.dumps(face_arguments))
        stale_model["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        assert call(stale_model, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        stale_controller = json.loads(json.dumps(face_arguments))
        stale_controller["tool_controller"]["expected_state_sha256"] = "0" * 64
        assert call(stale_controller, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        face_spiral = json.loads(json.dumps(face_arguments))
        face_spiral["rotary_surface"]["pattern"] = {
            "kind": "spiral",
            "axial_pitch_mm": 8.0,
            "start_angle_degrees": 0.0,
        }
        rejected = call(face_spiral, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "cannot honor" in rejected["error"]

        conventional_faces = json.loads(json.dumps(face_arguments))
        conventional_faces["rotary_surface"]["cut_mode"] = "conventional"
        rejected = call(conventional_faces, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "require cut_mode=climb" in rejected["error"]

        outside_stock = json.loads(json.dumps(face_arguments))
        outside_stock["rotary_surface"]["axial_window"] = {
            "kind": "explicit",
            "start_mm": -30.0,
            "stop_mm": 20.0,
        }
        assert call(outside_stock, succeeds=False)["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        unsafe_clearance = json.loads(json.dumps(face_arguments))
        unsafe_clearance["heights"]["clearance_height_mm"] = 12.0
        assert call(unsafe_clearance, succeeds=False)["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        excessive = json.loads(json.dumps(face_arguments))
        excessive["rotary_surface"]["pattern"]["surface_stepover_mm"] = 0.001
        excessive["rotary_surface"]["angular_resolution_degrees"] = 0.05
        assert call(excessive, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"

        _save_machine(wrap_strategy=WrapStrategy.UNWOUND, limits=(-360.0, 360.0))
        machine_stale = call(face_arguments, succeeds=False)
        assert machine_stale["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        unwound_arguments = _arguments(
            job,
            controller,
            model,
            label="Rejected unwound Rotary Surface",
            geometry={"kind": "entire_job"},
            pattern={
                "kind": "spiral",
                "axial_pitch_mm": 8.0,
                "start_angle_degrees": 0.0,
            },
        )
        assert call(unwound_arguments, succeeds=False)["error_code"] == "NATIVE_MANUFACTURE_MACHINE_LIMIT_EXCEEDED"
        _save_machine(wrap_strategy=WrapStrategy.MODULO)
        face_arguments = _arguments(
            job,
            controller,
            model,
            label="Native selected-face Rotary Surface",
            geometry={"kind": "faces", "items": [{"faces": [selected_face]}]},
            pattern={
                "kind": "parallel",
                "surface_stepover_mm": 4.0,
                "start_angle_degrees": 0.0,
                "sweep_degrees": 360.0,
            },
            feed_mode="surface_speed",
            coolant="mist",
        )

        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert _selection() == selection_before

        face_result = call(face_arguments)
        _events(12)
        face_name = face_result["rotary_surface"]["object_name"]
        face_operation = document.getObject(face_name)
        assert face_operation is not None
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            base=((resource, (selected_face,)),),
            label="Native selected-face Rotary Surface",
            pattern="Parallel",
            cut_mode="Climb",
            step_over_mm=4.0,
            step_down_mm=0.0,
            feed_mode="SurfaceSpeed",
            coolant="Mist",
        )
        assert face_result["rotary_surface"]["target_mode"] == "faces"
        assert face_result["rotary_surface"]["face_count"] == 1
        assert face_result["rotary_surface"]["rotary_axis"] == {
            "command_letter": "A",
            "world_axis": "X",
            "wrap_strategy": "modulo",
            "minimum_degrees": -10000.0,
            "maximum_degrees": 10000.0,
        }
        assert face_result["rotary_surface"]["estimated_processing_cells"] > 0
        assert face_result["assistant_undo_available"] is True
        face_state = operation_state(face_operation)

        spiral_arguments = _arguments(
            job,
            controller,
            model,
            label="Native spiral Rotary Surface",
            geometry={"kind": "entire_job"},
            pattern={
                "kind": "spiral",
                "axial_pitch_mm": 8.0,
                "start_angle_degrees": 20.0,
            },
            layers={"kind": "multi_pass", "step_down_mm": 2.0},
            cut_mode="conventional",
            coolant="flood",
        )
        spiral_result = call(spiral_arguments)
        _events(12)
        spiral_name = spiral_result["rotary_surface"]["object_name"]
        spiral_operation = document.getObject(spiral_name)
        assert spiral_operation is not None
        _assert_operation(
            document,
            job,
            spiral_operation,
            controller=controller,
            base=(),
            label="Native spiral Rotary Surface",
            pattern="Spiral",
            cut_mode="Conventional",
            step_over_mm=8.0,
            step_down_mm=2.0,
            feed_mode="AxialOnly",
            coolant="Flood",
        )
        spiral_state = operation_state(spiral_operation)

        rings_arguments = _arguments(
            job,
            controller,
            model,
            label="Native rings Rotary Surface",
            geometry={"kind": "entire_job"},
            pattern={
                "kind": "rings",
                "axial_spacing_mm": 10.0,
                "start_angle_degrees": 0.0,
                "sweep_degrees": 180.0,
            },
            coolant="none",
        )
        rings_result = call(rings_arguments)
        _events(12)
        rings_name = rings_result["rotary_surface"]["object_name"]
        rings_operation = document.getObject(rings_name)
        assert rings_operation is not None
        _assert_operation(
            document,
            job,
            rings_operation,
            controller=controller,
            base=(),
            label="Native rings Rotary Surface",
            pattern="Rings",
            cut_mode="Climb",
            step_over_mm=10.0,
            step_down_mm=0.0,
            feed_mode="AxialOnly",
            coolant="None",
        )
        rings_state = operation_state(rings_operation)

        conventional_arguments = _arguments(
            job,
            controller,
            model,
            label="Native conventional parallel Rotary Surface",
            geometry={"kind": "entire_job"},
            pattern={
                "kind": "parallel",
                "surface_stepover_mm": 6.0,
                "start_angle_degrees": 180.0,
                "sweep_degrees": 180.0,
            },
            cut_mode="conventional",
        )
        conventional_result = call(conventional_arguments)
        _events(12)
        conventional_name = conventional_result["rotary_surface"]["object_name"]
        conventional_operation = document.getObject(conventional_name)
        assert conventional_operation is not None
        _assert_operation(
            document,
            job,
            conventional_operation,
            controller=controller,
            base=(),
            label="Native conventional parallel Rotary Surface",
            pattern="Parallel",
            cut_mode="Conventional",
            step_over_mm=6.0,
            step_down_mm=0.0,
            feed_mode="AxialOnly",
            coolant="None",
        )
        conventional_state = operation_state(conventional_operation)

        assert len(job.Operations.Group) == len(initial_operations) + 4
        assert int(document.UndoCount) == undo_before + 4
        assert state_store.current_revision(context.document_uid) == revision_before + 4
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert shape_sha256(model.Shape, model.Name) == source_hash
        assert bool(model.ViewObject.Visibility) is source_visibility

        names = (face_name, spiral_name, rings_name, conventional_name)
        for expected_name in reversed(names):
            assert document.getObject(expected_name) is not None
            document.undo()
            _events(10)
            assert document.getObject(expected_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        for expected_name in names:
            document.redo()
            _events(10)
            assert document.getObject(expected_name) is not None
        assert document.recompute(None, True, True) is not False
        _events(12)
        states_before_save = {
            face_name: face_state,
            spiral_name: spiral_state,
            rings_name: rings_state,
            conventional_name: conventional_state,
        }
        redo_states = {}
        for name, expected in states_before_save.items():
            operation = document.getObject(name)
            current = operation_state(operation)
            _assert_regenerated(expected, current)
            redo_states[name] = current

        controller_name = controller.Name
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        reopened_model = document.getObject("RotarySurfaceGateModel")
        reopened_hash_before_recompute = shape_sha256(
            reopened_model.Shape,
            reopened_model.Name,
        )
        assert document.recompute(None, True, True) is not False
        _events(16)
        model = document.getObject("RotarySurfaceGateModel")
        job = document.getObject("RotarySurfaceJob")
        controller = document.getObject(controller_name)
        assert all(item is not None for item in (model, job, controller))
        resource = _resource(job, model)
        operation_specs = (
            (face_name, ((resource, (selected_face,)),), "Parallel", "Climb", 4.0, 0.0, "SurfaceSpeed", "Mist"),
            (spiral_name, (), "Spiral", "Conventional", 8.0, 2.0, "AxialOnly", "Flood"),
            (rings_name, (), "Rings", "Climb", 10.0, 0.0, "AxialOnly", "None"),
            (conventional_name, (), "Parallel", "Conventional", 6.0, 0.0, "AxialOnly", "None"),
        )
        for name, base, pattern, mode, stepover, step_down, feed, coolant in operation_specs:
            operation = document.getObject(name)
            assert operation is not None
            _assert_operation(
                document,
                job,
                operation,
                controller=controller,
                base=base,
                label=operation.Label,
                pattern=pattern,
                cut_mode=mode,
                step_over_mm=stepover,
                step_down_mm=step_down,
                feed_mode=feed,
                coolant=coolant,
                diagnostics=False,
            )
            _assert_regenerated(redo_states[name], operation_state(operation))
        assert _shape_signature(model.Shape) == source_signature
        # FCStd restoration can canonicalize the serialized BREP without
        # changing its topology or geometry.  Bind the reopened session to its
        # own exact BREP while comparing durable geometric facts across the
        # persistence boundary.
        assert (
            shape_sha256(model.Shape, model.Name)
            == reopened_hash_before_recompute
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_ROTARY_SURFACE_GUI_OK "
            "exact_targets=true exact_machine=true cylinder_stock=true faces=true "
            "spiral=true parallel=true rings=true climb=true conventional=true "
            "feed_modes=true bounded_work=true toolpath=true history=true rollback=true "
            "sources_preserved=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        preferences.SetBool(PathPreferences.EnableAdvancedOCLFeatures, prior_advanced)
        preferences.SetBool(PathPreferences.EnableExperimentalFeatures, prior_experimental)
        MachineFactory._config_dir = previous_machine_directory
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
