# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real human-command and Native lifecycle gate for Model fasteners."""

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
import PartDesign
from PySide import QtCore, QtWidgets

import VibeCADFastenersGui
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADFastenerModel import ModelFastenerGraph, validate_model_fastener_graph
from VibeCADFasteners import fastener_feature_identity, resolve_fastener
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelFastenerRuntime as runtime_module
from VibeCADNativeModelFastenerSchema import model_fastener_capability_definition
from VibeCADNativeModelSnapshot import build_model_snapshot
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


def _assert_signature(actual, expected) -> None:
    assert actual[:5] == expected[:5], (actual, expected)
    for left, right in zip(actual[5:], expected[5:]):
        assert _close(left, right), (actual, expected)


def _graph(body) -> ModelFastenerGraph:
    publication = body.Tip
    state = publication.CurrentState
    operation = state.Operation
    generator = operation.Generator
    return ModelFastenerGraph(
        body,
        publication,
        state,
        operation,
        generator,
        fastener_feature_identity(generator),
    )


def _validate_graph(document, body, *, label: str, canonical_key: str):
    graph = _graph(body)
    identity = validate_model_fastener_graph(
        document,
        graph,
        label=label,
        canonical_key=canonical_key,
    )
    assert graph.generator.ViewObject.Visibility is False
    assert graph.generator.ViewObject.ShowInTree is False
    assert graph.operation.VibeCADTimelineRole == "operation"
    assert graph.operation.VibeCADTimelineEditCommand == "VibeCAD_EditStandardFastener"
    assert "VibeCADTimelineEditor" not in graph.operation.PropertiesList
    assert graph.body.getParentGeoFeatureGroup() is None
    return graph, identity


def _constructor(
    *,
    length_mm: float | None = 10.0,
    model_thread: bool = False,
    standard: str = "ISO4762",
) -> dict[str, object]:
    return {
        "standard": standard,
        "nominal_thread": "M3",
        "length_mm": length_mm,
        "model_thread": model_thread,
        "left_handed": False,
        "options": {},
    }


def _human_insert(document):
    constructor = _constructor()
    identity = resolve_fastener(**constructor)
    values = {
        **constructor,
        "label": "Human M3 socket bolt",
        "identity": identity,
    }
    VibeCADFastenersGui.ensure_commands_registered()
    command = Gui.Command.get("VibeCAD_InsertStandardFastener")
    assert command is not None and command.isActive()
    with mock.patch.object(VibeCADFastenersGui, "_FastenerDialog") as dialog:
        dialog.return_value.exec.return_value = values
        Gui.runCommand("VibeCAD_InsertStandardFastener")
    _process_events()
    selected = Gui.Selection.getSelection()
    assert len(selected) == 1 and selected[0].TypeId == "PartDesign::Body"
    body = selected[0]
    graph, observed = _validate_graph(
        document,
        body,
        label=values["label"],
        canonical_key=identity["canonical_key"],
    )
    assert observed["canonical_key"] == identity["canonical_key"]
    assert graph.operation.Shape.isNull()
    assert len(graph.body.Shape.Solids) == 1
    Gui.Selection.clearSelection()
    return graph, _shape_signature(graph.body.Shape)


def _human_edit(document, graph):
    constructor = _constructor(length_mm=12.0)
    identity = resolve_fastener(**constructor)
    values = {
        **constructor,
        "label": "Human edited M3 socket bolt",
        "identity": identity,
    }
    initial = _record(graph)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(graph.body)
    _process_events()
    command = Gui.Command.get("VibeCAD_EditStandardFastener")
    assert command is not None and command.isActive()
    with mock.patch.object(VibeCADFastenersGui, "_FastenerDialog") as dialog:
        dialog.return_value.exec.return_value = values
        Gui.runCommand("VibeCAD_EditStandardFastener")
    _process_events()
    updated, observed = _validate_graph(
        document,
        graph.body,
        label=values["label"],
        canonical_key=identity["canonical_key"],
    )
    assert observed["canonical_key"] == identity["canonical_key"]
    assert updated.body.Name == initial["body_name"]
    assert updated.publication.Name == initial["publication_name"]
    assert updated.state.Name == initial["state_name"]
    assert updated.operation.Name == initial["operation_name"]
    assert updated.generator.Name == initial["generator_name"]
    Gui.Selection.clearSelection()
    return updated, _shape_signature(updated.body.Shape)


