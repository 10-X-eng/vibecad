# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for optional background CAMotics work."""

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
import Path.Preferences as PathPreferences
import VibeCADGui as VibeGui
import VibeCADNativeManufactureCamoticsInput as CamoticsInput
import VibeCADNativeManufactureCamoticsWorker as CamoticsWorker
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureCamoticsSchema import (
    MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from native_manufacture_camotics_gui_support import (
    FakeCamoticsSimulation,
    install_fake_camotics,
    read_launch_audit,
)


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _preference_snapshot(group, key: str, default: bool) -> tuple[bool, bool]:
    return key in tuple(group.GetBools()), bool(group.GetBool(key, default))


def _restore_preference(group, key: str, value: tuple[bool, bool]) -> None:
    present, setting = value
    if present:
        group.SetBool(key, setting)
    else:
        group.RemBool(key)


def _surface():
    preferences = PathPreferences.preferences()
    preferences.SetBool(PathPreferences.EnableAdvancedOCLFeatures, True)
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    assert "CAM_Camotics" in surface.command_ids
    plan = next(
        value
        for value in resolve_native_action_inventory(surface).plans
        if value.command_id == "CAM_Camotics"
    )
    actual = (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.classification.view,
        plan.classification.mutation,
        plan.classification.interactive,
        plan.transaction_behavior,
        plan.background_required,
    )
    expected = (
        MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
        "camotics",
        "ExactCamJobOrderedActiveOperationsCamoticsRequest",
        True,
        False,
        True,
        "presentation",
        True,
    )
    assert actual == expected, (actual, expected)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_CAMOTICS_CAPABILITY_NAME)
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    assert definition is not None and background is not None
    schema = definition.provider_schema(("camotics",))
    background_schema = background.provider_schema(("status", "cancel"))
    branch = schema["parameters"]["oneOf"][0]
    assert branch["required"] == ["job", "operations", "request"]
    assert branch["additionalProperties"] is False
    request = branch["properties"]["request"]
    assert [item["properties"]["kind"]["const"] for item in request["oneOf"]] == [
        "read_result",
        "launch",
    ]
    assert all(
        item["required"] == ["kind", "resolution"]
        and item["additionalProperties"] is False
        for item in request["oneOf"]
    )
    assert branch["properties"]["operations"]["maxItems"] == 64
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    property_names = set()

    def collect_properties(value) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                property_names.update(properties)
            for child in value.values():
                collect_properties(child)
        elif isinstance(value, list):
            for child in value:
                collect_properties(child)

    collect_properties(schema)
    assert not property_names.intersection({"path", "executable", "command"})
    assert len(encoded.encode("utf-8")) < 5_000
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(schema, background_schema),
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
    raise AssertionError("The CAMotics fixture has no exact top face")


