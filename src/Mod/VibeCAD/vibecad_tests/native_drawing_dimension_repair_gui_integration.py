# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing dimension repair."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import TechDrawGui
import VibeCADGui as VibeGui
import VibeCADNativeDrawingDimensionRepairRuntime as RepairRuntimeModule
from TechDrawTools.AxoLengthDimension import create_axonometric_length
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionRepairSchema import (
    DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
    DRAWING_DIMENSION_REPAIR_OPERATIONS,
)
from VibeCADNativeDrawingDimensionState import (
    drawing_dimension_repair_state,
)
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationError
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


_KINDS = (
    "length",
    "horizontal",
    "vertical",
    "radius",
    "diameter",
    "angle",
    "three_point_angle",
    "area",
    "horizontal_extent",
    "vertical_extent",
    "horizontal_chamfer",
    "vertical_chamfer",
    "arc_length",
    "axonometric_length",
)


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
    assert surface.surface_id == "drawing"
    return controller, surface


def _create_fixture(document):
    document.openTransaction("Create Drawing dimension-repair fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        chamfer_points = (
            App.Vector(-22.0, -12.0, 0.0),
            App.Vector(15.0, -12.0, 0.0),
            App.Vector(22.0, -5.0, 0.0),
            App.Vector(22.0, 12.0, 0.0),
            App.Vector(-22.0, 12.0, 0.0),
            App.Vector(-22.0, -12.0, 0.0),
        )
        chamfer_face = Part.Face(Part.makePolygon(chamfer_points))
        shapes = [
            chamfer_face.extrude(App.Vector(0.0, 0.0, 8.0)),
            Part.makeBox(
                24.0,
                18.0,
                8.0,
                App.Vector(-62.0, -9.0, 0.0),
            ),
            Part.makeCylinder(
                8.0,
                8.0,
                App.Vector(40.0, 0.0, 0.0),
                App.Vector(0.0, 0.0, 1.0),
            ),
            Part.makeCylinder(
                5.0,
                8.0,
                App.Vector(64.0, 0.0, 0.0),
                App.Vector(0.0, 0.0, 1.0),
            ),
            Part.Edge(
                Part.Arc(
                    App.Vector(78.0, -9.0, 0.0),
                    App.Vector(87.0, 0.0, 0.0),
                    App.Vector(78.0, 9.0, 0.0),
                )
            ),
            Part.Edge(
                Part.Arc(
                    App.Vector(99.0, -6.0, 0.0),
                    App.Vector(105.0, 0.0, 0.0),
                    App.Vector(99.0, 6.0, 0.0),
                )
            ),
        ]
        source = document.addObject("Part::Feature", "RepairSource")
        source.Label = "Repair Source"
        source.Shape = Part.makeCompound(shapes)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "RepairPage")
        page.Label = "Repair Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "RepairTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "RepairView")
        view.Label = "Repair View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
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


def _line_direction(edge: dict) -> tuple[float, float]:
    first = edge["start_in_view_mm"]
    second = edge["end_in_view_mm"]
    return second["x_mm"] - first["x_mm"], second["y_mm"] - first["y_mm"]


def _geometry(view) -> dict[str, object]:
    projection = drawing_projected_geometry_state(view)
    edges = [
        item
        for item in projection["elements"]
        if item["element_type"] == "edge" and item["visible"]
    ]
    horizontal = [
        edge
        for edge in edges
        if not edge["closed"]
        and "Circle" not in edge["geometry_type"]
        and "Ellipse" not in edge["geometry_type"]
        and abs(_line_direction(edge)[1]) <= 1.0e-7
        and abs(_line_direction(edge)[0]) > 2.0
    ]
    vertical = [
        edge
        for edge in edges
        if not edge["closed"]
        and "Circle" not in edge["geometry_type"]
        and "Ellipse" not in edge["geometry_type"]
        and abs(_line_direction(edge)[0]) <= 1.0e-7
        and abs(_line_direction(edge)[1]) > 2.0
    ]
    circles = [
        edge
        for edge in edges
        if edge["closed"]
        and "Circle" in edge["geometry_type"]
        and "radius_view_mm" in edge
    ]
    arcs = [
        edge
        for edge in edges
        if not edge["closed"]
        and "Circle" in edge["geometry_type"]
        and "radius_view_mm" in edge
    ]
    faces = [
        item
        for item in projection["elements"]
        if item["element_type"] == "face"
    ]
    vertices = [
        item
        for item in projection["elements"]
        if item["element_type"] == "vertex" and item["visible"]
    ]
    assert min(len(horizontal), len(vertical), len(circles), len(arcs), len(faces)) >= 2
    assert len(vertices) >= 8
    assert horizontal[0]["axonometric_value_mode"] == "x_axis_true_length"
    assert vertical[0]["axonometric_value_mode"] == "y_axis_true_length"
    return {
        "projection": projection,
        "horizontal": horizontal,
        "vertical": vertical,
        "circles": circles,
        "arcs": arcs,
        "faces": faces,
        "vertices": vertices,
    }


