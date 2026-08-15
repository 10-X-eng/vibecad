# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Drawing dimension prefix and precision tools."""

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
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingDimensionTextSchema import (
    DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
    DRAWING_DIMENSION_TEXT_OPERATIONS,
)
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingState import drawing_page_state
import VibeCADNativeDrawingDimensionTextRuntime as RuntimeModule
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject, "VibeCADRibbonController"
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing"
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _create_fixture(document):
    document.openTransaction("Create Drawing dimension-text fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "DimensionTextSource")
        source.Shape = Part.makeBox(36.0, 24.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "DimensionTextPage")
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "DimensionTextTemplate"
        )
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod"
            / "TechDraw"
            / "Templates"
            / "ISO"
            / "A4_Landscape_TD.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())
        view = document.addObject("TechDraw::DrawViewPart", "DimensionTextView")
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.25
        view.X = 100.0
        view.Y = 75.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)

    document.openTransaction("Create Drawing dimension-text dimensions")
    transaction = int(document.getBookedTransactionID())
    try:
        edges = [
            item
            for item in drawing_projected_geometry_state(view)["elements"]
            if item["element_type"] == "edge"
        ]
        assert len(edges) >= 2
        first = TechDrawGui.createProjectedDimension(
            view, "Distance", [edges[0]["name"]], False, 15.0, 28.0
        )
        first.Label = "First Dimension Text Target"
        first.FormatSpec = "%.2f"
        document.publishProvisionalTimelineOperationBlock(first, (), ())
        second = TechDrawGui.createProjectedDimension(
            view, "Distance", [edges[1]["name"]], False, 32.0, 18.0
        )
        second.Label = "Second Dimension Text Target"
        second.FormatSpec = "%.2f"
        document.publishProvisionalTimelineOperationBlock(second, (), ())
        assert document.recompute([first, second, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    _events(24)
    return source, page, view, first, second


def _dismiss_messages(messages: list[tuple[str, str]]) -> None:
    for box in Gui.getMainWindow().findChildren(QtWidgets.QMessageBox):
        messages.append((box.windowTitle(), box.text()))
        box.reject()


def _run_human_command(command: str, repeat_count: int | None = None) -> None:
    messages: list[tuple[str, str]] = []
    message_timer = QtCore.QTimer()
    message_timer.timeout.connect(lambda: _dismiss_messages(messages))
    message_timer.start(40)
    if repeat_count is not None:
        def accept_repetition() -> None:
            field = Gui.getMainWindow().findChild(QtWidgets.QLineEdit, "leInput")
            assert field is not None
            field.setText(str(repeat_count))
            dialog = field.window()
            assert isinstance(dialog, QtWidgets.QDialog)
            dialog.accept()

        QtCore.QTimer.singleShot(100, accept_repetition)
    try:
        Gui.runCommand(command)
    finally:
        message_timer.stop()
    _events(20)
    assert not messages, messages
    assert not Gui.Control.activeDialog()


def _cancel_human_repetition() -> None:
    def reject_repetition() -> None:
        field = Gui.getMainWindow().findChild(QtWidgets.QLineEdit, "leInput")
        assert field is not None
        dialog = field.window()
        assert isinstance(dialog, QtWidgets.QDialog)
        dialog.reject()

    QtCore.QTimer.singleShot(100, reject_repetition)
    Gui.runCommand("TechDraw_ExtensionInsertRepetition")
    _events(16)
    assert not Gui.Control.activeDialog()


def _human_result(document, dimension, command, before, repeat_count=None) -> str:
    dimension.FormatSpec = before
    assert document.recompute([dimension], True, True) is not False
    undo_before = int(document.UndoCount)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(dimension)
    _run_human_command(command, repeat_count)
    after = str(dimension.FormatSpec)
    assert after != before
    assert int(document.UndoCount) == undo_before + 1
    document.undo()
    _events(12)
    assert str(dimension.FormatSpec) == before
    return after


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_DIMENSION_TEXT_CAPABILITY_NAME)
    schema = definition.provider_schema(DRAWING_DIMENSION_TEXT_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    by_operation = {
        branch["properties"]["operation"]["const"]: branch
        for branch in branches
    }
    assert tuple(by_operation) == DRAWING_DIMENSION_TEXT_OPERATIONS
    assert all(branch["additionalProperties"] is False for branch in branches)
    assert by_operation["insert_repetition_prefix"]["required"] == [
        "operation", "page", "dimensions", "repeat_count"
    ]
    assert all(
        branch["properties"]["dimensions"]["maxItems"] == 64
        for branch in branches
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 20 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, dimensions, operation, repeat_count=None) -> dict:
    page_state = drawing_page_state(page)
    arguments = {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "dimensions": [
            {
                "object_name": dimension.Name,
                "expected_format_state_sha256": drawing_format_state(dimension)[
                    "format_state_sha256"
                ],
            }
            for dimension in dimensions
        ],
    }
    if repeat_count is not None:
        arguments["repeat_count"] = repeat_count
    return arguments


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-dimension-text-"
        )
        save_path = Path(temporary.name) / "drawing-dimension-text.FCStd"
        controller, surface = _surface()
        expected_actions = {
            "TechDraw_ExtensionInsertDiameter": (
                "insert_diameter_prefix",
                "ExactDrawingDimensionsAndDiameterPrefix",
            ),
            "TechDraw_ExtensionInsertSquare": (
                "insert_square_prefix",
                "ExactDrawingDimensionsAndSquarePrefix",
            ),
            "TechDraw_ExtensionInsertRepetition": (
                "insert_repetition_prefix",
                "ExactDrawingDimensionsAndRepetitionCount",
            ),
            "TechDraw_ExtensionRemovePrefixChar": (
                "remove_prefix",
                "ExactDrawingDimensionsAndPrefixRemoval",
            ),
            "TechDraw_ExtensionIncreaseDecimal": (
                "increase_decimals",
                "ExactDrawingDimensionsAndPrecisionIncrease",
            ),
            "TechDraw_ExtensionDecreaseDecimal": (
                "decrease_decimals",
                "ExactDrawingDimensionsAndPrecisionDecrease",
            ),
        }
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        for action_id, (operation, target_type) in expected_actions.items():
            plan = action_plans[action_id]
            actual = (
                plan.capability_family,
                plan.operation_variant,
                plan.exact_target_type,
                plan.transaction_behavior,
                plan.background_required,
            )
            expected = (
                DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )
            assert actual == expected, (action_id, actual, expected)

        document = App.newDocument("NativeDrawingDimensionTextGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, first, second = _create_fixture(document)

        human_cases = (
            (
                "insert_diameter_prefix",
                "TechDraw_ExtensionInsertDiameter",
                "%.2f",
                None,
            ),
            (
                "insert_square_prefix",
                "TechDraw_ExtensionInsertSquare",
                "⌀%.2f",
                None,
            ),
            (
                "insert_repetition_prefix",
                "TechDraw_ExtensionInsertRepetition",
                "□⌀%.2f",
                12,
            ),
            (
                "remove_prefix",
                "TechDraw_ExtensionRemovePrefixChar",
                "12× □⌀%.2f",
                None,
            ),
            (
                "increase_decimals",
                "TechDraw_ExtensionIncreaseDecimal",
                "%.2f",
                None,
            ),
            (
                "decrease_decimals",
                "TechDraw_ExtensionDecreaseDecimal",
                "%.3f",
                None,
            ),
        )
        human = {
            operation: _human_result(document, first, command, before, count)
            for operation, command, before, count in human_cases
        }
        assert human == {
            "insert_diameter_prefix": "⌀%.2f",
            "insert_square_prefix": "□⌀%.2f",
            "insert_repetition_prefix": "12× □⌀%.2f",
            "remove_prefix": "%.2f",
            "increase_decimals": "%.3f",
            "decrease_decimals": "%.2f",
        }

        first.FormatSpec = "%.2f"
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        cancel_undo = int(document.UndoCount)
        _cancel_human_repetition()
        assert str(first.FormatSpec) == "%.2f"
        assert int(document.UndoCount) == cancel_undo

        first.FormatSpec = "%.2f"
        second.FormatSpec = "%.2f"
        assert document.recompute([first, second], True, True) is not False
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-dimension-text-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(turn.surface, controller)

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

        def call(arguments: dict, succeeds: bool = True, call_id=None) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_DIMENSION_TEXT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-drawing-dimension-text-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        dimensions = (first, second)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(first)
        selection_before = _selection()
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility)
            for obj in (source, view, first, second)
        )
        page_before = drawing_page_state(page)
        projection_before = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]

        sequence = (
            ("insert_diameter_prefix", None),
            ("insert_square_prefix", None),
            ("insert_repetition_prefix", 12),
            ("remove_prefix", None),
            ("increase_decimals", None),
            ("decrease_decimals", None),
        )
        first_arguments = None
        first_response = None
        for operation, repeat_count in sequence:
            arguments = _arguments(page, dimensions, operation, repeat_count)
            revision = state_store.current_revision(str(document.Uid))
            call_id = f"native-drawing-dimension-text-{operation}"
            response = call(arguments, call_id=call_id)
            assert response["operation"] == operation
            assert response["changed_count"] == 2
            assert [item["object_name"] for item in response["dimensions"]] == [
                first.Name,
                second.Name,
            ]
            assert all(
                item["format_spec_after"] == human[operation]
                for item in response["dimensions"]
            )
            assert all(
                str(dimension.FormatSpec) == human[operation]
                for dimension in dimensions
            )
            assert state_store.current_revision(str(document.Uid)) == revision + 1
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 4096
            assert _selection() == selection_before
            assert not Gui.Control.activeDialog()
            if first_arguments is None:
                first_arguments = arguments
                first_response = response

        assert first_arguments is not None and first_response is not None
        repeated = call(
            first_arguments,
            call_id="native-drawing-dimension-text-insert_diameter_prefix",
        )
        assert repeated == first_response

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        inapplicable = call(
            _arguments(page, dimensions, "remove_prefix"),
            False,
        )
        assert inapplicable["error_code"] == (
            "NATIVE_DRAWING_DIMENSION_TEXT_INAPPLICABLE"
        )
        assert len(inapplicable["repair"]["inapplicable_targets"]) == 2

        stale = _arguments(page, dimensions, "insert_square_prefix")
        stale["dimensions"][0]["expected_format_state_sha256"] = "0" * 64
        rejected = call(stale, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_TEXT_TARGET_STALE"

        wrong_type = _arguments(page, dimensions, "insert_square_prefix")
        wrong_type["dimensions"][0]["object_name"] = view.Name
        rejected = call(wrong_type, False)
        assert rejected["error_code"] == "NATIVE_TARGET_INVALID"
        assert rejected["accepted_types"] == ["TechDraw::DrawViewDimension"]
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo

        first.FormatSpec = "%.9f"
        second.FormatSpec = "%.2f"
        assert document.recompute([first, second], True, True) is not False
        atomic_before = tuple(str(item.FormatSpec) for item in dimensions)
        rejected = call(
            _arguments(page, dimensions, "increase_decimals"),
            False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_DIMENSION_TEXT_INAPPLICABLE"
        assert tuple(str(item.FormatSpec) for item in dimensions) == atomic_before

        first.FormatSpec = "%.2f"
        second.FormatSpec = "%.2f"
        assert document.recompute([first, second], True, True) is not False
        rollback_states = tuple(drawing_format_state(item) for item in dimensions)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = RuntimeModule.verify_drawing_dimension_text

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected dimension-text verification failure")

        RuntimeModule.verify_drawing_dimension_text = fail_verify
        try:
            rejected = call(
                _arguments(page, dimensions, "insert_diameter_prefix"),
                False,
            )
        finally:
            RuntimeModule.verify_drawing_dimension_text = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert tuple(
            drawing_format_state(item)["format_state_sha256"] for item in dimensions
        ) == tuple(item["format_state_sha256"] for item in rollback_states)
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo

        durable = call(_arguments(page, dimensions, "insert_square_prefix"))
        durable_hashes = tuple(
            item["format_state_sha256"] for item in durable["dimensions"]
        )
        names = {
            "page": page.Name,
            "view": view.Name,
            "first": first.Name,
            "second": second.Name,
        }
        document.undo()
        _events(12)
        assert all(str(item.FormatSpec) == "%.2f" for item in dimensions)
        document.redo()
        _events(12)
        first = document.getObject(names["first"])
        second = document.getObject(names["second"])
        dimensions = (first, second)
        assert tuple(
            drawing_format_state(item)["format_state_sha256"] for item in dimensions
        ) == durable_hashes

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert drawing_page_state(page) == page_before
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == projection_before
        assert _selection() == selection_before
        assert tuple(
            bool(obj.ViewObject.Visibility)
            for obj in (source, view, first, second)
        ) == visibility_before

        snapshot = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection=drawing_selection_state(document),
        )
        selected_targets = snapshot["domain"]["selected_format_targets"]
        assert selected_targets == [drawing_format_state(first)]

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        first = document.getObject(names["first"])
        second = document.getObject(names["second"])
        assert all(item is not None for item in (page, view, first, second))
        page.ViewObject.show()
        assert document.recompute([first, second, view, page], True, True) is not False
        _events(20)
        assert tuple(
            drawing_format_state(item)["format_state_sha256"]
            for item in (first, second)
        ) == durable_hashes

        print(
            "VIBECAD_NATIVE_DRAWING_DIMENSION_TEXT_GUI_OK operations=6 "
            "diameter=true square=true repetition=true remove=true "
            "increase=true decrease=true human_oracle=true shared_host_builder=true "
            "dialog_cancel=true exact_page=true exact_targets=true batch=true "
            "atomic_refusal=true precise_repair=true closed_schema=true "
            "wrong_type=true stale=true selection=true visibility=true history=true "
            "page_boundary=true projection=true rollback=true revision=true "
            "idempotency=true undo=true redo=true snapshot=true reopen=true "
            "low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
