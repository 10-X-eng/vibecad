# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for the Native CAM Profile operation."""

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

import Path.Main.Gui.Job as PathJobGui
import Path.Main.Job as PathJob
import Path.Base.Util as PathUtil
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureProfile import (
    ProfileCreateSpec,
    _assert_preflight_current,
    preflight_profile_create,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


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


def _create_model_and_job(
    document,
    *,
    model_name: str = "ProfileGateModel",
    job_name: str = "ProfileJob",
    x_offset_mm: float = 0.0,
):
    def create_model():
        model = document.addObject("Part::Feature", model_name)
        model.Label = f"{job_name} model"
        model.Shape = Part.makeBox(
            40.0,
            30.0,
            10.0,
            App.Vector(x_offset_mm, 0.0, 0.0),
        )
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        return model

    model = _commit(document, "Create Profile gate model", create_model)

    def create_job():
        job = PathJob.Create(job_name, [model], templateFile=None)
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

    return model, _commit(document, "Create Profile gate Job", create_job)


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


def _top_face_name(model) -> str:
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for index, face in enumerate(model.Shape.Faces, start=1):
        if all(abs(float(vertex.Point.z) - maximum_z) <= 1e-9 for vertex in face.Vertexes):
            return f"Face{index}"
    raise AssertionError("The gate model has no exact top face")


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("profile",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    for field in (
        "tool_controller",
        "subelements",
        "cut_side",
        "step_down_mm",
        "clearance_height_mm",
        "profile_noncircular_holes",
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


def _arguments(model, job) -> dict:
    state = job_state(job)
    controller = state["tools"][0]
    job_model = next(
        item for item in state["models"] if item["object_name"] == model.Name
    )
    return {
        "operation": "profile",
        "label": "Native exterior Profile",
        "job": _target(state),
        "tool_controller": _target(controller),
        "geometry": {
            "kind": "subelements",
            "items": [
                {
                    "model": _target(job_model),
                    "subelements": [_top_face_name(model)],
                }
            ],
        },
        "profile": {
            "direction": "clockwise",
            "cut_side": "outside",
            "cutter_compensation": True,
            "extra_offset_mm": 0.0,
            "pass_count": 1,
            "stepover_mm": 0.0,
            "multiple_features": "individually",
            "sorting": "automatic",
            "start_on_longest_edge": False,
            "profile_outer_perimeter": True,
            "profile_noncircular_holes": False,
            "profile_circular_holes": False,
        },
        "depths": {
            "start_depth_mm": 10.0,
            "final_depth_mm": 0.0,
            "step_down_mm": 2.0,
        },
        "heights": {
            "safe_height_mm": 12.0,
            "clearance_height_mm": 15.0,
        },
        "coolant": "none",
    }


def _assert_profile_graph(
    document,
    job,
    operation,
    model,
    face_name: str,
    *,
    diagnostics_required: bool = True,
) -> None:
    assert operation in tuple(job.Operations.Group)
    assert operation is job.Operations.Group[-1]
    assert operation.VibeCADTimelineRole == "operation"
    assert PathUtil.timelineParentJob(operation) is job
    assert operation.ToolController in tuple(job.Tools.Group)
    assert operation.ViewObject.Proxy.__class__.__name__ == "ViewProvider"
    if hasattr(operation.ViewObject.Proxy, "deleteOnReject"):
        assert operation.ViewObject.Proxy.deleteOnReject is False
    assert tuple(operation.Base) == ((job.Model.Group[0], (face_name,)),)
    assert job.Proxy.baseObject(job, operation.Base[0][0]) is model
    assert operation.Label == "Native exterior Profile"
    assert operation.Direction == "CW"
    assert operation.Side == "Outside"
    assert operation.UseComp is True
    assert operation.NumPasses == 1
    assert operation.HandleMultipleFeatures == "Individually"
    assert operation.SortingMode == "Automatic"
    assert operation.processPerimeter is True
    assert operation.processHoles is False
    assert operation.processCircles is False
    assert operation.UseStartPoint is False
    assert tuple(document.VibeCADTimeline.Operations)[-1] is operation
    commands = tuple(operation.Path.Commands)
    assert any(command.Name in {"G1", "G2", "G3"} for command in commands)
    if diagnostics_required:
        diagnostics = operation.Proxy.getGenerationDiagnostics(operation)
        assert diagnostics["status"] == "succeeded", diagnostics
        assert diagnostics["stage"] == "complete", diagnostics
        assert diagnostics["error"] is None, diagnostics
        assert int(diagnostics["cutting_command_count"]) >= 1, diagnostics


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-profile-")
        save_path = Path(temporary.name) / "native-manufacture-profile.FCStd"
        document = App.newDocument("NativeManufactureProfileGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert (
            plans["CAM_Profile"].capability_family,
            plans["CAM_Profile"].operation_variant,
            plans["CAM_Profile"].exact_target_type,
            plans["CAM_Profile"].classification.mutation,
            plans["CAM_Profile"].classification.human_only,
        ) == (
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            "profile",
            "ExactCamJobProfileGeometryControllerAndParameters",
            True,
            False,
        )

        model, job = _create_model_and_job(document)
        _other_model, other_job = _create_model_and_job(
            document,
            model_name="OtherSetupModel",
            job_name="OtherSetup",
            x_offset_mm=60.0,
        )
        face_name = _top_face_name(model)
        initial_names = tuple(obj.Name for obj in document.Objects)
        initial_operations = tuple(job.Operations.Group)
        initial_timeline = tuple(document.VibeCADTimeline.Operations)
        arguments = _arguments(model, job)

        boundary = preflight_profile_create(
            document,
            ProfileCreateSpec(
                label=arguments["label"],
                job=arguments["job"],
                tool_controller=arguments["tool_controller"],
                geometry=arguments["geometry"],
                profile=arguments["profile"],
                depths=arguments["depths"],
                heights=arguments["heights"],
                coolant=arguments["coolant"],
            ),
        )
        other_postprocessor_args = str(other_job.PostProcessorArgs)
        other_job.PostProcessorArgs = "--other-setup-change"
        try:
            try:
                _assert_preflight_current(document, boundary)
            except NativeManufactureError as exc:
                assert exc.error_code == "NATIVE_MANUFACTURE_STATE_STALE"
            else:
                raise AssertionError(
                    "Profile preflight did not detect another setup changing"
                )
        finally:
            other_job.PostProcessorArgs = other_postprocessor_args
        other_job_before = job_state(other_job)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-profile-gui")

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
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-profile-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Edge1")
        selection_before = _selection()
        revision_before = state_store.current_revision(context.document_uid)
        undo_before = int(document.UndoCount)

        stale = json.loads(json.dumps(arguments))
        stale["geometry"]["items"][0]["model"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline
        assert int(document.UndoCount) == undo_before

        invalid = json.loads(json.dumps(arguments))
        invalid["profile"]["stepover_mm"] = 1.0
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == initial_names
        assert tuple(job.Operations.Group) == initial_operations
        assert int(document.UndoCount) == undo_before

        result = call(arguments)
        _events(12)
        assert job_state(other_job)["state_sha256"] == other_job_before["state_sha256"]
        operation_name = result["profile"]["object_name"]
        operation = document.getObject(operation_name)
        assert operation is not None
        _assert_profile_graph(document, job, operation, model, face_name)
        assert result["profile"]["geometry"] == {
            "kind": "subelements",
            "items": [{"object_name": model.Name, "subelements": [face_name]}],
        }
        assert result["profile"]["cutting_command_count"] >= 1
        assert result["job"]["operation_count"] == len(initial_operations) + 1
        assert [item["object_name"] for item in result["receipt"]["created"]] == [
            operation_name
        ]
        assert result["assistant_undo_available"] is True
        assert int(document.UndoCount) == undo_before + 1
        assert state_store.current_revision(context.document_uid) == revision_before + 1
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()
        created_state = operation_state(operation)

        document.undo()
        _events(12)
        assert document.getObject(operation_name) is None
        assert tuple(job.Operations.Group) == initial_operations
        assert tuple(document.VibeCADTimeline.Operations) == initial_timeline

        document.redo()
        _events(12)
        model = document.getObject("ProfileGateModel")
        job = document.getObject("ProfileJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_profile_graph(document, job, operation, model, face_name)
        assert operation_state(operation)["state_sha256"] == created_state["state_sha256"]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        model = document.getObject("ProfileGateModel")
        job = document.getObject("ProfileJob")
        operation = document.getObject(operation_name)
        assert model is not None and job is not None and operation is not None
        _assert_profile_graph(
            document,
            job,
            operation,
            model,
            face_name,
            diagnostics_required=False,
        )
        reopened_state = operation_state(operation)
        assert reopened_state["state_sha256"] == created_state["state_sha256"], (
            created_state,
            reopened_state,
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_PROFILE_GUI_OK "
            "exact_targets=true geometry=true parameters=true toolpath=true "
            "multi_setup=true history=true rollback=true undo=true redo=true reopen=true",
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