def _turn() -> NativeTurnSnapshot:
    definition = model_fastener_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "f" * 64,
            (
                "VibeCAD_InsertStandardFastener",
                "VibeCAD_EditStandardFastener",
            ),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(
            definition.provider_schema(
                ("insert_standard_fastener", "edit_standard_fastener")
            ),
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _insert_arguments(label: str, *, model_thread: bool = False):
    return {
        "operation": "insert_standard_fastener",
        "label": label,
        "definition": _constructor(model_thread=model_thread),
    }


def _edit_arguments(
    body,
    label: str,
    *,
    length_mm: float | None = 12.0,
    model_thread: bool = False,
    standard: str = "ISO4762",
):
    return {
        "operation": "edit_standard_fastener",
        "target": {"object_name": body.Name},
        "label": label,
        "definition": _constructor(
            length_mm=length_mm,
            model_thread=model_thread,
            standard=standard,
        ),
    }


def _assert_native_result(document, response, arguments, *, mutation: str):
    assert set(response) == {
        "ok",
        "operation",
        "body",
        "fastener",
        "solid_count",
        "volume_mm3",
        "receipt",
        "assistant_undo_available",
    }
    body = document.getObject(response["body"]["object_name"])
    operation = document.getObject(response["operation"]["object_name"])
    assert body is not None and operation is not None
    graph, identity = _validate_graph(
        document,
        body,
        label=arguments["label"],
        canonical_key=response["fastener"]["canonical_key"],
    )
    assert graph.operation is operation
    assert response["fastener"] == {
        "canonical_key": identity["canonical_key"],
        "part_number": identity["part_number"],
        "standard": identity["standard"],
        "nominal_thread": identity["nominal_size"],
        "length_mm": identity["length_mm"],
        "model_thread": identity["model_thread"],
        "left_handed": identity["left_handed"],
        "options": identity["options"],
    }
    assert response["solid_count"] == 1
    assert _close(response["volume_mm3"], body.Shape.Volume)
    assert response["assistant_undo_available"] is True
    created_names = {
        item["object_name"] for item in response["receipt"]["created"]
    }
    changed_names = {
        item["object_name"] for item in response["receipt"]["changed"]
    }
    if mutation == "insert":
        assert created_names == {operation.Name, body.Name}
        assert changed_names == set()
    else:
        assert created_names == set()
        assert changed_names == {operation.Name, body.Name}
    assert response["receipt"]["deleted"] == []
    assert response["receipt"]["replaced"] == []
    snapshot = build_model_snapshot(document)
    current = next(
        item
        for item in snapshot["standard_fasteners"]
        if item["body"]["object_name"] == body.Name
    )
    assert current["operation"]["object_name"] == operation.Name
    assert current["canonical_key"] == identity["canonical_key"]
    assert current["definition"] == arguments["definition"]
    return graph


def _record(graph) -> dict[str, object]:
    return {
        "label": str(graph.body.Label),
        "canonical_key": str(graph.identity["canonical_key"]),
        "body_name": graph.body.Name,
        "body_id": str(graph.body.VibeCADBodyId),
        "publication_name": graph.publication.Name,
        "state_name": graph.state.Name,
        "state_id": str(graph.state.BodyStateId),
        "operation_name": graph.operation.Name,
        "operation_id": str(graph.operation.OperationId),
        "generator_name": graph.generator.Name,
        "signature": _shape_signature(graph.body.Shape),
    }


def _assert_record(document, record, *, restored: bool = False):
    body = document.getObject(record["body_name"])
    assert body is not None
    graph, _identity = _validate_graph(
        document,
        body,
        label=record["label"],
        canonical_key=record["canonical_key"],
    )
    assert graph.publication.Name == record["publication_name"]
    assert graph.state.Name == record["state_name"]
    assert graph.operation.Name == record["operation_name"]
    assert graph.generator.Name == record["generator_name"]
    assert str(graph.body.VibeCADBodyId) == record["body_id"]
    assert str(graph.state.BodyStateId) == record["state_id"]
    assert str(graph.operation.OperationId) == record["operation_id"]
    actual_signature = _shape_signature(graph.body.Shape)
    if restored:
        expected_signature = record["signature"]
        assert actual_signature[:5] == expected_signature[:5]
        for left, right in zip(
            actual_signature[5:7],
            expected_signature[5:7],
            strict=True,
        ):
            assert _close(left, right), (actual_signature, expected_signature)
        for left, right in zip(
            actual_signature[7:],
            expected_signature[7:],
            strict=True,
        ):
            assert _close(left, right, tolerance=1.0e-2), (
                actual_signature,
                expected_signature,
            )
    else:
        _assert_signature(actual_signature, record["signature"])
    return graph


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelFastenerGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        _process_events()

        human_graph, human_insert_signature = _human_insert(document)
        PartDesign.validateDesign(human_graph.operation)
        human_graph, human_edit_signature = _human_edit(document, human_graph)
        PartDesign.validateDesign(human_graph.operation)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-fastener-gui")
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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.fastener",
                json.dumps(arguments, separators=(",", ":")),
                f"model-fastener-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments,
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        parity_arguments = _insert_arguments("Native M3 socket bolt")
        parity_response = native_call(parity_arguments)
        parity_graph = _assert_native_result(
            document,
            parity_response,
            parity_arguments,
            mutation="insert",
        )
        _assert_signature(
            _shape_signature(parity_graph.body.Shape),
            human_insert_signature,
        )

        parity_before_edit = _record(parity_graph)
        edit_arguments = _edit_arguments(
            parity_graph.body,
            "Native edited M3 socket bolt",
        )
        edit_response = native_call(edit_arguments)
        edited_graph = _assert_native_result(
            document,
            edit_response,
            edit_arguments,
            mutation="edit",
        )
        edited_record = _record(edited_graph)
        for field in (
            "body_name",
            "body_id",
            "publication_name",
            "state_name",
            "state_id",
            "operation_name",
            "operation_id",
            "generator_name",
        ):
            assert edited_record[field] == parity_before_edit[field]
        assert edited_record["canonical_key"] != parity_before_edit["canonical_key"]
        _assert_signature(edited_record["signature"], human_edit_signature)

        document.undo()
        _process_events()
        _assert_record(document, parity_before_edit)
        document.redo()
        _process_events()
        edited_graph = _assert_record(document, edited_record)

        threaded_arguments = _insert_arguments(
            "Native modeled-thread M3 socket bolt",
            model_thread=True,
        )
        threaded_response = native_call(threaded_arguments)
        threaded_graph = _assert_native_result(
            document,
            threaded_response,
            threaded_arguments,
            mutation="insert",
        )
        assert threaded_graph.identity["model_thread"] is True
        assert len(threaded_graph.body.Shape.Edges) > len(parity_graph.body.Shape.Edges)

        records = [_record(human_graph), edited_record, _record(threaded_graph)]
        threaded_record = records[-1]
        document.undo()
        _process_events()
        for name in (
            threaded_record["body_name"],
            threaded_record["publication_name"],
            threaded_record["state_name"],
            threaded_record["operation_name"],
            threaded_record["generator_name"],
        ):
            assert document.getObject(name) is None
        document.redo()
        _process_events()
        _assert_record(document, threaded_record)

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                **_insert_arguments("Invalid schema fastener"),
                "selection": [],
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        bad_catalog = _insert_arguments("Invalid catalog fastener")
        bad_catalog["definition"]["standard"] = "NOT_A_STANDARD"
        invalid_catalog = native_call(bad_catalog, succeeds=False)
        assert invalid_catalog["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert not document.HasPendingTransaction

        invalid_target = _edit_arguments(
            edited_graph.body,
            "Wrong target fastener",
        )
        invalid_target["target"] = {
            "object_name": edited_graph.generator.Name,
        }
        wrong_type = native_call(invalid_target, succeeds=False)
        assert wrong_type["error_code"] == "NATIVE_TARGET_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        _assert_record(document, edited_record)

        stale_target = _edit_arguments(
            edited_graph.body,
            "Stale target fastener",
        )
        stale_target["target"] = {"object_name": "DeletedFastenerBody"}
        stale = native_call(stale_target, succeeds=False)
        assert stale["error_code"] == "NATIVE_TARGET_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        _assert_record(document, edited_record)

        incompatible = _edit_arguments(
            edited_graph.body,
            "Incompatible nut",
            length_mm=None,
            standard="ISO4032",
        )
        incompatible_response = native_call(incompatible, succeeds=False)
        assert incompatible_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        _assert_record(document, edited_record)

        original_verifier = runtime_module.verify_model_fastener

        def reject_verification(_document, _draft):
            raise NativeModelError("Forced standard-fastener verifier failure.")

        runtime_module.verify_model_fastener = reject_verification
        try:
            rollback = native_call(
                _edit_arguments(
                    edited_graph.body,
                    "Rolled back edited fastener",
                    length_mm=16.0,
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_model_fastener = original_verifier
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert not document.HasPendingTransaction
        _assert_record(document, edited_record)

        for record in records:
            graph = _assert_record(document, record)
            signature = record["signature"]
            for _index in range(4):
                assert document.recompute(
                    [graph.operation, graph.body],
                    True,
                    True,
                ) is not False
                _assert_signature(_shape_signature(graph.body.Shape), signature)

        save_directory = tempfile.mkdtemp(prefix="vibecad-native-fastener-")
        saved_file = Path(save_directory) / "native-model-fasteners.FCStd"
        document.saveAs(str(saved_file))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(saved_file))
        document.UndoMode = True
        assert document.recompute(None, True, True) is not False
        _process_events()
        for record in records:
            _assert_record(document, record, restored=True)

        print("VIBECAD_NATIVE_MODEL_FASTENER_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
