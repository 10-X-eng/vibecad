# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human/Native parity and lifecycle gate for standard-fastener attachment."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest import mock

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADFastenersGui
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADFastenerModel import (
    create_model_fastener_graph,
    model_fastener_graph_from_body,
    validate_model_fastener_graph,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelFastenerRuntime as runtime_module
from VibeCADNativeModelFastenerSchema import model_fastener_capability_definition
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1.0e-9, abs_tol=tolerance)


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Solids),
        len(shape.Faces),
        len(shape.Edges),
        len(shape.Vertexes),
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _assert_signature(actual, expected, tolerance: float = 1.0e-7) -> None:
    assert actual[:5] == expected[:5], (actual, expected)
    for left, right in zip(actual[5:], expected[5:], strict=True):
        assert _close(left, right, tolerance), (actual, expected)


def _new_fastener(document, label: str):
    return create_model_fastener_graph(
        document,
        label=label,
        standard="ISO4762",
        nominal_thread="M3",
        length_mm=10.0,
        model_thread=False,
        left_handed=False,
        options={},
    )


def _new_design_host(document, name: str, x: float):
    operation = document.addObject("PartDesign::DesignCylinder", name)
    edit = PartDesign.beginDesignOperationEdit(operation)
    operation.Radius = 6.0
    operation.Height = 8.0
    operation.Placement.Base.x = x
    PartDesign.setDesignOperationTargets(edit, "New Body", [])
    assert document.recompute([operation], True, True) is not False
    body = PartDesign.finalizeDesignOperationEdit(edit)[0]
    body.Label = f"{name} Body"
    PartDesign.validateDesign(operation)
    return operation, body, body.Tip.CurrentState


def _new_body_owned_host(document, name: str, x: float):
    body = document.addObject("PartDesign::Body", f"{name}Body")
    feature = body.newObject("PartDesign::Feature", name)
    feature.Shape = Part.makeCylinder(5.0, 7.0)
    feature.Placement.Base.x = x
    body.Tip = feature
    assert document.recompute([feature, body], True, True) is not False
    return body, feature


def _edge_names(host) -> tuple[str, str]:
    circular = []
    noncircular = []
    for index, edge in enumerate(host.Shape.Edges, start=1):
        name = f"Edge{index}"
        if isinstance(edge.Curve, Part.Circle):
            circular.append((name, edge.Curve))
        else:
            noncircular.append(name)
    assert circular and noncircular
    circle_name, _curve = max(
        circular,
        key=lambda item: float(item[1].Center.z),
    )
    return circle_name, noncircular[0]


def _setup(document):
    document.openTransaction("Create fastener attachment inputs")
    human = _new_fastener(document, "Human attached fastener")
    native = _new_fastener(document, "Native attached fastener")
    rollback = _new_fastener(document, "Rollback attached fastener")
    host_operation, host, host_state = _new_design_host(
        document,
        "AttachmentHost",
        32.0,
    )
    rollback_operation, rollback_host, rollback_state = _new_design_host(
        document,
        "RollbackHost",
        64.0,
    )
    owned_host, owned_feature = _new_body_owned_host(
        document,
        "BodyOwnedHost",
        96.0,
    )
    occurrence = document.addObject("App::Link", "FastenerOccurrence")
    occurrence.LinkedObject = native.body
    document.commitTransaction()
    return {
        "human": human,
        "native": native,
        "rollback": rollback,
        "host_operation": host_operation,
        "host": host,
        "host_state": host_state,
        "rollback_operation": rollback_operation,
        "rollback_host": rollback_host,
        "rollback_state": rollback_state,
        "owned_host": owned_host,
        "owned_feature": owned_feature,
        "occurrence": occurrence,
    }


def _validate_graph(document, body):
    graph = model_fastener_graph_from_body(document, body)
    identity = validate_model_fastener_graph(
        document,
        graph,
        label=str(graph.body.Label),
        canonical_key=str(graph.identity["canonical_key"]),
    )
    assert not graph.body.Shape.isNull() and graph.body.Shape.isValid()
    assert len(graph.body.Shape.Solids) == 1
    PartDesign.validateDesign(graph.operation)
    return graph, identity


def _timeline_index(document, operation) -> int:
    return list(document.getObject("VibeCADTimeline").Operations).index(operation)


def _attachment(graph, requested_host, subelement: str):
    definition_host, exact_names = graph.generator.BaseObject
    exact_names = tuple(str(name) for name in list(exact_names or []))
    assert len(exact_names) == 1
    assert definition_host.Shape.getElementIndexedName(exact_names[0]) == subelement
    edge = requested_host.Shape.getElement(subelement)
    center = edge.Curve.Center
    assert _close(graph.body.getGlobalPlacement().Base.distanceToPoint(center), 0.0)
    return definition_host, exact_names, center


