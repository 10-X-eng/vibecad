# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for exact, isolated CAM postprocessing variants."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

from Machine.models.machine import MachineFactory
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Profile as PathProfileGui
import Path.Op.Profile as PathProfile
import Path.Preferences as PathPreferences
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    provider_visible_native_schema,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedPostSchema import (
    MANUFACTURE_FOCUSED_POST_CAPABILITIES,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


POST_JOB_CAPABILITY_NAME = MANUFACTURE_FOCUSED_POST_CAPABILITIES["complete_job"]
POST_SELECTED_CAPABILITY_NAME = MANUFACTURE_FOCUSED_POST_CAPABILITIES[
    "selected_operations"
]


def _events(rounds: int = 12) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


def _top_face_name(model) -> str:
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for index, face in enumerate(model.Shape.Faces, start=1):
        if all(
            abs(float(vertex.Point.z) - maximum_z) <= 1.0e-9
            for vertex in face.Vertexes
        ):
            return f"Face{index}"
    raise AssertionError("The postprocessing fixture has no exact top face")


def _create_fixture(document):
    model = document.addObject("Part::Feature", "PostModel")
    model.Shape = Part.makeBox(48.0, 32.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    document.openTransaction("Create Post Profile")
    try:
        operation = PathProfile.Create(
            "PostProfile",
            parentJob=job,
            toolController=job.Tools.Group[0],
        )
        operation.Proxy.addBase(operation, model, _top_face_name(model))
        provider = PathProfileGui.PathOpGui.ViewProvider(
            operation.ViewObject,
            PathProfileGui.Command.res,
        )
        operation.ViewObject.Proxy = provider
        provider.deleteOnReject = False
        for name in (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
        ):
            operation.setExpression(name, None)
        operation.StartDepth = 8.0
        operation.FinalDepth = 0.0
        operation.StepDown = 1.0
        operation.SafeHeight = 9.0
        operation.ClearanceHeight = 10.0
        assert document.recompute(None, True, True) is not False
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    assert operation.Path.Size > 0
    return model, job, operation


def _add_second_profile(document, job, model):
    document.openTransaction("Create Second Post Profile")
    try:
        operation = PathProfile.Create(
            "PostProfileSecond",
            parentJob=job,
            toolController=job.Tools.Group[0],
        )
        operation.Proxy.addBase(operation, model, _top_face_name(model))
        provider = PathProfileGui.PathOpGui.ViewProvider(
            operation.ViewObject,
            PathProfileGui.Command.res,
        )
        operation.ViewObject.Proxy = provider
        provider.deleteOnReject = False
        for name in (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
        ):
            operation.setExpression(name, None)
        operation.StartDepth = 8.0
        operation.FinalDepth = 4.0
        operation.StepDown = 1.0
        operation.SafeHeight = 9.0
        operation.ClearanceHeight = 10.0
        assert document.recompute(None, True, True) is not False
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    assert operation.Path.Size > 0
    return operation


def _surface_and_turn():
    preferences = PathPreferences.preferences()
    preferences.SetBool(PathPreferences.EnableAdvancedOCLFeatures, True)
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(QtCore.QObject, "VibeCADRibbonController")
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    plans = {
        value.command_id: value
        for value in resolve_native_action_inventory(surface).plans
        if value.command_id in {"CAM_Post", "CAM_PostSelected"}
    }
    plan = plans["CAM_Post"]
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.classification.export,
        plan.background_required,
    ) == (
        POST_JOB_CAPABILITY_NAME,
        "complete_job",
        "ExactCamJobAndHumanAuthorizedPostOutputs",
        True,
        True,
    )
    selected_plan = plans["CAM_PostSelected"]
    assert (
        selected_plan.capability_family,
        selected_plan.operation_variant,
        selected_plan.exact_target_type,
        selected_plan.classification.export,
        selected_plan.background_required,
    ) == (
        POST_SELECTED_CAPABILITY_NAME,
        "selected_operations",
        "ExactCamJobOrderedOperationsAndHumanAuthorizedPostOutputs",
        True,
        True,
    )
    registry = build_native_capability_registry()
    full_provider = resolve_native_provider_surface(surface, registry)
    for capability_name in (POST_JOB_CAPABILITY_NAME, POST_SELECTED_CAPABILITY_NAME):
        assert capability_name not in {
            *full_provider.missing_definition_names,
            *full_provider.missing_implementation_names,
            *full_provider.incomplete_definition_names,
        }
    job_definition = registry.definition(POST_JOB_CAPABILITY_NAME)
    selected_definition = registry.definition(POST_SELECTED_CAPABILITY_NAME)
    background = registry.definition(NATIVE_BACKGROUND_CAPABILITY_NAME)
    job_schema = provider_visible_native_schema(
        job_definition.provider_schema(("complete_job",))
    )
    selected_schema = provider_visible_native_schema(
        selected_definition.provider_schema(("selected_operations",))
    )
    job_parameters = job_schema["parameters"]["oneOf"][0]
    assert job_parameters["required"] == ["job"]
    assert set(job_parameters["properties"]) == {"job"}
    selected_parameters = selected_schema["parameters"]["oneOf"][0]
    assert selected_parameters["required"] == ["job", "operations"]
    operations_schema = selected_parameters["properties"]["operations"]
    assert operations_schema["minItems"] == 1
    assert operations_schema["maxItems"] == 64
    assert operations_schema["uniqueItems"] is True
    encoded = json.dumps(
        (job_schema, selected_schema),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert not any(value in encoded for value in ('"path"', '"processor"', '"executable"'))
    turn = NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                POST_JOB_CAPABILITY_NAME,
                POST_SELECTED_CAPABILITY_NAME,
                NATIVE_BACKGROUND_CAPABILITY_NAME,
            ),
            schemas=(
                job_schema,
                selected_schema,
                background.provider_schema(("status", "cancel")),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )
    return controller, registry, turn


def _target(job) -> dict[str, str]:
    state = job_state(job)
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _operation_target(operation) -> dict[str, str]:
    state = operation_state(operation)
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _document_state(document, state_store) -> dict:
    timeline = document.getObject("VibeCADTimeline")
    return {
        "objects": tuple(obj.Name for obj in document.Objects),
        "timeline": (
            tuple(obj.Name for obj in timeline.Operations),
            tuple(bool(value) for value in timeline.VisibilityAtEnd),
            tuple(bool(value) for value in timeline.SuppressionAtEnd),
            int(timeline.Position),
        ),
        "selection": tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        ),
        "visibility": tuple(
            (obj.Name, bool(obj.ViewObject.Visibility))
            for obj in document.Objects
            if getattr(obj, "ViewObject", None) is not None
        ),
        "undo": int(document.UndoCount),
        "redo": int(document.RedoCount),
        "transaction": int(document.getBookedTransactionID() or 0),
        "gui_modified": bool(Gui.getDocument(document.Name).Modified),
        "revision": state_store.current_revision(document_uid(document)),
    }


