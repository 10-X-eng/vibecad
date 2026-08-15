# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for structured Native CAM Custom operations."""

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
import Path.Op.Custom as PathCustom
import Path.Op.Gui.Base as PathOpGui
import PathScripts.PathUtils as PathUtils
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureProgramSchema import (
    MANUFACTURE_PROGRAM_CAPABILITY_NAME,
)
from VibeCADNativeManufactureState import (
    job_state,
    operation_state,
    tool_controller_state,
)
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


def _blocks() -> list[dict]:
    return [
        {"kind": "comment", "comment": "Touch-off motion"},
        {
            "kind": "command",
            "code": "G0",
            "parameters": [
                {"word": "X", "value": 2.0},
                {"word": "Y", "value": 3.0},
                {"word": "Z", "value": 5.0},
            ],
        },
        {
            "kind": "command",
            "code": "G1",
            "parameters": [
                {"word": "Z", "value": -0.5},
                {"word": "F", "value": 80.0},
            ],
        },
        {
            "kind": "command",
            "code": "G1",
            "parameters": [
                {"word": "X", "value": 12.0},
                {"word": "F", "value": 120.0},
            ],
        },
    ]


def _arguments(job, controller, *, label="Native Structured Custom", coolant="flood"):
    return {
        "operation": "custom",
        "label": label,
        "job": _target(job_state(job)),
        "tool_controller": _target(tool_controller_state(controller)),
        "coolant": coolant,
        "blocks": _blocks(),
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_PROGRAM_CAPABILITY_NAME)
    assert definition is not None
    full = definition.provider_schema(("comment", "stop", "custom"))
    full_parameters = full["parameters"]
    assert full_parameters["additionalProperties"] is False
    assert full_parameters["properties"]["operation"]["enum"] == [
        "comment",
        "stop",
        "custom",
    ]
    assert full_parameters["properties"]["operation"]["description"] == (
        "Fields: comment=label,job,comment; stop=label,job,stop_mode; "
        "custom=label,job,tool_controller,coolant,blocks."
    )

    schema = definition.provider_schema(("custom",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    assert "gcode" not in encoded.lower()
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "operation",
        "label",
        "job",
        "tool_controller",
        "coolant",
        "blocks",
    }
    assert set(variant["properties"]) == set(variant["required"])
    assert not {"file", "path", "gcode", "source"} & set(variant["properties"])
    blocks = variant["properties"]["blocks"]
    assert (blocks["minItems"], blocks["maxItems"]) == (1, 64)
    command, comment = blocks["items"]["oneOf"]
    assert command["additionalProperties"] is False
    assert comment["additionalProperties"] is False
    assert command["properties"]["parameters"]["maxItems"] == 16
    assert comment["properties"]["comment"]["maxLength"] == 256
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
    model = document.addObject("Part::Feature", "CustomGateModel")
    model.Label = "Custom gate model"
    model.Shape = Part.makeBox(20.0, 16.0, 6.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None
    assert tuple(job.Tools.Group)
    controller = job.Tools.Group[0]
    assert int(controller.ToolNumber) != 0
    assert document.recompute(None, True, True) is not False
    return model, job, controller


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


def _assert_custom(operation, job, controller, *, coolant: str) -> None:
    assert isinstance(operation.Proxy, PathCustom.ObjectCustom)
    assert not isinstance(operation.Proxy, PathCustom.ObjectEmbeddedPath)
    assert isinstance(operation.ViewObject.Proxy, PathOpGui.ViewProvider)
    assert str(operation.Source) == "Text"
    assert str(operation.GcodeFile) == ""
    assert tuple(operation.Gcode) == (
        "(Touch-off motion)",
        "G0 X2.000000 Y3.000000 Z5.000000",
        "G1 F80.000000 Z-0.500000",
        "G1 F120.000000 X12.000000",
    )
    assert PathUtils.findParentJob(operation) is job
    assert PathUtil.timelineParentJob(operation) is job
    assert PathUtil.toolControllerForOp(operation) is controller
    assert PathUtil.coolantModeForOp(operation) == coolant.capitalize()
    assert tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ()) == ()
    names = tuple(command.Name for command in operation.Path.Commands)
    if coolant == "flood":
        assert names == (
            f"({operation.Label})",
            "(Begin Custom)",
            "(Touch-off motion)",
            "G0",
            "M8",
            "G1",
            "G1",
            "M9",
            "(End Custom)",
        )
    else:
        assert names == (
            f"({operation.Label})",
            "(Begin Custom)",
            "(Touch-off motion)",
            "G0",
            "G1",
            "G1",
            "(End Custom)",
        )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-custom-")
        save_path = Path(temporary.name) / "native-manufacture-custom.FCStd"
        document = App.newDocument("NativeManufactureCustomGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_Custom"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            MANUFACTURE_PROGRAM_CAPABILITY_NAME,
            "custom",
            "ExactCamJobControllerAndStructuredCustomProgram",
            True,
            False,
            False,
        )

        model, job, tool_controller = _fixture(document)
        document.saveAs(str(save_path))
        document.clearUndos()
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-custom-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                controller_widget
            ).surface_id,
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
                f"native-manufacture-custom-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()

        stale_job = _arguments(job, tool_controller)
        stale_job["job"]["expected_state_sha256"] = "0" * 64
        assert call(stale_job, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        stale_controller = _arguments(job, tool_controller)
        stale_controller["tool_controller"]["expected_state_sha256"] = "0" * 64
        assert call(stale_controller, succeeds=False)["error_code"] == (
            "NATIVE_MANUFACTURE_STATE_STALE"
        )

        raw = _arguments(job, tool_controller)
        raw["gcode"] = ["G1 X0", "M30"]
        assert call(raw, succeeds=False)["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        bad_code = _arguments(job, tool_controller)
        bad_code["blocks"][1]["code"] = "G1 X0; M30"
        assert call(bad_code, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        injected = _arguments(job, tool_controller)
        injected["blocks"][0]["comment"] = "close) M30 (open"
        assert call(injected, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )

        duplicate = _arguments(job, tool_controller)
        duplicate["blocks"][1]["parameters"].append(
            {"word": "X", "value": 9.0}
        )
        assert call(duplicate, succeeds=False)["error_code"] == (
            "NATIVE_ARGUMENTS_INVALID"
        )
        assert tuple(job.Operations.Group) == ()

        document.openTransaction("Caller-owned Custom transaction")
        transaction = int(document.getBookedTransactionID())
        assert call(_arguments(job, tool_controller), succeeds=False)[
            "error_code"
        ] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)
        assert not document.HasPendingTransaction

        before_objects = tuple(document.Objects)
        before_group = tuple(job.Operations.Group)
        before_visibility = _visibility(document)
        before_timeline = _timeline(document)
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufactureProgramRuntime.verify_created_custom",
            side_effect=RuntimeError("forced Custom postcondition failure"),
        ):
            failed = call(_arguments(job, tool_controller), succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(job.Operations.Group) == before_group
        assert _visibility(document) == before_visibility
        assert _timeline(document) == before_timeline
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo

        flood_result = call(_arguments(job, tool_controller))
        flood = document.getObject(flood_result["object_name"])
        flood_name = str(flood.Name)
        _assert_custom(flood, job, tool_controller, coolant="flood")
        assert flood_result["block_count"] == 4
        assert flood_result["machine_command_count"] == 3
        assert flood_result["comment_block_count"] == 1
        assert flood_result["parameter_count"] == 7
        assert flood_result["path_command_count"] == 9
        assert flood_result["path_sha256"] == operation_state(flood)["path_sha256"]
        assert "blocks" not in flood_result
        assert "gcode" not in json.dumps(flood_result).lower()
        assert len(flood_result["receipt"]["created"]) == 1
        assert len(flood_result["receipt"]["changed"]) == 1

        none_result = call(
            _arguments(
                job,
                tool_controller,
                label="Native Custom Without Coolant",
                coolant="none",
            )
        )
        none = document.getObject(none_result["object_name"])
        none_name = str(none.Name)
        job_name = str(job.Name)
        controller_name = str(tool_controller.Name)
        _assert_custom(none, job, tool_controller, coolant="none")
        assert none_result["path_command_count"] == 7
        assert tuple(job.Operations.Group) == (flood, none)
        assert none_result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert _visibility(document)[: len(before_visibility)] == before_visibility
        assert int(document.UndoCount) == before_undo + 2

        document.undo()
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        flood = document.getObject(flood_name)
        assert document.getObject(none_name) is None
        assert tuple(job.Operations.Group) == (flood,)
        document.redo()
        _events(12)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        flood = document.getObject(flood_name)
        none = document.getObject(none_name)
        assert tuple(job.Operations.Group) == (flood, none)
        _assert_custom(flood, job, tool_controller, coolant="flood")
        _assert_custom(none, job, tool_controller, coolant="none")

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        job = document.getObject(job_name)
        tool_controller = document.getObject(controller_name)
        flood = document.getObject(flood_name)
        none = document.getObject(none_name)
        assert tuple(job.Operations.Group) == (flood, none)
        _assert_custom(flood, job, tool_controller, coolant="flood")
        _assert_custom(none, job, tool_controller, coolant="none")

        print(
            "VIBECAD_NATIVE_MANUFACTURE_CUSTOM_GUI_OK "
            "ribbon=true exact_job=true exact_controller=true closed_schema=true "
            "no_raw_gcode=true no_provider_path=true stale=true injection_guard=true "
            "duplicate_guard=true transaction_guard=true rollback=true structured=true "
            "coolant=true source_preserved=true job=true history=true receipt=true "
            "low_noise=true selection=true visibility=true undo=true redo=true reopen=true"
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
