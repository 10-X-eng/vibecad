# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM operation Active state changes."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import Path.Dressup.Array as PathDressupArray
import Path.Main.Gui.Job as PathJobGui
import Path.Op.Custom as PathCustom
import PathCommands
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE,
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)
from VibeCADNativeManufactureProviderScope import (
    scope_manufacture_provider_surface,
)
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot

from VibeCADNativeManufactureState import job_state, operation_reference_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CAPABILITY_NAME = MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES["set_active"]


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
    schema = definition.provider_schema(("set_active",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    assert '"maxItems":64' in encoded
    for field in ("job", "targets", "object_name", "expected_active", "active"):
        assert field in encoded
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


def _create_fixture(document):
    model = document.addObject("Part::Feature", "ActiveGateModel")
    model.Label = "Active state gate model"
    model.Shape = Part.makeBox(20.0, 16.0, 8.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    operations = []
    for name, x_value in (("RoughingPass", 10), ("FinishingPass", 18)):
        operation = PathCustom.Create(name, parentJob=job)
        job.Proxy.addOperation(operation)
        operation.Label = name.replace("Pass", " pass")
        operation.Gcode = [
            "G0 X0 Y0 Z5",
            "G0 X0 Y0 Z0",
            f"G1 X{x_value} Y0 Z0",
            f"G1 X{x_value} Y8 Z0",
        ]
        operation.Active = True
        operations.append(operation)
    finishing_base = operations[1]
    finishing_array = PathDressupArray.Create(finishing_base, "FinishingArray")
    assert finishing_array is not None
    finishing_array.Label = "Finishing pass array"
    finishing_array.Copies = 1
    finishing_array.Offset = App.Vector(24.0, 0.0, 0.0)
    operations = (operations[0], finishing_array)
    assert document.recompute(None, True, True) is not False
    assert tuple(job.Operations.Group) == operations
    assert all(operation.isValid() for operation in (*operations, finishing_base))
    assert all(
        operation_reference_state(operation)["active"]
        for operation in operations
    )
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(finishing_array)
    assert PathCommands._selected_toggle_operations() == [finishing_base]
    Gui.Selection.clearSelection()
    return model, job, operations, finishing_base


def _arguments(job, operations, desired: tuple[bool, ...]) -> dict:
    assert len(operations) == len(desired)
    state = job_state(job)
    return {
        "operation": "set_active",
        "job": _target(state),
        "targets": [
            {
                "object_name": operation.Name,
                "expected_active": operation_reference_state(operation)["active"],
                "active": active,
            }
            for operation, active in zip(operations, desired, strict=True)
        ],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-cam-active-")
        save_path = Path(temporary.name) / "native-manufacture-active.FCStd"
        document = App.newDocument("NativeManufactureActiveGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_OpActiveToggle"]
        actual_plan = (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
        )
        assert actual_plan == (
            CAPABILITY_NAME,
            "set_active",
            "ExactCamJobAndOperationActiveStates",
            True,
            False,
        ), actual_plan

        model, job, operations, finishing_base = _create_fixture(document)
        document.clearUndos()
        initial_objects = tuple(document.Objects)
        initial_group = tuple(job.Operations.Group)
        initial_timeline = (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )
        initial_configuration = {
            operation.Name: operation_reference_state(operation)["configuration_sha256"]
            for operation in (*operations, finishing_base)
        }

        registry = build_native_capability_registry()
        production_surface = resolve_native_provider_surface(surface, registry)
        assert production_surface.available, production_surface.debug_summary()
        scoped_surface = scope_manufacture_provider_surface(
            production_surface,
            {
                "surface_id": "manufacture",
                "domain": build_manufacture_snapshot(document),
            },
            registry=registry,
        )
        schema_bytes = len(
            json.dumps(
                scoped_surface.schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert schema_bytes <= MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE["manufacture"]
        assert "manufacture.operations" in scoped_surface.tool_names
        assert "manufacture.dressup" in scoped_surface.tool_names
        assert "manufacture.modify" not in scoped_surface.tool_names
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-operation-active-gui")

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
                CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-active-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = tuple(
            (obj.Name, bool(obj.ViewObject.Visibility))
            for obj in document.Objects
            if getattr(obj, "ViewObject", None) is not None
        )

        stale = _arguments(job, operations[:1], (False,))
        stale["job"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert all(
            operation_reference_state(operation)["active"]
            for operation in operations
        )
        assert int(document.UndoCount) == 0

        no_change = _arguments(job, operations[:1], (False,))
        no_change["targets"][0]["active"] = True
        no_change_result = call(no_change, succeeds=False)
        assert no_change_result["error_code"] == "NATIVE_MANUFACTURE_NO_CHANGE"
        assert int(document.UndoCount) == 0

        deactivate = _arguments(job, operations, (False, False))
        with patch(
            "VibeCADNativeManufactureModifyRuntime.verify_operation_active",
            side_effect=RuntimeError("forced postcondition failure"),
        ):
            failed = call(deactivate, succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert all(
            operation_reference_state(operation)["active"]
            for operation in operations
        )
        assert int(document.UndoCount) == 0

        inactive_result = call(deactivate)
        _events(12)
        assert [item["active"] for item in inactive_result["operations"]] == [
            False,
            False,
        ]
        assert inactive_result["job"]["active_operation_count"] == 0
        assert inactive_result["assistant_undo_available"] is True
        assert inactive_result["operations"][1]["underlying_operation_name"] == (
            finishing_base.Name
        )
        assert all(
            not operation_reference_state(operation)["active"]
            for operation in operations
        )
        assert finishing_base.Active is False
        assert int(document.UndoCount) == 1
        assert _selection() == selection_before
        assert visibility_before == tuple(
            (obj.Name, bool(obj.ViewObject.Visibility))
            for obj in document.Objects
            if getattr(obj, "ViewObject", None) is not None
        )
        assert initial_objects == tuple(document.Objects)
        assert initial_group == tuple(job.Operations.Group)
        assert initial_timeline == (
            tuple(document.VibeCADTimeline.Operations),
            tuple(bool(value) for value in document.VibeCADTimeline.VisibilityAtEnd),
            tuple(bool(value) for value in document.VibeCADTimeline.SuppressionAtEnd),
            int(document.VibeCADTimeline.Position),
        )

        activate_one = _arguments(job, operations[:1], (True,))
        active_result = call(activate_one)
        _events(12)
        assert active_result["operations"][0]["previous_active"] is False
        assert operations[0].Active is True
        assert operation_reference_state(operations[1])["active"] is False
        assert finishing_base.Active is False
        assert active_result["job"]["active_operation_count"] == 1
        assert int(document.UndoCount) == 2
        assert all(
            operation_reference_state(operation)["configuration_sha256"]
            == initial_configuration[operation.Name]
            for operation in operations
        )

        document.undo()
        _events(12)
        operations = (
            document.getObject("RoughingPass"),
            document.getObject("FinishingArray"),
        )
        assert all(operation is not None for operation in operations)
        assert all(
            not operation_reference_state(operation)["active"]
            for operation in operations
        )
        document.redo()
        _events(12)
        operations = (
            document.getObject("RoughingPass"),
            document.getObject("FinishingArray"),
        )
        finishing_base = document.getObject("FinishingPass")
        assert operations[0].Active is True
        assert operation_reference_state(operations[1])["active"] is False
        assert finishing_base.Active is False

        job_name = str(job.Name)
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        job = document.getObject(job_name)
        operations = (
            document.getObject("RoughingPass"),
            document.getObject("FinishingArray"),
        )
        finishing_base = document.getObject("FinishingPass")
        assert job is not None and all(operation is not None for operation in operations)
        assert finishing_base is not None
        assert operations[0].Active is True
        assert operation_reference_state(operations[1])["active"] is False
        assert finishing_base.Active is False
        assert [
            operation_reference_state(operation)["configuration_sha256"]
            for operation in (*operations, finishing_base)
        ] == [
            initial_configuration["RoughingPass"],
            initial_configuration["FinishingArray"],
            initial_configuration["FinishingPass"],
        ]
        assert job_state(job)["counts"]["active_operations"] == 1

        print(
            "VIBECAD_NATIVE_MANUFACTURE_OPERATION_ACTIVE_GUI_OK "
            "exact_job=true explicit_states=true batch=true dressup=true "
            "rollback=true selection=true visibility=true history=true "
            "undo=true redo=true reopen=true "
            f"surface_bytes={schema_bytes}",
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
