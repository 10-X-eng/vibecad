# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native CAM program stops."""

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
import Path.Op.Gui.Stop as StopGui
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


def _arguments(job, *, label="Native CAM Stop", stop_mode="optional"):
    return {
        "operation": "stop",
        "label": label,
        "job": _target(job_state(job)),
        "stop_mode": stop_mode,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_PROGRAM_CAPABILITY_NAME)
    assert definition is not None
    full_schema = definition.provider_schema(("comment", "stop"))
    full_parameters = full_schema["parameters"]
    assert full_parameters["additionalProperties"] is False
    assert full_parameters["properties"]["operation"]["enum"] == [
        "comment",
        "stop",
    ]
    assert full_parameters["properties"]["operation"]["description"] == (
        "Fields: comment=label,job,comment; stop=label,job,stop_mode."
    )
    assert "anyOf" not in full_parameters["properties"]["label"]

    schema = definition.provider_schema(("stop",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "operation",
        "label",
        "job",
        "stop_mode",
    }
    assert variant["properties"]["stop_mode"]["enum"] == [
        "optional",
        "mandatory",
    ]
    assert "M1" in variant["properties"]["stop_mode"]["description"]
    assert "M0" in variant["properties"]["stop_mode"]["description"]
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
    model = document.addObject("Part::Feature", "StopGateModel")
    model.Label = "Stop gate model"
    model.Shape = Part.makeBox(18.0, 14.0, 6.0)
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


def _assert_stop(operation, job, *, mode: str, command: str) -> None:
    assert isinstance(operation.Proxy, StopGui.Stop)
    assert isinstance(operation.ViewObject.Proxy, StopGui._ViewProviderStop)
    assert str(operation.Stop) == mode.capitalize()
    assert tuple(value.toGCode() for value in operation.Path.Commands) == (command,)
    assert PathUtils.findParentJob(operation) is job
    assert PathUtil.timelineParentJob(operation) is job
    assert tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ()) == ()


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-stop-")
        save_path = Path(temporary.name) / "native-manufacture-stop.FCStd"
        document = App.newDocument("NativeManufactureStopGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Stop"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            MANUFACTURE_PROGRAM_CAPABILITY_NAME,
            "stop",
            "ExactCamJobAndProgramStop",
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
        ledger.begin_run("native-manufacture-stop-gui")

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
                f"native-manufacture-stop-{call_index}",
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

        invalid = _arguments(job)
        invalid["stop_mode"] = "pause"
        invalid_result = call(invalid, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(job.Operations.Group) == ()

        document.openTransaction("Caller-owned stop transaction")
        transaction = int(document.getBookedTransactionID())
        blocked = call(_arguments(job), succeeds=False)
        assert blocked["error_code"] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)
        assert not document.HasPendingTransaction

        before_objects = tuple(document.Objects)
        before_group = tuple(job.Operations.Group)
        before_visibility = _visibility(document)
        before_timeline = _timeline(document)
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufactureProgramRuntime.verify_created_stop",
            side_effect=RuntimeError("forced stop postcondition failure"),
        ):
            failed = call(_arguments(job), succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(job.Operations.Group) == before_group
        assert _visibility(document) == before_visibility
        assert _timeline(document) == before_timeline
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo

        optional_result = call(
            _arguments(job, label="Optional Inspection Stop", stop_mode="optional")
        )
        optional = document.getObject(optional_result["object_name"])
        optional_name = str(optional.Name)
        _assert_stop(optional, job, mode="optional", command="M1")
        assert optional_result["stop_mode"] == "optional"
        assert optional_result["path_sha256"] == operation_state(optional)["path_sha256"]
        assert len(optional_result["receipt"]["created"]) == 1
        assert len(optional_result["receipt"]["changed"]) == 1
        assert _selection() == selection_before

        mandatory_result = call(
            _arguments(job, label="Mandatory Setup Stop", stop_mode="mandatory")
        )
        mandatory = document.getObject(mandatory_result["object_name"])
        mandatory_name = str(mandatory.Name)
        job_name = str(job.Name)
        _assert_stop(mandatory, job, mode="mandatory", command="M0")
        assert mandatory_result["stop_mode"] == "mandatory"
        assert tuple(job.Operations.Group) == (optional, mandatory)
        assert mandatory_result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert _visibility(document)[: len(before_visibility)] == before_visibility
        assert int(document.UndoCount) == before_undo + 2

        document.undo()
        _events(12)
        job = document.getObject(job_name)
        optional = document.getObject(optional_name)
        assert document.getObject(mandatory_name) is None
        assert tuple(job.Operations.Group) == (optional,)
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        optional = document.getObject(optional_name)
        mandatory = document.getObject(mandatory_name)
        assert tuple(job.Operations.Group) == (optional, mandatory)
        _assert_stop(optional, job, mode="optional", command="M1")
        _assert_stop(mandatory, job, mode="mandatory", command="M0")

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        job = document.getObject(job_name)
        optional = document.getObject(optional_name)
        mandatory = document.getObject(mandatory_name)
        assert tuple(job.Operations.Group) == (optional, mandatory)
        _assert_stop(optional, job, mode="optional", command="M1")
        _assert_stop(mandatory, job, mode="mandatory", command="M0")

        print(
            "VIBECAD_NATIVE_MANUFACTURE_STOP_GUI_OK "
            "ribbon=true exact_job=true closed_schema=true stale=true "
            "invalid_mode=true transaction_guard=true rollback=true "
            "optional=true mandatory=true source_preserved=true job=true "
            "history=true receipt=true selection=true visibility=true "
            "undo=true redo=true reopen=true"
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