def _human_parity(document, setup, subelement: str):
    graph = setup["human"]
    host = setup["host"]
    initial_signature = _shape_signature(graph.body.Shape)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(graph.body)
    Gui.Selection.addSelection(host, subelement)
    _process_events()
    VibeCADFastenersGui.ensure_commands_registered()
    command = Gui.Command.get("VibeCAD_AttachStandardFastener")
    assert command is not None and command.isActive()
    _occurrence, _generator, selected_host, selected_subelement = (
        VibeCADFastenersGui._selected_attachment_inputs()
    )
    assert selected_host is host
    assert host.Shape.getElementIndexedName(selected_subelement) == subelement
    with mock.patch.object(VibeCADFastenersGui, "_show_error") as show_error:
        Gui.runCommand("VibeCAD_AttachStandardFastener")
        _process_events()
    assert not show_error.called, show_error.call_args
    graph, _identity = _validate_graph(document, graph.body)
    definition_host, exact_names, center = _attachment(graph, host, subelement)
    assert definition_host is setup["host_state"]
    assert _timeline_index(document, setup["host_operation"]) < _timeline_index(
        document,
        graph.operation,
    )
    result = {
        "exact_names": exact_names,
        "center": (float(center.x), float(center.y), float(center.z)),
        "signature": _shape_signature(graph.body.Shape),
    }
    document.undo()
    _process_events()
    graph, _identity = _validate_graph(document, graph.body)
    assert graph.generator.BaseObject is None
    assert _close(graph.body.getGlobalPlacement().Base.Length, 0.0)
    _assert_signature(_shape_signature(graph.body.Shape), initial_signature)
    Gui.Selection.clearSelection()
    return result


