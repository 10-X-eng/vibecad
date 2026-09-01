# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM V-carve."""

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
import Path.Tool.Controller as PathToolController
from Path.Tool.toolbit import ToolBit
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


def _rectangle_face(x: float, z: float):
    points = (
        App.Vector(x, 5.0, z),
        App.Vector(x + 24.0, 5.0, z),
        App.Vector(x + 24.0, 23.0, z),
        App.Vector(x, 23.0, z),
    )
    return Part.Face(Part.makePolygon((*points, points[0])))


def _create_fixture(document):
    def create_models():
        plate = document.addObject("Part::Feature", "VCarveGatePlate")
        plate.Label = "V-carve gate plate"
        plate.Shape = Part.makeBox(48.0, 32.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(plate, (), ())

        guide = document.addObject("Part::Feature", "VCarveGateGuide")
        guide.Label = "V-carve gate face guide"
        guide.Shape = _rectangle_face(60.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(guide, (), ())

        raised = document.addObject("Part::Feature", "VCarveGateRaisedGuide")
        raised.Label = "V-carve noncoplanar gate face"
        raised.Shape = _rectangle_face(92.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(raised, (), ())
        return plate, guide, raised

    plate, guide, raised = _commit(
        document, "Create V-carve gate models", create_models
    )

    def create_job():
        job = PathJob.Create("VCarveJob", [plate, guide, raised], templateFile=None)
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

    job = _commit(document, "Create V-carve gate Job", create_job)

    def create_v_bit_controller():
        extension = PathUtil.stageTimelineResourceGraphExtension(job)
        toolbit = ToolBit.from_file(
            Path(App.getResourceDir()).parent
            / "Mod"
            / "CAM"
            / "Tools"
            / "Bit"
            / "90degree_Vbit.fctb"
        )
        tool = toolbit.attach_to_doc(doc=document, timeline_owner=job)
        controller = PathToolController.Create(
            name="VBitController",
            tool=tool,
            toolNumber=max(int(value.ToolNumber) for value in job.Tools.Group) + 1,
            document=document,
            timelineOwner=job,
        )
        controller.Label = "90 degree V-bit controller"
        controller.SpindleSpeed = 12000
        controller.VertFeed = "300 mm/min"
        controller.HorizFeed = "500 mm/min"
        controller.VertRapid = "1200 mm/min"
        controller.HorizRapid = "1200 mm/min"
        job.Proxy.addToolController(controller)
        assert document.recompute(None, True, True) is not False
        PathUtil.finalizeTimelineResourceGraphExtension(
            job,
            extension,
            PathUtil.toolControllerResourceGraph(controller),
        )
        return controller

    controller = _commit(
        document,
        "Create V-carve gate controller",
        create_v_bit_controller,
    )
    return plate, guide, raised, job, controller, job.Tools.Group[0]


def _face_names(plate) -> tuple[str, str]:
    top = next(
        f"Face{index}"
        for index, face in enumerate(plate.Shape.Faces, start=1)
        if face.BoundBox.ZLength <= 1.0e-7
        and face.BoundBox.ZMin >= 8.0 - 1.0e-7
        and face.normalAt(0.0, 0.0).z > 0.999999
    )
    vertical = next(
        f"Face{index}"
        for index, face in enumerate(plate.Shape.Faces, start=1)
        if face.BoundBox.ZLength >= 8.0 - 1.0e-7
    )
    return top, vertical


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
    *,
    label: str,
    geometry: dict,
    deflection_mm: float,
    colinear_degrees: float,
    optimize: bool,
    finishing: dict,
    final_depth_mm: float,
    step_down_mm: float,
    coolant: str,
) -> dict:
    state = job_state(job)
    return {
        "operation": "v_carve",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": geometry,
        "v_carve": {
            "discretization_deflection_mm": deflection_mm,
            "colinear_filter_degrees": colinear_degrees,
            "optimize_movements": optimize,
            "finishing": finishing,
        },
        "depths": {
            "final_depth_mm": final_depth_mm,
            "step_down_mm": step_down_mm,
        },
        "heights": {"safe_height_mm": 12.0, "clearance_height_mm": 15.0},
        "coolant": coolant,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("v_carve",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "whole_models",
        "faces",
        "discretization_deflection_mm",
        "colinear_filter_degrees",
        "optimize_movements",
        "finishing",
        "z_offset_mm",
        "final_depth_mm",
        "step_down_mm",
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
    controller,
    label: str,
    base: tuple,
    base_shapes: tuple,
    deflection_mm: float,
    colinear_degrees: float,
    optimize: bool,
    finishing: bool,
    finishing_offset_mm: float,
    final_depth_mm: float,
    step_down_mm: float,
    coolant: str,
    diagnostics: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController is controller
    assert tuple(operation.Base) == base
    assert tuple(operation.BaseShapes) == base_shapes
    assert operation.Label == label
    assert round(float(operation.Discretize), 7) == deflection_mm
    assert round(float(operation.Colinear), 7) == colinear_degrees
    assert operation.OptimizeMovements is optimize
    assert operation.FinishingPass is finishing
    assert round(operation.FinishingPassZOffset.getValueAs("mm"), 7) == (
        finishing_offset_mm
    )
    assert round(operation.StartDepth.getValueAs("mm"), 7) == 8.0
    assert round(operation.FinalDepth.getValueAs("mm"), 7) == final_depth_mm
    assert round(operation.StepDown.getValueAs("mm"), 7) == step_down_mm
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == 12.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == 15.0
    assert operation.CoolantMode == coolant
    assert tuple(round(float(value), 7) for value in operation.Workplane) == (
        0.0,
        0.0,
        1.0,
    )
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    assert any(
        command.Name in {"G1", "G2", "G3"} for command in operation.Path.Commands
    )
    if diagnostics:
        assert len(operation.Proxy.voronoiDebugMedialCache) == 1
        assert len(operation.Proxy.voronoiDebugEdgeCache) == 1
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-v-carve-")
        save_path = Path(temporary.name) / "native-manufacture-v-carve.FCStd"
        document = App.newDocument("NativeManufactureVCarveGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Vcarve"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.v_carve",
            "v_carve",
            "ExactCamJobVCarveFacesControllerAndParameters",
            True,
            False,
        )

        plate, guide, raised, job, controller, default_controller = _create_fixture(
            document
        )
        plate_resource = _resource(job, plate)
        guide_resource = _resource(job, guide)
        top_face, vertical_face = _face_names(plate)
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        source_hashes = {
            source.Name: shape_sha256(source.Shape, source.Name)
            for source in (plate, guide, raised)
        }
        source_signatures = {
            source.Name: _shape_signature(source.Shape)
            for source in (plate, guide, raised)
        }

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-v-carve-gui")

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
                f"native-manufacture-v-carve-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(plate, top_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        state = job_state(job)
        face_arguments = _arguments(
            job,
            controller,
            label="Native face V-carve",
            geometry={
                "kind": "faces",
                "items": [
                    {
                        "model": _model_target(state, plate),
                        "faces": [top_face],
                    }
                ],
            },
            deflection_mm=0.2,
            colinear_degrees=10.0,
            optimize=True,
            finishing={"enabled": True, "z_offset_mm": -0.05},
            final_depth_mm=4.0,
            step_down_mm=0.75,
            coolant="mist",
        )

        stale = json.loads(json.dumps(face_arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        assert call(stale, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        vertical = json.loads(json.dumps(face_arguments))
        vertical["geometry"]["items"][0]["faces"] = [vertical_face]
        vertical_result = call(vertical, succeeds=False)
        assert vertical_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "planar XY Face" in vertical_result["error"]

        wrong_tool = json.loads(json.dumps(face_arguments))
        wrong_tool["tool_controller"] = _controller_target(state, default_controller)
        wrong_tool_result = call(wrong_tool, succeeds=False)
        assert wrong_tool_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "V-bit" in wrong_tool_result["error"]

        whole_solid = _arguments(
            job,
            controller,
            label="Invalid solid V-carve",
            geometry={
                "kind": "whole_models",
                "models": [_model_target(state, plate)],
            },
            deflection_mm=0.2,
            colinear_degrees=10.0,
            optimize=False,
            finishing={"enabled": False},
            final_depth_mm=6.0,
            step_down_mm=0.0,
            coolant="none",
        )
        whole_solid_result = call(whole_solid, succeeds=False)
        assert whole_solid_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "zero-volume" in whole_solid_result["error"]

        noncoplanar = json.loads(json.dumps(face_arguments))
        noncoplanar["geometry"]["items"].append(
            {
                "model": _model_target(state, raised),
                "faces": ["Face1"],
            }
        )
        noncoplanar_result = call(noncoplanar, succeeds=False)
        assert noncoplanar_result["error_code"] == (
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        )
        assert "coplanar" in noncoplanar_result["error"]

        unsafe_depth = json.loads(json.dumps(face_arguments))
        unsafe_depth["depths"]["final_depth_mm"] = 8.0
        depth_result = call(unsafe_depth, succeeds=False)
        assert depth_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "below the selected Face plane" in depth_result["error"]
        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        face_result = call(face_arguments)
        _events(12)
        face_name = face_result["v_carve"]["object_name"]
        face_operation = document.getObject(face_name)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            label="Native face V-carve",
            base=((plate_resource, (top_face,)),),
            base_shapes=(),
            deflection_mm=0.2,
            colinear_degrees=10.0,
            optimize=True,
            finishing=True,
            finishing_offset_mm=-0.05,
            final_depth_mm=4.0,
            step_down_mm=0.75,
            coolant="Mist",
        )
        assert face_result["v_carve"]["face_count"] == 1
        assert face_result["v_carve"]["boundary_wire_count"] == 1
        assert face_result["v_carve"]["surface_z_mm"] == 8.0
        assert face_result["v_carve"]["tool"] == {
            "diameter_mm": 10.0,
            "tip_diameter_mm": 0.1,
            "cutting_edge_angle_degrees": 90.0,
            "maximum_carve_depth_mm": 4.95,
            "effective_final_depth_mm": 4.0,
        }
        face_state = operation_state(face_operation)

        state = job_state(job)
        whole_result = call(
            _arguments(
                job,
                controller,
                label="Native whole-model V-carve",
                geometry={
                    "kind": "whole_models",
                    "models": [_model_target(state, guide)],
                },
                deflection_mm=0.15,
                colinear_degrees=5.0,
                optimize=False,
                finishing={"enabled": False},
                final_depth_mm=6.0,
                step_down_mm=0.0,
                coolant="flood",
            )
        )
        _events(12)
        whole_name = whole_result["v_carve"]["object_name"]
        whole_operation = document.getObject(whole_name)
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            label="Native whole-model V-carve",
            base=(),
            base_shapes=(guide_resource,),
            deflection_mm=0.15,
            colinear_degrees=5.0,
            optimize=False,
            finishing=False,
            finishing_offset_mm=0.0,
            final_depth_mm=6.0,
            step_down_mm=0.0,
            coolant="Flood",
        )
        assert whole_result["v_carve"]["geometry"] == {
            "kind": "whole_models",
            "model_names": [guide.Name],
        }
        assert whole_result["v_carve"]["minimum_cutting_z_mm"] < 8.0
        assert whole_result["job"]["operation_count"] == len(initial_operations) + 2
        assert int(document.UndoCount) == undo_before + 2
        assert state_store.current_revision(context.document_uid) == revision_before + 2
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        assert {
            source.Name: shape_sha256(source.Shape, source.Name)
            for source in (plate, guide, raised)
        } == source_hashes
        whole_state = operation_state(whole_operation)

        for name in (whole_name, face_name):
            document.undo()
            _events(12)
            assert document.getObject(name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        document.redo()
        _events(12)
        document.redo()
        _events(12)

        plate = document.getObject("VCarveGatePlate")
        guide = document.getObject("VCarveGateGuide")
        raised = document.getObject("VCarveGateRaisedGuide")
        job = document.getObject("VCarveJob")
        controller = document.getObject("VBitController")
        plate_resource = _resource(job, plate)
        guide_resource = _resource(job, guide)
        face_operation = document.getObject(face_name)
        whole_operation = document.getObject(whole_name)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            label="Native face V-carve",
            base=((plate_resource, (top_face,)),),
            base_shapes=(),
            deflection_mm=0.2,
            colinear_degrees=10.0,
            optimize=True,
            finishing=True,
            finishing_offset_mm=-0.05,
            final_depth_mm=4.0,
            step_down_mm=0.75,
            coolant="Mist",
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            label="Native whole-model V-carve",
            base=(),
            base_shapes=(guide_resource,),
            deflection_mm=0.15,
            colinear_degrees=5.0,
            optimize=False,
            finishing=False,
            finishing_offset_mm=0.0,
            final_depth_mm=6.0,
            step_down_mm=0.0,
            coolant="Flood",
        )
        assert (
            operation_state(face_operation)["state_sha256"]
            == (face_state["state_sha256"])
        )
        assert (
            operation_state(whole_operation)["state_sha256"]
            == (whole_state["state_sha256"])
        )

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        plate = document.getObject("VCarveGatePlate")
        guide = document.getObject("VCarveGateGuide")
        raised = document.getObject("VCarveGateRaisedGuide")
        job = document.getObject("VCarveJob")
        controller = document.getObject("VBitController")
        plate_resource = _resource(job, plate)
        guide_resource = _resource(job, guide)
        face_operation = document.getObject(face_name)
        whole_operation = document.getObject(whole_name)
        _assert_operation(
            document,
            job,
            face_operation,
            controller=controller,
            label="Native face V-carve",
            base=((plate_resource, (top_face,)),),
            base_shapes=(),
            deflection_mm=0.2,
            colinear_degrees=10.0,
            optimize=True,
            finishing=True,
            finishing_offset_mm=-0.05,
            final_depth_mm=4.0,
            step_down_mm=0.75,
            coolant="Mist",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            controller=controller,
            label="Native whole-model V-carve",
            base=(),
            base_shapes=(guide_resource,),
            deflection_mm=0.15,
            colinear_degrees=5.0,
            optimize=False,
            finishing=False,
            finishing_offset_mm=0.0,
            final_depth_mm=6.0,
            step_down_mm=0.0,
            coolant="Flood",
            diagnostics=False,
        )
        assert (
            operation_state(face_operation)["state_sha256"]
            == (face_state["state_sha256"])
        )
        assert (
            operation_state(whole_operation)["state_sha256"]
            == (whole_state["state_sha256"])
        )
        assert {
            source.Name: _shape_signature(source.Shape)
            for source in (plate, guide, raised)
        } == source_signatures

        print(
            "VIBECAD_NATIVE_MANUFACTURE_V_CARVE_GUI_OK exact_targets=true "
            "faces=true whole_models=true v_bit=true parameters=true "
            "toolpath=true history=true rollback=true sources_preserved=true "
            "undo=true redo=true reopen=true",
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
