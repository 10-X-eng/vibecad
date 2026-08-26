# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for exact Native Drawing projected geometry reads."""

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
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeCommonRuntime import NativeCommonRuntime
from VibeCADNativeCommonSchema import common_capability_definitions
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTargets import read_current_selection
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADRibbonSurface import read_active_ribbon_surface


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _create_projection(document):
    source = document.addObject("Part::Feature", "ProjectedGeometrySource")
    source.Label = "Projected Geometry Source"
    source.Shape = Part.makeBox(40.0, 24.0, 12.0)

    page = document.addObject("TechDraw::DrawPage", "ProjectedGeometryPage")
    page.Label = "Projected Geometry Page"
    template = document.addObject(
        "TechDraw::DrawSVGTemplate",
        "ProjectedGeometryTemplate",
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
    view = document.addObject("TechDraw::DrawViewPart", "ProjectedGeometryView")
    view.Label = "Projected Geometry View"
    view.Source = [source]
    view.Direction = App.Vector(0.0, 0.0, 1.0)
    view.XDirection = App.Vector(1.0, 0.0, 0.0)
    view.ScaleType = "Custom"
    view.Scale = 1.0
    view.X = 90.0
    view.Y = 80.0
    assert int(page.addView(view)) >= 1
    assert document.recompute([source, view, page], True, True) is not False
    page.ViewObject.show()
    _events(20)
    return source, page, view


def _turn() -> NativeTurnSnapshot:
    definition = next(
        item
        for item in common_capability_definitions()
        if item.name == "drawing.projected_geometry"
    )
    schema = definition.provider_schema(("read",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    branch = schema["parameters"]["oneOf"][0]
    assert branch["required"] == [
        "view",
    ]
    assert branch["properties"]["offset"]["default"] == 0
    assert "page_size" not in branch["properties"]
    assert branch["properties"]["view"]["required"] == ["object_name"]
    assert branch["properties"]["view"]["properties"][
        "expected_projection_state_sha256"
    ][
        "default"
    ] == ""
    assert branch["properties"]["offset"]["maximum"] == 4096
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot(
                "drawing",
                1,
                "a" * 64,
                ("VibeCAD_DrawingProjectedGeometryGate",),
                ("VibeCAD_DrawingProjectedGeometryGate",),
                (),
            ),
            available=True,
            unavailable_reason="",
            tool_names=("drawing.projected_geometry",),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(view) -> dict[str, str]:
    return {
        "object_name": view.Name,
    }


def _arguments(
    view,
    *,
    offset: int,
    projection_hash: str,
) -> dict:
    target = _target(view)
    if projection_hash:
        target["expected_projection_state_sha256"] = projection_hash
    return {
        "operation": "read",
        "view": target,
        "offset": offset,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("TechDrawWorkbench")
        _events(20)
        provider = resolve_native_provider_surface(
            read_active_ribbon_surface(),
            build_native_capability_registry(),
        )
        assert provider.available, provider
        assert "drawing.page" not in provider.tool_names
        assert "drawing.view" not in provider.tool_names
        assert {
            "drawing.sources",
            "drawing.projected_geometry",
            "drawing.create_page",
            "drawing.template_fields",
            "drawing.redraw_page",
            "drawing.standard_view",
            "drawing.projection_group",
            "drawing.broken_view",
        } <= set(provider.tool_names)
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-projected-geometry-"
        )
        save_path = Path(temporary.name) / "projected-geometry.FCStd"
        document = App.newDocument("NativeDrawingProjectedGeometryGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_projection(document)

        legacy_descriptors = view.getProjectedElementDescriptors()
        assert set(legacy_descriptors) == {
            "coordinate_space",
            "view_scale",
            "edges",
            "vertices",
        }
        descriptors = view.getExactProjectedElementDescriptors()
        assert descriptors["coordinate_space"] == "view_projection_scaled_centered"
        assert descriptors["axis_convention"] == "x_right_y_up"
        assert descriptors["edges"][0]["name"] == "Edge0"
        assert descriptors["vertices"][0]["name"] == "Vertex0"
        assert descriptors["faces"][0]["name"] == "Face0"
        assert descriptors["faces"][0]["area_view_mm2"] > 0.0
        assert any(
            edge["source_mapping"]["candidates"]
            for edge in descriptors["edges"]
        )
        descriptor_vertices = {
            (
                round(vertex["point_2d"]["x"], 9),
                round(vertex["point_2d"]["y"], 9),
            )
            for vertex in descriptors["vertices"]
            if vertex["visible"]
        }
        conventional_vertices = {
            (round(vertex.x, 9), round(vertex.y, 9))
            for vertex in view.getVisibleVertexes(True)
        }
        assert descriptor_vertices == conventional_vertices
        assert any(abs(y) > 1.0e-9 for _x, y in descriptor_vertices)

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = service.native_assistant_undo_ledger()
        ledger.begin_run("native-drawing-projected-geometry-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "drawing",
            edit_or_task_active=lambda: False,
        )
        runtime = NativeCommonRuntime(context=context)
        turn = _turn()
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes={"drawing.projected_geometry": runtime},
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_index = 0

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                "drawing.projected_geometry",
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-projected-geometry-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        uid = str(document.Uid)
        revision_before = state_store.current_revision(uid)
        undo_before = int(document.UndoCount)
        history_before = tuple(document.VibeCADTimeline.Operations)
        first = call(
            _arguments(view, offset=0, projection_hash="")
        )
        assert first["returned_count"] == first["counts"]["total"]
        assert first["offset"] == 0
        assert first["next_offset"] is None
        assert len(first["view"]["projection_state_sha256"]) == 64
        projected_edges = {
            item["name"]: item
            for item in first["elements"]
            if item["kind"] == "line"
        }
        assert projected_edges
        for item in projected_edges.values():
            expected = ["aligned", item["orientation"]]
            if item["orientation"] == "diagonal":
                expected = ["aligned", "horizontal", "vertical"]
            assert item["valid_dimensions"] == expected, item
        projected_faces = [
            item for item in first["elements"] if item["kind"] == "face"
        ]
        assert projected_faces
        assert all(item["valid_dimensions"] == ["area"] for item in projected_faces)
        assert all(
            "valid_dimensions" not in item
            for item in first["elements"]
            if item["kind"] == "point"
        )
        projection_hash = first["view"]["projection_state_sha256"]
        names = [item["name"] for item in first["elements"]]
        next_offset = first["next_offset"]
        while next_offset is not None:
            page_result = call(
                _arguments(
                    view,
                    offset=next_offset,
                    projection_hash=projection_hash,
                )
            )
            assert page_result["view"]["projection_state_sha256"] == projection_hash
            names.extend(item["name"] for item in page_result["elements"])
            next_offset = page_result["next_offset"]
        assert len(names) == first["counts"]["total"]
        assert len(names) == len(set(names))
        assert {"Edge0", "Vertex0", "Face0"} <= set(names)
        assert state_store.current_revision(uid) == revision_before
        assert int(document.UndoCount) == undo_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, "Edge0")
        _events(8)
        selection = read_current_selection(document)
        assert selection["items"][0]["subelements"] == ["Edge0"]
        snapshot = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(uid),
            selection=selection,
        )
        selected = snapshot["domain"]["selected_projected_geometry"]
        assert len(selected) == 1
        assert selected[0]["projection_state_sha256"] == projection_hash
        assert selected[0]["selected_elements"][0]["name"] == "Edge0"
        assert len(selected[0]["selected_elements"][0]["element_state_sha256"]) == 64
        assert state_store.current_revision(uid) == revision_before

        view.Scale = 1.25
        assert document.recompute([view, page], True, True) is not False
        _events(12)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes={"drawing.projected_geometry": runtime},
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        fresh_after_change = call(
            _arguments(view, offset=0, projection_hash="")
        )
        assert fresh_after_change["view"]["projection_state_sha256"] != projection_hash
        stale_page = call(
            _arguments(
                view,
                offset=1,
                projection_hash=projection_hash,
            ),
            succeeds=False,
        )
        assert stale_page["error_code"] == "NATIVE_DRAWING_GEOMETRY_STATE_INVALID"

        current = drawing_projected_geometry_state(view)
        document.saveAs(str(save_path))
        names_before_reopen = (source.Name, page.Name, view.Name)
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        source = document.getObject(names_before_reopen[0])
        page = document.getObject(names_before_reopen[1])
        view = document.getObject(names_before_reopen[2])
        assert all(item is not None for item in (source, page, view))
        page.ViewObject.show()
        assert document.recompute([source, view, page], True, True) is not False
        _events(20)
        reopened = drawing_projected_geometry_state(view)
        assert reopened["projection_state_sha256"] == current["projection_state_sha256"]
        assert reopened["element_count"] == current["element_count"]

        print(
            "VIBECAD_NATIVE_DRAWING_PROJECTED_GEOMETRY_GUI_OK "
            "edge0=true vertex0=true face0=true source_mapping=true "
            "coordinate_system=true conventional_y_up=true legacy_compatible=true "
            "schema_limits=true pagination=true "
            "projection_hash=true element_hash=true selection_snapshot=true "
            "read_only_revision=true read_only_history=true fresh_view=true "
            "stale_page=true reopen=true host_dimension_applicability=true low_noise=true",
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