def _create_fixture(document, prefix: str):
    model = document.addObject("Part::Feature", f"{prefix}Model")
    model.Label = f"{prefix} model"
    model.Shape = Part.makeBox(48.0, 32.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    document.openTransaction(f"Create {prefix} CAMotics Profile")
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


def _arguments(job, operation, kind: str, resolution: str = "medium") -> dict:
    return {
        "operation": "camotics",
        "job": _target(job_state(job)),
        "operations": [_target(operation_state(operation))],
        "request": {"kind": kind, "resolution": resolution},
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


def _document_state(document, state_store) -> dict:
    gui_document = Gui.getDocument(document.Name)
    return {
        "objects": tuple(obj.Name for obj in document.Objects),
        "timeline": _timeline(document),
        "selection": _selection(),
        "visibility": _visibility(document),
        "undo": int(document.UndoCount),
        "redo": int(document.RedoCount),
        "transaction": int(document.getBookedTransactionID() or 0),
        "gui_modified": bool(gui_document.Modified),
        "revision": state_store.current_revision(document_uid(document)),
    }


def _await(manager, job_id: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return snapshot
    raise AssertionError(f"Background CAMotics job {job_id} did not finish")


def _await_instance(previous_count: int, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(FakeCamoticsSimulation.instances) > previous_count:
            instance = FakeCamoticsSimulation.instances[-1]
            if instance.compute_entered.wait(0.01):
                return instance
        _events(1)
    raise AssertionError("The fake CAMotics runtime was not entered")


def _wait_file(path: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        if path.is_file():
            return
        time.sleep(0.01)
    raise AssertionError(f"Expected CAMotics launch artifact {path} was not written")


def _wait_absent(path: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        if not path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"CAMotics private workspace {path} was not cleaned")


def _assert_unavailable_boundaries() -> None:
    with patch.dict(sys.modules, {"camotics": None}):
        try:
            CamoticsInput._simulation_factory()
        except NativeManufactureError as exc:
            assert exc.failure()["error_code"] == (
                "NATIVE_MANUFACTURE_CAMOTICS_UNAVAILABLE"
            )
        else:
            raise AssertionError("Missing CAMotics Python runtime was accepted")
    with patch.object(CamoticsInput.shutil, "which", return_value=None):
        try:
            CamoticsInput._executable_identity()
        except NativeManufactureError as exc:
            assert exc.failure()["error_code"] == (
                "NATIVE_MANUFACTURE_CAMOTICS_LAUNCH_UNAVAILABLE"
            )
        else:
            raise AssertionError("Missing CAMotics executable was accepted")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    secondary = None
    temporary = None
    installation = None
    exit_code = 1
    preferences = PathPreferences.preferences()
    advanced_before = _preference_snapshot(
        preferences,
        PathPreferences.EnableAdvancedOCLFeatures,
        False,
    )
    try:
        _assert_unavailable_boundaries()
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-camotics-")
        root = Path(temporary.name)
        installation = install_fake_camotics(root / "installation")
        __import__("Path.Main.Gui.Camotics")
        assert "CAM_Camotics" in Gui.listCommands()
        document = App.newDocument("NativeManufactureCamoticsGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        model, job, operation = _create_fixture(document, "Camotics")
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
            return f"camotics-{job_id}"

        manager = NativeBackgroundManager(diagnostic_sink=diagnostic)
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-camotics-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        def make_dispatcher(target_document):
            context = NativeRuntimeContext(
                service=service,
                document=target_document,
                state=state_store,
                undo_ledger=ledger,
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
                active_surface_id=lambda: read_active_ribbon_surface(
                    controller
                ).surface_id,
                edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
                background_manager=manager,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            )
            return NativeTurnDispatcher(
                document=target_document,
                state=state_store,
                registry=registry,
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = make_dispatcher(document)
        call_index = 0

        def call(tool: str, payload: dict, *, call_id: str | None = None):
            nonlocal call_index
            call_index += 1
            return dispatcher.call(
                tool,
                json.dumps(payload, separators=(",", ":")),
                call_id or f"native-camotics-{call_index}",
            )

        invalid = _arguments(job, operation, "launch")
        invalid["request"]["executable"] = "/tmp/provider-controlled"
        invalid_result = call(MANUFACTURE_CAMOTICS_CAPABILITY_NAME, invalid)
        assert invalid_result["ok"] is False
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert invalid_result["argument_error"]["path"] == ["request"]

        initial_state = _document_state(document, state_store)
        before_instances = len(FakeCamoticsSimulation.instances)
        cancelled_start = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result"),
        )
        cancelled_id = cancelled_start["job"]["job_id"]
        _await_instance(before_instances)
        cancel_result = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "cancel", "job_id": cancelled_id},
        )
        assert cancel_result["ok"] is True
        assert cancel_result["cancel_accepted"] is True
        cancelled_terminal = _await(manager, cancelled_id)
        assert cancelled_terminal.phase == "cancelled"
        assert _document_state(document, state_store) == initial_state

        before_instances = len(FakeCamoticsSimulation.instances)
        selection_start = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result"),
        )
        selection_id = selection_start["job"]["job_id"]
        _await_instance(before_instances)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        selection_terminal = _await(manager, selection_id)
        assert selection_terminal.phase == "failed"
        assert selection_terminal.error["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        assert _document_state(document, state_store) == initial_state

        before_instances = len(FakeCamoticsSimulation.instances)
        stale_start = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result"),
        )
        stale_id = stale_start["job"]["job_id"]
        _await_instance(before_instances)
        state_store.note_structural_change(document_uid(document))
        stale_terminal = _await(manager, stale_id)
        assert stale_terminal.phase == "failed"
        assert stale_terminal.error["error_code"] == "NATIVE_REVISION_CONFLICT"
        assert _document_state(document, state_store)["objects"] == initial_state["objects"]

        secondary = App.newDocument("NativeManufactureCamoticsCloseGate")
        secondary.UndoMode = 1
        _secondary_model, secondary_job, secondary_operation = _create_fixture(
            secondary,
            "Close",
        )
        secondary_dispatcher = make_dispatcher(secondary)
        before_instances = len(FakeCamoticsSimulation.instances)
        close_start = secondary_dispatcher.call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            json.dumps(_arguments(secondary_job, secondary_operation, "read_result")),
            "native-camotics-close",
        )
        close_id = close_start["job"]["job_id"]
        _await_instance(before_instances)
        App.closeDocument(secondary.Name)
        secondary = None
        close_terminal = _await(manager, close_id)
        assert close_terminal.phase == "failed"

        App.setActiveDocument(document.Name)
        _events(8)
        turn = _turn(surface, registry)
        frozen_surface = turn.surface
        dispatcher = make_dispatcher(document)
        before_instances = len(FakeCamoticsSimulation.instances)
        switch_start = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result"),
        )
        switch_id = switch_start["job"]["job_id"]
        _await_instance(before_instances)
        Gui.activateWorkbench("PartDesignWorkbench")
        _events(12)
        switch_terminal = _await(manager, switch_id)
        assert switch_terminal.phase == "failed"
        Gui.activateWorkbench("CAMWorkbench")
        _events(16)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "manufacture"
        turn = _turn(surface, registry)
        frozen_surface = turn.surface
        dispatcher = make_dispatcher(document)

        success_state = _document_state(document, state_store)
        heartbeat = {"count": 0}
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(
            lambda: heartbeat.__setitem__("count", heartbeat["count"] + 1)
        )
        timer.start()
        before_instances = len(FakeCamoticsSimulation.instances)
        started_at = time.monotonic()
        success_start = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result", "high"),
            call_id="native-camotics-success",
        )
        launch_elapsed = time.monotonic() - started_at
        success_id = success_start["job"]["job_id"]
        assert success_start["ok"] is True
        assert launch_elapsed < 0.75
        instance = _await_instance(before_instances)
        duplicate = call(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _arguments(job, operation, "read_result", "high"),
            call_id="native-camotics-success",
        )
        assert duplicate == success_start
        success_terminal = _await(manager, success_id)
        timer.stop()
        assert success_terminal.phase == "completed", (
            success_terminal,
            diagnostics.get(success_id),
        )
        assert instance.simulation_entered.is_set()
        assert heartbeat["count"] >= 12, heartbeat
        assert FakeCamoticsSimulation.call_threads
        assert all(
            thread_id != threading.get_ident()
            for thread_id in FakeCamoticsSimulation.call_threads
        )
        result = success_terminal.result
        assert result["request"] == "read_result"
        assert result["job"] == job.Name
        assert result["operation_count"] == 1
        assert result["command_count"] == operation.Path.Size
        assert result["resolution"] == "high"
        assert result["path_step_count"] == 3
        assert abs(float(result["duration_seconds"]) - 0.18) <= 1.0e-9
        assert result["surface"]["facet_count"] == 1
        assert result["surface"]["bounds_mm"] == {
            "min": [0.0, 0.0, 0.0],
            "max": [4.0, 3.0, 0.0],
        }
        assert len(result["surface"]["sha256"]) == 64
        assert len(result["program_sha256"]) == 64
        assert len(json.dumps(result, separators=(",", ":")).encode("utf-8")) < 1_500
        assert _document_state(document, state_store) == success_state

        status = call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            {"operation": "status", "job_id": success_id},
        )
        assert status["ok"] is True
        assert status["job"]["terminal"] is True
        assert status["job"]["result"] == result
        assert _document_state(document, state_store) == success_state

        installation.audit_path.unlink(missing_ok=True)
        launch_worker_threads = []
        original_program = CamoticsWorker._program

        def recording_program(*args, **kwargs):
            launch_worker_threads.append(threading.get_ident())
            return original_program(*args, **kwargs)

        with patch.object(CamoticsWorker, "_program", recording_program):
            launch_start = call(
                MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
                _arguments(job, operation, "launch", "low"),
            )
            launch_id = launch_start["job"]["job_id"]
            launch_terminal = _await(manager, launch_id)
        assert launch_terminal.phase == "completed", (
            launch_terminal,
            diagnostics.get(launch_id),
        )
        launch_result = launch_terminal.result
        assert launch_result == {
            "request": "launch",
            "launched": True,
            "job": job.Name,
            "operation_count": 1,
            "command_count": operation.Path.Size,
            "resolution": "low",
            "program_sha256": launch_result["program_sha256"],
        }
        assert len(launch_result["program_sha256"]) == 64
        assert launch_worker_threads == [launch_worker_threads[0]]
        assert launch_worker_threads[0] != threading.get_ident()
        _wait_file(installation.audit_path)
        audit = read_launch_audit(installation)
        assert audit["program_sha256"] == launch_result["program_sha256"]
        assert audit["program_bytes"] > 0
        assert audit["program_prefix"][:3] == ["G21", "G90", "G17"]
        assert audit["project"]["resolution-mode"] == "low"
        assert audit["project"]["workpiece"]["automatic"] is False
        assert list(audit["project"]["tools"]) == [
            str(job.Tools.Group[0].ToolNumber)
        ]
        workspace = Path(audit["workspace"])
        assert workspace.is_dir()
        _wait_absent(workspace)
        assert _document_state(document, state_store) == success_state
        assert not Gui.Control.activeDialog()

        print(
            "VIBECAD_NATIVE_MANUFACTURE_CAMOTICS_GUI_OK "
            "optional_unavailable=true optional_available=true closed_schema=true "
            "no_provider_path=true exact_job=true ordered_operations=true "
            "resolution=true background=true gui_responsive=true cancel=true "
            "selection_stale=true revision_stale=true document_close=true "
            "ribbon_switch=true duplicate_guard=true bounded_result=true "
            "fixed_executable=true private_project=true process_reaped=true "
            "workspace_cleanup=true document_unchanged=true history=true undo=true "
            "redo=true selection=true visibility=true low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        _restore_preference(
            preferences,
            PathPreferences.EnableAdvancedOCLFeatures,
            advanced_before,
        )
        if secondary is not None and secondary.Name in App.listDocuments():
            App.closeDocument(secondary.Name)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if installation is not None:
            installation.restore()
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
