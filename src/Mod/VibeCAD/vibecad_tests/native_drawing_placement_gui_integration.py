# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing placement tools."""

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

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import (
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
)
from VibeCADNativeDrawingPlacementSchema import (
    DRAWING_PLACEMENT_CAPABILITY_NAMES,
)
from VibeCADNativeDrawingPlacementState import (
    drawing_dimension_label_placement_state,
    drawing_note_placement_state,
    drawing_view_placement_state,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 12) -> None:
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


def _fixture(document):
    document.openTransaction("Create Drawing placement fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "PlacementSource")
        source.Shape = Part.makeBox(42.0, 30.0, 10.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "PlacementPage")
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "PlacementTemplate"
        )
        template.Template = str(
            Path(App.getResourceDir())
            / "Mod" / "TechDraw" / "Templates" / "ISO"
            / "A4_Landscape_ISO5457_advanced.svg"
        )
        page.Template = template
        document.publishProvisionalTimelineOperationBlock(page, (template,), ())

        view = document.addObject("TechDraw::DrawViewPart", "PlacementView")
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        assert int(page.addView(view)) >= 1
        view.X = 70.0
        view.Y = 60.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())

        group = document.addObject(
            "TechDraw::DrawProjGroup", "PlacementProjectionGroup"
        )
        group.Source = [source]
        group.ProjectionType = "Third angle"
        group.ScaleType = "Custom"
        group.Scale = 1.0
        assert int(page.addView(group)) >= 1
        group.X = 155.0
        group.Y = 75.0
        front = group.addProjection("Front")
        top = group.addProjection("Top")
        right = group.addProjection("Right")
        front.Direction = App.Vector(0.0, 0.0, 1.0)
        front.XDirection = App.Vector(1.0, 0.0, 0.0)
        document.publishProvisionalTimelineOperationBlock(
            group,
            (front, top, right),
            (),
        )

        dimension = document.addObject(
            "TechDraw::DrawViewDimension", "PlacementDimension"
        )
        dimension.Type = "Distance"
        dimension.MeasureType = "Projected"
        dimension.References2D = [(view, "Edge1")]
        assert int(page.addView(dimension)) >= 1
        dimension.X = 12.0
        dimension.Y = 18.0
        document.publishProvisionalTimelineOperationBlock(dimension, (), ())

        note = document.addObject("TechDraw::DrawRichAnno", "PlacementNote")
        note.Label = "Placement Note"
        note.AnnoText = "<p>INSPECT</p>"
        note.MaxWidth = -1.0
        note.ShowFrame = False
        assert int(page.addView(note)) >= 1
        note.X = 210.0
        note.Y = 35.0
        document.publishProvisionalTimelineOperationBlock(note, (), ())
        assert document.recompute(
            [source, view, group, dimension, note, page], True, True
        )
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    return page, view, group, dimension, note


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-placement-"
        )
        saved_path = Path(temporary.name) / "drawing-placement.FCStd"
        controller, surface = _surface()
        document = App.newDocument("NativeDrawingPlacementGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        page, view, group, dimension, note = _fixture(document)

        registry = build_native_capability_registry()
        provider = resolve_native_provider_surface(surface, registry)
        assert provider.available is True, provider.summary()
        assert set(DRAWING_PLACEMENT_CAPABILITY_NAMES) <= set(provider.tool_names)
        schemas = {
            schema["name"]: schema
            for schema in provider.schemas
            if schema["name"] in DRAWING_PLACEMENT_CAPABILITY_NAMES
        }
        assert set(schemas) == set(DRAWING_PLACEMENT_CAPABILITY_NAMES)
        encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
        assert "expected_placement_state_sha256" not in encoded
        assert "unknown" not in encoded.casefold()
        turn = NativeTurnSnapshot.from_provider_surface(provider)

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-placement-gui")

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
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
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

        def call(tool: str, arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-placement-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        objects_before = tuple(document.Objects)
        page_views_before = tuple(page.Views or ())
        timeline_before = tuple(drawing_timeline_operations(document))
        selection_before = drawing_selection_state(document)
        visibility_before = drawing_visibility_state(document)
        undo_before = int(document.UndoCount)

        children = tuple(group.Views or ())
        child_targets = call(
            "drawing.place_views",
            {
                "page": {"object_name": page.Name},
                "views": [
                    {
                        "object_name": child.Name,
                        "position_on_page_mm": {
                            "x_mm": float(group.X) + float(child.X) + 10.0,
                            "y_mm": float(group.Y) + float(child.Y) - 5.0,
                        },
                    }
                    for child in children
                ],
            },
        )
        assert child_targets["changed_count"] == 1
        assert drawing_view_placement_state(group)["position_on_page_mm"] == {
            "x_mm": 165.0,
            "y_mm": 70.0,
        }
        assert int(document.UndoCount) == undo_before + 1

        contradictory_children = call(
            "drawing.place_views",
            {
                "page": {"object_name": page.Name},
                "views": [
                    {
                        "object_name": children[0].Name,
                        "position_on_page_mm": {
                            "x_mm": float(group.X) + float(children[0].X) + 4.0,
                            "y_mm": float(group.Y) + float(children[0].Y),
                        },
                    },
                    {
                        "object_name": children[1].Name,
                        "position_on_page_mm": {
                            "x_mm": float(group.X) + float(children[1].X) + 5.0,
                            "y_mm": float(group.Y) + float(children[1].Y),
                        },
                    },
                ],
            },
            False,
        )
        assert contradictory_children["error_code"] == (
            "NATIVE_DRAWING_PLACEMENT_TARGETS_INVALID"
        )
        assert int(document.UndoCount) == undo_before + 1

        duplicate = call(
            "drawing.place_views",
            {
                "page": {"object_name": page.Name},
                "views": [
                    {
                        "object_name": view.Name,
                        "position_on_page_mm": {"x_mm": 82.0, "y_mm": 72.0},
                    },
                    {
                        "object_name": view.Name,
                        "position_on_page_mm": {"x_mm": 84.0, "y_mm": 74.0},
                    },
                ],
            },
            False,
        )
        assert duplicate["error_code"] == "NATIVE_DRAWING_PLACEMENT_TARGETS_INVALID"
        assert int(document.UndoCount) == undo_before + 1

        placed_views = call(
            "drawing.place_views",
            {
                "page": {"object_name": page.Name},
                "views": [
                    {
                        "object_name": view.Name,
                        "position_on_page_mm": {"x_mm": 82.0, "y_mm": 72.0},
                    },
                    {
                        "object_name": group.Name,
                        "position_on_page_mm": {"x_mm": 172.0, "y_mm": 78.0},
                    },
                ],
            },
        )
        assert placed_views["changed_count"] == 2
        placed_dimension = call(
            "drawing.place_dimension_labels",
            {
                "page": {"object_name": page.Name},
                "dimensions": [
                    {
                        "object_name": dimension.Name,
                        "label_position_on_page_mm": {
                            "x_mm": float(view.X) + 28.0,
                            "y_mm": float(view.Y) + 24.0,
                        },
                    }
                ],
            },
        )
        assert placed_dimension["changed_count"] == 1
        assert placed_dimension["items"] == [
            {
                "object_name": dimension.Name,
                "label_position_on_page_mm": {
                    "x_mm": float(view.X) + 28.0,
                    "y_mm": float(view.Y) + 24.0,
                },
            }
        ]
        placed_note = call(
            "drawing.place_notes",
            {
                "page": {"object_name": page.Name},
                "notes": [
                    {
                        "object_name": note.Name,
                        "position_on_page_mm": {"x_mm": 225.0, "y_mm": 72.0},
                    }
                ],
            },
        )
        assert placed_note["changed_count"] == 1

        assert drawing_view_placement_state(view)["position_on_page_mm"] == {
            "x_mm": 82.0,
            "y_mm": 72.0,
        }
        assert drawing_view_placement_state(group)["position_on_page_mm"] == {
            "x_mm": 172.0,
            "y_mm": 78.0,
        }
        assert drawing_dimension_label_placement_state(dimension)[
            "label_position_in_view_mm"
        ] == {"x_mm": 28.0, "y_mm": 24.0}
        assert drawing_note_placement_state(note)["position_on_page_mm"] == {
            "x_mm": 225.0,
            "y_mm": 72.0,
        }
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views or ()) == page_views_before
        assert tuple(drawing_timeline_operations(document)) == timeline_before
        assert drawing_selection_state(document) == selection_before
        assert drawing_visibility_state(document) == visibility_before
        assert int(document.UndoCount) == undo_before + 4
        assert document.getBookedTransactionID() == 0

        assert document.recompute(None, True, True) is not False
        document.saveAs(str(saved_path))
        assert saved_path.is_file()
        App.closeDocument(document.Name)
        document = App.openDocument(str(saved_path))
        view = document.getObject("PlacementView")
        group = document.getObject("PlacementProjectionGroup")
        dimension = document.getObject("PlacementDimension")
        note = document.getObject("PlacementNote")
        assert drawing_view_placement_state(view)["position_on_page_mm"] == {
            "x_mm": 82.0,
            "y_mm": 72.0,
        }
        assert drawing_view_placement_state(group)["position_on_page_mm"] == {
            "x_mm": 172.0,
            "y_mm": 78.0,
        }
        assert drawing_dimension_label_placement_state(dimension)[
            "label_position_in_view_mm"
        ] == {"x_mm": 28.0, "y_mm": 24.0}
        assert drawing_note_placement_state(note)["position_on_page_mm"] == {
            "x_mm": 225.0,
            "y_mm": 72.0,
        }
        print(
            "VIBECAD_NATIVE_DRAWING_PLACEMENT_GUI_OK tools=3 batch=true "
            "projection_group=true projected_child_translation=true "
            "contradictory_child_refusal=true dimension_label=true note=true "
            "internal_targets=true duplicate_refusal=true atomic=true undo=true save=true reopen=true "
            "history_stable=true selection_stable=true visibility_stable=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            if str(getattr(document, "FileName", "") or ""):
                document.save()
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
