# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing format customization."""

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
import VibeCADNativeDrawingFormatRuntime as RuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingFormatSchema import (
    DRAWING_FORMAT_CAPABILITY_NAME,
    DRAWING_FORMAT_OPERATIONS,
)
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingMeasurementAnnotationState import (
    drawing_measurement_annotation_state,
)
from VibeCADNativeDrawingState import drawing_page_state
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
    document.openTransaction("Create Drawing format page fixture")
    page_transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "FormatSource")
        source.Shape = Part.makeBox(36.0, 24.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "FormatPage")
        template = document.addObject("TechDraw::DrawSVGTemplate", "FormatTemplate")
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
        view = document.addObject("TechDraw::DrawViewPart", "FormatView")
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
        App.closeActiveTransaction(True, page_transaction)
        raise
    App.closeActiveTransaction(False, page_transaction)
    page.ViewObject.show()
    _events(24)

    document.openTransaction("Create Drawing format annotations")
    annotation_transaction = int(document.getBookedTransactionID())
    try:
        projection = drawing_projected_geometry_state(view)
        edge = next(
            item for item in projection["elements"] if item["element_type"] == "edge"
        )
        face = next(
            item for item in projection["elements"] if item["element_type"] == "face"
        )
        dimension = TechDrawGui.createProjectedDimension(
            view, "Distance", [edge["name"]], False, 15.0, 28.0
        )
        dimension.Label = "Format Dimension"
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())
        balloon = TechDrawGui.createProjectedBalloon(
            view, edge["name"], "B-01", "Format Balloon", 20.0, 15.0
        )
        document.publishProvisionalTimelineOperationBlock(balloon, (), ())
        measurement = TechDrawGui.createProjectedMeasurementAnnotation(
            view, "area", [face["name"]], "Format Measurement"
        )
        document.publishProvisionalTimelineOperationBlock(measurement, (), ())
        assert document.recompute(
            [dimension, balloon, measurement, view, page], True, True
        ) is not False
    except Exception:
        App.closeActiveTransaction(True, annotation_transaction)
        raise
    App.closeActiveTransaction(False, annotation_transaction)
    _events(24)
    return source, page, view, dimension, balloon, measurement


