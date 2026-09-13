# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for the OCL-gated Native CAM Steep/Shallow action."""

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


def _ocl_available() -> bool:
    try:
        import ocl  # noqa: F401

        return True
    except ImportError:
        try:
            import opencamlib  # noqa: F401

            return True
        except ImportError:
            return False


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
    controller = Gui.getMainWindow().findChild(QtCore.QObject, "VibeCADRibbonController")
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    assert "CAM_SteepShallow" in surface.command_ids
    return controller, surface


def _create_fixture(document):
    def create_models():
        model = document.addObject("Part::Feature", "SteepShallowGateModel")
        model.Label = "Steep/Shallow gate model"
        base = Part.makeBox(40.0, 30.0, 5.0)
        boss = Part.makeBox(12.0, 12.0, 5.0, App.Vector(6.0, 6.0, 5.0))
        model.Shape = base.fuse(boss).removeSplitter()
        assert model.Shape.isValid() and len(model.Shape.Solids) == 1
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        secondary = document.addObject(
            "Part::Feature",
            "SteepShallowSecondaryModel",
        )
        secondary.Label = "Steep/Shallow secondary model"
        secondary.Shape = Part.makeBox(
            12.0,
            10.0,
            7.0,
            App.Vector(50.0, 5.0, 0.0),
        )
        assert secondary.Shape.isValid() and len(secondary.Shape.Solids) == 1
        document.publishProvisionalTimelineOperationBlock(secondary, (), ())
        return model, secondary

    model, secondary = _commit(
        document,
        "Create Steep/Shallow gate models",
        create_models,
    )

    def create_job():
        job = PathJob.Create(
            "SteepShallowJob",
            [model, secondary],
            templateFile=None,
        )
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

    job = _commit(document, "Create Steep/Shallow gate Job", create_job)
    return model, secondary, job, job.Tools.Group[0]


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames)) for item in Gui.Selection.getSelectionEx()
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


def _controller_target(state: dict, controller) -> dict:
    return _target(next(item for item in state["tools"] if item["object_name"] == controller.Name))


