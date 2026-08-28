# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Deburr."""

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
        plate = document.addObject("Part::Feature", "DeburrGatePlate")
        plate.Label = "Deburr gate plate"
        plate.Shape = Part.makeBox(50.0, 36.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(plate, (), ())
        return plate

    plate = _commit(document, "Create Deburr gate model", create_model)

    def create_job():
        job = PathJob.Create("DeburrJob", [plate], templateFile=None)
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

    job = _commit(document, "Create Deburr gate Job", create_job)
    return plate, job, job.Tools.Group[0]


def _feature_names(plate) -> tuple[str, str, str, str]:
    top_face = next(
        f"Face{index}"
        for index, face in enumerate(plate.Shape.Faces, start=1)
        if face.BoundBox.ZLength <= 1.0e-7
        and face.BoundBox.ZMin >= 8.0 - 1.0e-7
        and face.normalAt(0.0, 0.0).z > 0.999999
    )
    bottom_face = next(
        f"Face{index}"
        for index, face in enumerate(plate.Shape.Faces, start=1)
        if face.BoundBox.ZLength <= 1.0e-7
        and face.BoundBox.ZMax <= 1.0e-7
        and face.normalAt(0.0, 0.0).z < -0.999999
    )
    top_edge = next(
        f"Edge{index}"
        for index, edge in enumerate(plate.Shape.Edges, start=1)
        if edge.BoundBox.ZLength <= 1.0e-7 and edge.BoundBox.ZMin >= 8.0 - 1.0e-7
    )
    vertical_edge = next(
        f"Edge{index}"
        for index, edge in enumerate(plate.Shape.Edges, start=1)
        if edge.BoundBox.ZLength >= 8.0 - 1.0e-7
    )
    return top_face, bottom_face, top_edge, vertical_edge


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


def _resource(job, source):
    matches = tuple(
        value for value in job.Model.Group if job.Proxy.baseObject(job, value) is source
    )
    assert len(matches) == 1, matches
    return matches[0]


def _model_target(state: dict, source) -> dict:
    return _target(
        next(item for item in state["models"] if item["object_name"] == source.Name)
    )


def _controller_target(state: dict, controller) -> dict:
    return _target(
        next(item for item in state["tools"] if item["object_name"] == controller.Name)
    )


def _arguments(
    job,
    controller,
    source,
    *,
    label: str,
    features: list[str],
    width_mm: float,
    extra_depth_mm: float,
    direction: str,
    step_down_mm: float,
    safe_height_mm: float,
    linking: str,
    coolant: str,
) -> dict:
    state = job_state(job)
    return {
        "operation": "deburr",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": {
            "kind": "features",
            "items": [
                {
                    "model": _model_target(state, source),
                    "features": features,
                }
            ],
        },
        "deburr": {
            "width_mm": width_mm,
            "extra_depth_mm": extra_depth_mm,
            "direction": direction,
        },
        "depths": {"step_down_mm": step_down_mm},
        "heights": {
            "safe_height_mm": safe_height_mm,
            "clearance_height_mm": 13.0,
        },
        "linking": {
            "strategy": linking,
            "collision_clearance_mm": 0.4,
        },
        "coolant": coolant,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("deburr",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "features",
        "width_mm",
        "extra_depth_mm",
        "direction",
        "step_down_mm",
        "collision_clearance_mm",
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


def _assert_operation(
    document,
    job,
    operation,
    *,
    label: str,
    base: tuple,
    width_mm: float,
    extra_depth_mm: float,
    direction: str,
    step_down_mm: float,
    linking: str,
    coolant: str,
    diagnostics: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert tuple(operation.Base) == base
    assert operation.Label == label
    assert round(operation.Width.getValueAs("mm"), 7) == width_mm
    assert round(operation.ExtraDepth.getValueAs("mm"), 7) == extra_depth_mm
    assert operation.Join == "Round"
    assert operation.Direction == direction
    assert operation.Side in {"Outside", "Inside"}
    assert operation.EntryPoint == 0
    assert round(operation.StepDown.getValueAs("mm"), 7) == step_down_mm
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == 10.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == 13.0
    assert operation.CollisionAvoidanceStrategy == linking
    assert operation.CoolantMode == coolant
    assert any(
        command.Name in {"G1", "G2", "G3"} for command in operation.Path.Commands
    )
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    if diagnostics:
        assert tuple(operation.Proxy.basewires)
        assert tuple(operation.Proxy.adjusted_basewires)
        assert tuple(operation.Proxy.wires)
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-deburr-")
        save_path = Path(temporary.name) / "native-manufacture-deburr.FCStd"
        document = App.newDocument("NativeManufactureDeburrGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Deburr"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.deburr",
            "deburr",
            "ExactCamJobDeburrFeaturesControllerAndParameters",
            True,
            False,
        )
        plate, job, controller = _create_fixture(document)
        resource = _resource(job, plate)
        top_face, bottom_face, top_edge, vertical_edge = _feature_names(plate)
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-deburr-gui")

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
                f"native-manufacture-deburr-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(plate, top_edge)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        edge_arguments = _arguments(
            job,
            controller,
            plate,
            label="Native edge Deburr",
            features=[top_edge],
            width_mm=0.6,
            extra_depth_mm=0.3,
            direction="clockwise",
            step_down_mm=0.15,
            safe_height_mm=10.0,
            linking="tool_diameter",
            coolant="mist",
        )

        stale = json.loads(json.dumps(edge_arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        vertical = json.loads(json.dumps(edge_arguments))
        vertical["geometry"]["items"][0]["features"] = [vertical_edge]
        vertical_result = call(vertical, succeeds=False)
        assert vertical_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "horizontal" in vertical_result["error"]

        downward = json.loads(json.dumps(edge_arguments))
        downward["geometry"]["items"][0]["features"] = [bottom_face]
        downward_result = call(downward, succeeds=False)
        assert downward_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "does not face upward" in downward_result["error"]

        oversized = json.loads(json.dumps(edge_arguments))
        oversized["deburr"]["width_mm"] = 3.0
        oversized_result = call(oversized, succeeds=False)
        assert oversized_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "cutting radius" in oversized_result["error"]

        unsafe = json.loads(json.dumps(edge_arguments))
        unsafe["heights"]["safe_height_mm"] = 7.0
        unsafe_result = call(unsafe, succeeds=False)
        assert unsafe_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "highest selected feature" in unsafe_result["error"]
        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        edge_result = call(edge_arguments)
        _events(12)
        edge_name = edge_result["deburr"]["object_name"]
        edge_operation = document.getObject(edge_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Deburr",
            base=((resource, (top_edge,)),),
            width_mm=0.6,
            extra_depth_mm=0.3,
            direction="CW",
            step_down_mm=0.15,
            linking="Tool Diameter",
            coolant="Mist",
        )
        assert edge_result["deburr"]["features"] == {
            "feature_count": 1,
            "edge_count": 1,
            "face_count": 0,
        }
        edge_state = operation_state(edge_operation)

        face_result = call(
            _arguments(
                job,
                controller,
                plate,
                label="Native face Deburr",
                features=[top_face],
                width_mm=0.8,
                extra_depth_mm=0.4,
                direction="counterclockwise",
                step_down_mm=0.0,
                safe_height_mm=10.0,
                linking="retract_height",
                coolant="flood",
            )
        )
        _events(12)
        face_name = face_result["deburr"]["object_name"]
        face_operation = document.getObject(face_name)
        _assert_operation(
            document,
            job,
            face_operation,
            label="Native face Deburr",
            base=((resource, (top_face,)),),
            width_mm=0.8,
            extra_depth_mm=0.4,
            direction="CCW",
            step_down_mm=0.0,
            linking="Retract Height",
            coolant="Flood",
        )
        assert face_result["deburr"]["features"] == {
            "feature_count": 1,
            "edge_count": 0,
            "face_count": 1,
        }
        assert face_result["deburr"]["path_wire_count"] == 1
        assert face_result["job"]["operation_count"] == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == revision_before + 2
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        face_state = operation_state(face_operation)

        for name in (face_name, edge_name):
            document.undo()
            _events(12)
            assert document.getObject(name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        document.redo()
        _events(12)
        document.redo()
        _events(12)

        plate = document.getObject("DeburrGatePlate")
        job = document.getObject("DeburrJob")
        resource = _resource(job, plate)
        edge_operation = document.getObject(edge_name)
        face_operation = document.getObject(face_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Deburr",
            base=((resource, (top_edge,)),),
            width_mm=0.6,
            extra_depth_mm=0.3,
            direction="CW",
            step_down_mm=0.15,
            linking="Tool Diameter",
            coolant="Mist",
        )
        _assert_operation(
            document,
            job,
            face_operation,
            label="Native face Deburr",
            base=((resource, (top_face,)),),
            width_mm=0.8,
            extra_depth_mm=0.4,
            direction="CCW",
            step_down_mm=0.0,
            linking="Retract Height",
            coolant="Flood",
        )
        assert (
            operation_state(edge_operation)["state_sha256"]
            == edge_state["state_sha256"]
        )
        assert (
            operation_state(face_operation)["state_sha256"]
            == face_state["state_sha256"]
        )

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        plate = document.getObject("DeburrGatePlate")
        job = document.getObject("DeburrJob")
        resource = _resource(job, plate)
        edge_operation = document.getObject(edge_name)
        face_operation = document.getObject(face_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Deburr",
            base=((resource, (top_edge,)),),
            width_mm=0.6,
            extra_depth_mm=0.3,
            direction="CW",
            step_down_mm=0.15,
            linking="Tool Diameter",
            coolant="Mist",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            face_operation,
            label="Native face Deburr",
            base=((resource, (top_face,)),),
            width_mm=0.8,
            extra_depth_mm=0.4,
            direction="CCW",
            step_down_mm=0.0,
            linking="Retract Height",
            coolant="Flood",
            diagnostics=False,
        )
        assert (
            operation_state(edge_operation)["state_sha256"]
            == edge_state["state_sha256"]
        )
        assert (
            operation_state(face_operation)["state_sha256"]
            == face_state["state_sha256"]
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_DEBURR_GUI_OK exact_targets=true "
            "edges=true faces=true cutter_capacity=true parameters=true "
            "linking=true coolant=true toolpath=true history=true rollback=true "
            "sources_preserved=true undo=true redo=true reopen=true",
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
