# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI gate for one complete Native machined-part drawing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import TechDrawGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingCircleCenterLineSchema import (
    DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
)
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_BY_OPERATION,
)
from VibeCADNativeDrawingDimensionSeriesSchema import (
    DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingProviderState import compact_drawing_provider_state
from VibeCADNativeDrawingReadiness import drawing_page_readiness
from VibeCADNativeDrawingPlacementState import drawing_view_position_on_page
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_source_state, drawing_view_state
from VibeCADNativeProviderContext import provider_visible_native_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import require_frozen_native_surface
from VibeCADNativeTargets import read_current_selection
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADSession import _build_context_for_provider


class _RestoreRecomputeProbe:
    def __init__(self) -> None:
        self.object_names: list[str] = []

    def slotRecomputedObject(self, obj) -> None:
        self.object_names.append(str(obj.Name))


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _scene_positions_mm(page) -> dict[str, tuple[float, float, str]]:
    scene = TechDrawGui.getSceneForPage(page)
    items = scene.items()
    result = {}
    for item in items:
        object_name = str(item.data(1) or "")
        if not object_name:
            continue
        position = item.scenePos()
        parent = item.parentItem()
        assert object_name not in result, object_name
        result[object_name] = (
            float(position.x()) / 10.0,
            -float(position.y()) / 10.0,
            str(parent.data(1) or "") if parent is not None else "",
        )
    return result


def _surface():
    Gui.activateWorkbench("TechDrawWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "drawing"
    return controller, surface


def _create_fixture(document):
    document.openTransaction("Create machined mounting plate and Drawing page")
    transaction = int(document.getBookedTransactionID())
    try:
        plate = Part.makeBox(
            80.0,
            50.0,
            12.0,
            App.Vector(-40.0, -25.0, 0.0),
        )
        boss = Part.makeCylinder(14.0, 8.0, App.Vector(0.0, 0.0, 12.0))
        shape = plate.fuse(boss)
        bores = (
            Part.makeCylinder(6.0, 20.0, App.Vector(0.0, 0.0, 0.0)),
            Part.makeCylinder(3.0, 12.0, App.Vector(-28.0, -15.0, 0.0)),
            Part.makeCylinder(3.0, 12.0, App.Vector(28.0, -15.0, 0.0)),
            Part.makeCylinder(3.0, 12.0, App.Vector(-28.0, 15.0, 0.0)),
            Part.makeCylinder(3.0, 12.0, App.Vector(28.0, 15.0, 0.0)),
        )
        for bore in bores:
            shape = shape.cut(bore)
        source = document.addObject("Part::Feature", "MachinedMountingPlate")
        source.Label = "Machined Mounting Plate"
        source.Shape = shape
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        assert document.recompute([source], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return source


def _target(state: dict) -> dict[str, str]:
    return {"object_name": state["object_name"]}


def _view_target(view_state: dict, projection_hash: str) -> dict[str, str]:
    del projection_hash
    return _target(view_state)


def _projected_read_target(
    view_state: dict,
    projection_hash: str = "",
) -> dict[str, str]:
    del projection_hash
    return {"object_name": view_state["object_name"]}


def _element_target(element: dict) -> dict[str, str]:
    return {"subelement": element["name"]}


def _projection_arguments(page_state: dict, source_state: dict) -> dict:
    return {
        # A preferred label may already be used by the source.  FreeCAD then
        # assigns its deterministic unique-label form to the new group.
        "label": "Machined Mounting Plate",
        "page": _target(page_state),
        "sources": [_target(source_state)],
        "front_orientation": "front",
        "views": ["front", "top", "right", "left", "bottom", "rear"],
        "convention": "third_angle",
        "line_style": "visible",
    }


def _title_updates(page_state: dict) -> list[dict[str, str]]:
    current = {
        item["field_name"]: item["value"]
        for item in page_state["editable_fields"]
    }
    requested = {
        "title": "MACHINED MOUNTING PLATE",
        "supplementary_title_1": "ORTHOGRAPHIC PRODUCTION DRAWING",
        "drawing_number": "VC-DWG-001",
        "creator": "VibeCAD",
        "scale": "3 : 4",
        "sheet_number": "1 / 1",
    }
    assert set(requested) <= set(current)
    return [
        {
            "field_name": name,
            "value": value,
        }
        for name, value in requested.items()
        if current[name] != value
    ]


def _dimension_arguments(
    *,
    label: str,
    page_state: dict,
    view_state: dict,
    projection_hash: str,
    position: tuple[float, float],
) -> dict:
    return {
        "label": label,
        "page": _target(page_state),
        "view": _view_target(view_state, projection_hash),
        "label_position_on_page_mm": {
            "x_mm": float(view_state["x_mm"]) + position[0],
            "y_mm": float(view_state["y_mm"]) + position[1],
        },
    }


def _output_path(temporary: tempfile.TemporaryDirectory[str]) -> Path:
    requested = str(os.environ.get("VIBECAD_DRAWING_ACCEPTANCE_OUTPUT") or "")
    return (
        Path(requested).expanduser()
        if requested
        else Path(temporary.name) / "drawing.FCStd"
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = tempfile.TemporaryDirectory(
        prefix="vibecad-native-drawing-projection-group-"
    )
    exit_code = 1
    try:
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        projection_plan = plans["TechDraw_ProjectionGroup"]
        assert (
            projection_plan.capability_family,
            projection_plan.operation_variant,
            projection_plan.transaction_behavior,
            projection_plan.background_required,
        ) == (
            "drawing.projection_group",
            "create_projection_group",
            "background",
            True,
        )

        document = App.newDocument("NativeDrawingMachinedPartGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source = _create_fixture(document)
        source.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        document.saveAs(str(Path(temporary.name) / "checkpoint.FCStd"))

        registry = build_native_capability_registry()
        provider_surface = resolve_native_provider_surface(surface, registry)
        assert provider_surface.available, provider_surface.unavailable_reason
        provider_schema_bytes = len(
            json.dumps(
                provider_surface.schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert provider_schema_bytes <= MAX_NATIVE_SCHEMAS_JSON_BYTES_BY_SURFACE[
            "drawing"
        ]
        provider_schema_sizes = sorted(
            (
                (
                    str(schema.get("name") or ""),
                    len(
                        json.dumps(
                            schema,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                )
                for schema in provider_surface.schemas
            ),
            key=lambda item: (-item[1], item[0]),
        )
        assert {
            "drawing.template_fields",
            "drawing.projection_group",
            "drawing.standard_view",
            "drawing.projected_geometry",
            "drawing.page_readiness",
            DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_linear"],
            DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_view_extent"],
            DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_radial"],
            DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
            DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            NATIVE_BACKGROUND_CAPABILITY_NAME,
        } <= set(provider_surface.tool_names)
        turn = NativeTurnSnapshot.from_provider_surface(provider_surface)
        frozen = turn.surface

        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-machined-part-gui")

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
            background_manager=service.native_background_manager(),
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
            debug_sink=lambda payload: print(payload, file=sys.__stderr__),
        )
        call_index = 0

        def call(tool_name: str, arguments: dict) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                tool_name,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-machined-part-{call_index}",
            )
            assert response.get("ok") is True, response
            return response

        def wait_for_job(job_id: str, timeout: float = 90.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                _events(2)
                snapshot = context.background_manager.snapshot(job_id)
                if snapshot.terminal:
                    result = call(
                        NATIVE_BACKGROUND_CAPABILITY_NAME,
                        {"operation": "status", "job_id": job_id},
                    )["job"]
                    assert result["phase"] == "completed", result
                    return result["result"]
                time.sleep(0.01)
            raise AssertionError(f"Background Drawing job {job_id} did not finish")

        page_result = call(
            "drawing.create_page",
            {"template": "iso_a4_landscape"},
        )
        page = document.getObject(page_result["page"]["object_name"])
        assert page is not None
        assert page_result["template_input"]["source"] == (
            "built_in:iso_a4_landscape"
        )
        assert page_result["page"]["editable_field_count"] >= 1
        template_fields = {
            item["field_name"]: item["value"]
            for item in page_result["page"]["editable_fields"]
        }
        _translated, _factor, expected_length_unit = App.Units.schemaTranslate(
            App.Units.Quantity("1 mm"),
            App.Units.getSchema(),
        )
        assert template_fields["unit_system"] == expected_length_unit
        page.ViewObject.show()
        _events(16)

        raw_state = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection=read_current_selection(document),
        )
        compact_state = provider_visible_native_state(raw_state)
        assert compact_state == compact_drawing_provider_state(raw_state)
        assert "source_target" in compact_state["domain"]["sources"][0]

        page_state = drawing_page_state(page)
        source_state = drawing_source_state(source)
        title_result = call(
            "drawing.template_fields",
            {
                "operation": "fill_template_fields",
                "page": _target(page_state),
                "updates": _title_updates(page_state),
            },
        )
        page_state = title_result["page"]
        started = call(
            "drawing.projection_group",
            _projection_arguments(page_state, source_state),
        )
        projection_result = wait_for_job(started["job"]["job_id"])
        page_state = {
            "object_name": projection_result["page"]["object_name"],
            "state_sha256": projection_result["page"]["state_sha256"],
        }
        child_states = {
            item["orientation"]: {
                **item["view"],
                "x_mm": item["position_on_page_mm"]["x_mm"],
                "y_mm": item["position_on_page_mm"]["y_mm"],
            }
            for item in projection_result["projection_group"]["views"]
        }
        assert set(child_states) == {
            "front",
            "top",
            "right",
            "left",
            "bottom",
            "rear",
        }
        readiness = call(
            "drawing.page_readiness",
            {"page": _target(page_state), "offset": 0},
        )
        assert readiness["units"] == {
            "supported": True,
            "declared": True,
            "field_name": "unit_system",
            "value": expected_length_unit,
        }
        assert readiness["ready"] is True, readiness
        assert readiness["clipping"]["count"] == 0, readiness
        assert readiness["collisions"]["count"] == 0, readiness
        assert readiness["next_offset"] is None, readiness
        projection_bounds_before_dimensions = {
            item["object_name"]: item["bounds_mm"]
            for item in readiness["items"]
            if item["type_id"] == "TechDraw::DrawProjGroupItem"
        }
        projection_group = document.getObject(
            projection_result["projection_group"]["object_name"]
        )
        assert projection_group is not None
        assert projection_result["projection_group"]["placement_target"] == {
            "object_name": str(projection_group.Name)
        }
        assert all(
            item["placement_parent"]
            == {"object_name": str(projection_group.Name)}
            for item in projection_result["projection_group"]["views"]
        )
        assert all(
            item["placement_target"]
            == {"object_name": str(projection_group.Name)}
            for item in projection_result["projection_group"]["views"]
        )
        assert str(page.ProjectionType) == "Third angle"
        assert projection_group.ScaleType == "Custom"
        assert projection_group.AutoDistribute is False
        raw_state = build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection=read_current_selection(document),
        )
        page_snapshot = next(
            item
            for item in raw_state["domain"]["pages"]
            if item["object_name"] == str(page.Name)
        )
        group_snapshot = next(
            item
            for item in page_snapshot["views"]
            if item["object_name"] == str(projection_group.Name)
        )
        assert group_snapshot["projection_group"]["convention"] == "third_angle"
        assert [
            item["orientation"]
            for item in group_snapshot["projection_group"]["views"]
        ] == ["front", "top", "right", "left", "bottom", "rear"]
        compact_group = next(
            item
            for item in compact_drawing_provider_state(raw_state)["domain"]["pages"][
                0
            ]["views"]
            if item["view_name"] == str(projection_group.Name)
        )
        assert all(
            item["view_target"]["object_name"]
            for item in compact_group["projection_group"]["views"]
        )
        page_geometry = drawing_page_state(page)["template_geometry"]
        drawing_bounds = page_geometry["drawing_bounds_mm"]
        assert drawing_bounds == {
            "min_x_mm": 27.0,
            "min_y_mm": 65.0,
            "max_x_mm": 280.0,
            "max_y_mm": 193.0,
        }
        scene_positions = _scene_positions_mm(page)
        group_name = projection_result["projection_group"]["object_name"]
        assert group_name in scene_positions, scene_positions
        group_scene_position = scene_positions[group_name]
        for item in projection_result["projection_group"]["views"]:
            child = document.getObject(item["view"]["object_name"])
            assert child is not None
            bounds = child.getPrecomputedProjection()["edges"].BoundBox
            position = item["position_on_page_mm"]
            minimum_x = float(position["x_mm"]) - float(bounds.XLength) / 2.0
            maximum_x = float(position["x_mm"]) + float(bounds.XLength) / 2.0
            minimum_y = float(position["y_mm"]) - float(bounds.YLength) / 2.0
            maximum_y = float(position["y_mm"]) + float(bounds.YLength) / 2.0
            assert (
                drawing_bounds["min_x_mm"] - 1.0e-7
                <= minimum_x
                <= maximum_x
                <= drawing_bounds["max_x_mm"] + 1.0e-7
            )
            assert (
                drawing_bounds["min_y_mm"] - 1.0e-7
                <= minimum_y
                <= maximum_y
                <= drawing_bounds["max_y_mm"] + 1.0e-7
            )
            assert str(child.Name) in scene_positions, scene_positions
            scene_position = scene_positions[str(child.Name)]
            assert abs(scene_position[0] - float(position["x_mm"])) < 1.0e-7, (
                child.Name,
                scene_position,
                position,
                group_scene_position,
            )
            assert abs(scene_position[1] - float(position["y_mm"])) < 1.0e-7, (
                child.Name,
                scene_position,
                position,
            )
        projection_revision = projection_result["receipt"]["revision_after"]
        build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection=read_current_selection(document),
        )
        _events(20)
        assert state_store.current_revision(str(document.Uid)) == (
            projection_revision
        )

        def inspect(view_state: dict) -> dict:
            first = call(
                "drawing.projected_geometry",
                {
                    "view": _projected_read_target(view_state),
                    "offset": 0,
                },
            )
            elements = list(first["elements"])
            next_offset = first["next_offset"]
            while next_offset is not None:
                continued = call(
                    "drawing.projected_geometry",
                    {
                        "view": _projected_read_target(
                            view_state,
                            first["view"]["projection_state_sha256"],
                        ),
                        "offset": next_offset,
                    },
                )
                elements.extend(continued["elements"])
                next_offset = continued["next_offset"]
            assert len(elements) == first["counts"]["total"]
            return {**first, "elements": elements}

        front_geometry = inspect(child_states["front"])
        top_geometry = inspect(child_states["top"])
        right_geometry = inspect(child_states["right"])
        for geometry in (front_geometry, top_geometry, right_geometry):
            bounds = geometry["view_bounds_in_view_mm"]
            assert bounds["min_x_mm"] < bounds["max_x_mm"]
            assert bounds["min_y_mm"] < bounds["max_y_mm"]
        assert all(
            item["name"].startswith(("Edge", "Vertex", "Face"))
            for item in front_geometry["elements"] + top_geometry["elements"]
        )

        linear_arguments = _dimension_arguments(
            label="Base Thickness 12 mm",
            page_state=page_state,
            view_state=child_states["right"],
            projection_hash=right_geometry["view"]["projection_state_sha256"],
            position=(42.0, 0.0),
        )
        linear_arguments.update(
            {
                "direction": "vertical",
                "references": [{"subelement": "Edge0"}],
            }
        )
        call_index += 1
        queued_result: dict[str, object] = {}

        def queued_linear_call_and_refresh() -> None:
            try:
                response = VibeGui._dispatch_to_document_thread(
                    lambda: dispatcher.call(
                        DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_linear"],
                        json.dumps(linear_arguments, separators=(",", ":")),
                        f"native-drawing-machined-part-{call_index}",
                    )
                )
                queued_result["response"] = response
                queued_result["context"] = _build_context_for_provider(
                    service,
                    None,
                    VibeGui._dispatch_to_document_thread,
                )
            except BaseException as exc:
                queued_result["error"] = exc

        queued_worker = threading.Thread(target=queued_linear_call_and_refresh)
        queued_worker.start()
        deadline = time.monotonic() + 90.0
        while queued_worker.is_alive() and time.monotonic() < deadline:
            _events(2)
            time.sleep(0.01)
        queued_worker.join(timeout=0.1)
        assert not queued_worker.is_alive()
        assert "error" not in queued_result, queued_result
        linear_result = queued_result["response"]
        assert isinstance(linear_result, dict) and linear_result.get("ok") is True
        refreshed_context = queued_result["context"]
        assert isinstance(refreshed_context, dict)
        assert refreshed_context["native_state"]["surface_id"] == "drawing"
        provider_context_bytes = len(
            json.dumps(
                refreshed_context,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        provider_context_sizes = sorted(
            (
                (
                    str(name),
                    len(
                        json.dumps(
                            value,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                )
                for name, value in refreshed_context.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )
        page_state = linear_result["page"]
        created_dimensions = [linear_result["dimension"]["object_name"]]
        revision_before_snapshot = state_store.current_revision(str(document.Uid))
        _events(20)
        assert state_store.current_revision(str(document.Uid)) == (
            revision_before_snapshot
        )

        dimension_specs = (
            (
                "horizontal",
                "Overall Width 80 mm",
                "front",
                front_geometry,
                (0.0, -18.0),
            ),
            (
                "vertical",
                "Overall Height 20 mm",
                "front",
                front_geometry,
                (-50.0, 0.0),
            ),
            (
                "horizontal",
                "Right View Depth 50 mm",
                "right",
                right_geometry,
                (0.0, -32.0),
            ),
        )
        for direction, label, view_name, geometry, position in dimension_specs:
            arguments = _dimension_arguments(
                label=label,
                page_state=page_state,
                view_state=child_states[view_name],
                projection_hash=geometry["view"]["projection_state_sha256"],
                position=position,
            )
            arguments["direction"] = direction
            result = call(
                DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_view_extent"],
                arguments,
            )
            page_state = result["page"]
            created_dimensions.append(result["dimension"]["object_name"])
            revision_before_snapshot = state_store.current_revision(str(document.Uid))
            build_active_snapshot(
                document,
                "drawing",
                state_store.snapshot(str(document.Uid)),
                selection=read_current_selection(document),
            )
            _events(20)
            assert state_store.current_revision(str(document.Uid)) == (
                revision_before_snapshot
            )
            source_view = document.getObject(
                child_states[view_name]["object_name"]
            )
            assert drawing_view_state(source_view)["state_sha256"] == (
                child_states[view_name]["state_sha256"]
            )
            assert drawing_projected_geometry_state(source_view)[
                "projection_state_sha256"
            ] == geometry["view"]["projection_state_sha256"]

        hole_edges = [
            item
            for item in top_geometry["elements"]
            if item["kind"] == "circle"
            and item.get("visible") is True
            and abs(float(item.get("radius_mm", 0.0)) - 3.0) < 1.0e-7
        ]
        assert len(hole_edges) == 4
        diameter_arguments = _dimension_arguments(
            label="Four Mounting Holes Diameter 6 mm",
            page_state=page_state,
            view_state=child_states["top"],
            projection_hash=top_geometry["view"]["projection_state_sha256"],
            position=(-55.0, 0.0),
        )
        diameter_arguments["edge"] = _element_target(hole_edges[0])
        diameter_arguments["allow_approximate"] = False
        diameter_arguments["kind"] = "diameter"
        diameter_result = call(
            DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_radial"],
            diameter_arguments,
        )
        page_state = diameter_result["page"]
        created_dimensions.append(diameter_result["dimension"]["object_name"])

        redraw_started = call(
            "drawing.redraw_page",
            {"page": _target(page_state)},
        )
        redraw_result = wait_for_job(redraw_started["job"]["job_id"])
        page_state = redraw_result["page"]
        post_redraw_arguments = _dimension_arguments(
            label="Second Mounting Hole Diameter 6 mm",
            page_state=page_state,
            view_state=child_states["top"],
            projection_hash=top_geometry["view"]["projection_state_sha256"],
            position=(-56.0, -51.5),
        )
        post_redraw_arguments["edge"] = _element_target(hole_edges[1])
        post_redraw_arguments["allow_approximate"] = False
        post_redraw_arguments["kind"] = "diameter"
        post_redraw_result = call(
            DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_radial"],
            post_redraw_arguments,
        )
        page_state = post_redraw_result["page"]
        created_dimensions.append(
            post_redraw_result["dimension"]["object_name"]
        )

        centerline_result = call(
            DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
            {
                "page": _target(page_state),
                "view": _view_target(
                    child_states["top"],
                    top_geometry["view"]["projection_state_sha256"],
                ),
                "circles": [_element_target(item) for item in hole_edges],
            },
        )
        assert centerline_result["circle_center_lines"]["pair_count"] == 4
        centerline_revision = centerline_result["receipt"]["revision_after"]
        build_active_snapshot(
            document,
            "drawing",
            state_store.snapshot(str(document.Uid)),
            selection=read_current_selection(document),
        )
        _events(20)
        assert state_store.current_revision(str(document.Uid)) == (
            centerline_revision
        )

        # A coordinate series must preserve the exact projected geometry of a
        # projection-group child after ordinary dimensions and center marks
        # have already been attached to the page.
        top_view = document.getObject(child_states["top"]["object_name"])
        assert top_view is not None
        top_projection = drawing_projected_geometry_state(top_view)
        vertices_by_x = {}
        for item in top_projection["elements"]:
            if item["element_type"] != "vertex":
                continue
            x_mm = round(float(item["point_in_view_mm"]["x_mm"]), 9)
            vertices_by_x.setdefault(x_mm, item["name"])
        assert len(vertices_by_x) >= 4, vertices_by_x
        series_vertex_names = tuple(
            vertices_by_x[x_mm] for x_mm in sorted(vertices_by_x)[:4]
        )
        series_result = call(
            DRAWING_DIMENSION_SERIES_CAPABILITY_NAME,
            {
                "operation": "create_horizontal_coordinate",
                "label": "Hole Locations X",
                "page": _target(drawing_page_state(page)),
                "view": _view_target(
                    drawing_view_state(top_view),
                    top_projection["projection_state_sha256"],
                ),
                "vertices": [
                    {"subelement": name} for name in series_vertex_names
                ],
            },
        )
        assert series_result["series"]["dimension_count"] == 3
        created_dimensions.extend(
            item["object_name"] for item in series_result["dimensions"]
        )

        _events(20)
        dimensioned_readiness = call(
            "drawing.page_readiness",
            {"page": {"object_name": str(page.Name)}},
        )
        projection_bounds_after_dimensions = {
            item["object_name"]: item["bounds_mm"]
            for item in dimensioned_readiness["items"]
            if item["type_id"] == "TechDraw::DrawProjGroupItem"
        }
        assert projection_bounds_after_dimensions == projection_bounds_before_dimensions
        collision_pairs = {
            frozenset(
                (pair["first_object_name"], pair["second_object_name"])
            )
            for pair in dimensioned_readiness["collisions"]["pairs"]
        }
        assert frozenset(
            (created_dimensions[0], created_dimensions[3])
        ) not in collision_pairs, dimensioned_readiness
        assert dimensioned_readiness["collisions"]["count"] >= 1, (
            dimensioned_readiness
        )

        mixed_page_result = call(
            "drawing.create_page",
            {"template": "iso_a4_landscape"},
        )
        mixed_page = document.getObject(mixed_page_result["page"]["object_name"])
        assert mixed_page is not None
        mixed_group_arguments = _projection_arguments(
            mixed_page_result["page"],
            source_state,
        )
        mixed_group_arguments["views"] = ["front", "top", "right"]
        mixed_group_start = call(
            "drawing.projection_group",
            mixed_group_arguments,
        )
        mixed_group_result = wait_for_job(mixed_group_start["job"]["job_id"])
        mixed_standard_start = call(
            "drawing.standard_view",
            {
                "label": "Machined Mounting Plate Isometric",
                "page": _target(mixed_group_result["page"]),
                "sources": [_target(source_state)],
                "orientation": "isometric",
                "position": {"x_mm": 245.0, "y_mm": 150.0},
                "line_style": "visible",
            },
        )
        mixed_standard_result = wait_for_job(
            mixed_standard_start["job"]["job_id"]
        )
        mixed_standard = document.getObject(
            mixed_standard_result["view"]["object_name"]
        )
        assert mixed_standard is not None
        assert tuple(mixed_page.Views)[-1] is mixed_standard

        # A dimension label crossing geometry in the view it dimensions is a
        # real drafting collision even though the dimension is a dependent of
        # that view.  The exact rendered scene must report it.
        obstructed_name = created_dimensions[1]
        obstructed = document.getObject(obstructed_name)
        front_view = document.getObject(child_states["front"]["object_name"])
        assert obstructed is not None and front_view is not None
        original_label_position = (float(obstructed.X), float(obstructed.Y))
        front_edge_y = [
            point["y_mm"]
            for item in front_geometry["elements"]
            if item.get("kind") not in {"point", "face"} and item["visible"]
            for point in (item["start_in_view_mm"], item["end_in_view_mm"])
        ]
        assert front_edge_y
        obstructed.X = 0.0
        obstructed.Y = max(front_edge_y)
        assert document.recompute([obstructed, page], True, True) is not False
        _events(12)
        obstructed_readiness = drawing_page_readiness(
            document,
            target={
                "object_name": str(page.Name),
                "expected_state_sha256": drawing_page_state(page)["state_sha256"],
            },
        )
        obstructed_pairs = {
            frozenset((pair["first_object_name"], pair["second_object_name"]))
            for pair in obstructed_readiness["collisions"]["pairs"]
        }
        assert frozenset((obstructed_name, str(front_view.Name))) in obstructed_pairs, (
            obstructed_readiness
        )
        assert "item_collisions" in obstructed_readiness["issues"]
        obstructed_item = next(
            item
            for item in obstructed_readiness["items"]
            if item["object_name"] == obstructed_name
        )
        label_bounds = obstructed_item["label_bounds_mm"]
        item_bounds = obstructed_item["bounds_mm"]
        assert label_bounds["min_x_mm"] < label_bounds["max_x_mm"]
        assert label_bounds["min_y_mm"] < label_bounds["max_y_mm"]
        assert item_bounds["min_x_mm"] <= label_bounds["min_x_mm"]
        assert item_bounds["min_y_mm"] <= label_bounds["min_y_mm"]
        assert item_bounds["max_x_mm"] >= label_bounds["max_x_mm"]
        assert item_bounds["max_y_mm"] >= label_bounds["max_y_mm"]
        obstructed_pair = next(
            pair
            for pair in obstructed_readiness["collisions"]["pairs"]
            if frozenset((pair["first_object_name"], pair["second_object_name"]))
            == frozenset((obstructed_name, str(front_view.Name)))
        )
        overlap_bounds = obstructed_pair["overlap_bounds_mm"]
        assert overlap_bounds["min_x_mm"] < overlap_bounds["max_x_mm"]
        assert overlap_bounds["min_y_mm"] < overlap_bounds["max_y_mm"]
        assert obstructed_item["label_position_in_view_mm"] == {
            "x_mm": 0.0,
            "y_mm": round(max(front_edge_y), 9),
        }
        assert obstructed_item["view_origin_on_page_mm"] == (
            drawing_view_position_on_page(front_view)
        )
        assert obstructed_item["label_position_on_page_mm"] == {
            "x_mm": obstructed_item["view_origin_on_page_mm"]["x_mm"],
            "y_mm": round(
                obstructed_item["view_origin_on_page_mm"]["y_mm"]
                + max(front_edge_y),
                9,
            ),
        }
        obstructed.X, obstructed.Y = original_label_position
        assert document.recompute([obstructed, page], True, True) is not False
        _events(12)

        template = page.Template
        asserted_fields = dict(template.EditableTexts)
        asserted_fields["unit_system"] = ""
        template.EditableTexts = asserted_fields
        assert document.recompute([template, page], True, True) is not False
        _events(12)
        missing_units = drawing_page_readiness(
            document,
            target={
                "object_name": str(page.Name),
                "expected_state_sha256": drawing_page_state(page)["state_sha256"],
            },
        )
        assert missing_units["units"] == {
            "supported": True,
            "declared": False,
            "field_name": "unit_system",
            "value": "",
        }
        assert "unit_system_missing" in missing_units["issues"]
        asserted_fields["unit_system"] = expected_length_unit
        template.EditableTexts = asserted_fields
        assert document.recompute([template, page], True, True) is not False
        _events(12)

        group = document.getObject(
            projection_result["projection_group"]["object_name"]
        )
        assert group is not None and str(group.TypeId) == "TechDraw::DrawProjGroup"
        children = tuple(group.Views)
        assert tuple(str(child.Type) for child in children) == (
            "Front",
            "Top",
            "Right",
            "Left",
            "Bottom",
            "Rear",
        )
        assert all(
            drawing_view_state(child)["visible_edge_count"] >= 1
            for child in children
        )
        cached_projection_states = {
            str(child.Name): str(child.PrecomputedProjectionSourceState)
            for child in children
        }
        assert all(cached_projection_states.values())
        assert all(document.getObject(name) is not None for name in created_dimensions)
        assert not Gui.Control.activeDialog()

        output = _output_path(temporary)
        output.parent.mkdir(parents=True, exist_ok=True)
        document.saveAs(str(output))
        names = {
            "source": str(source.Name),
            "page": str(page.Name),
            "group": str(group.Name),
            "children": tuple(str(child.Name) for child in children),
            "dimensions": tuple(created_dimensions),
        }
        App.closeDocument(document.Name)
        restore_probe = _RestoreRecomputeProbe()
        App.addDocumentObserver(restore_probe)
        try:
            document = App.openDocument(str(output))
        finally:
            App.removeDocumentObserver(restore_probe)
        _events(20)
        reopened_page = document.getObject(names["page"])
        reopened_group = document.getObject(names["group"])
        assert reopened_page is not None and reopened_group is not None
        assert tuple(reopened_page.Views)[0] is reopened_group
        assert tuple(str(child.Name) for child in reopened_group.Views) == names["children"]
        reopened_projection_states = {
            str(child.Name): str(child.PrecomputedProjectionSourceState)
            for child in reopened_group.Views
        }
        assert reopened_projection_states == cached_projection_states, (
            cached_projection_states,
            reopened_projection_states,
        )
        assert all(document.getObject(name) is not None for name in names["dimensions"])
        assert document.getObject(names["source"]) is not None
        assert not set(restore_probe.object_names).intersection(
            {names["group"], *names["children"], *names["dimensions"]}
        ), restore_probe.object_names

        legacy_dimension_name = names["dimensions"][0]
        legacy_dimension = document.getObject(legacy_dimension_name)
        assert legacy_dimension is not None
        legacy_dimension.PrecomputedDimensionVectors = []
        document.save()
        App.closeDocument(document.Name)
        legacy_restore_probe = _RestoreRecomputeProbe()
        App.addDocumentObserver(legacy_restore_probe)
        try:
            document = App.openDocument(str(output))
        finally:
            App.removeDocumentObserver(legacy_restore_probe)
        assert not set(legacy_restore_probe.object_names).intersection(
            {names["group"], *names["children"], *names["dimensions"]}
        ), legacy_restore_probe.object_names
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            legacy_dimension = document.getObject(legacy_dimension_name)
            if legacy_dimension is not None and list(
                legacy_dimension.PrecomputedDimensionVectors
            ):
                break
            _events(2)
            time.sleep(0.01)
        assert legacy_dimension is not None
        assert list(legacy_dimension.PrecomputedDimensionVectors)
        if os.environ.get("VIBECAD_DRAWING_SURFACE_DETAILS") == "1":
            print(
                "VIBECAD_NATIVE_DRAWING_SURFACE_DETAILS "
                + json.dumps(
                    {
                        "tool_names": list(provider_surface.tool_names),
                        "schema_sizes": provider_schema_sizes,
                        "context_sizes": provider_context_sizes,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
        print(
            "VIBECAD_NATIVE_DRAWING_MACHINED_PART_GUI_OK "
            "human_projection=true full_provider_surface=true compact_context=true "
            "frozen_turn=true title_block=true detached_batch=true orthographic=all_six "
            "projected_inspection=true semantic_hashes=true dimensions=8 "
            "center_marks=4 history=true save=true reopen=true "
            "legacy_cache_upgrade=true nonblocking_restore=true "
            "mixed_projection_group_standard_view=true "
            f"tools={len(provider_surface.tool_names)} "
            f"schema_bytes={provider_schema_bytes} "
            f"context_bytes={provider_context_bytes} "
            "largest_schemas="
            + ",".join(f"{name}:{size}" for name, size in provider_schema_sizes[:8])
            + " largest_context="
            + ",".join(f"{name}:{size}" for name, size in provider_context_sizes[:8])
            + f" output={output}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