def _await(manager, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            return manager.wait(job_id, 2.0)
    raise AssertionError(f"Background CAM post job {job_id} did not finish")


def _wait_for_file(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _events(1)
        if path.is_file():
            return
        time.sleep(0.01)
    raise AssertionError(f"Expected isolated post marker {path} was not written")


_SLOW_POST = '''from pathlib import Path
import os
import time
from Path.Post.scripts.linuxcnc_post import Linuxcnc

class Slow_Gate(Linuxcnc):
    def export2(self):
        Path(os.environ["VIBECAD_NATIVE_POST_GATE_MARKER"]).write_text("entered")
        time.sleep(1.2)
        return super().export2()

    def remote_post(self, sections):
        Path(os.environ["VIBECAD_NATIVE_POST_REMOTE_MARKER"]).write_text("called")
'''


_LEGACY_POST = '''def export(objectslist, filename, argstring):
    raise RuntimeError("legacy post must never execute in Native mode")
'''


_SPLIT_POST = '''from Path.Post.scripts.linuxcnc_post import Linuxcnc

class Split_Gate(Linuxcnc):
    def export2(self):
        self.apply_configuration_bundle()
        return [
            ("rough", "G21\\nG90\\nG0 X0 Y0\\nM30\\n"),
            ("finish", "G21\\nG90\\nG1 X10 F100\\nM30\\n"),
        ]
'''


def _run() -> None:
    document = None
    secondary = None
    temporary = None
    old_machine_dir = MachineFactory._config_dir
    inserted_post_path = None
    preferences = PathPreferences.preferences()
    advanced_present = (
        PathPreferences.EnableAdvancedOCLFeatures in tuple(preferences.GetBools())
    )
    advanced_before = bool(
        preferences.GetBool(PathPreferences.EnableAdvancedOCLFeatures, False)
    )
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-post-gui-")
        root = Path(temporary.name)
        post_dir = root / "Posts"
        post_dir.mkdir()
        slow_post_path = post_dir / "slow_gate_post.py"
        slow_post_path.write_text(_SLOW_POST, encoding="utf-8")
        (post_dir / "legacy_gate_post.py").write_text(_LEGACY_POST, encoding="utf-8")
        (post_dir / "split_gate_post.py").write_text(_SPLIT_POST, encoding="utf-8")
        sys.path.insert(0, str(post_dir))
        inserted_post_path = str(post_dir)
        marker = root / "post-entered.marker"
        remote_marker = root / "remote-post.marker"
        os.environ["VIBECAD_NATIVE_POST_GATE_MARKER"] = str(marker)
        os.environ["VIBECAD_NATIVE_POST_REMOTE_MARKER"] = str(remote_marker)
        machine_dir = root / "Machines"
        MachineFactory.set_config_directory(machine_dir)
        machine = MachineFactory.load_configuration(
            Path(App.getHomePath())
            / "Mod"
            / "CAM"
            / "Machine"
            / "machines"
            / "Generic_LinuxCNC_Mill.fcm"
        )
        MachineFactory.save_configuration(machine, "NativePostMachine.fcm")

        def configure_processor(name: str) -> None:
            machine.postprocessor_file_name = name
            MachineFactory.save_configuration(machine, "NativePostMachine.fcm")

        document = App.newDocument("NativeManufacturePostGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, registry, turn = _surface_and_turn()
        model, job, operation = _create_fixture(document)
        second_operation = _add_second_profile(document, job, model)
        job.Machine = machine.name
        job.PostProcessorOutputFile = "NativeProgram-%S.ngc"
        assert document.recompute(None, True, True) is not False
        document.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        diagnostics = {}

        def diagnostic(job_id, exception):
            diagnostics[job_id] = "".join(traceback.format_exception(exception))
            return f"post-{job_id}"

        manager = NativeBackgroundManager(diagnostic_sink=diagnostic)
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-post-gui")
        output_dir = root / "authorized"
        output_dir.mkdir()
        authorizer_threads = []
        requests = []
        main_thread_id = threading.get_ident()

        authorization_mode = {"value": "allow"}

        def authorize(request):
            authorizer_threads.append(threading.get_ident())
            requests.append(request)
            if authorization_mode["value"] == "cancel":
                return None
            destination = (
                output_dir / "duplicate.ngc"
                if authorization_mode["value"] == "duplicate"
                else output_dir / request.suggested_file_name
            )
            return authorize_native_output_path(
                request,
                destination,
            )

        def make_dispatcher(target_document, local_turn):
            def reauthorize() -> None:
                require_frozen_native_surface(local_turn.surface, controller)

            context = NativeRuntimeContext(
                service=service,
                document=target_document,
                state=state_store,
                undo_ledger=ledger,
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
                active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
                edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
                background_manager=manager,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                authorize_output=authorize,
            )
            return NativeTurnDispatcher(
                document=target_document,
                state=state_store,
                registry=registry,
                turn=local_turn,
                runtimes=build_native_runtime_bindings(context, local_turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = make_dispatcher(document, turn)

        invalid = {"job": _target(job), "processor": "provider_choice"}
        invalid_result = dispatcher.call(
            POST_JOB_CAPABILITY_NAME,
            json.dumps(invalid, separators=(",", ":")),
            "native-post-invalid",
        )
        assert invalid_result["ok"] is False
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        before = _document_state(document, state_store)
        workspaces_before = set(
            Path(tempfile.gettempdir()).glob("vibecad-native-post-*")
        )
        heartbeat = {"count": 0}
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: heartbeat.__setitem__("count", heartbeat["count"] + 1))
        timer.start()
        payload = {"job": _target(job)}
        started_at = time.monotonic()
        started = dispatcher.call(
            POST_JOB_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-post-success",
        )
        launch_elapsed = time.monotonic() - started_at
        assert started["ok"] is True and launch_elapsed < 1.5
        duplicate = dispatcher.call(
            POST_JOB_CAPABILITY_NAME,
            json.dumps(payload, separators=(",", ":")),
            "native-post-success",
        )
        assert duplicate == started
        terminal = _await(manager, started["job"]["job_id"])
        timer.stop()
        assert terminal.phase == "completed", (
            terminal,
            diagnostics.get(started["job"]["job_id"]),
        )
        assert heartbeat["count"] >= 5
        result = terminal.result
        assert result["operation"] == "complete_job"
        assert result["job"]["object_name"] == job.Name
        assert result["job"]["active_operation_count"] == 2
        assert result["job"]["posted_operation_count"] == 2
        assert result["job"]["command_count"] == (
            operation.Path.Size + second_operation.Path.Size
        )
        assert result["postprocessor"]["name"] == "linuxcnc"
        assert result["postprocessor"]["machine_configured"] is True
        assert result["output_count"] == 1
        assert len(requests) == 1
        assert authorizer_threads == [main_thread_id]
        artifact = result["outputs"][0]
        generated = output_dir / artifact["file_name"]
        assert generated.is_file() and generated.stat().st_size == artifact["size_bytes"]
        program = generated.read_text(encoding="utf-8")
        assert "G21" in program and ("G0" in program or "G00" in program)
        complete_program = generated.read_bytes()
        assert str(root) not in json.dumps(result, separators=(",", ":"))
        assert _document_state(document, state_store) == before
        assert set(Path(tempfile.gettempdir()).glob("vibecad-native-post-*")) == (
            workspaces_before
        )

        reversed_selection = dispatcher.call(
            POST_SELECTED_CAPABILITY_NAME,
            json.dumps(
                {
                    "job": _target(job),
                    "operations": [
                        _operation_target(second_operation),
                        _operation_target(operation),
                    ],
                },
                separators=(",", ":"),
            ),
            "native-post-selected-order-invalid",
        )
        assert reversed_selection["ok"] is False
        assert reversed_selection["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        selected_payload = {
            "job": _target(job),
            "operations": [_operation_target(operation)],
        }
        selected_started = dispatcher.call(
            POST_SELECTED_CAPABILITY_NAME,
            json.dumps(selected_payload, separators=(",", ":")),
            "native-post-selected-success",
        )
        assert selected_started["ok"] is True
        selected_terminal = _await(manager, selected_started["job"]["job_id"])
        assert selected_terminal.phase == "completed", (
            selected_terminal,
            diagnostics.get(selected_started["job"]["job_id"]),
        )
        selected_result = selected_terminal.result
        assert selected_result["operation"] == "selected_operations"
        assert selected_result["job"]["posted_operation_count"] == 1
        assert selected_result["job"]["command_count"] == operation.Path.Size
        assert selected_result["operations"] == [
            {
                "object_name": operation.Name,
                "state_sha256": _operation_target(operation)[
                    "expected_state_sha256"
                ],
            }
        ]
        selected_artifact = selected_result["outputs"][0]
        selected_program_path = output_dir / selected_artifact["file_name"]
        selected_program = selected_program_path.read_bytes()
        assert selected_program != complete_program
        assert len(selected_program) < len(complete_program)
        assert _document_state(document, state_store) == before

        def begin_post(target_dispatcher, target_job, call_id: str) -> str:
            value = target_dispatcher.call(
                POST_JOB_CAPABILITY_NAME,
                json.dumps(
                    {"job": _target(target_job)},
                    separators=(",", ":"),
                ),
                call_id,
            )
            assert value["ok"] is True, value
            return value["job"]["job_id"]

        configure_processor("split_gate")
        authorization_mode["value"] = "duplicate"
        duplicate_destination_id = begin_post(
            dispatcher,
            job,
            "native-post-duplicate-destination",
        )
        duplicate_destination = _await(manager, duplicate_destination_id)
        assert duplicate_destination.phase == "failed"
        assert duplicate_destination.error["error_code"] == (
            "NATIVE_OUTPUT_AUTHORIZATION_FAILED"
        )
        assert not (output_dir / "duplicate.ngc").exists()

        authorization_mode["value"] = "allow"
        split_id = begin_post(dispatcher, job, "native-post-split-output")
        split_terminal = _await(manager, split_id)
        assert split_terminal.phase == "completed", (
            split_terminal,
            diagnostics.get(split_id),
        )
        assert split_terminal.result["output_count"] == 2
        split_files = [
            output_dir / item["file_name"]
            for item in split_terminal.result["outputs"]
        ]
        assert len({path.name for path in split_files}) == 2
        assert all(path.is_file() and path.stat().st_size > 0 for path in split_files)
        assert _document_state(document, state_store) == before

        configure_processor("slow_gate")
        request_count = len(requests)
        marker.unlink(missing_ok=True)
        cancelled_id = begin_post(dispatcher, job, "native-post-cancel")
        _wait_for_file(marker)
        cancelled = dispatcher.call(
            NATIVE_BACKGROUND_CAPABILITY_NAME,
            json.dumps({"operation": "cancel", "job_id": cancelled_id}),
            "native-post-cancel-request",
        )
        assert cancelled["ok"] is True and cancelled["cancel_accepted"] is True
        cancelled_terminal = _await(manager, cancelled_id)
        assert cancelled_terminal.phase == "cancelled"
        assert len(requests) == request_count
        assert _document_state(document, state_store) == before
        assert not remote_marker.exists()

        marker.unlink(missing_ok=True)
        selection_id = begin_post(dispatcher, job, "native-post-selection-stale")
        _wait_for_file(marker)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        selection_terminal = _await(manager, selection_id)
        assert selection_terminal.phase == "failed"
        assert selection_terminal.error["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        assert _document_state(document, state_store) == before
        assert not remote_marker.exists()

        marker.unlink(missing_ok=True)
        source_id = begin_post(dispatcher, job, "native-post-source-stale")
        _wait_for_file(marker)
        slow_post_path.write_text(_SLOW_POST + "\n# changed after preflight\n", encoding="utf-8")
        source_terminal = _await(manager, source_id)
        assert source_terminal.phase == "failed"
        assert source_terminal.error["error_code"] == "NATIVE_MANUFACTURE_POST_UNAVAILABLE"
        slow_post_path.write_text(_SLOW_POST, encoding="utf-8")
        assert _document_state(document, state_store) == before
        assert not remote_marker.exists()

        marker.unlink(missing_ok=True)
        ribbon_id = begin_post(dispatcher, job, "native-post-ribbon-stale")
        _wait_for_file(marker)
        Gui.activateWorkbench("PartDesignWorkbench")
        _events(12)
        ribbon_terminal = _await(manager, ribbon_id)
        assert ribbon_terminal.phase == "failed"
        controller, registry, turn = _surface_and_turn()
        dispatcher = make_dispatcher(document, turn)
        assert _document_state(document, state_store) == before
        assert not remote_marker.exists()

        configure_processor("linuxcnc")
        existing_program = generated.read_bytes()
        authorization_mode["value"] = "cancel"
        cancelled_output_id = begin_post(dispatcher, job, "native-post-output-cancel")
        cancelled_output = _await(manager, cancelled_output_id)
        assert cancelled_output.phase == "failed"
        assert cancelled_output.error["error_code"] == (
            "NATIVE_MANUFACTURE_POST_OUTPUT_CANCELLED"
        )
        assert generated.read_bytes() == existing_program
        assert _document_state(document, state_store) == before
        authorization_mode["value"] = "allow"

        configure_processor("legacy_gate")
        legacy = dispatcher.call(
            POST_JOB_CAPABILITY_NAME,
            json.dumps(
                {"job": _target(job)},
                separators=(",", ":"),
            ),
            "native-post-legacy-rejected",
        )
        assert legacy["ok"] is False
        assert legacy["error_code"] == "NATIVE_MANUFACTURE_POST_PROCESSOR_UNSUPPORTED"
        assert _document_state(document, state_store) == before

        configure_processor("slow_gate")
        marker.unlink(missing_ok=True)
        revision_id = begin_post(dispatcher, job, "native-post-revision-stale")
        _wait_for_file(marker)
        state_store.note_structural_change(document_uid(document))
        revision_terminal = _await(manager, revision_id)
        assert revision_terminal.phase == "failed"
        assert revision_terminal.error["error_code"] == "NATIVE_REVISION_CONFLICT"
        revision_state = _document_state(document, state_store)
        assert revision_state | {"revision": before["revision"]} == before
        controller, registry, turn = _surface_and_turn()
        dispatcher = make_dispatcher(document, turn)

        marker.unlink(missing_ok=True)
        secondary = App.newDocument("NativeManufacturePostCloseGate")
        secondary.UndoMode = 1
        secondary_model, secondary_job, _secondary_operation = _create_fixture(secondary)
        secondary_job.Machine = machine.name
        secondary_job.PostProcessorOutputFile = "CloseProgram-%S.ngc"
        assert secondary.recompute(None, True, True) is not False
        secondary.clearUndos()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(secondary_model, "Face1")
        secondary_dispatcher = make_dispatcher(secondary, turn)
        close_id = begin_post(
            secondary_dispatcher,
            secondary_job,
            "native-post-document-close",
        )
        _wait_for_file(marker)
        App.closeDocument(secondary.Name)
        secondary = None
        close_terminal = _await(manager, close_id)
        assert close_terminal.phase == "failed"
        App.setActiveDocument(document.Name)
        _events(8)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        assert not remote_marker.exists()

        print(
            "VIBECAD_NATIVE_MANUFACTURE_POST_GUI_OK "
            "closed_schema=true no_provider_processor=true exact_job=true "
            "selected_operations=true exact_operation_order=true "
            "configured_machine=true isolated_freecadcmd=true background=true "
            "gui_responsive=true cancel=true human_authorized=true output_cancel=true "
            "atomic_output=true split_output=true duplicate_destination=true "
            "selection_stale=true revision_stale=true "
            "processor_stale=true document_close=true ribbon_switch=true "
            "legacy_rejected=true remote_disabled=true duplicate_guard=true "
            "workspace_cleanup=true bounded_result=true document_unchanged=true "
            "history=true undo=true redo=true selection=true visibility=true low_noise=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if secondary is not None and App.getDocument(secondary.Name) is not None:
            App.closeDocument(secondary.Name)
        if document is not None and App.getDocument(document.Name) is not None:
            App.closeDocument(document.Name)
        MachineFactory._config_dir = old_machine_dir
        if advanced_present:
            preferences.SetBool(
                PathPreferences.EnableAdvancedOCLFeatures,
                advanced_before,
            )
        else:
            preferences.RemBool(PathPreferences.EnableAdvancedOCLFeatures)
        os.environ.pop("VIBECAD_NATIVE_POST_GATE_MARKER", None)
        os.environ.pop("VIBECAD_NATIVE_POST_REMOTE_MARKER", None)
        if inserted_post_path is not None:
            try:
                sys.path.remove(inserted_post_path)
            except ValueError:
                pass
        if temporary is not None:
            temporary.cleanup()
        QtWidgets.QApplication.instance().exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
