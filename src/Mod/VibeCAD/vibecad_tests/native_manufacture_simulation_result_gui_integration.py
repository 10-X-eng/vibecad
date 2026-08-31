# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for retained background CAM simulation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Profile as PathProfileGui
import Path.Op.Profile as PathProfile
import VibeCADGui as VibeGui
import VibeCADNativeManufactureSimulationResultRuntime as SimulationRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundManager
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureSimulationResultSchema import (
    MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
)
from VibeCADNativeManufactureFollowUpSchema import (
    MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
)
from VibeCADNativeManufactureFollowUpState import (
    setup_relationship_state,
    simulation_result_state,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _surface():
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    plan = next(
        value
        for value in resolve_native_action_inventory(surface).plans
        if value.command_id == "CAM_Simulator"
    )
    actual = (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.classification.mutation,
        plan.classification.view,
        plan.transaction_behavior,
        plan.background_required,
    )
    expected = (
        MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
        "native",
        "ExactCamJobOrderedActiveOperationsAndNativeQuality",
        True,
        False,
        "background",
        True,
    )
    assert actual == expected, (actual, expected)
    follow_up = next(
        value
        for value in resolve_native_action_inventory(surface).plans
        if value.command_id == "CAM_FollowUpSetup"
    )
    assert (
        follow_up.capability_family,
        follow_up.operation_variant,
        follow_up.exact_target_type,
        follow_up.classification.mutation,
        follow_up.transaction_behavior,
        follow_up.background_required,
    ) == (
        MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
        "create",
        "ExactCurrentRetainedStockResult",
        True,
        "background",
        True,
    )
    assert Gui.Command.get("CAM_FollowUpSetup") is not None
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    simulation = registry.definition(MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME)
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert simulation is not None and background is not None
    schema = simulation.provider_schema(("native",))
    background_schema = background.provider_schema(("status", "cancel"))
    branch = schema["parameters"]["oneOf"][0]
    assert branch["required"] == ["job", "operations", "quality"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["operation"]["type"] == "string"
    assert branch["properties"]["operation"]["const"] == "native"
    assert branch["properties"]["operations"]["maxItems"] == 64
    assert branch["properties"]["quality"]["minimum"] == 1
    assert branch["properties"]["quality"]["maximum"] == 10
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5_000
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(schema, background_schema),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _follow_up_turn(surface, registry) -> NativeTurnSnapshot:
    follow_up = registry.definition(MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME)
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert follow_up is not None and background is not None
    schema = follow_up.provider_schema(("create",))
    branch = schema["parameters"]["oneOf"][0]
    assert branch["required"] == ["remaining_stock", "label"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["operation"]["const"] == "create"
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 2_500
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                schema,
                background.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _top_face_name(model) -> str:
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for index, face in enumerate(model.Shape.Faces, start=1):
        if all(
            abs(float(vertex.Point.z) - maximum_z) <= 1.0e-9
            for vertex in face.Vertexes
        ):
            return f"Face{index}"
    raise AssertionError("The simulation fixture has no exact top face")


def _create_fixture(document, prefix: str):
    model = document.addObject("Part::Feature", f"{prefix}Model")
    model.Label = f"{prefix} model"
    model.Shape = Part.makeBox(48.0, 32.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    job.Machine = "Generic LinuxCNC Mill"
    document.openTransaction(f"Create {prefix} retained simulation Profile")
    try:
        controller = job.Tools.Group[0]
        controller.HorizFeed = 300.0
        controller.VertFeed = 120.0
        controller.HorizRapid = 1800.0
        controller.VertRapid = 900.0
        operation = PathProfile.Create(
            f"{prefix}Profile",
            parentJob=job,
            toolController=controller,
        )
        operation.Proxy.addBase(operation, model, _top_face_name(model))
        operation.Label = f"{prefix} Profile operation"
        provider = PathProfileGui.PathOpGui.ViewProvider(
            operation.ViewObject,
            PathProfileGui.Command.res,
        )
        operation.ViewObject.Proxy = provider
        provider.deleteOnReject = False
        for property_name in (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
        ):
            operation.setExpression(property_name, None)
        operation.StartDepth = 8.0
        operation.FinalDepth = 0.0
        operation.StepDown = 1.0
        operation.SafeHeight = 9.0
        operation.ClearanceHeight = 10.0
        assert document.recompute(None, True, True) is not False
        assert document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation)
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    assert operation in tuple(job.Operations.Group)
    assert operation.isValid() and operation.Path.Size > 0
    return model, job, operation


def _target(state: dict) -> dict[str, str]:
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _arguments(job, operation, quality: int = 10) -> dict:
    return {
        "operation": "native",
        "job": _target(job_state(job)),
        "operations": [_target(operation_state(operation))],
        "quality": quality,
    }


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> tuple:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _timeline(document) -> tuple:
    timeline = document.getObject("VibeCADTimeline")
    return (
        tuple(obj.Name for obj in timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _await(manager, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
    raise AssertionError(f"Background retained simulation {job_id} did not finish")


def _controlled_execute(original, entered, release, worker_threads, heartbeats=None):
    def run(frozen, *, cancelled, progress):
        worker_threads.append(threading.get_ident())
        entered.set()
        progress(3, "Waiting in retained simulation lifecycle gate")
        while not release.wait(0.01):
            if cancelled():
                raise NativeBackgroundCancelled()
        if cancelled():
            raise NativeBackgroundCancelled()
        before = heartbeats["count"] if heartbeats is not None else 0
        result = original(frozen, cancelled=cancelled, progress=progress)
        if heartbeats is not None:
            heartbeats["native_delta"] = heartbeats["count"] - before
        return result

    return run


def _assert_retained_result(document, result, job, operation, quality: int) -> None:
    assert result is not None
    assert result.TypeId == "Mesh::FeaturePython"
    assert result.isValid()
    assert result.Mesh.CountPoints >= 3
    assert result.Mesh.CountFacets >= 1
    assert result.SimulationJobName == job.Name
    assert result.SimulationJob is job
    assert result.SimulationJobStateSHA256 == job_state(job)["state_sha256"]
    assert list(result.SimulationOperationNames) == [operation.Name]
    assert result.SimulationQuality == quality
    assert float(result.SimulationResolution.getValueAs("mm")) > 0.0
    assert len(result.SimulationProgramSHA256) == 64
    assert not result.RetainedStockShape.isNull()
    assert result.RetainedStockShape.isValid()
    assert len(result.RetainedStockShape.Solids) >= 1
    assert len(result.RetainedStockShapeSHA256) == 64
    assert result.RetainedStockSolidCount == len(result.RetainedStockShape.Solids)
    assert result.SimulationProtectedModelChecked is True
    assert result.SimulationProtectedModelCollision is False
    assert result.SimulationCollisionCommandCount == 0
    verification = json.loads(result.SimulationVerificationJSON)
    assert verification["protected_model"] == {
        "checked": True,
        "collision": False,
        "collision_command_count": 0,
        "collisions": [],
        "collisions_truncated": False,
    }
    assert verification["rapid_clearance"] == {
        "protected_model_checked": True,
        "protected_model_collision": False,
        "collision_command_count": 0,
        "collisions": [],
        "collisions_truncated": False,
        "current_stock_checked": False,
    }
    cycle_time = verification["cycle_time"]
    assert cycle_time["complete"] is True, cycle_time
    assert cycle_time["method"] == "CAM Path estimates from setup feeds and rapids"
    assert cycle_time["total_seconds"] > 0.0
    assert cycle_time["operations"] == [
        {
            "operation": operation.Name,
            "seconds": cycle_time["total_seconds"],
            "rapid_speed_fallback": False,
        }
    ]
    assert {
        "holder_collision",
        "fixture_collision",
        "rapid_clearance_current_stock",
    }.issubset(verification["unavailable_checks"])
    assert "cycle_time" not in verification["unavailable_checks"]
    machine_travel = verification["machine_travel"]
    assert machine_travel["machine"] == "Generic LinuxCNC Mill"
    assert machine_travel["configured"] is True
    assert machine_travel["axis_span_checked"] is True
    assert machine_travel["fits_axis_spans"] is True
    assert machine_travel["position_checked"] is False
    assert {value["axis"] for value in machine_travel["axes"]} == {"X", "Y", "Z"}
    assert machine_travel["violations"] == []
    assert "machine_travel" not in verification["unavailable_checks"]
    assert "machine_travel_position" in verification["unavailable_checks"]
    assert result.VibeCADTimelineRole == "operation"
    assert getattr(result, "VibeCADTimelineOwner", None) is None
    assert not tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
    assert tuple(document.VibeCADTimeline.Operations).count(result) == 1
    assert result not in tuple(job.Operations.Group)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    secondary = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-cam-simulation-result-"
        )
        save_path = Path(temporary.name) / "native-cam-simulation-result.FCStd"
        document = App.newDocument("NativeManufactureSimulationResultGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        model, job, operation = _create_fixture(document, "Retained")
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen_surface = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        diagnostics = {}

        def diagnostic(job_id, exception):
            diagnostics[job_id] = "".join(traceback.format_exception(exception))
            return f"retained-simulation-{job_id}"

        manager = NativeBackgroundManager(diagnostic_sink=diagnostic)
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-simulation-result-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=manager,
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

        def call(tool: str, payload: dict, *, call_id: str | None = None):
            nonlocal call_index
            call_index += 1
            return dispatcher.call(
                tool,
                json.dumps(payload, separators=(",", ":")),
                call_id or f"native-retained-simulation-{call_index}",
            )

        invalid = _arguments(job, operation)
        invalid["quality"] = 0
        invalid_result = call(MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME, invalid)
        assert invalid_result["ok"] is False
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert invalid_result["argument_error"]["path"] == ["quality"]

        original_execute = SimulationRuntimeModule.execute_native_simulation
        objects_initial = tuple(obj.Name for obj in document.Objects)
        timeline_initial = _timeline(document)
        selection_initial = _selection()
        visibility_initial = _visibility(document)
        undo_initial = int(document.UndoCount)

        entered = threading.Event()
        release = threading.Event()
        workers = []
        with patch.object(
            SimulationRuntimeModule,
            "execute_native_simulation",
            _controlled_execute(original_execute, entered, release, workers),
        ):
            cancelled_start = call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            cancelled_id = cancelled_start["job"]["job_id"]
            assert cancelled_start["job"]["terminal"] is False
            assert manager.snapshot(cancelled_id).changes_document is True
            assert entered.wait(2.0)
            cancel_result = call(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                {"operation": "cancel", "job_id": cancelled_id},
            )
            assert cancel_result["ok"] is True
            assert cancel_result["cancel_accepted"] is True
            release.set()
            cancelled_terminal = _await(manager, cancelled_id)
        assert cancelled_terminal.phase == "cancelled"
        assert tuple(obj.Name for obj in document.Objects) == objects_initial
        assert _timeline(document) == timeline_initial
        assert int(document.UndoCount) == undo_initial

        entered = threading.Event()
        release = threading.Event()
        with patch.object(
            SimulationRuntimeModule,
            "execute_native_simulation",
            _controlled_execute(original_execute, entered, release, []),
        ):
            stale_start = call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            stale_id = stale_start["job"]["job_id"]
            assert entered.wait(2.0)
            state_store.note_structural_change(document_uid(document))
            release.set()
            stale_terminal = _await(manager, stale_id)
        assert stale_terminal.phase == "failed"
        assert stale_terminal.error["error_code"] == "NATIVE_REVISION_CONFLICT", (
            stale_terminal,
            diagnostics.get(stale_id),
        )
        assert tuple(obj.Name for obj in document.Objects) == objects_initial

        secondary = App.newDocument("NativeManufactureSimulationCloseGate")
        secondary.UndoMode = 1
        _secondary_model, secondary_job, secondary_operation = _create_fixture(
            secondary,
            "Close",
        )
        secondary_context = NativeRuntimeContext(
            service=service,
            document=secondary,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=manager,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        secondary_dispatcher = NativeTurnDispatcher(
            document=secondary,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(secondary_context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        entered = threading.Event()
        release = threading.Event()
        with patch.object(
            SimulationRuntimeModule,
            "execute_native_simulation",
            _controlled_execute(original_execute, entered, release, []),
        ):
            close_start = secondary_dispatcher.call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                json.dumps(_arguments(secondary_job, secondary_operation)),
                "native-retained-simulation-close",
            )
            close_id = close_start["job"]["job_id"]
            assert entered.wait(2.0)
            App.closeDocument(secondary.Name)
            secondary = None
            release.set()
            close_terminal = _await(manager, close_id)
        assert close_terminal.phase == "failed"

        App.setActiveDocument(document.Name)
        _events(8)
        turn = _turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        revision_before = state_store.current_revision(document_uid(document))
        heartbeat = {"count": 0, "native_delta": 0}
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(
            lambda: heartbeat.__setitem__("count", heartbeat["count"] + 1)
        )
        timer.start()
        entered = threading.Event()
        release = threading.Event()
        workers = []
        with patch.object(
            SimulationRuntimeModule,
            "execute_native_simulation",
            _controlled_execute(
                original_execute,
                entered,
                release,
                workers,
                heartbeat,
            ),
        ):
            started_at = time.monotonic()
            success_start = call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                _arguments(job, operation),
                call_id="native-retained-simulation-success",
            )
            launch_elapsed = time.monotonic() - started_at
            assert success_start["ok"] is True, success_start
            success_id = success_start["job"]["job_id"]
            assert launch_elapsed < 0.75
            assert entered.wait(2.0)
            duplicate = call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                _arguments(job, operation),
                call_id="native-retained-simulation-success",
            )
            assert duplicate == success_start
            QtCore.QTimer.singleShot(180, release.set)
            success_terminal = _await(manager, success_id)
        timer.stop()
        assert success_terminal.phase == "completed", (
            success_terminal,
            diagnostics.get(success_id),
        )
        assert workers and workers[0] != threading.get_ident()
        assert heartbeat["count"] >= 5
        assert heartbeat["native_delta"] >= 1, heartbeat

        payload = success_terminal.result
        result_state = payload["simulation_result"]["result"]
        result = document.getObject(result_state["object_name"])
        _assert_retained_result(document, result, job, operation, 10)
        assert payload["simulation_result"]["job"] == {
            "document_uid": document_uid(document),
            "object_name": job.Name,
            "type_id": job.TypeId,
        }
        assert payload["simulation_result"]["operations"] == [operation.Name]
        assert payload["simulation_result"]["source_command_count"] == operation.Path.Size
        assert payload["simulation_result"]["executed_command_count"] >= 1
        assert payload["simulation_result"]["tool_run_count"] == 1
        assert len(payload["simulation_result"]["program_sha256"]) == 64
        assert payload["simulation_result"]["verification"] == json.loads(
            result.SimulationVerificationJSON
        )
        assert payload["assistant_undo_available"] is True
        assert [item["object_name"] for item in payload["receipt"]["created"]] == [
            result.Name
        ]
        assert tuple(obj.Name for obj in document.Objects) == (*objects_initial, result.Name)
        assert _selection() == selection_initial
        assert _visibility(document)[:-1] == visibility_initial
        assert int(document.UndoCount) == undo_initial + 1
        assert state_store.current_revision(document_uid(document)) == revision_before + 1
        assert not Gui.Control.activeDialog()

        status = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "status", "job_id": success_id},
        )
        assert status["ok"] is True
        assert status["job"]["terminal"] is True
        assert status["job"]["result"] == payload

        entered = threading.Event()
        release = threading.Event()
        with patch.object(
            SimulationRuntimeModule,
            "execute_native_simulation",
            _controlled_execute(original_execute, entered, release, []),
        ):
            switch_start = call(
                MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            switch_id = switch_start["job"]["job_id"]
            assert entered.wait(2.0)
            Gui.activateWorkbench("PartDesignWorkbench")
            _events(12)
            release.set()
            switch_terminal = _await(manager, switch_id)
        assert switch_terminal.phase == "failed"
        Gui.activateWorkbench("CAMWorkbench")
        _events(16)
        assert read_active_ribbon_surface(controller).surface_id == "manufacture"

        result_name = result.Name
        model_name = model.Name
        job_name = job.Name
        operation_name = operation.Name
        result_facet_count = int(result.Mesh.CountFacets)
        result_program = str(result.SimulationProgramSHA256)
        document.undo()
        _events(12)
        assert document.getObject(result_name) is None
        assert tuple(obj.Name for obj in document.Objects) == objects_initial
        assert _timeline(document) == timeline_initial
        assert _selection() == selection_initial

        document.redo()
        _events(12)
        model = document.getObject(model_name)
        job = document.getObject(job_name)
        operation = document.getObject(operation_name)
        result = document.getObject(result_name)
        _assert_retained_result(document, result, job, operation, 10)
        assert result.Mesh.CountFacets == result_facet_count
        assert result.SimulationProgramSHA256 == result_program

        follow_up_surface = read_active_ribbon_surface(controller)
        follow_up_turn = _follow_up_turn(follow_up_surface, registry)
        follow_up_context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=lambda: require_frozen_native_surface(
                follow_up_turn.surface,
                controller,
            ),
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=manager,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        follow_up_dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=follow_up_turn,
            runtimes=build_native_runtime_bindings(
                follow_up_context,
                follow_up_turn.tool_names,
            ),
            reauthorize_turn=follow_up_context.reauthorize_turn,
            active_document=lambda: App.ActiveDocument,
        )
        source_job_state = job_state(job)
        source_result_state = simulation_result_state(result)
        follow_up_start = follow_up_dispatcher.call(
            MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create",
                    "remaining_stock": _target(source_result_state),
                    "label": "Retained second setup",
                },
                separators=(",", ":"),
            ),
            "native-retained-follow-up-success",
        )
        assert follow_up_start["ok"] is True, follow_up_start
        follow_up_terminal = _await(
            manager,
            follow_up_start["job"]["job_id"],
            timeout=60.0,
        )
        assert follow_up_terminal.phase == "completed", (
            follow_up_terminal,
            diagnostics.get(follow_up_start["job"]["job_id"]),
        )
        follow_up_payload = follow_up_terminal.result["follow_up_setup"]
        follow_up_job = document.getObject(follow_up_payload["setup"]["object_name"])
        converted_stock = document.getObject(
            follow_up_payload["remaining_stock"]["object_name"]
        )
        assert follow_up_job is not None and converted_stock is not None
        assert follow_up_job is not job
        assert follow_up_job.PreviousSetup is job
        assert follow_up_job.RemainingStockResult is result
        assert follow_up_job.RemainingStockSolid is converted_stock
        assert follow_up_job.PreviousSetupStateSHA256 == source_job_state["state_sha256"]
        assert follow_up_job.RemainingStockResultStateSHA256 == source_result_state[
            "state_sha256"
        ]
        assert follow_up_job.MachiningProgramSHA256 == result_program
        assert follow_up_job.Stock is converted_stock
        assert converted_stock.Source is result
        shared_stock = converted_stock.Shape.common(result.RetainedStockShape)
        volume_tolerance = max(
            1.0e-7,
            abs(float(result.RetainedStockShape.Volume)) * 1.0e-9,
        )
        assert abs(
            float(shared_stock.Volume) - float(result.RetainedStockShape.Volume)
        ) <= volume_tolerance
        assert abs(
            float(converted_stock.Shape.Volume)
            - float(result.RetainedStockShape.Volume)
        ) <= volume_tolerance
        assert converted_stock.ArtifactSHA256 == result.RetainedStockShapeSHA256
        assert converted_stock.Shape.ShapeType in {"Solid", "CompSolid", "Compound"}
        assert len(converted_stock.Shape.Solids) >= 1
        assert job_state(job)["state_sha256"] == source_job_state["state_sha256"]
        assert follow_up_payload["relationship"]["current"] is True

        follow_up_name = follow_up_job.Name
        converted_name = converted_stock.Name
        document.undo()
        _events(12)
        assert document.getObject(follow_up_name) is None
        assert document.getObject(converted_name) is None
        assert job_state(document.getObject(job_name))["state_sha256"] == source_job_state[
            "state_sha256"
        ]
        document.redo()
        _events(12)
        follow_up_job = document.getObject(follow_up_name)
        converted_stock = document.getObject(converted_name)
        assert follow_up_job is not None and converted_stock is not None
        assert follow_up_job.PreviousSetup is document.getObject(job_name)
        assert follow_up_job.RemainingStockResult is document.getObject(result_name)
        assert follow_up_job.RemainingStockSolid is converted_stock
        assert follow_up_job.Stock is converted_stock
        assert converted_stock.Source is document.getObject(result_name)

        branch_surface = read_active_ribbon_surface(controller)
        branch_turn = _follow_up_turn(branch_surface, registry)
        branch_context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=lambda: require_frozen_native_surface(
                branch_turn.surface,
                controller,
            ),
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
            background_manager=manager,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        branch_dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=branch_turn,
            runtimes=build_native_runtime_bindings(
                branch_context,
                branch_turn.tool_names,
            ),
            reauthorize_turn=branch_context.reauthorize_turn,
            active_document=lambda: App.ActiveDocument,
        )
        branch_start = branch_dispatcher.call(
            MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
            json.dumps(
                {
                    "operation": "create",
                    "remaining_stock": _target(source_result_state),
                    "label": "Retained alternate setup",
                },
                separators=(",", ":"),
            ),
            "native-retained-follow-up-branch",
        )
        assert branch_start["ok"] is True, branch_start
        branch_terminal = _await(
            manager,
            branch_start["job"]["job_id"],
            timeout=60.0,
        )
        assert branch_terminal.phase == "completed", (
            branch_terminal,
            diagnostics.get(branch_start["job"]["job_id"]),
        )
        branch_payload = branch_terminal.result["follow_up_setup"]
        branch_job = document.getObject(branch_payload["setup"]["object_name"])
        branch_stock = document.getObject(
            branch_payload["remaining_stock"]["object_name"]
        )
        assert branch_job is not None and branch_stock is not None
        assert branch_job is not follow_up_job
        assert branch_job.PreviousSetup is document.getObject(job_name)
        assert branch_job.RemainingStockResult is document.getObject(result_name)
        assert branch_job.Stock is branch_stock
        assert branch_stock.Source is document.getObject(result_name)
        assert branch_payload["relationship"]["current"] is True

        branch_name = branch_job.Name
        branch_stock_name = branch_stock.Name
        source_label = document.getObject(job_name).Label
        document.getObject(job_name).Label = f"{source_label} changed"
        document.recompute()
        assert setup_relationship_state(follow_up_job)["current"] is False
        assert setup_relationship_state(branch_job)["current"] is False
        document.getObject(job_name).Label = source_label
        document.recompute()
        assert setup_relationship_state(follow_up_job)["current"] is True
        assert setup_relationship_state(branch_job)["current"] is True

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        follow_up_job = document.getObject(follow_up_name)
        converted_stock = document.getObject(converted_name)
        branch_job = document.getObject(branch_name)
        branch_stock = document.getObject(branch_stock_name)
        assert follow_up_job is not None and converted_stock is not None
        assert branch_job is not None and branch_stock is not None
        assert follow_up_job.PreviousSetup is document.getObject(job_name)
        assert follow_up_job.RemainingStockResult is document.getObject(result_name)
        assert follow_up_job.RemainingStockSolid is converted_stock
        assert follow_up_job.Stock is converted_stock
        assert converted_stock.Source is document.getObject(result_name)
        assert branch_job.PreviousSetup is document.getObject(job_name)
        assert branch_job.RemainingStockResult is document.getObject(result_name)
        assert branch_job.Stock is branch_stock
        assert branch_stock.Source is document.getObject(result_name)

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        job = document.getObject(job_name)
        operation = document.getObject(operation_name)
        result = document.getObject(result_name)
        _assert_retained_result(document, result, job, operation, 10)
        assert result.Mesh.CountFacets == result_facet_count
        assert result.SimulationProgramSHA256 == result_program

        print(
            "VIBECAD_NATIVE_MANUFACTURE_SIMULATION_RESULT_GUI_OK "
            "context=true closed_schema=true exact_job=true ordered_operations=true "
            "quality=true background=true gui_responsive=true native_gil_release=true "
            "cancel=true stale=true document_close=true ribbon_switch=true "
            "duplicate_guard=true "
            "durable_mesh=true provenance=true history=true receipt=true undo=true "
            "redo=true reopen=true selection=true visibility=true low_noise=true",
            "follow_up=true related_setup=true branching=true stale=true "
            "remaining_stock=true acyclic=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if secondary is not None and secondary.Name in App.listDocuments():
            App.closeDocument(secondary.Name)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
