# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native CAM comments."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Base.Util as PathUtil
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Comment as CommentGui
import PathScripts.PathUtils as PathUtils
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureProgramSchema import (
    MANUFACTURE_PROGRAM_CAPABILITY_NAME,
)
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
    assert surface.surface_id == "manufacture"
    program = next(group for group in surface.groups if group.label == "Program")
    assert tuple(action.command_id for action in program.actions) == (
        "CAM_Comment",
        "CAM_Stop",
        "CAM_Custom",
        "CAM_Probe",
    )
    return controller, surface


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _arguments(job, *, label="Native CAM Comment", comment="Inspect setup before run"):
    return {
        "operation": "comment",
        "label": label,
        "job": _target(job_state(job)),
        "comment": comment,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_PROGRAM_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("comment",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "operation",
        "label",
        "job",
        "comment",
    }
    comment_schema = variant["properties"]["comment"]
    assert comment_schema["minLength"] == 1
    assert comment_schema["maxLength"] == 1024
    assert "parentheses" in comment_schema["description"]
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_PROGRAM_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _fixture(document):
    model = document.addObject("Part::Feature", "CommentGateModel")
    model.Label = "Comment gate model"
    model.Shape = Part.makeBox(20.0, 15.0, 5.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None
    assert document.recompute(None, True, True) is not False
    return model, job


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
    timeline = document.VibeCADTimeline
    return (
        tuple(timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-comment-")
        save_path = Path(temporary.name) / "native-manufacture-comment.FCStd"
        document = App.newDocument("NativeManufactureCommentGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Comment"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            MANUFACTURE_PROGRAM_CAPABILITY_NAME,
            "comment",
            "ExactCamJobAndProgramComment",
            True,
            False,
            False,
        )

        model, job = _fixture(document)
        document.saveAs(str(save_path))
        document.clearUndos()
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-comment-gui")

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
                MANUFACTURE_PROGRAM_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-comment-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale = _arguments(job)
        stale["job"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        injection = _arguments(job)
        injection["comment"] = "setup complete)\nG0 X0 Y0 ("
        invalid_result = call(injection, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(job.Operations.Group) == ()

        unicode_comment = _arguments(job)
        unicode_comment["comment"] = "Caf\u00e9"
        unicode_result = call(unicode_comment, succeeds=False)
        assert unicode_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert unicode_result["argument_error"]["path"] == ["comment"]
        assert unicode_result["argument_error"]["rule"] == "pattern"
        assert "valid_example" in unicode_result["argument_error"]

        document.openTransaction("Caller-owned comment transaction")
        transaction = int(document.getBookedTransactionID())
        assert transaction
        blocked = call(_arguments(job), succeeds=False)
        assert blocked["error_code"] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)
        assert not document.HasPendingTransaction
        assert tuple(job.Operations.Group) == ()

        before_objects = tuple(document.Objects)
        before_group = tuple(job.Operations.Group)
        before_visibility = _visibility(document)
        before_timeline = _timeline(document)
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufactureProgramRuntime.verify_created_comment",
            side_effect=RuntimeError("forced comment postcondition failure"),
        ):
            failed = call(_arguments(job), succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(job.Operations.Group) == before_group
        assert _visibility(document) == before_visibility
        assert _timeline(document) == before_timeline
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo
        assert not document.HasPendingTransaction

        result = call(
            _arguments(
                job,
                label="Verify Workholding",
                comment="Verify workholding before spindle start",
            )
        )
        output = document.getObject(result["object_name"])
        output_name = str(output.Name)
        job_name = str(job.Name)
        assert isinstance(output.Proxy, CommentGui.Comment)
        assert isinstance(output.ViewObject.Proxy, CommentGui._ViewProviderComment)
        assert output.Label == "Verify Workholding"
        assert output.Comment == "Verify workholding before spindle start"
        assert tuple(command.toGCode() for command in output.Path.Commands) == (
            "(Verify workholding before spindle start)",
        )
        assert PathUtils.findParentJob(output) is job
        assert PathUtil.timelineParentJob(output) is job
        assert tuple(getattr(output, "VibeCADTimelineReplacedInputs", ()) or ()) == ()
        assert tuple(job.Operations.Group) == (output,)
        assert result["comment_length"] == 39
        assert result["command_count"] == 1
        assert result["path_sha256"] == operation_state(output)["path_sha256"]
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["changed"]) == 1
        assert result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert _visibility(document)[: len(before_visibility)] == before_visibility
        assert int(document.UndoCount) == before_undo + 1

        document.undo()
        _events(12)
        job = document.getObject(job_name)
        assert document.getObject(output_name) is None
        assert tuple(job.Operations.Group) == ()
        assert _selection() == selection_before
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        output = document.getObject(output_name)
        assert output in job.Operations.Group
        assert output.Path.Commands[0].toGCode() == (
            "(Verify workholding before spindle start)"
        )

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        job = document.getObject(job_name)
        output = document.getObject(output_name)
        assert isinstance(output.Proxy, CommentGui.Comment)
        assert isinstance(output.ViewObject.Proxy, CommentGui._ViewProviderComment)
        assert output in job.Operations.Group
        assert PathUtils.findParentJob(output) is job
        assert PathUtil.timelineParentJob(output) is job
        assert tuple(getattr(output, "VibeCADTimelineReplacedInputs", ()) or ()) == ()
        assert output.Path.Commands[0].toGCode() == (
            "(Verify workholding before spindle start)"
        )

        print(
            "VIBECAD_NATIVE_MANUFACTURE_COMMENT_GUI_OK "
            "ribbon=true exact_job=true closed_schema=true stale=true "
            "injection_guard=true ascii_guard=true transaction_guard=true "
            "rollback=true text=true "
            "source_preserved=true job=true history=true receipt=true "
            "selection=true visibility=true undo=true redo=true reopen=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        if application is not None:
            application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
