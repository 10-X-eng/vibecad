# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact CAM Start Point editing."""

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

import Path.Main.Gui.Job as PathJobGui
import Path.Op.Gui.Profile as PathProfileGui
import Path.Op.Profile as PathProfile
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)
from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot
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
    plan = next(
        plan
        for plan in provider_context_actions_for_surface("manufacture")
        if plan.action_id == "CAM_SetStartPoint"
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.classification.mutation,
        plan.classification.human_only,
    ) == (
        "manufacture.start_point",
        "set_start_point",
        "ExactCamJobOperationAndPlanarStartPoint",
        True,
        False,
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_OPERATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("set_start_point",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 4_000
    variants = schema["parameters"]["oneOf"]
    assert len(variants) == 1
    parameters = variants[0]
    assert parameters["required"] == ["operation", "job", "target", "point_mm"]
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["operation"]["const"] == "set_start_point"
    point = parameters["properties"]["point_mm"]
    assert point["required"] == ["x_mm", "y_mm"]
    assert set(point["properties"]) == {"x_mm", "y_mm"}
    assert point["additionalProperties"] is False
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


def _target(state: dict) -> dict[str, str]:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _top_face_name(model) -> str:
    maximum_z = float(model.Shape.BoundBox.ZMax)
    for index, face in enumerate(model.Shape.Faces, start=1):
        if all(
            abs(float(vertex.Point.z) - maximum_z) <= 1.0e-9
            for vertex in face.Vertexes
        ):
            return f"Face{index}"
    raise AssertionError("The start-point model has no exact top face")


def _create_fixture(document):
    model = document.addObject("Part::Feature", "StartPointModel")
    model.Label = "Start point model"
    model.Shape = Part.makeBox(20.0, 16.0, 5.0)
    document.recompute()
    job = PathJobGui.Create([model], None, openTaskPanel=False)
    assert job is not None and job.Tools.Group
    document.openTransaction("Create Start Point Profile fixture")
    try:
        operation = PathProfile.Create(
            "StartPointOperation",
            parentJob=job,
            toolController=job.Tools.Group[0],
        )
        operation.Proxy.addBase(operation, model, _top_face_name(model))
        operation.Label = "Start point Profile operation"
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
        operation.StartDepth = 5.0
        operation.FinalDepth = 0.0
        operation.StepDown = 2.0
        operation.SafeHeight = 6.5
        operation.ClearanceHeight = 7.5
        operation.StartPoint = App.Vector(1.0, 2.0, 3.0)
        operation.UseStartPoint = False
        assert document.recompute(None, True, True) is not False
        assert document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            operation
        )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except Exception:
        document.abortTransaction()
        raise
    document.commitTransaction()
    assert operation.getTypeIdOfProperty("StartPoint") == (
        "App::PropertyVectorDistance"
    )
    assert operation in tuple(job.Operations.Group)
    assert operation.isValid()
    assert any(
        command.Name in {"G1", "G2", "G3"}
        for command in tuple(operation.Path.Commands)
    )
    state = operation_state(operation)
    assert state["start_point"] == {
        "enabled": False,
        "point_mm": {"x_mm": 1.0, "y_mm": 2.0, "z_mm": 3.0},
        "clearance_height_mm": 7.5,
    }
    return model, job, operation


def _arguments(job, operation, x_mm: float, y_mm: float) -> dict:
    return {
        "operation": "set_start_point",
        "job": _target(job_state(job)),
        "target": _target(operation_state(operation)),
        "point_mm": {"x_mm": x_mm, "y_mm": y_mm},
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-start-point-")
        save_path = Path(temporary.name) / "native-manufacture-start-point.FCStd"
        document = App.newDocument("NativeManufactureStartPointGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        model, job, operation = _create_fixture(document)
        job_name = str(job.Name)
        operation_name = str(operation.Name)
        document.clearUndos()

        snapshot = build_manufacture_snapshot(document)
        snapshot_operation = next(
            item
            for item in snapshot["jobs"][0]["operations"]
            if item["object_name"] == operation.Name
        )
        assert snapshot_operation["start_point"]["clearance_height_mm"] == 7.5
        initial_path_sha256 = operation_state(operation)["path_sha256"]
        assert len(initial_path_sha256) == 64

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-start-point-gui")

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

        def call(
            payload: dict,
            *,
            succeeds: bool = True,
            call_id: str | None = None,
        ) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                MANUFACTURE_OPERATION_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                call_id or f"native-manufacture-start-point-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(model, "Face1")
        selection_before = _selection()
        visibility_before = _visibility(document)
        timeline_before = _timeline(document)
        objects_before = tuple(document.Objects)

        invalid_z = _arguments(job, operation, 4.0, 5.0)
        invalid_z["point_mm"]["z_mm"] = -100.0
        invalid_result = call(invalid_z, succeeds=False)
        assert invalid_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert invalid_result["argument_error"]["path"] == ["point_mm"]
        assert int(document.UndoCount) == 0

        stale = _arguments(job, operation, 4.0, 5.0)
        stale["target"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"
        assert int(document.UndoCount) == 0

        document.openTransaction("Caller-owned start point transaction")
        transaction_result = call(
            _arguments(job, operation, 4.0, 5.0),
            succeeds=False,
        )
        assert transaction_result["error_code"] == "NATIVE_TRANSACTION_ACTIVE"
        document.abortTransaction()
        assert int(document.UndoCount) == 0

        intended = _arguments(job, operation, 4.25, 5.5)
        with patch(
            "VibeCADNativeManufactureOperationRuntime.verify_start_point",
            side_effect=RuntimeError("forced start-point verifier failure"),
        ):
            rollback = call(intended, succeeds=False)
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        operation = document.getObject(operation_name)
        assert operation.StartPoint.isEqual(App.Vector(1.0, 2.0, 3.0), 1.0e-9)
        assert operation.UseStartPoint is False
        assert int(document.UndoCount) == 0
        assert tuple(document.Objects) == objects_before
        assert _timeline(document) == timeline_before

        result = call(intended, call_id="start-point-success")
        _events(12)
        operation = document.getObject(operation_name)
        expected = App.Vector(4.25, 5.5, 7.5)
        assert operation.StartPoint.isEqual(expected, 1.0e-9)
        assert operation.UseStartPoint is True
        assert result["previous"] == {
            "enabled": False,
            "point_mm": [1.0, 2.0, 3.0],
        }
        assert result["start_point"] == {
            "enabled": True,
            "point_mm": [4.25, 5.5, 7.5],
            "clearance_height_mm": 7.5,
        }
        assert len(result["path"]["path_sha256"]) == 64
        assert result["path"]["path_sha256"] != initial_path_sha256
        assert result["assistant_undo_available"] is True
        changed_names = [
            item["object_name"] for item in result["receipt"]["changed"]
        ]
        assert changed_names == [job_name, operation_name], changed_names
        assert int(document.UndoCount) == 1
        assert tuple(document.Objects) == objects_before
        assert _timeline(document) == timeline_before
        assert _selection() == selection_before
        assert _visibility(document) == visibility_before

        duplicate = call(intended, call_id="start-point-success")
        assert duplicate == result
        assert int(document.UndoCount) == 1

        no_change = call(
            _arguments(job, operation, 4.25, 5.5),
            succeeds=False,
        )
        assert no_change["error_code"] == "NATIVE_MANUFACTURE_NO_CHANGE"
        assert int(document.UndoCount) == 1

        document.undo()
        _events(12)
        operation = document.getObject(operation_name)
        assert operation.StartPoint.isEqual(App.Vector(1.0, 2.0, 3.0), 1.0e-9)
        assert operation.UseStartPoint is False
        assert _timeline(document) == timeline_before
        document.redo()
        _events(12)
        operation = document.getObject(operation_name)
        assert operation.StartPoint.isEqual(expected, 1.0e-9)
        assert operation.UseStartPoint is True
        assert _timeline(document) == timeline_before

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        job = document.getObject(job_name)
        operation = document.getObject(operation_name)
        assert job is not None and operation is not None
        assert operation.StartPoint.isEqual(expected, 1.0e-9)
        assert operation.UseStartPoint is True
        reopened = operation_state(operation)
        assert reopened["start_point"] == {
            "enabled": True,
            "point_mm": {"x_mm": 4.25, "y_mm": 5.5, "z_mm": 7.5},
            "clearance_height_mm": 7.5,
        }
        assert list(document.VibeCADTimeline.Operations).count(operation) == 1

        print(
            "VIBECAD_NATIVE_MANUFACTURE_START_POINT_GUI_OK "
            "context=true closed_schema=true planar_input=true derived_z=true "
            "snapshot=true real_profile=true property_contract=true "
            "regenerated_path=true exact_job=true exact_operation=true stale=true "
            "transaction_guard=true rollback=true duplicate_guard=true "
            "no_change=true receipt=true low_noise=true history=true "
            "selection=true visibility=true undo=true redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
