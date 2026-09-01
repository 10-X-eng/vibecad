# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Drilling and Tapping."""

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
import Path.Tool.Controller as PathToolController
from Path.Tool.toolbit import ToolBit
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


CAPABILITY_NAME = MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES["drilling"]


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _await_job(manager: NativeBackgroundManager, job_id: str) -> dict:
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        _events(1)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            assert snapshot.phase == "completed", snapshot
            assert snapshot.result is not None
            return snapshot.result
        time.sleep(0.01)
    raise AssertionError("The isolated Drilling path gate timed out")


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
        model = document.addObject("Part::Feature", "DrillingGateModel")
        model.Label = "Drilling gate model"
        blank = Part.makeBox(50.0, 40.0, 12.0)
        left = Part.makeCylinder(7.0, 12.0, App.Vector(14.0, 20.0, 0.0))
        right = Part.makeCylinder(7.0, 12.0, App.Vector(36.0, 20.0, 0.0))
        model.Shape = blank.cut(left.fuse(right))
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Drilling gate model", create_model)

    def create_job():
        job = PathJob.Create("DrillingJob", [model], templateFile=None)
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

    job = _commit(document, "Create Drilling gate Job", create_job)

    def create_controller(tool_file: str, name: str, label: str, spindle_speed: int):
        extension = PathUtil.stageTimelineResourceGraphExtension(job)
        toolbit = ToolBit.from_file(
            Path(App.getResourceDir()).parent
            / "Mod"
            / "CAM"
            / "Tools"
            / "Bit"
            / tool_file
        )
        tool = toolbit.attach_to_doc(doc=document, timeline_owner=job)
        controller = PathToolController.Create(
            name=name,
            tool=tool,
            toolNumber=max(int(value.ToolNumber) for value in job.Tools.Group) + 1,
            document=document,
            timelineOwner=job,
        )
        controller.Label = label
        controller.SpindleSpeed = spindle_speed
        controller.VertFeed = "750 mm/min"
        controller.HorizFeed = "750 mm/min"
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

    drill_controller = _commit(
        document,
        "Create Drilling gate drill controller",
        lambda: create_controller(
            "5mm_Drill.fctb",
            "DrillController",
            "5 mm drill controller",
            1800,
        ),
    )
    tap_controller = _commit(
        document,
        "Create Drilling gate tap controller",
        lambda: create_controller(
            "M8x1.25_Tap.fctb",
            "TapController",
            "M8 x 1.25 tapping controller",
            600,
        ),
    )
    return model, job, drill_controller, tap_controller


def _hole_faces(model) -> tuple[str, str]:
    found = []
    for index, face in enumerate(model.Shape.Faces, start=1):
        if isinstance(face.Surface, Part.Cylinder):
            found.append((round(float(face.Surface.Center.x), 9), f"Face{index}"))
    assert len(found) == 2, found
    found.sort()
    return found[0][1], found[1][1]


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


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("drilling",))
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


def _controller_target(state: dict, controller) -> dict:
    controller_state = next(
        value for value in state["tools"] if value["object_name"] == controller.Name
    )
    return _target(controller_state)


def _model_target(state: dict, model) -> dict:
    model_state = next(
        value for value in state["models"] if value["object_name"] == model.Name
    )
    return _target(model_state)


def _drill_arguments(model, job, controller, left_face: str, right_face: str) -> dict:
    state = job_state(job)
    return {
        "job": _target(state),
        "tool_controller": _controller_target(state, controller),
        "geometry": [
            {
                "model": _model_target(state, model),
                "subelements": [left_face, right_face],
            }
        ],
    }


def _job_resource(job, model):
    matches = tuple(
        resource
        for resource in job.Model.Group
        if job.Proxy.baseObject(job, resource) is model
    )
    assert len(matches) == 1, matches
    return matches[0]


