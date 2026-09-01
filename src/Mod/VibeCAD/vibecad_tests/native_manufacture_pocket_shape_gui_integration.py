# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Pocket Shape."""

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
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedOperationSchema import (
    MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES["pocket_shape"]


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _await_job(
    manager: NativeBackgroundManager,
    job_id: str,
    *,
    timeout: float = 120.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            assert snapshot.phase == "completed", snapshot
            assert snapshot.result is not None
            return snapshot.result
        time.sleep(0.01)
    raise AssertionError("The isolated CAM path gate timed out")


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


def _create_bracket_model_and_job(document):
    def create_model():
        shape = Part.makeBox(60.0, 40.0, 12.0).cut(
            Part.makeBox(32.0, 18.0, 7.0, App.Vector(14.0, 11.0, 6.0))
        )
        for x_mm, y_mm in ((8.0, 8.0), (52.0, 8.0), (8.0, 32.0), (52.0, 32.0)):
            shape = shape.cut(
                Part.makeCylinder(2.5, 14.0, App.Vector(x_mm, y_mm, -1.0))
            )
        model = document.addObject("Part::Feature", "BracketModel")
        model.Label = "Three-axis bracket model"
        model.Shape = shape.removeSplitter()
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create bracket model", create_model)

    def create_job():
        job = PathJob.Create("BracketJob", [model], templateFile=None)
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

    return model, _commit(document, "Create bracket Job", create_job)


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


def _horizontal_face_at(model, z_mm: float) -> str:
    for face_index, face in enumerate(model.Shape.Faces, start=1):
        if face.Vertexes and all(
            abs(float(vertex.Point.z) - z_mm) <= 1e-9
            for vertex in face.Vertexes
        ):
            return f"Face{face_index}"
    raise AssertionError(f"The model has no horizontal face at Z={z_mm}")


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("pocket_shape",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    branch = schema["parameters"]["oneOf"][0]
    assert set(branch["properties"]) == {
        "operation",
        "label",
        "job",
        "tool_controller",
        "geometry",
        "coolant",
    }
    assert set(branch["required"]) == {"job", "tool_controller", "geometry"}
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


def _arguments(model, job, face_name: str) -> dict:
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


def _assert_pocket_graph(
    document,
    job,
    operation,
    model,
    face_name: str,
    *,
    diagnostics_required: bool = True,
    timeline_last: bool = True,
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
    assert operation.Label
    assert operation.CutMode in {"Climb", "Conventional"}
    assert operation.ClearingPattern in {
        "Offset",
        "ZigZag",
        "ZigZagOffset",
        "Line",
        "Grid",
    }
    assert 1 <= int(operation.StepOver) <= 100
    expressions = {str(name) for name, _expression in operation.ExpressionEngine}
    assert {
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    } <= expressions
    if timeline_last:
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
        VibeGui._ensure_document_thread_invoker()
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
            CAPABILITY_NAME,
            "pocket_shape",
            "ExactCamJobPocketGeometryAndController",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        bracket, bracket_job = _create_bracket_model_and_job(document)
        bracket_face = _horizontal_face_at(bracket, 6.0)
        bracket_shape_before = bracket.Shape.exportBrepToString()
        face_name, _edge_name = _top_face_and_edge(model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        arguments = _arguments(model, job, face_name)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        background = service.native_background_manager()
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
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-pocket-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        def submit(payload: dict, expected_scope: str) -> dict:
            started = time.monotonic()
            accepted = call(payload)
            assert time.monotonic() - started < 2.0, accepted
            background_job = accepted["job"]
            assert background_job["resource_scope"] == expected_scope
            assert accepted["next"]["tool"] == "native.job"
            active_jobs = service.native_active_snapshot()["domain"]["background_jobs"]
            assert any(
                item["job_id"] == background_job["job_id"]
                and item["resource_scope"] == expected_scope
                and item["terminal"] is False
                for item in active_jobs
            )
            return background_job

        def generate(
            payload: dict,
            expected_scope: str,
            *,
            while_running=None,
        ) -> dict:
            background_job = submit(payload, expected_scope)
            if while_running is not None:
                while_running()
            return _await_job(background, background_job["job_id"])

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(arguments))
        stale["job"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert stale_result["repair"]["target"] == _target(job_state(job))
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before
        assert _selection() == selection_before

        heartbeat_count = 0

        def heartbeat() -> None:
            nonlocal heartbeat_count
            heartbeat_count += 1

        heartbeat_timer = QtCore.QTimer()
        heartbeat_timer.setInterval(10)
        heartbeat_timer.timeout.connect(heartbeat)
        heartbeat_timer.start()
        selection_during_generation = ()

        def change_selection_while_running() -> None:
            nonlocal selection_during_generation
            Gui.Selection.clearSelection()
            selection_during_generation = _selection()

        result = generate(
            arguments,
            f"manufacture:{job.Name}",
            while_running=change_selection_while_running,
        )
        heartbeat_timer.stop()
        assert heartbeat_count > 0
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
        )
        assert result["pocket_shape"]["geometry"] == {
            "kind": "subelements",
            "items": [{"object_name": model.Name, "subelements": [face_name]}],
        }
        assert result["pocket_shape"]["parameters"]["source"] == "setup_defaults"
        assert result["pocket_shape"]["cutting_command_count"] >= 1
        assert result["job"]["operation_count"] == len(initial_operations) + 1
        assert [item["object_name"] for item in result["receipt"]["created"]] == [
            operation_name
        ]
        assert result["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 1
        assert state_store.current_revision(context.document_uid) == revision_before + 1
        assert _selection() == selection_during_generation
        assert not Gui.Control.activeDialog()
        created_state = operation_state(operation)

        bracket_arguments = _arguments(bracket, bracket_job, bracket_face)
        bracket_arguments["label"] = "Top pocket"
        bracket_result = generate(
            bracket_arguments,
            f"manufacture:{bracket_job.Name}",
        )
        bracket_operation_name = bracket_result["pocket_shape"]["object_name"]
        assert document.getObject(bracket_operation_name).Label == "Top pocket"
        assert bracket_result["pocket_shape"]["geometry"] == {
            "kind": "subelements",
            "items": [
                {"object_name": bracket.Name, "subelements": [bracket_face]}
            ],
        }
        assert bracket.Shape.exportBrepToString() == bracket_shape_before

        parallel_arguments = _arguments(model, job, face_name)
        parallel_arguments["label"] = "Parallel first setup"
        parallel_bracket_arguments = _arguments(bracket, bracket_job, bracket_face)
        parallel_bracket_arguments["label"] = "Parallel second setup"
        first_parallel_job = submit(
            parallel_arguments,
            f"manufacture:{job.Name}",
        )
        second_parallel_job = submit(
            parallel_bracket_arguments,
            f"manufacture:{bracket_job.Name}",
        )
        active_job_ids = {
            item["job_id"]
            for item in service.native_active_snapshot()["domain"]["background_jobs"]
        }
        assert {
            first_parallel_job["job_id"],
            second_parallel_job["job_id"],
        } <= active_job_ids
        first_parallel_result = _await_job(
            background,
            first_parallel_job["job_id"],
        )
        second_parallel_result = _await_job(
            background,
            second_parallel_job["job_id"],
        )
        first_parallel_name = first_parallel_result["pocket_shape"]["object_name"]
        second_parallel_name = second_parallel_result["pocket_shape"]["object_name"]
        assert document.getObject(first_parallel_name) in tuple(job.Operations.Group)
        assert document.getObject(second_parallel_name) in tuple(
            bracket_job.Operations.Group
        )

        document.undo()
        document.undo()
        _events(12)
        assert document.getObject(first_parallel_name) is None
        assert document.getObject(second_parallel_name) is None
        assert document.getObject(bracket_operation_name) is not None
        assert document.getObject(operation_name) is operation

        document.undo()
        _events(12)
        assert document.getObject(bracket_operation_name) is None
        assert document.getObject(operation_name) is operation

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
        )
        reopened_state = operation_state(operation)
        assert reopened_state["state_sha256"] == created_state["state_sha256"], {
            key: (created_state.get(key), reopened_state.get(key))
            for key in set(created_state) | set(reopened_state)
            if created_state.get(key) != reopened_state.get(key)
        }

        document.redo()
        _events(12)
        assert document.getObject(bracket_operation_name) is not None

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
            diagnostics_required=False,
            timeline_last=False,
        )
        reopened_state = operation_state(operation)
        assert reopened_state["state_sha256"] == created_state["state_sha256"], {
            key: (created_state.get(key), reopened_state.get(key))
            for key in set(created_state) | set(reopened_state)
            if created_state.get(key) != reopened_state.get(key)
        }

        print(
            "VIBECAD_NATIVE_MANUFACTURE_POCKET_SHAPE_GUI_OK "
            "exact_targets=true geometry=true extensions=true parameters=true "
            "toolpath=true recessed_pocket=true multi_setup=true parallel_setups=true "
            "repair_target=true "
            "history=true rollback=true undo=true redo=true reopen=true",
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
