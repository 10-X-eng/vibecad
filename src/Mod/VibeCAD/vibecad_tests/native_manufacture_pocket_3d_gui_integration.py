# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM 3D Pocket."""

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
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    return controller, surface


def _create_fixture(document):
    def create_model():
        model = document.addObject("Part::Feature", "Pocket3DGateModel")
        model.Label = "3D Pocket gate model"
        stock = Part.makeBox(60.0, 40.0, 12.0)
        upper_recess = Part.makeBox(20.0, 12.0, 8.0, App.Vector(5.0, 5.0, 6.0))
        lower_recess = Part.makeBox(18.0, 12.0, 10.0, App.Vector(36.0, 22.0, 4.0))
        conical_recess = Part.makeCone(5.0, 2.0, 5.0, App.Vector(30.0, 8.0, 8.0))
        model.Shape = stock.cut((upper_recess, lower_recess, conical_recess))
        assert model.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create 3D Pocket gate model", create_model)

    def create_job():
        job = PathJob.Create("Pocket3DJob", [model], templateFile=None)
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

    job = _commit(document, "Create 3D Pocket gate Job", create_job)
    return model, job, job.Tools.Group[0]


def _horizontal_face_at(model, z_mm: float) -> str:
    matches = [
        f"Face{index}"
        for index, face in enumerate(model.Shape.Faces, start=1)
        if face.BoundBox.ZLength <= _TOLERANCE
        and abs(float(face.BoundBox.ZMin) - z_mm) <= _TOLERANCE
        and float(face.Area) > 50.0
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _conical_face(model) -> str:
    matches = [
        f"Face{index}"
        for index, face in enumerate(model.Shape.Faces, start=1)
        if face.Surface.__class__.__name__ == "Cone"
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _face_edge_names(model, face_name: str) -> tuple[str, ...]:
    face = model.Shape.getElement(face_name)
    names = tuple(
        f"Edge{index}"
        for index, edge in enumerate(model.Shape.Edges, start=1)
        if any(edge.isSame(candidate) for candidate in face.OuterWire.Edges)
    )
    assert len(names) == 4, names
    return names


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


def _motion_facts(operation) -> dict[str, object]:
    position = {axis: 0.0 for axis in "XYZ"}
    counts: dict[str, int] = {}
    cutting_points = []
    for command in tuple(operation.Path.Commands):
        name = str(command.Name)
        for axis in "XYZ":
            if axis in command.Parameters:
                position[axis] = float(command.Parameters[axis])
        after = tuple(position[axis] for axis in "XYZ")
        counts[name] = counts.get(name, 0) + 1
        if name == "G1":
            cutting_points.append(tuple(round(value, 6) for value in after))
    z_counts: dict[float, int] = {}
    for _x, _y, z_value in cutting_points:
        z_counts[z_value] = z_counts.get(z_value, 0) + 1
    return {
        "counts": counts,
        "cutting_bounds": (
            tuple(
                (
                    min(point[index] for point in cutting_points),
                    max(point[index] for point in cutting_points),
                )
                for index in range(3)
            )
            if cutting_points
            else ()
        ),
        "cutting_z_counts": tuple(sorted(z_counts.items())),
    }


def _assert_regenerated_operation_state(
    before: dict,
    after: dict,
    before_motion: dict[str, object],
    after_motion: dict[str, object],
) -> None:
    """Require durable settings and cutting coverage across route ordering."""

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
    assert int(before["command_count"]) > 0 and int(after["command_count"]) > 0
    before_g1 = int(before_motion["counts"].get("G1", 0))
    after_g1 = int(after_motion["counts"].get("G1", 0))
    assert before_g1 == after_g1
    assert before_motion["cutting_bounds"] == after_motion["cutting_bounds"]
    assert before_motion["cutting_z_counts"] == after_motion["cutting_z_counts"]
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
    subelements: tuple[str, ...],
    cut_mode: str,
    pattern: dict,
    stepover_percent: int,
    pass_extension_mm: float,
    rest_machining: bool,
    start: dict,
    start_depth_mm: float,
    step_down_mm: float,
    finish_step_mm: float,
    coolant: str,
) -> dict:
    state = job_state(job)
    return {
        "operation": "pocket_3d",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": {
            "kind": "subelements",
            "items": [
                {
                    "model": _model_target(state, model),
                    "subelements": list(subelements),
                }
            ],
        },
        "pocket": {
            "cut_mode": cut_mode,
            "pattern": pattern,
            "stepover_percent": stepover_percent,
            "pass_extension_mm": pass_extension_mm,
            "rest_machining": rest_machining,
            "start": start,
        },
        "depths": {
            "start_depth_mm": start_depth_mm,
            "step_down_mm": step_down_mm,
            "finish_step_mm": finish_step_mm,
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
    schema = definition.provider_schema(("pocket_3d",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "tool_controller",
        "subelements",
        "pass_extension_mm",
        "rest_machining",
        "automatic",
        "point_mm",
        "minimize_travel",
        "step_down_mm",
        "finish_step_mm",
    ):
        assert field in encoded
    assert "final_depth_mm" not in encoded
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
    resource,
    label: str,
    subelements: tuple[str, ...],
    cut_mode: str,
    pattern: str,
    angle_degrees: float,
    stepover_percent: int,
    pass_extension_mm: float,
    rest_machining: bool,
    use_start_point: bool,
    start_point_mm: tuple[float, float, float],
    minimize_travel: bool,
    retract_threshold_mm: float,
    source_top_mm: float,
    start_depth_mm: float,
    final_depth_mm: float,
    step_down_mm: float,
    finish_step_mm: float,
    coolant: str,
    cutting_required: bool,
    diagnostics: bool = True,
) -> None:
    assert operation is job.Operations.Group[-1] or operation in job.Operations.Group
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController is controller
    assert tuple(operation.Base) == ((resource, subelements),)
    assert operation.Label == label
    assert operation.CutMode == cut_mode
    assert operation.ClearingPattern == pattern
    assert round(float(operation.Angle), 7) == angle_degrees
    assert int(operation.StepOver) == stepover_percent
    assert round(operation.ExtraOffset.getValueAs("mm"), 7) == pass_extension_mm
    assert operation.UseRestMachining is rest_machining
    assert operation.UseStartPoint is use_start_point
    assert tuple(round(float(value), 7) for value in operation.StartPoint) == (
        start_point_mm
    )
    assert operation.MinTravel is minimize_travel
    assert operation.StartAt == "Center"
    assert operation.SortingMode == "Automatic"
    assert operation.ForceMaxStepOver is False
    assert operation.SplitArcs is False
    assert round(operation.RetractThreshold.getValueAs("mm"), 7) == (
        retract_threshold_mm
    )
    assert operation.HandleMultipleFeatures == "Collectively"
    assert operation.AdaptivePocketStart is False
    assert operation.AdaptivePocketFinish is False
    assert operation.ProcessStockArea is False
    assert round(operation.OpStartDepth.getValueAs("mm"), 7) == source_top_mm
    assert round(operation.OpFinalDepth.getValueAs("mm"), 7) == final_depth_mm
    assert round(operation.StartDepth.getValueAs("mm"), 7) == start_depth_mm
    assert round(operation.FinalDepth.getValueAs("mm"), 7) == final_depth_mm
    assert round(operation.StepDown.getValueAs("mm"), 7) == step_down_mm
    assert round(operation.FinishDepth.getValueAs("mm"), 7) == finish_step_mm
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == start_depth_mm + 2.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == (
        start_depth_mm + 4.0
    )
    assert operation.CoolantMode == coolant
    assert tuple(round(float(value), 7) for value in operation.Workplane) == (
        0.0,
        0.0,
        1.0,
    )
    expression_paths = {
        str(path).lstrip(".") for path, _expression in tuple(operation.ExpressionEngine)
    }
    for property_name in (
        "RetractThreshold",
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "FinishDepth",
        "SafeHeight",
        "ClearanceHeight",
    ):
        assert property_name not in expression_paths
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    cutting = tuple(
        command
        for command in operation.Path.Commands
        if command.Name in {"G1", "G2", "G3"}
    )
    if cutting_required:
        assert cutting
        current_z = None
        effective_cutting_z = []
        for command in operation.Path.Commands:
            if "Z" in command.Parameters:
                current_z = float(command.Parameters["Z"])
            if command.Name in {"G1", "G2", "G3"} and current_z is not None:
                effective_cutting_z.append(current_z)
        assert effective_cutting_z
        assert min(effective_cutting_z) <= final_depth_mm + 1.0e-5
        removal = operation.removalshape
        assert not removal.isNull() and removal.isValid() and removal.Volume > 0.0
    if diagnostics:
        facts = operation.Proxy.getGenerationDiagnostics(operation)
        assert facts["status"] == "succeeded", facts
        assert facts["stage"] == "complete", facts
        assert facts["error"] is None, facts


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-pocket-3d-")
        save_path = Path(temporary.name) / "native-manufacture-pocket-3d.FCStd"
        document = App.newDocument("NativeManufacturePocket3DGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Pocket3D"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.pocket_3d",
            "pocket_3d",
            "ExactCamJobPocket3DFeaturesControllerAndParameters",
            True,
            False,
        )

        model, job, controller = _create_fixture(document)
        resource = _resource(job, model)
        source_top = round(float(job.Stock.Shape.BoundBox.ZMax), 7)
        assert source_top >= 12.0
        face_name = _horizontal_face_at(model, 6.0)
        edge_face = _horizontal_face_at(model, 4.0)
        edge_names = _face_edge_names(model, edge_face)
        unsupported_face = _conical_face(model)
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        source_hash = shape_sha256(model.Shape, model.Name)
        source_signature = _shape_signature(model.Shape)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-pocket-3d-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, ribbon_controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=undo_ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: (
                read_active_ribbon_surface(ribbon_controller).surface_id
            ),
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
                f"native-manufacture-pocket-3d-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, face_name)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        face_arguments = _arguments(
            job,
            controller,
            model,
            label="Native face 3D Pocket",
            subelements=(face_name,),
            cut_mode="climb",
            pattern={"kind": "grid", "angle_degrees": 22.5},
            stepover_percent=42,
            pass_extension_mm=0.25,
            rest_machining=False,
            start={
                "kind": "point",
                "point_mm": {"x_mm": 7.0, "y_mm": 7.0, "z_mm": source_top},
                "minimize_travel": True,
            },
            start_depth_mm=source_top,
            step_down_mm=1.5,
            finish_step_mm=0.5,
            coolant="mist",
        )

        stale = json.loads(json.dumps(face_arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        unsupported = json.loads(json.dumps(face_arguments))
        unsupported["geometry"]["items"][0]["subelements"] = [unsupported_face]
        unsupported_result = call(unsupported, succeeds=False)
        assert unsupported_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "cannot machine" in unsupported_result["error"]

        open_loop = json.loads(json.dumps(face_arguments))
        open_loop["geometry"]["items"][0]["subelements"] = list(edge_names[:-1])
        open_result = call(open_loop, succeeds=False)
        assert open_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "closed horizontal wires" in open_result["error"]

        mixed = json.loads(json.dumps(face_arguments))
        mixed["geometry"]["items"][0]["subelements"] = [
            face_name,
            edge_names[0],
        ]
        mixed_result = call(mixed, succeeds=False)
        assert mixed_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "cannot mix" in mixed_result["error"]

        premature_rest = json.loads(json.dumps(face_arguments))
        premature_rest["pocket"]["rest_machining"] = True
        rest_result = call(premature_rest, succeeds=False)
        assert rest_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "earlier active cutting operation" in rest_result["error"]

        invalid_depth = json.loads(json.dumps(face_arguments))
        invalid_depth["depths"]["start_depth_mm"] = 6.0
        depth_result = call(invalid_depth, succeeds=False)
        assert depth_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "derived source top" in depth_result["error"]

        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(context.document_uid) == revision_before
        assert _selection() == selection_before

        face_result = call(face_arguments)
        _events(12)
        face_operation_name = face_result["pocket_3d"]["object_name"]
        face_operation = document.getObject(face_operation_name)
        assert face_operation is not None
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            resource=resource,
            label="Native face 3D Pocket",
            subelements=(face_name,),
            cut_mode="Climb",
            pattern="Grid",
            angle_degrees=22.5,
            stepover_percent=42,
            pass_extension_mm=0.25,
            rest_machining=False,
            use_start_point=True,
            start_point_mm=(7.0, 7.0, source_top),
            minimize_travel=True,
            retract_threshold_mm=round(float(controller.Tool.Diameter.Value), 7),
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=6.0,
            step_down_mm=1.5,
            finish_step_mm=0.5,
            coolant="Mist",
            cutting_required=True,
        )
        assert face_result["pocket_3d"]["features"] == {
            "feature_count": 1,
            "face_count": 1,
            "edge_count": 0,
            "closed_edge_wire_count": 0,
        }
        assert face_result["pocket_3d"]["derived_source_top_mm"] == source_top
        assert face_result["pocket_3d"]["derived_final_depth_mm"] == 6.0
        assert face_result["pocket_3d"]["removal_volume_mm3"] > 0.0
        assert face_result["pocket_3d"]["minimum_cutting_z_mm"] <= 6.00001
        assert face_result["assistant_undo_available"] is True
        face_state = operation_state(face_operation)
        face_motion = _motion_facts(face_operation)

        edge_arguments = _arguments(
            job,
            controller,
            model,
            label="Native edge-loop 3D Pocket",
            subelements=edge_names,
            cut_mode="conventional",
            pattern={"kind": "zigzag_offset", "angle_degrees": 35.0},
            stepover_percent=55,
            pass_extension_mm=-0.15,
            rest_machining=True,
            start={"kind": "automatic"},
            start_depth_mm=source_top,
            step_down_mm=2.0,
            finish_step_mm=0.0,
            coolant="flood",
        )
        edge_result = call(edge_arguments)
        _events(12)
        edge_operation_name = edge_result["pocket_3d"]["object_name"]
        edge_operation = document.getObject(edge_operation_name)
        assert edge_operation is not None
        _assert_operation(
            document,
            job,
            edge_operation,
            controller=controller,
            resource=resource,
            label="Native edge-loop 3D Pocket",
            subelements=edge_names,
            cut_mode="Conventional",
            pattern="ZigZagOffset",
            angle_degrees=35.0,
            stepover_percent=55,
            pass_extension_mm=-0.15,
            rest_machining=True,
            use_start_point=False,
            start_point_mm=(0.0, 0.0, 0.0),
            minimize_travel=False,
            retract_threshold_mm=0.0,
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=4.0,
            step_down_mm=2.0,
            finish_step_mm=0.0,
            coolant="Flood",
            cutting_required=False,
        )
        assert edge_result["pocket_3d"]["features"] == {
            "feature_count": 4,
            "face_count": 0,
            "edge_count": 4,
            "closed_edge_wire_count": 1,
        }
        assert edge_result["pocket_3d"]["derived_final_depth_mm"] == 4.0
        assert len(job.Operations.Group) == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == (
            revision_before + 2
        )
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert shape_sha256(model.Shape, model.Name) == source_hash
        edge_state = operation_state(edge_operation)
        edge_motion = _motion_facts(edge_operation)
        face_state_after_edge = operation_state(face_operation)
        face_motion_after_edge = _motion_facts(face_operation)
        _assert_regenerated_operation_state(
            face_state,
            face_state_after_edge,
            face_motion,
            face_motion_after_edge,
        )

        document.undo()
        _events(12)
        assert document.getObject(edge_operation_name) is None
        assert document.getObject(face_operation_name) is not None
        document.undo()
        _events(12)
        assert document.getObject(face_operation_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        document.redo()
        _events(12)
        assert document.recompute(None, True, True) is not False
        _events(12)
        model = document.getObject("Pocket3DGateModel")
        job = document.getObject("Pocket3DJob")
        face_operation = document.getObject(face_operation_name)
        edge_operation = document.getObject(edge_operation_name)
        assert all(
            item is not None for item in (model, job, face_operation, edge_operation)
        )
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            resource=resource,
            label="Native face 3D Pocket",
            subelements=(face_name,),
            cut_mode="Climb",
            pattern="Grid",
            angle_degrees=22.5,
            stepover_percent=42,
            pass_extension_mm=0.25,
            rest_machining=False,
            use_start_point=True,
            start_point_mm=(7.0, 7.0, source_top),
            minimize_travel=True,
            retract_threshold_mm=round(float(controller.Tool.Diameter.Value), 7),
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=6.0,
            step_down_mm=1.5,
            finish_step_mm=0.5,
            coolant="Mist",
            cutting_required=True,
        )
        _assert_operation(
            document,
            job,
            edge_operation,
            controller=controller,
            resource=resource,
            label="Native edge-loop 3D Pocket",
            subelements=edge_names,
            cut_mode="Conventional",
            pattern="ZigZagOffset",
            angle_degrees=35.0,
            stepover_percent=55,
            pass_extension_mm=-0.15,
            rest_machining=True,
            use_start_point=False,
            start_point_mm=(0.0, 0.0, 0.0),
            minimize_travel=False,
            retract_threshold_mm=0.0,
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=4.0,
            step_down_mm=2.0,
            finish_step_mm=0.0,
            coolant="Flood",
            cutting_required=False,
        )
        face_state_after_redo = operation_state(face_operation)
        edge_state_after_redo = operation_state(edge_operation)
        face_motion_after_redo = _motion_facts(face_operation)
        edge_motion_after_redo = _motion_facts(edge_operation)
        _assert_regenerated_operation_state(
            face_state,
            face_state_after_redo,
            face_motion,
            face_motion_after_redo,
        )
        _assert_regenerated_operation_state(
            edge_state,
            edge_state_after_redo,
            edge_motion,
            edge_motion_after_redo,
        )
        assert shape_sha256(model.Shape, model.Name) == source_hash

        controller_name = controller.Name
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("Pocket3DGateModel")
        job = document.getObject("Pocket3DJob")
        controller = document.getObject(controller_name)
        face_operation = document.getObject(face_operation_name)
        edge_operation = document.getObject(edge_operation_name)
        assert all(
            item is not None
            for item in (model, job, controller, face_operation, edge_operation)
        )
        resource = _resource(job, model)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            resource=resource,
            label="Native face 3D Pocket",
            subelements=(face_name,),
            cut_mode="Climb",
            pattern="Grid",
            angle_degrees=22.5,
            stepover_percent=42,
            pass_extension_mm=0.25,
            rest_machining=False,
            use_start_point=True,
            start_point_mm=(7.0, 7.0, source_top),
            minimize_travel=True,
            retract_threshold_mm=round(float(controller.Tool.Diameter.Value), 7),
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=6.0,
            step_down_mm=1.5,
            finish_step_mm=0.5,
            coolant="Mist",
            cutting_required=True,
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            edge_operation,
            controller=controller,
            resource=resource,
            label="Native edge-loop 3D Pocket",
            subelements=edge_names,
            cut_mode="Conventional",
            pattern="ZigZagOffset",
            angle_degrees=35.0,
            stepover_percent=55,
            pass_extension_mm=-0.15,
            rest_machining=True,
            use_start_point=False,
            start_point_mm=(0.0, 0.0, 0.0),
            minimize_travel=False,
            retract_threshold_mm=0.0,
            source_top_mm=source_top,
            start_depth_mm=source_top,
            final_depth_mm=4.0,
            step_down_mm=2.0,
            finish_step_mm=0.0,
            coolant="Flood",
            cutting_required=False,
            diagnostics=False,
        )
        _assert_regenerated_operation_state(
            face_state_after_redo,
            operation_state(face_operation),
            face_motion_after_redo,
            _motion_facts(face_operation),
        )
        _assert_regenerated_operation_state(
            edge_state_after_redo,
            operation_state(edge_operation),
            edge_motion_after_redo,
            _motion_facts(edge_operation),
        )
        assert _shape_signature(model.Shape) == source_signature

        print(
            "VIBECAD_NATIVE_MANUFACTURE_POCKET_3D_GUI_OK "
            "exact_targets=true faces=true edge_loops=true parameters=true "
            "derived_depth=true rest_machining=true toolpath=true history=true "
            "rollback=true sources_preserved=true undo=true redo=true reopen=true",
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