def _assert_operation(
    document,
    job,
    operation,
    *,
    expected_base: tuple,
    cycle_count: int,
    diagnostics_required: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController in tuple(job.Tools.Group)
    assert operation.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
    if hasattr(operation.ViewObject.Proxy, "deleteOnReject"):
        assert operation.ViewObject.Proxy.deleteOnReject is False
    assert tuple(operation.Base) == expected_base
    assert tuple(operation.Locations) == ()
    assert operation.Label
    assert operation.Strategy == "Drilling"
    expressions = {str(name) for name, _expression in operation.ExpressionEngine}
    assert {"StartDepth", "FinalDepth", "SafeHeight", "ClearanceHeight"} <= expressions
    assert operation.AddTipLength is False
    assert operation.UseEndPoint is False
    assert tuple(document.VibeCADTimeline.Operations).count(operation) == 1
    commands = tuple(operation.Path.Commands)
    assert sum(command.Name == "G81" for command in commands) == cycle_count
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-drilling-")
        save_path = Path(temporary.name) / "native-manufacture-drilling.FCStd"
        document = App.newDocument("NativeManufactureDrillingGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        ribbon_controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Drilling"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        ) == (
            CAPABILITY_NAME,
            "drilling",
            "ExactCamJobDrillableGeometryAndController",
            True,
            False,
        )

        model, job, drill_controller, _tap_controller = _create_model_and_job(document)
        left_face, right_face = _hole_faces(model)
        model_resource = _job_resource(job, model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        background = service.native_background_manager()
        undo_ledger = NativeAssistantUndoLedger()
        undo_ledger.begin_run("native-manufacture-drilling-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, ribbon_controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=undo_ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                ribbon_controller
            ).surface_id,
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
                f"native-manufacture-drilling-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            if succeeds and response.get("next", {}).get("tool") == "native.job":
                assert response["job"]["resource_scope"] == (
                    f"manufacture:{payload['job']['object_name']}"
                )
                active_jobs = service.native_active_snapshot()["domain"][
                    "background_jobs"
                ]
                assert any(
                    item["job_id"] == response["job"]["job_id"]
                    and item["resource_scope"] == response["job"]["resource_scope"]
                    and item["terminal"] is False
                    for item in active_jobs
                )
                return _await_job(background, response["job"]["job_id"])
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, left_face)
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)
        drill_arguments = _drill_arguments(
            model,
            job,
            drill_controller,
            left_face,
            right_face,
        )

        stale = json.loads(json.dumps(drill_arguments))
        stale["geometry"][0]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert int(document.UndoCount) == undo_before

        drill_result = call(drill_arguments)
        _events(12)
        drill_name = drill_result["drilling"]["object_name"]
        drill_operation = document.getObject(drill_name)
        assert drill_operation is not None
        _assert_operation(
            document,
            job,
            drill_operation,
            expected_base=((model_resource, (left_face, right_face)),),
            cycle_count=2,
        )
        assert drill_operation.SortingMode == "Automatic"
        assert drill_operation.PeckEnabled is False
        assert drill_operation.DwellEnabled is False
        assert drill_operation.FeedRetractEnabled is False
        assert drill_operation.ExtraOffset == "None"
        assert drill_operation.KeepToolDown is False
        assert tuple(drill_operation.Disabled) == ()
        assert drill_result["drilling"]["enabled_target_count"] == 2
        assert drill_result["drilling"]["cycle_command"] == "G81"
        assert drill_result["drilling"]["geometry"] == {
            "kind": "subelements",
            "items": [
                {
                    "object_name": model.Name,
                    "subelements": [left_face, right_face],
                }
            ],
        }
        assert drill_result["drilling"]["parameters"]["source"] == "setup_defaults"
        assert drill_result["drilling"]["cutting_command_count"] == 0
        assert drill_result["job"]["operation_count"] == len(initial_operations) + 1
        assert int(document.UndoCount) == undo_before + 1
        assert state_store.current_revision(context.document_uid) == revision_before + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        drill_state = operation_state(drill_operation)

        document.undo()
        _events(12)
        assert document.getObject(drill_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        model = document.getObject("DrillingGateModel")
        job = document.getObject("DrillingJob")
        model_resource = _job_resource(job, model)
        drill_operation = document.getObject(drill_name)
        assert model is not None and job is not None and drill_operation is not None
        _assert_operation(
            document,
            job,
            drill_operation,
            expected_base=((model_resource, (left_face, right_face)),),
            cycle_count=2,
        )
        assert operation_state(drill_operation)["state_sha256"] == drill_state["state_sha256"]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("DrillingGateModel")
        job = document.getObject("DrillingJob")
        model_resource = _job_resource(job, model)
        drill_operation = document.getObject(drill_name)
        assert model is not None and job is not None and drill_operation is not None
        _assert_operation(
            document,
            job,
            drill_operation,
            expected_base=((model_resource, (left_face, right_face)),),
            cycle_count=2,
            diagnostics_required=False,
        )
        assert operation_state(drill_operation)["state_sha256"] == drill_state["state_sha256"]

        print(
            "VIBECAD_NATIVE_MANUFACTURE_DRILLING_GUI_OK "
            "exact_targets=true defaults=true drilling=true cycles=true "
            "toolpath=true history=true rollback=true undo=true redo=true reopen=true",
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