def _arguments(
    job,
    controller,
    *,
    label: str,
    slope_threshold_degrees: float = 45.0,
    stepover_mm: float = 1.5,
    boundary_overlap_mm: float = 0.5,
    sample_interval_mm: float = 1.5,
    cut_mode: str = "climb",
    use_rest_machining: bool = False,
    rest_reference_tool_diameter_mm: float | None = None,
    linear_deflection_mm: float = 0.1,
    angular_deflection_radians: float = 0.524,
    start_depth_mm: float = 10.0,
    final_depth_mm: float = 0.0,
    step_down_mm: float = 2.0,
    safe_height_mm: float = 13.0,
    clearance_height_mm: float = 15.0,
    coolant: str = "none",
) -> dict:
    state = job_state(job)
    settings: dict = {
        "slope_threshold_degrees": slope_threshold_degrees,
        "stepover_mm": stepover_mm,
        "boundary_overlap_mm": boundary_overlap_mm,
        "sample_interval_mm": sample_interval_mm,
        "cut_mode": cut_mode,
        "use_rest_machining": use_rest_machining,
        "mesh": {
            "linear_deflection_mm": linear_deflection_mm,
            "angular_deflection_radians": angular_deflection_radians,
        },
    }
    if rest_reference_tool_diameter_mm is not None:
        settings["rest_reference_tool_diameter_mm"] = rest_reference_tool_diameter_mm
    return {
        "operation": "steep_shallow",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "steep_shallow": settings,
        "depths": {
            "start_depth_mm": start_depth_mm,
            "final_depth_mm": final_depth_mm,
            "step_down_mm": step_down_mm,
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
    schema = definition.provider_schema(("steep_shallow",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        '"const":"steep_shallow"',
        '"slope_threshold_degrees"',
        '"stepover_mm"',
        '"boundary_overlap_mm"',
        '"sample_interval_mm"',
        '"use_rest_machining"',
        '"rest_reference_tool_diameter_mm"',
        '"linear_deflection_mm"',
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
    label: str,
    slope_threshold_degrees: float,
    stepover_mm: float,
    boundary_overlap_mm: float,
    sample_interval_mm: float,
    cut_mode: str,
    use_rest_machining: bool,
    rest_reference_tool_diameter_mm: float,
    linear_deflection_mm: float,
    angular_deflection_radians: float,
    start_depth_mm: float,
    final_depth_mm: float,
    step_down_mm: float,
    safe_height_mm: float,
    clearance_height_mm: float,
    coolant: str,
) -> None:
    assert operation in job.Operations.Group
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController is controller
    assert operation.Label == label
    assert round(float(operation.SlopeThreshold.Value), 7) == slope_threshold_degrees
    assert _mm(operation, "StepOver") == stepover_mm
    assert _mm(operation, "BoundaryOverlap") == boundary_overlap_mm
    assert _mm(operation, "SampleInterval") == sample_interval_mm
    assert operation.CutMode == cut_mode
    assert bool(operation.UseRestMachining) is use_rest_machining
    assert _mm(operation, "RestReferenceToolDiameter") == (rest_reference_tool_diameter_mm)
    assert _mm(operation, "LinearDeflection") == linear_deflection_mm
    assert round(float(operation.AngularDeflection.Value), 7) == angular_deflection_radians
    assert _mm(operation, "StartDepth") == start_depth_mm
    assert _mm(operation, "FinalDepth") == final_depth_mm
    assert _mm(operation, "StepDown") == step_down_mm
    assert _mm(operation, "SafeHeight") == safe_height_mm
    assert _mm(operation, "ClearanceHeight") == clearance_height_mm
    assert operation.CoolantMode == coolant
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    expressions = {str(path).lstrip(".") for path, _expression in tuple(operation.ExpressionEngine)}
    assert not {
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    }.intersection(expressions)
    cutting = tuple(
        command for command in operation.Path.Commands if command.Name in {"G1", "G2", "G3"}
    )
    assert cutting
    lowest = min(float(command.Parameters["Z"]) for command in cutting if "Z" in command.Parameters)
    assert lowest >= final_depth_mm - _TOLERANCE, lowest


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
    prior_advanced_ocl = PathPreferences.advancedOCLFeaturesEnabled()
    try:
        if not _ocl_available():
            print(
                "VIBECAD_NATIVE_MANUFACTURE_STEEP_SHALLOW_GUI_SKIP ocl=false",
                flush=True,
            )
            exit_code = 0
            return
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-steep-shallow-")
        save_path = Path(temporary.name) / "native-manufacture-steep-shallow.FCStd"
        document = App.newDocument("NativeManufactureSteepShallowGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {item.command_id: item for item in resolve_native_action_inventory(surface).plans}[
            "CAM_SteepShallow"
        ]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.steep_shallow",
            "steep_shallow",
            "ExactCamJobModelControllerAndSteepShallowParameters",
            True,
            False,
        )

        model, secondary, job, controller = _create_fixture(document)
        tool_diameter = getattr(controller.Tool, "Diameter", None)
        tool_diameter_mm = round(
            float(getattr(tool_diameter, "Value", tool_diameter)),
            7,
        )
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        source_hash = shape_sha256(model.Shape, model.Name)
        source_signature = _shape_signature(model.Shape)
        source_visibility = bool(model.ViewObject.Visibility)
        secondary_hash = shape_sha256(secondary.Shape, secondary.Name)
        secondary_signature = _shape_signature(secondary.Shape)
        secondary_visibility = bool(secondary.ViewObject.Visibility)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-steep-shallow-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, ribbon_controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=undo_ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: (read_active_ribbon_surface(ribbon_controller).surface_id),
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        runtimes = build_native_runtime_bindings(context, turn.tool_names)
        runtimes[MANUFACTURE_OPERATION_CAPABILITY_NAME] = NativeManufactureOperationRuntime(context)
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
                f"native-manufacture-steep-shallow-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        rest_off_arguments = _arguments(
            job,
            controller,
            label="Native rest-off Steep Shallow",
            cut_mode="climb",
            coolant="mist",
        )

        stale_job = json.loads(json.dumps(rest_off_arguments))
        stale_job["job"]["expected_state_sha256"] = "0" * 64
        assert call(stale_job, succeeds=False)["error_code"] == ("NATIVE_MANUFACTURE_STATE_STALE")
        stale_controller = json.loads(json.dumps(rest_off_arguments))
        stale_controller["tool_controller"]["expected_state_sha256"] = "0" * 64
        assert call(stale_controller, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        invalid_slope = json.loads(json.dumps(rest_off_arguments))
        invalid_slope["steep_shallow"]["slope_threshold_degrees"] = 90.5
        assert call(invalid_slope, succeeds=False)["error_code"] == ("NATIVE_ARGUMENTS_INVALID")

        invalid_interval = json.loads(json.dumps(rest_off_arguments))
        invalid_interval["steep_shallow"]["sample_interval_mm"] = 0.0
        assert call(invalid_interval, succeeds=False)["error_code"] == ("NATIVE_ARGUMENTS_INVALID")

        invalid_overlap = json.loads(json.dumps(rest_off_arguments))
        invalid_overlap["steep_shallow"]["boundary_overlap_mm"] = -0.5
        assert call(invalid_overlap, succeeds=False)["error_code"] == ("NATIVE_ARGUMENTS_INVALID")

        rest_without_flag = json.loads(json.dumps(rest_off_arguments))
        rest_without_flag["steep_shallow"]["rest_reference_tool_diameter_mm"] = 12.0
        assert call(rest_without_flag, succeeds=False)["error_code"] == ("NATIVE_ARGUMENTS_INVALID")

        small_reference = _arguments(
            job,
            controller,
            label="Rejected small-reference Steep Shallow",
            use_rest_machining=True,
            rest_reference_tool_diameter_mm=tool_diameter_mm,
        )
        rejected = call(small_reference, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "must exceed" in rejected["error"]

        excessive = json.loads(json.dumps(rest_off_arguments))
        excessive["steep_shallow"]["sample_interval_mm"] = 0.01
        assert call(excessive, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
        )

        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert _selection() == selection_before

        rest_off_result = call(rest_off_arguments)
        _events(12)
        rest_off_name = rest_off_result["steep_shallow"]["object_name"]
        rest_off_operation = document.getObject(rest_off_name)
        assert rest_off_operation is not None
        _assert_operation(
            document,
            job,
            rest_off_operation,
            controller=controller,
            label="Native rest-off Steep Shallow",
            slope_threshold_degrees=45.0,
            stepover_mm=1.5,
            boundary_overlap_mm=0.5,
            sample_interval_mm=1.5,
            cut_mode="Climb",
            use_rest_machining=False,
            rest_reference_tool_diameter_mm=0.0,
            linear_deflection_mm=0.1,
            angular_deflection_radians=0.524,
            start_depth_mm=10.0,
            final_depth_mm=0.0,
            step_down_mm=2.0,
            safe_height_mm=13.0,
            clearance_height_mm=15.0,
            coolant="Mist",
        )
        assert rest_off_result["steep_shallow"]["target_mode"] == "entire_job"
        assert rest_off_result["steep_shallow"]["model_names"] == [
            model.Name,
            secondary.Name,
        ]
        assert rest_off_result["steep_shallow"]["use_rest_machining"] is False
        assert rest_off_result["steep_shallow"]["tool_diameter_mm"] == (tool_diameter_mm)
        assert rest_off_result["steep_shallow"]["estimated_processing_cells"] > 0
        assert rest_off_result["steep_shallow"]["cutting_command_count"] > 0
        rest_off_x = [
            float(command.Parameters["X"])
            for command in rest_off_operation.Path.Commands
            if command.Name == "G1" and "X" in command.Parameters
        ]
        assert any(49.0 <= x <= 63.0 for x in rest_off_x), rest_off_x
        assert rest_off_result["assistant_undo_available"] is True
        rest_off_state = operation_state(rest_off_operation)

        rest_on_arguments = _arguments(
            job,
            controller,
            label="Native rest-on Steep Shallow",
            slope_threshold_degrees=40.0,
            stepover_mm=2.0,
            boundary_overlap_mm=0.75,
            sample_interval_mm=1.25,
            cut_mode="conventional",
            use_rest_machining=True,
            rest_reference_tool_diameter_mm=12.0,
            linear_deflection_mm=0.2,
            angular_deflection_radians=0.5,
            step_down_mm=2.5,
            coolant="flood",
        )
        rest_on_result = call(rest_on_arguments)
        _events(12)
        rest_on_name = rest_on_result["steep_shallow"]["object_name"]
        rest_on_operation = document.getObject(rest_on_name)
        assert rest_on_operation is not None
        _assert_operation(
            document,
            job,
            rest_on_operation,
            controller=controller,
            label="Native rest-on Steep Shallow",
            slope_threshold_degrees=40.0,
            stepover_mm=2.0,
            boundary_overlap_mm=0.75,
            sample_interval_mm=1.25,
            cut_mode="Conventional",
            use_rest_machining=True,
            rest_reference_tool_diameter_mm=12.0,
            linear_deflection_mm=0.2,
            angular_deflection_radians=0.5,
            start_depth_mm=10.0,
            final_depth_mm=0.0,
            step_down_mm=2.5,
            safe_height_mm=13.0,
            clearance_height_mm=15.0,
            coolant="Flood",
        )
        assert rest_on_result["steep_shallow"]["use_rest_machining"] is True
        assert rest_on_result["steep_shallow"]["rest_reference_tool_diameter_mm"] == (12.0)
        assert rest_on_result["steep_shallow"]["cutting_command_count"] > 0
        assert rest_on_result["assistant_undo_available"] is True
        rest_on_state = operation_state(rest_on_operation)

        assert len(job.Operations.Group) == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == (revision_before + 2)
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert shape_sha256(model.Shape, model.Name) == source_hash
        assert bool(model.ViewObject.Visibility) is source_visibility
        assert shape_sha256(secondary.Shape, secondary.Name) == secondary_hash
        assert bool(secondary.ViewObject.Visibility) is secondary_visibility

        names = (rest_off_name, rest_on_name)
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
            rest_off_name: rest_off_state,
            rest_on_name: rest_on_state,
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
        assert document.recompute(None, True, True) is not False
        _events(16)
        model = document.getObject("SteepShallowGateModel")
        secondary = document.getObject("SteepShallowSecondaryModel")
        job = document.getObject("SteepShallowJob")
        controller = document.getObject(controller_name)
        assert all(item is not None for item in (model, secondary, job, controller))
        reopened_specs = (
            (
                rest_off_name,
                "Native rest-off Steep Shallow",
                45.0,
                "Climb",
                False,
                0.0,
                2.0,
                "Mist",
            ),
            (
                rest_on_name,
                "Native rest-on Steep Shallow",
                40.0,
                "Conventional",
                True,
                12.0,
                2.5,
                "Flood",
            ),
        )
        for (
            name,
            label,
            slope,
            cut_mode,
            use_rest,
            rest_diameter,
            step_down,
            coolant,
        ) in reopened_specs:
            operation = document.getObject(name)
            assert operation is not None
            assert operation in job.Operations.Group
            assert operation.Label == label
            assert round(float(operation.SlopeThreshold.Value), 7) == slope
            assert operation.CutMode == cut_mode
            assert bool(operation.UseRestMachining) is use_rest
            assert _mm(operation, "RestReferenceToolDiameter") == rest_diameter
            assert _mm(operation, "StepDown") == step_down
            assert operation.CoolantMode == coolant
            assert operation.ToolController is controller
            _assert_regenerated(redo_states[name], operation_state(operation))
        assert _shape_signature(model.Shape) == source_signature
        assert _shape_signature(secondary.Shape) == secondary_signature

        print(
            "VIBECAD_NATIVE_MANUFACTURE_STEEP_SHALLOW_GUI_OK "
            "exact_targets=true entire_job=true multi_model=true rest_off=true rest_on=true "
            "climb=true conventional=true bounded_work=true toolpath=true "
            "gouge_free=true history=true rollback=true sources_preserved=true "
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