def _human_format(target, value: str) -> str:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(target)
    Gui.runCommand("TechDraw_ExtensionCustomizeFormat")
    _events(16)
    assert Gui.Control.activeDialog()
    line_edit = Gui.getMainWindow().findChild(QtWidgets.QLineEdit, "leFormat")
    assert line_edit is not None
    line_edit.setText(value)
    _events(10)
    preview = Gui.getMainWindow().findChild(QtWidgets.QLabel, "lbShowPreview")
    assert preview is not None and preview.text() != "Invalid dimension format"
    preview_text = preview.text()
    task = Gui.Control.activeTaskDialog()
    assert task is not None
    task.accept()
    _events(16)
    assert not Gui.Control.activeDialog()
    return preview_text


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_FORMAT_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_FORMAT_OPERATIONS)
    branches = {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }
    assert set(branches) == set(DRAWING_FORMAT_OPERATIONS)
    assert branches["set_dimension_format"]["required"] == [
        "operation", "dimension", "format_spec"
    ]
    assert branches["set_balloon_text"]["required"] == [
        "operation", "balloon", "text"
    ]
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    assert len(json.dumps(schema, separators=(",", ":")).encode()) < 6 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_FORMAT_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(obj) -> dict[str, str]:
    state = drawing_format_state(obj)
    return {
        "object_name": state["object_name"],
        "expected_format_state_sha256": state["format_state_sha256"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-format-")
        save_path = Path(temporary.name) / "drawing-format.FCStd"
        controller, surface = _surface()
        plan = next(
            item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id == "TechDraw_ExtensionCustomizeFormat"
        )
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
        ) == (
            DRAWING_FORMAT_CAPABILITY_NAME,
            "set_dimension_format",
            "ExactDrawingDimensionAndCompleteFormat",
        )

        document = App.newDocument("NativeDrawingFormatGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, dimension, balloon, measurement = _create_fixture(document)

        dimension_before = drawing_format_state(dimension)
        human_preview = _human_format(dimension, "QA %.3f REF")
        human_dimension = drawing_format_state(dimension)
        assert human_dimension["current_value"] == "QA %.3f REF"
        assert human_preview and human_preview == human_dimension["rendered_text"], (
            human_preview,
            human_dimension,
        )
        document.undo()
        _events(12)
        assert drawing_format_state(dimension)["format_state_sha256"] == (
            dimension_before["format_state_sha256"]
        )

        balloon_before = drawing_format_state(balloon)
        assert _human_format(balloon, "") == ""
        assert drawing_format_state(balloon)["current_value"] == ""
        document.undo()
        _events(12)
        assert drawing_format_state(balloon)["format_state_sha256"] == (
            balloon_before["format_state_sha256"]
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-format-gui")

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

        def call(arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_FORMAT_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-format-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(dimension)
        selection_before = _selection()
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, dimension, balloon)
        )
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        page_before = drawing_page_state(page)
        projection_before = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        revision_before = state_store.current_revision(str(document.Uid))

        stale = _target(dimension)
        stale["expected_format_state_sha256"] = "0" * 64
        rejected = call(
            {
                "operation": "set_dimension_format",
                "dimension": stale,
                "format_spec": "N %.2f",
            },
            False,
        )
        assert rejected["error_code"] == "NATIVE_DRAWING_FORMAT_TARGET_STALE"

        invalid = call(
            {
                "operation": "set_dimension_format",
                "dimension": _target(dimension),
                "format_spec": "NO NUMERIC PLACEHOLDER",
            },
            False,
        )
        assert invalid["error_code"] == "NATIVE_DRAWING_FORMAT_INVALID"
        assert invalid["repair"]["accepted_placeholders"] == [
            "%f", "%.2f", "%g", "%w", "%r"
        ]

        wrong_type = call(
            {
                "operation": "set_dimension_format",
                "dimension": _target(balloon),
                "format_spec": "N %.2f",
            },
            False,
        )
        assert wrong_type["error_code"] == "NATIVE_TARGET_INVALID"
        assert wrong_type["accepted_types"] == ["TechDraw::DrawViewDimension"]
        assert state_store.current_revision(str(document.Uid)) == revision_before

        dimension_result = call(
            {
                "operation": "set_dimension_format",
                "dimension": _target(dimension),
                "format_spec": "N %.2f mm NOM",
            }
        )
        dimension_state = dimension_result["format_target"]
        assert dimension_result["operation"] == "set_dimension_format"
        assert dimension_state["target_kind"] == "dimension"
        assert dimension_state["current_value"] == "N %.2f mm NOM"
        assert dimension_result["host_preview"] == dimension_state["rendered_text"]
        assert len(json.dumps(dimension_result, separators=(",", ":")).encode()) < 4 * 1024
        dimension_hash = dimension_state["format_state_sha256"]
        dimension_name = dimension.Name
        assert state_store.current_revision(str(document.Uid)) == revision_before + 1
        assert not Gui.Control.activeDialog()

        document.undo()
        _events(12)
        assert drawing_format_state(dimension)["format_state_sha256"] == (
            dimension_before["format_state_sha256"]
        )
        document.redo()
        _events(12)
        dimension = document.getObject(dimension_name)
        assert drawing_format_state(dimension)["format_state_sha256"] == dimension_hash

        measurement_revision_before = state_store.current_revision(
            str(document.Uid)
        )
        measurement_result = call(
            {
                "operation": "set_balloon_text",
                "balloon": _target(measurement),
                "text": "AREA – INSPECTED",
            }
        )
        measurement_format = measurement_result["format_target"]
        assert measurement_result["operation"] == "set_balloon_text"
        assert measurement_format["target_kind"] == "balloon"
        assert measurement_format["current_value"] == "AREA – INSPECTED"
        assert measurement_result["host_preview"] == "AREA – INSPECTED"
        measured_state = drawing_measurement_annotation_state(measurement)
        assert measured_state["text"] == "AREA – INSPECTED"
        assert measured_state["measurement_current"]
        measurement_hash = measurement_format["format_state_sha256"]
        measurement_name = measurement.Name
        assert state_store.current_revision(str(document.Uid)) == (
            measurement_revision_before + 1
        )

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert drawing_page_state(page) == page_before
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == projection_before
        assert _selection() == selection_before
        assert tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, dimension, balloon)
        ) == visibility_before
        assert not Gui.Control.activeDialog()

        rollback_state = drawing_format_state(measurement)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = RuntimeModule.verify_drawing_format

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected format verification failure")

        RuntimeModule.verify_drawing_format = fail_verify
        try:
            rejected = call(
                {
                    "operation": "set_balloon_text",
                    "balloon": _target(measurement),
                    "text": "ROLLBACK",
                },
                False,
            )
        finally:
            RuntimeModule.verify_drawing_format = original_verify
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        rollback_after = drawing_format_state(measurement)
        assert rollback_after["format_state_sha256"] == (
            rollback_state["format_state_sha256"]
        )
        assert rollback_after["current_value"] == rollback_state["current_value"]
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo

        snapshot = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 1,
                "items": [
                    {
                        "object": {
                            "document_uid": str(document.Uid),
                            "object_name": measurement_name,
                            "type_id": measurement.TypeId,
                        }
                    }
                ],
            },
        )
        assert snapshot["domain"]["selected_format_targets"] == [
            drawing_format_state(measurement)
        ]
        assert len(json.dumps(snapshot, separators=(",", ":")).encode()) < 96 * 1024

        names = {
            "page": page.Name,
            "dimension": dimension_name,
            "measurement": measurement_name,
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        dimension = document.getObject(names["dimension"])
        measurement = document.getObject(names["measurement"])
        assert all(obj is not None for obj in (page, dimension, measurement))
        page.ViewObject.show()
        assert document.recompute() is not False
        _events(20)
        assert drawing_format_state(dimension)["format_state_sha256"] == dimension_hash
        assert drawing_format_state(measurement)["format_state_sha256"] == measurement_hash
        assert measurement in tuple(page.Views)
        assert dimension in tuple(document.VibeCADTimeline.Operations)
        assert measurement in tuple(document.VibeCADTimeline.Operations)

        print(
            "VIBECAD_NATIVE_DRAWING_FORMAT_GUI_OK operations=2 "
            "dimension=true balloon=true measured_balloon=true human_oracle=true "
            "shared_host_builder=true host_validation=true exact_target=true "
            "closed_schema=true empty_human_text=true stale_target=true "
            "selection=true visibility=true page_boundary=true history=true "
            "rollback=true revision=true undo=true redo=true snapshot=true "
            "reopen=true low_noise=true no_task=true",
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
