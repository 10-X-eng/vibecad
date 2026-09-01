# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact background GL CAM simulation."""

from __future__ import annotations

import json
import threading
import time
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Main.Gui.Job as PathJobGui
import Path.Main.Gui.SimulatorGL as PathSimulatorGL
import Path.Op.Gui.Profile as PathProfileGui
import Path.Op.Profile as PathProfile
import VibeCADGui as VibeGui
import VibeCADNativeManufactureSimulationRuntime as SimulationRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeBackground import (
    NativeBackgroundCancelled,
    NativeBackgroundManager,
)
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    provider_visible_native_schema,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureSimulationSchema import (
    MANUFACTURE_SIMULATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureSimulationControlSchema import (
    MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
)
from VibeCADNativeManufactureFocusedInspectSchema import (
    MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES,
)
from VibeCADNativeManufactureState import (
    job_state,
    operation_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeProviderContext import (
    provider_authorized_native_surface,
    resolve_production_native_surface,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADNativeActionManifest import resolve_native_action_inventory


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
        if value.command_id == "CAM_SimulatorGL"
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.classification.view,
        plan.classification.mutation,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        MANUFACTURE_SIMULATION_CAPABILITY_NAME,
        "gl",
        "ExactCamJobOrderedActiveOperationsAndGlQuality",
        True,
        False,
        "presentation",
        True,
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    read_setup_name = MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["read_job"]
    simulation = registry.definition(MANUFACTURE_SIMULATION_CAPABILITY_NAME)
    simulation_control = registry.definition(
        MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME
    )
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    read_setup = registry.definition(read_setup_name)
    assert simulation is not None and simulation_control is not None
    assert background is not None and read_setup is not None
    simulation_schema = simulation.provider_schema(("gl",))
    control_schema = provider_visible_native_schema(
        simulation_control.provider_schema(("close",))
    )
    read_setup_schema = provider_visible_native_schema(
        read_setup.provider_schema(("read_job",))
    )
    background_schema = background.provider_schema(("status", "cancel"))
    branch = simulation_schema["parameters"]["oneOf"][0]
    assert branch["required"] == ["job", "operations", "quality"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["operation"]["type"] == "string"
    assert branch["properties"]["operation"]["const"] == "gl"
    assert branch["properties"]["operations"]["maxItems"] == 64
    assert branch["properties"]["quality"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
        "description": (
            "GL simulation quality from 1 (low) through 10 (high), matching the "
            "human simulator control."
        ),
    }
    encoded = json.dumps(simulation_schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5_000
    control_parameters = control_schema["parameters"]["oneOf"][0]
    assert set(control_parameters["properties"]) == {"simulation_id"}
    assert control_parameters["required"] == ["simulation_id"]
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
                read_setup_name,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                simulation_schema,
                control_schema,
                read_setup_schema,
                background_schema,
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
    raise AssertionError("The simulation model has no exact top face")


def _create_fixture(document, prefix: str):
    model = document.addObject("Part::Feature", f"{prefix}Model")
    model.Label = f"{prefix} model"
    model.Shape = Part.makeBox(24.0, 18.0, 6.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    document.openTransaction(f"Create {prefix} GL Profile fixture")
    try:
        operation = PathProfile.Create(
            f"{prefix}Profile",
            parentJob=job,
            toolController=job.Tools.Group[0],
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
        operation.StartDepth = 6.0
        operation.FinalDepth = 0.0
        operation.StepDown = 2.0
        operation.SafeHeight = 7.0
        operation.ClearanceHeight = 8.0
        assert document.recompute(None, True, True) is not False
        assert document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation)
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    assert operation in tuple(job.Operations.Group)
    assert operation.isValid() and operation.Path.Size > 0
    controller = job.Tools.Group[0]
    assert controller.Tool.Shape.isValid() and not controller.Tool.Shape.isNull()
    return model, job, operation


def _target(state: dict) -> dict[str, str]:
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _arguments(job, operation, quality: int = 8) -> dict:
    return {
        "operation": "gl",
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
        tuple(timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _await(manager, job_id: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
    raise AssertionError(f"Background GL simulation {job_id} did not finish")


def _controlled_prepare(original, entered, release, worker_threads):
    def run(frozen, *, cancelled, progress):
        worker_threads.append(threading.get_ident())
        entered.set()
        progress(4, "Waiting in compiled background gate")
        while not release.wait(0.01):
            if cancelled():
                raise NativeBackgroundCancelled()
        if cancelled():
            raise NativeBackgroundCancelled()
        return original(frozen, cancelled=cancelled, progress=progress)

    return run


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    secondary = None
    exit_code = 1
    try:
        document = App.newDocument("NativeManufactureGlSimulationGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        model, job, operation = _create_fixture(document, "GlSimulation")
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen_surface = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        background_diagnostics = {}

        def capture_background_failure(job_id, exception):
            background_diagnostics[job_id] = "".join(
                traceback.format_exception(exception)
            )
            return f"gl-gate-{job_id}"

        manager = NativeBackgroundManager(
            diagnostic_sink=capture_background_failure,
        )
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-gl-simulation-gui")

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
                call_id or f"native-gl-simulation-{call_index}",
            )

        invalid = _arguments(job, operation)
        invalid["quality"] = 11
        invalid_result = call(MANUFACTURE_SIMULATION_CAPABILITY_NAME, invalid)
        assert invalid_result["ok"] is False
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert invalid_result["argument_error"]["path"] == ["quality"]

        original_prepare = SimulationRuntimeModule.prepare_gl_simulation

        # Cancellation must stop before presentation and retain exact document state.
        entered = threading.Event()
        release = threading.Event()
        worker_threads = []
        with patch.object(
            SimulationRuntimeModule,
            "prepare_gl_simulation",
            _controlled_prepare(original_prepare, entered, release, worker_threads),
        ):
            cancelled_start = call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            assert cancelled_start["ok"] is True, cancelled_start
            cancelled_id = cancelled_start["job"]["job_id"]
            assert entered.wait(2.0)
            cancelled = call(
                NATIVE_BACKGROUND_CAPABILITY_NAME,
                {"operation": "cancel", "job_id": cancelled_id},
            )
            assert cancelled["ok"] is True
            assert cancelled["cancel_accepted"] is True
            release.set()
            cancelled_terminal = _await(manager, cancelled_id)
        assert cancelled_terminal.phase == "cancelled"
        assert not Gui.Control.activeDialog()

        # A structural revision change after launch must fail before presentation.
        entered = threading.Event()
        release = threading.Event()
        with patch.object(
            SimulationRuntimeModule,
            "prepare_gl_simulation",
            _controlled_prepare(original_prepare, entered, release, []),
        ):
            stale_start = call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            stale_id = stale_start["job"]["job_id"]
            assert entered.wait(2.0)
            state_store.note_structural_change(document_uid(document))
            release.set()
            stale_terminal = _await(manager, stale_id)
        assert stale_terminal.phase == "failed"
        assert stale_terminal.error["error_code"] == "NATIVE_REVISION_CONFLICT"
        assert not Gui.Control.activeDialog()

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

        objects_before = tuple(document.Objects)
        timeline_before = _timeline(document)
        selection_before = _selection()
        visibility_before = _visibility(document)
        undo_before = int(document.UndoCount)
        revision_before = state_store.current_revision(document_uid(document))
        windows_before = tuple(Gui.getMainWindow().getWindows())
        active_window_before = Gui.getMainWindow().getActiveWindow()
        heartbeat = {"count": 0}
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(
            lambda: heartbeat.__setitem__("count", heartbeat["count"] + 1)
        )
        timer.start()
        entered = threading.Event()
        release = threading.Event()
        worker_threads = []
        with patch.object(
            SimulationRuntimeModule,
            "prepare_gl_simulation",
            _controlled_prepare(original_prepare, entered, release, worker_threads),
        ):
            started_at = time.monotonic()
            success_start = call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                _arguments(job, operation),
                call_id="native-gl-simulation-success",
            )
            launch_elapsed = time.monotonic() - started_at
            assert success_start["ok"] is True, success_start
            success_id = success_start["job"]["job_id"]
            assert launch_elapsed < 0.75, launch_elapsed
            assert entered.wait(2.0)
            duplicate = call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                _arguments(job, operation),
                call_id="native-gl-simulation-success",
            )
            assert duplicate == success_start
            QtCore.QTimer.singleShot(180, release.set)
            success_terminal = _await(manager, success_id)
        timer.stop()
        assert success_terminal.phase == "completed", (
            success_terminal,
            background_diagnostics.get(success_id),
        )
        assert heartbeat["count"] >= 5, heartbeat
        assert worker_threads and worker_threads[0] != threading.get_ident()
        result = success_terminal.result["simulation"]
        assert result == {
            "mode": "gl",
            "simulation_id": result["simulation_id"],
            "job": job.Name,
            "operations": [operation.Name],
            "operation_count": 1,
            "command_count": int(operation.Path.Size),
            "tool_run_count": 1,
            "quality": 8,
            "program_sha256": result["program_sha256"],
            "task_active": True,
            "document_changed": False,
        }
        assert success_terminal.result["next"] == {
            "tool": MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
            "simulation_id": result["simulation_id"],
        }
        assert len(result["simulation_id"]) == 32
        assert len(result["program_sha256"]) == 64
        assert tuple(document.Objects) == objects_before
        assert _timeline(document) == timeline_before
        assert _selection() == selection_before
        assert _visibility(document) == visibility_before
        assert int(document.UndoCount) == undo_before
        assert state_store.current_revision(document_uid(document)) == revision_before
        assert Gui.Control.activeDialog()

        active_state = service.native_active_snapshot()
        assert active_state["domain"]["active_simulation"] == {
            "mode": "gl",
            "simulation_id": result["simulation_id"],
            "job": job.Name,
        }
        production_registry, production_surface = resolve_production_native_surface()
        task_surface = provider_authorized_native_surface(
            production_surface,
            active_state,
            registry=production_registry,
        )
        assert MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME in task_surface.tool_names
        assert MANUFACTURE_SIMULATION_CAPABILITY_NAME not in task_surface.tool_names
        assert "manufacture.start_point" not in task_surface.tool_names
        assert "manufacture.set_controller" not in task_surface.tool_names
        assert "document.save" not in task_surface.tool_names

        status = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "status", "job_id": success_id},
        )
        assert status["ok"] is True
        assert status["job"]["terminal"] is True
        assert status["job"]["result"] == success_terminal.result
        active_simulation = PathSimulatorGL.active_prepared_simulation()
        assert active_simulation is not None
        assert active_simulation.nativeSimulationId == result["simulation_id"]
        read_while_open = call(
            MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES["read_job"],
            {
                "target": _target(job_state(job)),
                "operation_offset": 0,
                "page_size": 32,
            },
        )
        assert read_while_open["ok"] is True
        assert read_while_open["job"]["object_name"] == job.Name
        stale_close = call(
            MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
            {"simulation_id": "0" * 32},
        )
        assert stale_close["ok"] is False
        assert stale_close["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        closed = call(
            MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
            {"simulation_id": result["simulation_id"]},
        )
        assert closed["ok"] is True
        assert closed["simulation"] == {
            "mode": "gl",
            "simulation_id": result["simulation_id"],
            "closed": True,
            "document_changed": False,
        }
        _events(8)
        assert not Gui.Control.activeDialog()
        assert PathSimulatorGL.active_prepared_simulation() is None
        assert tuple(Gui.getMainWindow().getWindows()) == windows_before
        assert Gui.getMainWindow().getActiveWindow() is active_window_before

        # Closing the exact document while preparation runs must never present UI.
        secondary = App.newDocument("NativeManufactureGlCloseGate")
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
            "prepare_gl_simulation",
            _controlled_prepare(original_prepare, entered, release, []),
        ):
            close_start = secondary_dispatcher.call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                json.dumps(_arguments(secondary_job, secondary_operation)),
                "native-gl-close-document",
            )
            close_id = close_start["job"]["job_id"]
            assert entered.wait(2.0)
            secondary_name = secondary.Name
            App.closeDocument(secondary_name)
            secondary = None
            release.set()
            close_terminal = _await(manager, close_id)
        assert close_terminal.phase == "failed"
        assert not Gui.Control.activeDialog()

        App.setActiveDocument(document.Name)
        _events(8)

        # A human ribbon switch invalidates finalization before task creation.
        entered = threading.Event()
        release = threading.Event()
        with patch.object(
            SimulationRuntimeModule,
            "prepare_gl_simulation",
            _controlled_prepare(original_prepare, entered, release, []),
        ):
            switch_start = call(
                MANUFACTURE_SIMULATION_CAPABILITY_NAME,
                _arguments(job, operation),
            )
            switch_id = switch_start["job"]["job_id"]
            assert entered.wait(2.0)
            Gui.activateWorkbench("PartDesignWorkbench")
            _events(12)
            release.set()
            switch_terminal = _await(manager, switch_id)
        assert switch_terminal.phase == "failed"
        assert not Gui.Control.activeDialog()

        print(
            "VIBECAD_NATIVE_MANUFACTURE_GL_SIMULATION_GUI_OK "
            "context=true closed_schema=true exact_job=true ordered_operations=true "
            "active_path=true quality=true background=true gui_responsive=true "
            "compiled_mesh=true detached_tools=true placed_gcode=true cancel=true "
            "stale=true document_close=true ribbon_switch=true duplicate_guard=true "
            "task_owned=true task_scoped=true status_during_task=true low_noise=true history=true "
            "undo_neutral=true selection=true visibility=true revision_neutral=true "
            "view_teardown=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        active_simulation = PathSimulatorGL.active_prepared_simulation()
        if active_simulation is not None:
            active_simulation.taskForm.reject()
            _events(4)
        elif Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if secondary is not None and secondary.Name in App.listDocuments():
            App.closeDocument(secondary.Name)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
