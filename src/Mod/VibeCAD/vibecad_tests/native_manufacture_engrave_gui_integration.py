# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Engrave."""

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
    def create_models():
        solid = document.addObject("Part::Feature", "EngraveGatePlate")
        solid.Label = "Engrave gate plate"
        solid.Shape = Part.makeBox(50.0, 36.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(solid, (), ())
        guide = document.addObject("Part::Feature", "EngraveGateGuide")
        guide.Label = "Engrave gate wire guide"
        points = (
            App.Vector(10.0, 10.0, 8.0),
            App.Vector(40.0, 10.0, 8.0),
            App.Vector(40.0, 26.0, 8.0),
            App.Vector(10.0, 26.0, 8.0),
        )
        guide.Shape = Part.Wire(
            [
                Part.makeLine(points[index], points[(index + 1) % len(points)])
                for index in range(len(points))
            ]
        )
        document.publishProvisionalTimelineOperationBlock(guide, (), ())
        return solid, guide

    solid, guide = _commit(document, "Create Engrave gate models", create_models)

    def create_job():
        job = PathJob.Create("EngraveJob", [solid, guide], templateFile=None)
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

    job = _commit(document, "Create Engrave gate Job", create_job)
    return solid, guide, job, job.Tools.Group[0]


def _top_edges(solid) -> tuple[str, str]:
    names = []
    for index, edge in enumerate(solid.Shape.Edges, start=1):
        if edge.Vertexes and all(
            round(float(vertex.Point.z), 7) == 8.0 for vertex in edge.Vertexes
        ):
            names.append(f"Edge{index}")
    assert len(names) == 4, names
    return names[0], names[1]


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
    *,
    label: str,
    geometry: dict,
    start_vertex: int,
    linking: str,
    coolant: str,
) -> dict:
    state = job_state(job)
    return {
        "operation": "engrave",
        "label": label,
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": geometry,
        "engrave": {"start_vertex": start_vertex},
        "depths": {
            "start_depth_mm": 8.0,
            "final_depth_mm": 7.0,
            "step_down_mm": 0.5,
        },
        "heights": {"safe_height_mm": 10.0, "clearance_height_mm": 13.0},
        "linking": {"strategy": linking, "collision_clearance_mm": 0.4},
        "coolant": coolant,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("engrave",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "entire_job",
        "whole_models",
        "edges",
        "start_vertex",
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
    base_shapes: tuple,
    start_vertex: int,
    linking: str,
    coolant: str,
    diagnostics: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert tuple(operation.Base) == base
    assert tuple(operation.BaseShapes) == base_shapes
    assert operation.Label == label
    assert operation.StartVertex == start_vertex
    assert operation.Reverse is False
    assert operation.CutPattern == "Bidirectional"
    assert operation.Approximation is False
    assert operation.SortingMode == "Automatic"
    assert operation.UseEndPoint is False
    assert round(operation.StartDepth.getValueAs("mm"), 7) == 8.0
    assert round(operation.FinalDepth.getValueAs("mm"), 7) == 7.0
    assert round(operation.StepDown.getValueAs("mm"), 7) == 0.5
    assert round(operation.SafeHeight.getValueAs("mm"), 7) == 10.0
    assert round(operation.ClearanceHeight.getValueAs("mm"), 7) == 13.0
    assert operation.CollisionAvoidanceStrategy == linking
    assert operation.CoolantMode == coolant
    assert any(
        command.Name in {"G1", "G2", "G3"} for command in operation.Path.Commands
    )
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-engrave-")
        save_path = Path(temporary.name) / "native-manufacture-engrave.FCStd"
        document = App.newDocument("NativeManufactureEngraveGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plan = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
        }["CAM_Engrave"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            "manufacture.engrave",
            "engrave",
            "ExactCamJobEngraveGeometryControllerAndParameters",
            True,
            False,
        )
        solid, guide, job, controller = _create_fixture(document)
        solid_resource = _resource(job, solid)
        guide_resource = _resource(job, guide)
        edge_a, edge_b = _top_edges(solid)
        initial_names = tuple(item.Name for item in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-engrave-gui")

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
                f"native-manufacture-engrave-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(solid, edge_a)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        state = job_state(job)
        edge_arguments = _arguments(
            job,
            controller,
            label="Native edge Engrave",
            geometry={
                "kind": "edges",
                "items": [
                    {
                        "model": _model_target(state, solid),
                        "edges": [edge_a, edge_b],
                    }
                ],
            },
            start_vertex=0,
            linking="tool_diameter",
            coolant="mist",
        )

        stale = json.loads(json.dumps(edge_arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        invalid_whole = _arguments(
            job,
            controller,
            label="Invalid solid Engrave",
            geometry={"kind": "whole_models", "models": [_model_target(state, solid)]},
            start_vertex=0,
            linking="clearance_height",
            coolant="none",
        )
        invalid_result = call(invalid_whole, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
        assert "zero-volume" in invalid_result["error"]

        invalid_vertex = _arguments(
            job,
            controller,
            label="Invalid start Engrave",
            geometry={"kind": "whole_models", "models": [_model_target(state, guide)]},
            start_vertex=4,
            linking="clearance_height",
            coolant="none",
        )
        vertex_result = call(invalid_vertex, succeeds=False)
        assert vertex_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert "outside" in vertex_result["error"]
        assert tuple(item.Name for item in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        edge_result = call(edge_arguments)
        _events(12)
        edge_name = edge_result["engrave"]["object_name"]
        edge_operation = document.getObject(edge_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Engrave",
            base=((solid_resource, (edge_a, edge_b)),),
            base_shapes=(),
            start_vertex=0,
            linking="Tool Diameter",
            coolant="Mist",
        )
        assert edge_result["engrave"]["geometry"]["kind"] == "subelements"
        edge_state = operation_state(edge_operation)

        state = job_state(job)
        whole_result = call(
            _arguments(
                job,
                controller,
                label="Native whole-model Engrave",
                geometry={
                    "kind": "whole_models",
                    "models": [_model_target(state, guide)],
                },
                start_vertex=1,
                linking="retract_height",
                coolant="flood",
            )
        )
        _events(12)
        whole_name = whole_result["engrave"]["object_name"]
        whole_operation = document.getObject(whole_name)
        _assert_operation(
            document,
            job,
            whole_operation,
            label="Native whole-model Engrave",
            base=(),
            base_shapes=(guide_resource,),
            start_vertex=1,
            linking="Retract Height",
            coolant="Flood",
        )
        assert whole_result["engrave"]["geometry"] == {
            "kind": "whole_models",
            "model_names": [guide.Name],
        }
        whole_state = operation_state(whole_operation)

        entire_result = call(
            _arguments(
                job,
                controller,
                label="Native entire-Job Engrave",
                geometry={"kind": "entire_job"},
                start_vertex=0,
                linking="clearance_height",
                coolant="none",
            )
        )
        _events(12)
        entire_name = entire_result["engrave"]["object_name"]
        entire_operation = document.getObject(entire_name)
        _assert_operation(
            document,
            job,
            entire_operation,
            label="Native entire-Job Engrave",
            base=(),
            base_shapes=(),
            start_vertex=0,
            linking="Clearance Height",
            coolant="None",
        )
        assert entire_result["engrave"]["wire_count"] == 1
        assert entire_result["job"]["operation_count"] == len(initial_operations) + 3
        assert int(document.UndoCount) == undo_before + 3
        assert state_store.current_revision(context.document_uid) == revision_before + 3
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        entire_state = operation_state(entire_operation)

        for name in (entire_name, whole_name, edge_name):
            document.undo()
            _events(12)
            assert document.getObject(name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        for _index in range(3):
            document.redo()
            _events(12)

        solid = document.getObject("EngraveGatePlate")
        guide = document.getObject("EngraveGateGuide")
        job = document.getObject("EngraveJob")
        solid_resource = _resource(job, solid)
        guide_resource = _resource(job, guide)
        edge_operation = document.getObject(edge_name)
        whole_operation = document.getObject(whole_name)
        entire_operation = document.getObject(entire_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Engrave",
            base=((solid_resource, (edge_a, edge_b)),),
            base_shapes=(),
            start_vertex=0,
            linking="Tool Diameter",
            coolant="Mist",
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            label="Native whole-model Engrave",
            base=(),
            base_shapes=(guide_resource,),
            start_vertex=1,
            linking="Retract Height",
            coolant="Flood",
        )
        _assert_operation(
            document,
            job,
            entire_operation,
            label="Native entire-Job Engrave",
            base=(),
            base_shapes=(),
            start_vertex=0,
            linking="Clearance Height",
            coolant="None",
        )
        assert (
            operation_state(edge_operation)["state_sha256"]
            == edge_state["state_sha256"]
        )
        assert (
            operation_state(whole_operation)["state_sha256"]
            == whole_state["state_sha256"]
        )
        assert (
            operation_state(entire_operation)["state_sha256"]
            == entire_state["state_sha256"]
        )

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        solid = document.getObject("EngraveGatePlate")
        guide = document.getObject("EngraveGateGuide")
        job = document.getObject("EngraveJob")
        solid_resource = _resource(job, solid)
        guide_resource = _resource(job, guide)
        edge_operation = document.getObject(edge_name)
        whole_operation = document.getObject(whole_name)
        entire_operation = document.getObject(entire_name)
        _assert_operation(
            document,
            job,
            edge_operation,
            label="Native edge Engrave",
            base=((solid_resource, (edge_a, edge_b)),),
            base_shapes=(),
            start_vertex=0,
            linking="Tool Diameter",
            coolant="Mist",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            whole_operation,
            label="Native whole-model Engrave",
            base=(),
            base_shapes=(guide_resource,),
            start_vertex=1,
            linking="Retract Height",
            coolant="Flood",
            diagnostics=False,
        )
        _assert_operation(
            document,
            job,
            entire_operation,
            label="Native entire-Job Engrave",
            base=(),
            base_shapes=(),
            start_vertex=0,
            linking="Clearance Height",
            coolant="None",
            diagnostics=False,
        )
        assert (
            operation_state(edge_operation)["state_sha256"]
            == edge_state["state_sha256"]
        )
        assert (
            operation_state(whole_operation)["state_sha256"]
            == whole_state["state_sha256"]
        )
        assert (
            operation_state(entire_operation)["state_sha256"]
            == entire_state["state_sha256"]
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_ENGRAVE_GUI_OK exact_targets=true "
            "edges=true whole_models=true entire_job=true parameters=true "
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
