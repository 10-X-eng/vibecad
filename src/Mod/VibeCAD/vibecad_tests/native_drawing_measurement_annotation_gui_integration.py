# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing measurement annotations."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingDimensionRuntime as DimensionRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_NAME,
)
from VibeCADNativeDrawingMeasurementAnnotationSchema import (
    DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS,
)
from VibeCADNativeDrawingMeasurementAnnotationState import (
    drawing_measurement_annotation_state,
    is_drawing_measurement_annotation,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/TechDraw/dimensioning"


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing", surface.surface_id
    assert surface.to_environment()["preferences"][
        "techdraw.separated_dimensioning_tools"
    ]
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _create_fixture(document):
    document.openTransaction("Create Drawing measurement fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "MeasurementSource")
        source.Label = "Measurement Source"
        source.Shape = Part.makeCompound(
            [
                Part.makeCylinder(
                    13.0,
                    8.0,
                    App.Vector(-22.0, 0.0, 0.0),
                    App.Vector(0.0, 0.0, 1.0),
                ),
                Part.makeCylinder(
                    8.0,
                    8.0,
                    App.Vector(20.0, 0.0, 0.0),
                    App.Vector(0.0, 0.0, 1.0),
                ),
            ]
        )
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "MeasurementPage")
        page.Label = "Measurement Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate",
            "MeasurementTemplate",
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

        view = document.addObject("TechDraw::DrawViewPart", "MeasurementView")
        view.Label = "Measurement View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.5
        view.Rotation = 17.0
        view.X = 105.0
        view.Y = 80.0
        view.CoarseView = False
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return source, page, view


def _elements(view, kind: str) -> tuple[dict, ...]:
    projection = drawing_projected_geometry_state(view)
    candidates = tuple(
        item
        for item in projection["elements"]
        if item["element_type"] == kind and item.get("visible", True)
    )
    assert len(candidates) >= 2, (kind, len(candidates))
    if kind == "edge":
        circular = tuple(
            item
            for item in candidates
            if "circle" in str(item.get("geometry_type", "")).casefold()
        )
        if len(circular) >= 2:
            candidates = circular
    return candidates[:2]


def _target(element: dict, *, state_hash: str | None = None) -> dict[str, str]:
    return {
        "subelement": element["name"],
        "expected_element_state_sha256": (
            element["element_state_sha256"] if state_hash is None else state_hash
        ),
    }


def _arguments(
    page,
    view,
    elements: tuple[dict, ...],
    operation: str,
    suffix: str = "",
) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": operation,
        "page": {
            "object_name": page_state["object_name"],
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view_state["object_name"],
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection[
                "projection_state_sha256"
            ],
        },
        "elements": [_target(element) for element in elements],
        "label": (
            f"Area Annotation{suffix}"
            if operation == "create_area_annotation"
            else f"Arc Length Annotation{suffix}"
        ),
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(
        DRAWING_DIMENSION_CAPABILITY_NAME
    )
    assert definition is not None
    schema = definition.provider_schema(
        DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024
    branches = {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }
    assert set(branches) == set(DRAWING_MEASUREMENT_ANNOTATION_OPERATIONS)
    for branch in branches.values():
        assert branch["required"] == [
            "operation",
            "page",
            "view",
            "elements",
            "label",
        ]
        assert branch["additionalProperties"] is False
        assert branch["properties"]["elements"]["maxItems"] == 64
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_DIMENSION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _annotations(document) -> tuple:
    return tuple(
        obj
        for obj in document.Objects
        if is_drawing_measurement_annotation(obj)
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    preferences = App.ParamGet(_PREFERENCE_PATH)
    separated_before = preferences.GetBool("SeparatedDimensioningTools", False)
    exit_code = 1
    try:
        preferences.SetBool("SeparatedDimensioningTools", True)
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-measurement-"
        )
        save_path = Path(temporary.name) / "drawing-measurement.FCStd"
        controller, surface = _surface()
        plans = {
            item.command_id: item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id
            in {
                "TechDraw_ExtensionAreaAnnotation",
                "TechDraw_ExtensionArcLengthAnnotation",
            }
        }
        assert set(plans) == {
            "TechDraw_ExtensionAreaAnnotation",
            "TechDraw_ExtensionArcLengthAnnotation",
        }
        assert (
            plans["TechDraw_ExtensionAreaAnnotation"].capability_family,
            plans["TechDraw_ExtensionAreaAnnotation"].operation_variant,
            plans["TechDraw_ExtensionAreaAnnotation"].exact_target_type,
        ) == (
            DRAWING_DIMENSION_CAPABILITY_NAME,
            "create_area_annotation",
            "ExactDrawingProjectedFacesAndAreaAnnotation",
        )
        assert (
            plans["TechDraw_ExtensionArcLengthAnnotation"].capability_family,
            plans["TechDraw_ExtensionArcLengthAnnotation"].operation_variant,
            plans["TechDraw_ExtensionArcLengthAnnotation"].exact_target_type,
        ) == (
            DRAWING_DIMENSION_CAPABILITY_NAME,
            "create_arc_length_annotation",
            "ExactDrawingOrderedProjectedEdgesAndArcLengthAnnotation",
        )

        document = App.newDocument("NativeDrawingMeasurementGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        faces = _elements(view, "face")
        edges = _elements(view, "edge")

        human_objects = tuple(document.Objects)
        human_views = tuple(page.Views)
        human_history = tuple(document.VibeCADTimeline.Operations)
        human_projection = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        human_index = int(page.NextBalloonIndex)
        for command, elements, kind in (
            ("TechDraw_ExtensionAreaAnnotation", faces, "area"),
            ("TechDraw_ExtensionArcLengthAnnotation", edges, "arc_length"),
        ):
            Gui.Selection.clearSelection()
            for element in elements:
                Gui.Selection.addSelection(view, element["name"])
            selection_before_command = _selection()
            Gui.runCommand(command)
            _events(20)
            created = tuple(
                obj for obj in document.Objects if obj not in human_objects
            )
            assert len(created) == 1
            state = drawing_measurement_annotation_state(created[0])
            assert state["kind"] == kind
            assert [item["subelement"] for item in state["source_elements"]] == [
                item["name"] for item in elements
            ]
            assert state["value"] > 0.0 and state["text"]
            assert state["measurement_current"]
            assert state["default_placement"]
            assert _selection() == selection_before_command
            assert int(page.NextBalloonIndex) == human_index
            assert not Gui.Control.activeDialog()
            created_name = str(created[0].Name)
            document.openTransaction("Move human measurement annotation")
            move_transaction = int(document.getBookedTransactionID())
            created[0].X = float(created[0].X) + 4.0
            App.closeActiveTransaction(False, move_transaction)
            moved_state = drawing_measurement_annotation_state(created[0])
            assert moved_state["measurement_current"]
            assert not moved_state["default_placement"]
            document.undo()
            _events(12)
            assert drawing_measurement_annotation_state(created[0])[
                "measurement_state_sha256"
            ] == state["measurement_state_sha256"]
            document.undo()
            _events(16)
            assert document.getObject(created_name) is None
            assert tuple(document.Objects) == human_objects
            assert tuple(page.Views) == human_views
            assert tuple(document.VibeCADTimeline.Operations) == human_history
            assert drawing_projected_geometry_state(view)[
                "projection_state_sha256"
            ] == human_projection

        Gui.Selection.clearSelection()
        faces = _elements(view, "face")
        edges = _elements(view, "edge")
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-measurement-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(
                controller
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

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_DIMENSION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-measurement-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(view, faces[0]["name"])
        selection_before = _selection()
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        projection_before = drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ]
        view_before = drawing_view_state(view)["state_sha256"]
        history_before = tuple(document.VibeCADTimeline.Operations)
        index_before = int(page.NextBalloonIndex)
        revision_before = state_store.current_revision(str(document.Uid))

        stale = _arguments(
            page, view, faces, "create_area_annotation", " stale"
        )
        stale["elements"][0] = _target(faces[0], state_hash="0" * 64)
        rejected = call(stale, succeeds=False)
        assert rejected["error_code"] == "NATIVE_DRAWING_MEASUREMENT_REFERENCE_STALE"
        assert _annotations(document) == ()
        assert state_store.current_revision(str(document.Uid)) == revision_before

        area_result = call(
            _arguments(page, view, faces, "create_area_annotation")
        )
        area_state = area_result["measurement_annotation"]
        assert area_result["operation"] == "create_area_annotation"
        assert area_state["kind"] == "area" and area_state["unit"] == "mm^2"
        assert area_state["value"] > 0.0 and area_state["text"]
        assert area_state["measurement_current"]
        assert area_state["anchor_matches_source"] is True
        assert area_state["default_placement"]
        assert area_state["bubble_offset_in_view_mm"] == {
            "x_mm": 0.0,
            "y_mm": 0.0,
        }
        assert area_state["style"]["bubble_shape"] == "Rectangle"
        assert area_state["style"]["leader_end"] == "None"
        assert area_state["style"]["line_visible"] is False
        assert len(json.dumps(area_result, separators=(",", ":")).encode()) < (
            16 * 1024
        )
        area_name = area_state["object_name"]
        area = document.getObject(area_name)
        assert area is not None
        assert tuple(document.VibeCADTimeline.Operations) == (*history_before, area)
        assert area in tuple(page.Views) and area in tuple(view.InList)
        assert int(page.NextBalloonIndex) == index_before
        assert _selection() == selection_before
        assert (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        ) == visibility_before
        assert drawing_view_state(view)["state_sha256"] == view_before
        assert drawing_projected_geometry_state(view)[
            "projection_state_sha256"
        ] == projection_before
        assert state_store.current_revision(str(document.Uid)) == revision_before + 1
        assert not Gui.Control.activeDialog()

        document.undo()
        _events(12)
        assert document.getObject(area_name) is None
        document.redo()
        _events(16)
        area = document.getObject(area_name)
        assert area is not None
        assert drawing_measurement_annotation_state(area)[
            "measurement_state_sha256"
        ] == area_state["measurement_state_sha256"]

        edges = _elements(view, "edge")
        arc_result = call(
            _arguments(page, view, edges, "create_arc_length_annotation")
        )
        arc_state = arc_result["measurement_annotation"]
        assert arc_result["operation"] == "create_arc_length_annotation"
        assert arc_state["kind"] == "arc_length" and arc_state["unit"] == "mm"
        expected_length = sum(float(item["length_view_mm"]) for item in edges)
        expected_length /= float(view.Scale)
        assert math.isclose(
            float(arc_state["value"]),
            expected_length,
            rel_tol=1.0e-9,
            abs_tol=1.0e-8,
        )
        assert arc_state["text"].startswith("◠ ")
        assert arc_state["measurement_current"]
        assert arc_state["anchor_matches_source"] is None
        assert arc_state["default_placement"]
        assert math.isclose(
            float(arc_state["bubble_offset_in_view_mm"]["x_mm"]),
            20.0,
            rel_tol=1.0e-9,
            abs_tol=1.0e-8,
        )
        assert math.isclose(
            float(arc_state["bubble_offset_in_view_mm"]["y_mm"]),
            20.0,
            rel_tol=1.0e-9,
            abs_tol=1.0e-8,
        )
        arc_name = arc_state["object_name"]
        arc = document.getObject(arc_name)
        assert arc is not None
        assert tuple(document.VibeCADTimeline.Operations) == (
            *history_before,
            area,
            arc,
        )
        assert int(page.NextBalloonIndex) == index_before
        assert _selection() == selection_before
        assert not Gui.Control.activeDialog()

        rollback_objects = tuple(document.Objects)
        rollback_views = tuple(page.Views)
        rollback_history = tuple(document.VibeCADTimeline.Operations)
        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = (
            DimensionRuntimeModule.verify_drawing_measurement_annotation
        )

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected measurement verification failure")

        DimensionRuntimeModule.verify_drawing_measurement_annotation = fail_verify
        try:
            rejected = call(
                _arguments(
                    page,
                    view,
                    faces,
                    "create_area_annotation",
                    " rollback",
                ),
                succeeds=False,
            )
        finally:
            DimensionRuntimeModule.verify_drawing_measurement_annotation = (
                original_verify
            )
        _events(12)
        assert rejected["error_code"] == "NATIVE_POSTCONDITION_FAILED", rejected
        assert tuple(document.Objects) == rollback_objects
        assert tuple(page.Views) == rollback_views
        assert tuple(document.VibeCADTimeline.Operations) == rollback_history
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        selected_snapshot = build_active_snapshot(
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
                            "object_name": arc_name,
                            "type_id": arc.TypeId,
                        }
                    }
                ],
            },
        )
        selected = selected_snapshot["domain"][
            "selected_measurement_annotations"
        ]
        assert selected == [drawing_measurement_annotation_state(arc)]
        assert selected_snapshot["domain"]["selected_balloons"] == []
        page_summary = next(
            item
            for item in selected_snapshot["domain"]["pages"]
            if item["object_name"] == page.Name
        )
        arc_summary = next(
            item
            for item in page_summary["views"]
            if item["object_name"] == arc_name
        )
        assert arc_summary["measurement_annotation"]["kind"] == "arc_length"
        assert arc_summary["measurement_annotation"]["source_elements"] == (
            arc_state["source_elements"]
        )
        assert len(json.dumps(selected_snapshot, separators=(",", ":")).encode()) < (
            96 * 1024
        )

        final_states = {
            annotation.Name: drawing_measurement_annotation_state(annotation)[
                "measurement_state_sha256"
            ]
            for annotation in _annotations(document)
        }
        names = {
            "source": str(source.Name),
            "page": str(page.Name),
            "view": str(view.Name),
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        source = document.getObject(names["source"])
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert all(obj is not None for obj in (source, page, view))
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        for name, expected_hash in final_states.items():
            annotation = document.getObject(name)
            assert annotation is not None
            assert drawing_measurement_annotation_state(annotation)[
                "measurement_state_sha256"
            ] == expected_hash
            assert annotation in tuple(page.Views)
            assert annotation in tuple(view.InList)
            assert annotation in tuple(document.VibeCADTimeline.Operations)

        print(
            "VIBECAD_NATIVE_DRAWING_MEASUREMENT_ANNOTATION_GUI_OK operations=2 "
            "area=true arc_length=true human_oracle=true shared_host_builder=true "
            "host_measured=true exact_page=true exact_view=true projection_hash=true "
            "element_hash=true ordered_elements=true multi_element=true "
            "typed_persistence=true human_edit=true currentness=true "
            "unit_aware_text=true selection=true "
            "visibility=true tree_parent=true history=true stale_target=true "
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
        preferences.SetBool("SeparatedDimensioningTools", separated_before)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