def _target(element: dict) -> dict[str, str]:
    return {
        "subelement": element["name"],
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _three_points(vertices: list[dict], offset: int) -> tuple[dict, dict, dict]:
    count = len(vertices)
    for apex_index in range(count):
        apex = vertices[(apex_index + offset) % count]
        origin = apex["point_in_view_mm"]
        for first_index in range(count):
            first = vertices[(first_index + offset) % count]
            if first is apex:
                continue
            first_point = first["point_in_view_mm"]
            first_vector = (
                first_point["x_mm"] - origin["x_mm"],
                first_point["y_mm"] - origin["y_mm"],
            )
            for second_index in range(count):
                second = vertices[(second_index + offset + 1) % count]
                if second is apex or second is first:
                    continue
                second_point = second["point_in_view_mm"]
                second_vector = (
                    second_point["x_mm"] - origin["x_mm"],
                    second_point["y_mm"] - origin["y_mm"],
                )
                cross = abs(
                    first_vector[0] * second_vector[1]
                    - first_vector[1] * second_vector[0]
                )
                if cross > 1.0:
                    return first, apex, second
    raise AssertionError("No three-point angle configuration")


def _vertex_pair(vertices: list[dict], offset: int) -> tuple[dict, dict]:
    count = len(vertices)
    for first_index in range(count):
        first = vertices[(first_index + offset) % count]
        first_point = first["point_in_view_mm"]
        for second_index in range(first_index + 1, count):
            second = vertices[(second_index + offset) % count]
            second_point = second["point_in_view_mm"]
            if (
                abs(first_point["x_mm"] - second_point["x_mm"]) > 1.0
                and abs(first_point["y_mm"] - second_point["y_mm"]) > 1.0
            ):
                return first, second
    raise AssertionError("No diagonal vertex pair")


def _create_dimensions(document, page, view, geometry) -> dict[str, object]:
    h = geometry["horizontal"]
    v = geometry["vertical"]
    c = geometry["circles"]
    a = geometry["arcs"]
    f = geometry["faces"]
    vertices = geometry["vertices"]
    first_angle = _three_points(vertices, 0)
    first_chamfer = _vertex_pair(vertices, 0)
    second_chamfer = _vertex_pair(vertices, 3)
    document.openTransaction("Create exact repair targets")
    transaction = int(document.getBookedTransactionID())
    result = {}
    try:
        standard = {
            "length": ("Distance", [h[0]["name"]]),
            "horizontal": ("DistanceX", [h[0]["name"]]),
            "vertical": ("DistanceY", [v[0]["name"]]),
            "radius": ("Radius", [c[0]["name"]]),
            "diameter": ("Diameter", [c[1]["name"]]),
            "angle": ("Angle", [h[0]["name"], v[0]["name"]]),
            "three_point_angle": (
                "Angle3Pt",
                [item["name"] for item in first_angle],
            ),
            "area": ("Area", [f[0]["name"]]),
        }
        for index, (kind, (dimension_type, names)) in enumerate(standard.items()):
            dimension = TechDrawGui.createProjectedDimension(
                view,
                dimension_type,
                names,
                False,
                -45.0 + index * 9.0,
                32.0 - index * 3.0,
            )
            dimension.Label = f"Repair {kind}"
            document.publishProvisionalTimelineOperationBlock(dimension, (), ())
            result[kind] = dimension
        horizontal_extent = TechDrawGui.createProjectedExtent(
            view, "DistanceX", [], 18.0, 30.0
        )
        vertical_extent = TechDrawGui.createProjectedExtent(
            view, "DistanceY", [v[0]["name"]], 28.0, 22.0
        )
        result["horizontal_extent"] = horizontal_extent
        result["vertical_extent"] = vertical_extent
        horizontal_chamfer = TechDrawGui.createProjectedChamfer(
            view,
            "DistanceX",
            [item["name"] for item in first_chamfer],
            30.0,
            12.0,
        )
        vertical_chamfer = TechDrawGui.createProjectedChamfer(
            view,
            "DistanceY",
            [item["name"] for item in second_chamfer],
            36.0,
            4.0,
        )
        result["horizontal_chamfer"] = horizontal_chamfer
        result["vertical_chamfer"] = vertical_chamfer
        result["arc_length"] = TechDrawGui.createProjectedArcLength(
            view, a[0]["name"], 44.0, 18.0
        )
        axonometric = create_axonometric_length(
            view,
            [h[0]["name"]],
            h[0]["name"],
            v[0]["name"],
            label_position_in_view_mm=(6.0, -22.0),
        ).dimension
        result["axonometric_length"] = axonometric
        for kind, dimension in result.items():
            dimension.Label = f"Repair {kind}"
            if dimension not in tuple(document.VibeCADTimeline.Operations):
                document.publishProvisionalTimelineOperationBlock(dimension, (), ())
        assert document.recompute([*result.values(), view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return result


def _replacement(kind: str, geometry) -> dict:
    h = geometry["horizontal"]
    v = geometry["vertical"]
    c = geometry["circles"]
    a = geometry["arcs"]
    f = geometry["faces"]
    vertices = geometry["vertices"]
    if kind in {"length", "horizontal"}:
        return {"kind": kind, "references": [_target(h[1])]}
    if kind == "vertical":
        return {"kind": kind, "references": [_target(v[1])]}
    if kind in {"radius", "diameter"}:
        index = 1 if kind == "radius" else 0
        return {
            "kind": kind,
            "edge": _target(c[index]),
            "allow_approximate": False,
        }
    if kind == "angle":
        return {
            "kind": kind,
            "first_edge": _target(h[1]),
            "second_edge": _target(v[1]),
        }
    if kind == "three_point_angle":
        first, apex, second = _three_points(vertices, 4)
        return {
            "kind": kind,
            "first_arm_point": _target(first),
            "apex_point": _target(apex),
            "second_arm_point": _target(second),
        }
    if kind == "area":
        return {"kind": kind, "face": _target(f[1])}
    if kind == "horizontal_extent":
        return {
            "kind": kind,
            "extent": {"scope": "edges", "edges": [_target(h[1])]},
        }
    if kind == "vertical_extent":
        return {"kind": kind, "extent": {"scope": "whole_view"}}
    if kind in {"horizontal_chamfer", "vertical_chamfer"}:
        first, second = _vertex_pair(vertices, 7)
        return {
            "kind": kind,
            "first_vertex": _target(first),
            "second_vertex": _target(second),
        }
    if kind == "arc_length":
        return {"kind": kind, "arc_edge": _target(a[1])}
    if kind == "axonometric_length":
        return {
            "kind": kind,
            "measurement": {"kind": "edge", "dimension_edge": _target(h[1])},
            "extension_direction_edge": _target(v[1]),
            "expected_value_mode": "x_axis_true_length",
        }
    raise AssertionError(kind)


def _arguments(dimension, page, view, replacement: dict) -> dict:
    dimension_state = drawing_dimension_repair_state(dimension)
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": "repair_references",
        "dimension": {
            "object_name": dimension_state["object_name"],
            "expected_repair_state_sha256": dimension_state[
                "repair_state_sha256"
            ],
        },
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
        "replacement": replacement,
    }


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_DIMENSION_REPAIR_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 24 * 1024
    outer = schema["parameters"]["oneOf"]
    assert len(outer) == 1
    replacement = outer[0]["properties"]["replacement"]
    branches = replacement["oneOf"]
    assert tuple(branch["properties"]["kind"]["const"] for branch in branches) == _KINDS
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _human_oracle(document, dimension, view, replacement_edge: dict) -> None:
    before = drawing_dimension_repair_state(dimension)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(dimension)
    Gui.runCommand("TechDraw_DimensionRepair")
    _events(12)
    dialog = Gui.Control.activeTaskDialog()
    assert dialog is not None
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view, replacement_edge["name"])
    buttons = [
        button
        for button in Gui.getMainWindow().findChildren(QtWidgets.QPushButton)
        if "Replace References" in button.text()
    ]
    assert len(buttons) == 1
    buttons[0].click()
    _events(8)
    dialog.accept()
    _events(12)
    assert not Gui.Control.activeDialog()
    after = drawing_dimension_repair_state(dimension)
    assert after["repair_state_sha256"] != before["repair_state_sha256"]
    assert after["valid"]
    document.undo()
    _events(12)
    assert (
        drawing_dimension_repair_state(dimension)["repair_state_sha256"]
        == before["repair_state_sha256"]
    )
    document.redo()
    _events(12)
    assert (
        drawing_dimension_repair_state(dimension)["repair_state_sha256"]
        == after["repair_state_sha256"]
    )
    Gui.Selection.clearSelection()


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    reopened = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-dimension-repair-"
        )
        save_path = Path(temporary.name) / "drawing-dimension-repair.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_DimensionRepair"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
            "repair_references",
            "ExactDrawingDimensionAndReplacementReferences",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingDimensionRepairGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        geometry = _geometry(view)

        document.openTransaction("Create human repair oracle")
        transaction = int(document.getBookedTransactionID())
        try:
            human = TechDrawGui.createProjectedDimension(
                view,
                "Distance",
                [geometry["horizontal"][0]["name"]],
                False,
                -40.0,
                -30.0,
            )
            document.publishProvisionalTimelineOperationBlock(human, (), ())
            assert document.recompute([human, page], True, True) is not False
        except Exception:
            App.closeActiveTransaction(True, transaction)
            raise
        App.closeActiveTransaction(False, transaction)
        _human_oracle(document, human, view, geometry["horizontal"][1])

        dimensions = _create_dimensions(document, page, view, geometry)
        # Simulate a restored document whose exact dimension reference no longer exists.
        broken = dimensions["length"]
        broken.References2D = [(view, ["Edge999999"])]
        assert document.recompute([broken, page], True, True) is not False
        broken_state = drawing_dimension_repair_state(broken)
        assert not broken_state["valid"] and broken_state["repairable"], broken_state
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(broken)
        snapshot = build_drawing_snapshot(
            document,
            selection={
                "items": [
                    {
                        "object": {
                            "object_name": broken.Name,
                        }
                    }
                ]
            },
        )
        selected = snapshot["selected_dimensions"]
        assert selected[0]["repair_target"]["object_name"] == broken.Name
        assert selected[0]["repair_target"]["repair_kind"] == "length"

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-dimension-repair-gui")

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

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_DIMENSION_REPAIR_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-dimension-repair-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        source.ViewObject.Visibility = True
        view.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        selection_before = tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        revision_before = state_store.current_revision(str(document.Uid))
        final_hashes = {}
        first_before = broken_state["repair_state_sha256"]
        for kind in _KINDS:
            dimension = dimensions[kind]
            response = call(
                _arguments(dimension, page, view, _replacement(kind, geometry))
            )
            assert response["operation"] == "repair_references"
            assert response["repair_kind"] == kind
            assert response["dimension"]["valid"]
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 8 * 1024
            final = drawing_dimension_repair_state(dimension)
            assert final["repair_kind"] == kind and final["valid"]
            final_hashes[dimension.Name] = final["repair_state_sha256"]
        assert final_hashes[broken.Name] != first_before
        assert state_store.current_revision(str(document.Uid)) == (
            revision_before + len(_KINDS)
        )
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        assert selection_before == tuple(
            (item.Object.Name, tuple(item.SubElementNames))
            for item in Gui.Selection.getSelectionEx()
        )
        assert not Gui.Control.activeDialog()

        last = dimensions["axonometric_length"]
        last_hash = final_hashes[last.Name]
        document.undo()
        _events(12)
        assert drawing_dimension_repair_state(last)["repair_state_sha256"] != last_hash
        document.redo()
        _events(12)
        assert drawing_dimension_repair_state(last)["repair_state_sha256"] == last_hash

        no_change = call(
            _arguments(
                dimensions["length"],
                page,
                view,
                _replacement("length", geometry),
            ),
            succeeds=False,
        )
        assert no_change["error_code"] == "NATIVE_DRAWING_NO_CHANGE"

        stale_arguments = _arguments(
            dimensions["horizontal"],
            page,
            view,
            {
                "kind": "horizontal",
                "references": [_target(geometry["horizontal"][0])],
            },
        )
        old_label = str(dimensions["horizontal"].Label)
        dimensions["horizontal"].Label = old_label + " stale"
        stale = call(stale_arguments, succeeds=False)
        assert stale["error_code"] == "NATIVE_DRAWING_DIMENSION_REPAIR_STALE"
        dimensions["horizontal"].Label = old_label

        document.openTransaction("Create repair rollback oracle")
        transaction = int(document.getBookedTransactionID())
        try:
            rollback = TechDrawGui.createProjectedDimension(
                view,
                "Distance",
                [geometry["horizontal"][0]["name"]],
                False,
                20.0,
                -30.0,
            )
            document.publishProvisionalTimelineOperationBlock(rollback, (), ())
            assert document.recompute([rollback, page], True, True) is not False
        except Exception:
            App.closeActiveTransaction(True, transaction)
            raise
        App.closeActiveTransaction(False, transaction)
        rollback_before = drawing_dimension_repair_state(rollback)
        rollback_arguments = _arguments(
            rollback,
            page,
            view,
            {
                "kind": "length",
                "references": [_target(geometry["horizontal"][1])],
            },
        )
        original_verify = RepairRuntimeModule.verify_drawing_dimension_repair

        def fail_verify(_document, _draft):
            raise NativeMutationError("FORCED_REPAIR_ROLLBACK", "forced rollback")

        RepairRuntimeModule.verify_drawing_dimension_repair = fail_verify
        try:
            failed = call(rollback_arguments, succeeds=False)
            assert failed["error_code"] == "FORCED_REPAIR_ROLLBACK"
        finally:
            RepairRuntimeModule.verify_drawing_dimension_repair = original_verify
        assert (
            drawing_dimension_repair_state(rollback)["repair_state_sha256"]
            == rollback_before["repair_state_sha256"]
        )

        document.recompute()
        document.saveAs(str(save_path))
        assert save_path.exists() and save_path.stat().st_size > 0
        document_name = document.Name
        source_name = source.Name
        page_name = page.Name
        view_name = view.Name
        expected_view_names = tuple(obj.Name for obj in views_before) + (
            rollback.Name,
        )
        App.closeDocument(document_name)
        document = None
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        reopened_page = reopened.getObject(page_name)
        reopened_source = reopened.getObject(source_name)
        reopened_view = reopened.getObject(view_name)
        assert all(
            obj is not None
            for obj in (reopened_source, reopened_page, reopened_view)
        )
        assert tuple(obj.Name for obj in reopened_page.Views) == expected_view_names
        reopened_page.ViewObject.show()
        assert reopened.recompute([reopened_view, reopened_page], True, True) is not False
        _events(24)
        for name, expected_hash in final_hashes.items():
            repaired = reopened.getObject(name)
            assert repaired is not None
            reopened_state = drawing_dimension_repair_state(repaired)
            assert reopened_state["repair_state_sha256"] == expected_hash, (
                name,
                expected_hash,
                reopened_state,
            )

        print(
            "VIBECAD_NATIVE_DRAWING_DIMENSION_REPAIR_GUI_OK "
            "kinds=" + ",".join(_KINDS) + " "
            "human_oracle=true shared_host_builder=true broken_target=true "
            "dedicated_tool=true closed_discriminated_schema=true exact_dimension=true "
            "exact_page=true exact_view=true projection_hash=true element_hash=true "
            "kind_preserved=true identity_preserved=true placement_preserved=true "
            "style_preserved=true selection=true visibility=true history=true "
            "stale=true no_op=true rollback=true revision=true undo=true redo=true "
            "snapshot=true reopen=true low_noise=true no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
        except Exception:
            pass
        for candidate in (reopened, document):
            try:
                if candidate is not None and App.getDocument(candidate.Name) is not None:
                    App.closeDocument(candidate.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