def _turn():
    definition = model_fastener_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "a" * 64,
            ("VibeCAD_AttachStandardFastener",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(
            definition.provider_schema(("attach_standard_fastener",)),
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(fastener, host, subelement: str):
    return {
        "operation": "attach_standard_fastener",
        "fastener": {"object_name": fastener.body.Name},
        "host": {
            "object_name": host.Name,
            "subelement": subelement,
        },
    }


def _assert_response(document, response, arguments, expected_host):
    assert set(response) == {
        "ok",
        "operation",
        "body",
        "canonical_key",
        "attachment",
        "receipt",
        "assistant_undo_available",
    }
    body = document.getObject(response["body"]["object_name"])
    operation = document.getObject(response["operation"]["object_name"])
    graph, identity = _validate_graph(document, body)
    assert graph.operation is operation
    assert response["canonical_key"] == identity["canonical_key"]
    assert response["attachment"]["host"]["object_name"] == expected_host.Name
    assert response["attachment"]["subelement"] == arguments["host"]["subelement"]
    definition_host, exact_names, center = _attachment(
        graph,
        expected_host,
        arguments["host"]["subelement"],
    )
    assert response["attachment"]["center_mm"] == {
        "x": float(center.x),
        "y": float(center.y),
        "z": float(center.z),
    }
    assert response["assistant_undo_available"] is True
    assert response["receipt"]["created"] == []
    assert {item["object_name"] for item in response["receipt"]["changed"]} == {
        graph.operation.Name,
        graph.body.Name,
    }
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    return graph, definition_host, exact_names, center


def _record(graph, requested_host, subelement: str):
    definition_host, exact_names, center = _attachment(
        graph,
        requested_host,
        subelement,
    )
    return {
        "body_name": graph.body.Name,
        "body_id": str(graph.body.VibeCADBodyId),
        "publication_name": graph.publication.Name,
        "state_name": graph.state.Name,
        "state_id": str(graph.state.BodyStateId),
        "operation_name": graph.operation.Name,
        "operation_id": str(graph.operation.OperationId),
        "generator_name": graph.generator.Name,
        "definition_host_name": definition_host.Name,
        "requested_host_name": requested_host.Name,
        "subelement": subelement,
        "exact_names": exact_names,
        "center": (float(center.x), float(center.y), float(center.z)),
        "signature": _shape_signature(graph.body.Shape),
    }


def _assert_record(document, record, *, restored: bool = False):
    body = document.getObject(record["body_name"])
    requested_host = document.getObject(record["requested_host_name"])
    graph, _identity = _validate_graph(document, body)
    assert str(graph.body.VibeCADBodyId) == record["body_id"]
    assert graph.publication.Name == record["publication_name"]
    assert graph.state.Name == record["state_name"]
    assert str(graph.state.BodyStateId) == record["state_id"]
    assert graph.operation.Name == record["operation_name"]
    assert str(graph.operation.OperationId) == record["operation_id"]
    assert graph.generator.Name == record["generator_name"]
    definition_host, exact_names, center = _attachment(
        graph,
        requested_host,
        record["subelement"],
    )
    assert definition_host.Name == record["definition_host_name"]
    assert exact_names == record["exact_names"]
    assert all(
        _close(value, expected, 1.0e-2 if restored else 1.0e-7)
        for value, expected in zip(
            (center.x, center.y, center.z),
            record["center"],
            strict=True,
        )
    )
    _assert_signature(
        _shape_signature(graph.body.Shape),
        record["signature"],
        1.0e-2 if restored else 1.0e-7,
    )
    return graph


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeFastenerAttachmentGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        setup = _setup(document)
        _process_events()
        workbench = Gui.activeWorkbench().name()
        circular_edge, line_edge = _edge_names(setup["host"])
        human = _human_parity(document, setup, circular_edge)
        assert Gui.activeWorkbench().name() == workbench

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-fastener-attachment-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
        turn = _turn()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.fastener",
                json.dumps(arguments, separators=(",", ":")),
                f"fastener-attachment-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments, response)
            assert not Gui.Control.activeDialog()
            assert Gui.activeWorkbench().name() == workbench
            return response

        before = tuple(obj.Name for obj in document.Objects)
        arguments = _arguments(setup["native"], setup["host"], circular_edge)
        invalid_cases = (
            ({**arguments, "selection": []}, "NATIVE_ARGUMENTS_INVALID"),
            (
                {**arguments, "fastener": {"object_name": "DeletedFastener"}},
                "NATIVE_TARGET_INVALID",
            ),
            (
                {
                    **arguments,
                    "fastener": {"object_name": setup["host"].Name},
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    **arguments,
                    "host": {
                        "object_name": setup["host"].Name,
                        "subelement": "Edge999",
                    },
                },
                "NATIVE_TARGET_INVALID",
            ),
            (
                {
                    **arguments,
                    "host": {
                        "object_name": setup["host"].Name,
                        "subelement": line_edge,
                    },
                },
                "NATIVE_MODEL_INVALID",
            ),
            (
                {
                    **arguments,
                    "fastener": {"object_name": setup["occurrence"].Name},
                },
                "NATIVE_TARGET_INVALID",
            ),
            (
                {
                    **arguments,
                    "host": {
                        "object_name": setup["owned_host"].Name,
                        "subelement": _edge_names(setup["owned_host"])[0],
                    },
                },
                "NATIVE_MODEL_INVALID",
            ),
        )
        for invalid, error_code in invalid_cases:
            response = native_call(invalid, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert not document.HasPendingTransaction

        self_edge, _line = _edge_names(setup["native"].body)
        self_target = _arguments(
            setup["native"],
            setup["native"].body,
            self_edge,
        )
        response = native_call(self_target, succeeds=False)
        assert response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        rollback_edge, _line = _edge_names(setup["rollback_host"])
        rollback_arguments = _arguments(
            setup["rollback"],
            setup["rollback_host"],
            rollback_edge,
        )
        original_verifier = runtime_module.verify_model_fastener_attachment

        def reject_verification(_document, _draft):
            raise NativeModelError("Forced fastener-attachment verifier failure.")

        runtime_module.verify_model_fastener_attachment = reject_verification
        try:
            response = native_call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_model_fastener_attachment = original_verifier
        assert response["error_code"] == "NATIVE_MODEL_INVALID"
        rollback_graph, _identity = _validate_graph(
            document,
            setup["rollback"].body,
        )
        assert rollback_graph.generator.BaseObject is None
        assert _timeline_index(document, rollback_graph.operation) < _timeline_index(
            document,
            setup["rollback_operation"],
        )
        assert tuple(obj.Name for obj in document.Objects) == before
        assert not document.HasPendingTransaction

        response = native_call(arguments)
        graph, definition_host, exact_names, center = _assert_response(
            document,
            response,
            arguments,
            setup["host"],
        )
        assert definition_host is setup["host_state"]
        assert exact_names == human["exact_names"]
        assert all(
            _close(value, expected)
            for value, expected in zip(
                (center.x, center.y, center.z),
                human["center"],
                strict=True,
            )
        )
        _assert_signature(_shape_signature(graph.body.Shape), human["signature"])
        assert _timeline_index(document, setup["host_operation"]) < _timeline_index(
            document,
            graph.operation,
        )
        record = _record(graph, setup["host"], circular_edge)

        response = native_call(arguments, succeeds=False)
        assert response["error_code"] == "NATIVE_MODEL_INVALID"
        _assert_record(document, record)

        document.undo()
        _process_events()
        graph, _identity = _validate_graph(document, setup["native"].body)
        assert graph.generator.BaseObject is None
        assert _close(graph.body.getGlobalPlacement().Base.Length, 0.0)
        assert _timeline_index(document, graph.operation) < _timeline_index(
            document,
            setup["host_operation"],
        )
        document.redo()
        _process_events()
        _assert_record(document, record)

        for current in (record,):
            current_graph = _assert_record(document, current)
            for _index in range(4):
                assert document.recompute(
                    [current_graph.generator, current_graph.operation],
                    True,
                    True,
                ) is not False
                _assert_record(document, current)

        save_directory = tempfile.mkdtemp(prefix="vibecad-native-attachment-")
        save_path = Path(save_directory) / "attached-standard-fasteners.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        document.UndoMode = True
        assert document.recompute(None, True, True) is not False
        _process_events()
        _assert_record(document, record, restored=True)

        print("VIBECAD_NATIVE_MODEL_FASTENER_ATTACHMENT_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
