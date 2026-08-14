# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for all four Drawing cosmetic-curve actions."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
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
from VibeCADNativeDrawingCosmeticCurveState import (
    drawing_cosmetic_curve_inventory_state,
)
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
import VibeCADNativeDrawingCosmeticCurveRuntime as CurveRuntimeModule
from VibeCADNativeDrawingCosmeticCurveSchema import (
    DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
    DRAWING_COSMETIC_CURVE_OPERATIONS,
)
from VibeCADNativeDrawingViewState import drawing_view_state
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


def _noncollinear(left, middle, right) -> bool:
    first = left["point_in_view_mm"]
    second = middle["point_in_view_mm"]
    third = right["point_in_view_mm"]
    area = (second["x_mm"] - first["x_mm"]) * (third["y_mm"] - first["y_mm"]) - (
        second["y_mm"] - first["y_mm"]
    ) * (third["x_mm"] - first["x_mm"])
    return abs(area) > 1.0e-7


def _create_fixture(document):
    document.openTransaction("Create Drawing cosmetic-curve fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "CurveSource")
        source.Label = "Cosmetic Curve Source"
        points = (
            App.Vector(-20.0, 0.0, 0.0),
            App.Vector(0.0, 0.0, 0.0),
            App.Vector(20.0, 0.0, 0.0),
            App.Vector(0.0, 20.0, 0.0),
            App.Vector(-15.0, 10.0, 0.0),
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeLine(points[0], points[1]),
                Part.makeLine(points[1], points[2]),
                Part.makeLine(points[1], points[3]),
                Part.makeLine(points[3], points[4]),
                Part.makeLine(points[4], points[0]),
            ]
        )
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "CurvePage")
        page.Label = "Cosmetic Curve Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "CurveTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "CurveView")
        view.Label = "Cosmetic Curve View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.4
        view.Rotation = 11.0
        view.X = 112.0
        view.Y = 76.0
        document.publishProvisionalTimelineOperationBlock(view, (), ())
        assert int(page.addView(view)) >= 1
        assert document.recompute([source, view, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(24)
    assert document.recompute([view, page], True, True) is not False

    vertices = tuple(
        item
        for item in drawing_projected_geometry_state(view)["elements"]
        if item["element_type"] == "vertex"
    )
    assert len(vertices) >= 4, vertices
    noncollinear = next(
        trio for trio in itertools.combinations(vertices, 3) if _noncollinear(*trio)
    )
    return source, page, view, vertices, noncollinear


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_COSMETIC_CURVE_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_COSMETIC_CURVE_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 24 * 1024
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_COSMETIC_CURVE_OPERATIONS
    )
    assert all(branch["additionalProperties"] is False for branch in branches)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _base_arguments(page, view, operation) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    return {
        "operation": operation,
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": page_state["state_sha256"],
        },
        "view": {
            "object_name": view.Name,
            "expected_state_sha256": view_state["state_sha256"],
            "expected_projection_state_sha256": projection["projection_state_sha256"],
        },
    }


def _vertex_target(view, name) -> dict:
    element = next(
        item
        for item in drawing_projected_geometry_state(view)["elements"]
        if item["name"] == name
    )
    assert element["element_type"] == "vertex"
    return {
        "subelement": name,
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _arguments(page, view, operation, names, radius=None) -> dict:
    result = _base_arguments(page, view, operation)
    targets = [_vertex_target(view, name) for name in names]
    if operation == "create_one_point_circle":
        result.update({"center_vertex": targets[0], "radius_mm": radius})
    elif operation == "create_two_point_circle":
        result.update({"center_vertex": targets[0], "radius_vertex": targets[1]})
    elif operation == "create_three_point_circle":
        result.update(
            {
                "first_perimeter_vertex": targets[0],
                "second_perimeter_vertex": targets[1],
                "third_perimeter_vertex": targets[2],
            }
        )
    else:
        result.update(
            {
                "center_vertex": targets[0],
                "start_vertex": targets[1],
                "end_vertex": targets[2],
            }
        )
    return result


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _curve_signature(curve) -> str:
    return json.dumps(
        {"geometry": curve["geometry"], "line_format": curve["line_format"]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _created_curve(before, after):
    old_tags = {item["tag"] for item in before["curves"]}
    created = [item for item in after["curves"] if item["tag"] not in old_tags]
    assert len(created) == 1
    return created[0]


def _open_one_point_task(view, vertex_name) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view, vertex_name)
    Gui.runCommand("TechDraw_CosmeticCircle")
    _events(16)
    assert Gui.Control.activeDialog()
    assert Gui.Control.activeTaskDialog() is not None


def _set_task_radius(radius: float) -> None:
    radius_box = Gui.getMainWindow().findChild(
        QtWidgets.QAbstractSpinBox,
        "qsbRadius",
    )
    assert radius_box is not None
    assert radius_box.setProperty("rawValue", radius)
    _events(8)
    assert math.isclose(float(radius_box.property("rawValue")), radius)


def _set_task_quantity(name: str, value: float) -> None:
    quantity = Gui.getMainWindow().findChild(QtWidgets.QAbstractSpinBox, name)
    assert quantity is not None
    assert quantity.setProperty("rawValue", value)
    _events(4)
    assert math.isclose(float(quantity.property("rawValue")), value)


def _human_one_point(document, view, name, radius):
    before = drawing_cosmetic_curve_inventory_state(view)
    _open_one_point_task(view, name)
    _set_task_radius(radius + 1.0)
    Gui.Control.activeTaskDialog().reject()
    _events(16)
    assert not Gui.Control.activeDialog()
    assert (
        drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )

    _open_one_point_task(view, name)
    _set_task_radius(radius)
    Gui.Control.activeTaskDialog().accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    after = drawing_cosmetic_curve_inventory_state(view)
    created = _created_curve(before, after)
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _curve_signature(created)


def _human_one_point_arc(document, view, name, radius):
    before = drawing_cosmetic_curve_inventory_state(view)
    _open_one_point_task(view, name)
    _set_task_radius(radius)
    arc_button = Gui.getMainWindow().findChild(QtWidgets.QRadioButton, "rbArc")
    assert arc_button is not None
    arc_button.click()
    _events(4)
    assert arc_button.isChecked()
    _set_task_quantity("qsbStartAngle", 15.0)
    _set_task_quantity("qsbEndAngle", 135.0)
    Gui.Control.activeTaskDialog().accept()
    _events(20)
    after = drawing_cosmetic_curve_inventory_state(view)
    created = _created_curve(before, after)
    assert created["geometry"]["geometry_configuration"] == "circular_arc"
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )


def _human_direct(document, view, command, names):
    before = drawing_cosmetic_curve_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(view, name)
    Gui.runCommand(command)
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    after = drawing_cosmetic_curve_inventory_state(view)
    created = _created_curve(before, after)
    document.undo()
    _events(16)
    assert (
        drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
        == before["inventory_state_sha256"]
    )
    return _curve_signature(created)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-cosmetic-curve-"
        )
        save_path = Path(temporary.name) / "cosmetic-curves.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_actions = {
            "TechDraw_CosmeticCircle": (
                "create_one_point_circle",
                "ExactDrawingCenterVertexAndExplicitRadius",
            ),
            "TechDraw_ExtensionDrawCosmCircle": (
                "create_two_point_circle",
                "ExactDrawingCenterAndRadiusVertices",
            ),
            "TechDraw_ExtensionDrawCosmCircle3Points": (
                "create_three_point_circle",
                "ExactDrawingThreePerimeterVertices",
            ),
            "TechDraw_ExtensionDrawCosmArc": (
                "create_center_start_end_arc",
                "ExactDrawingCenterStartAndEndAngleVertices",
            ),
        }
        for action_id, (operation, target_type) in expected_actions.items():
            action = action_plans[action_id]
            assert (
                action.capability_family,
                action.operation_variant,
                action.exact_target_type,
                action.transaction_behavior,
                action.background_required,
            ) == (
                DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingCosmeticCurveGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view, vertices, perimeter = _create_fixture(document)
        vertex_names = tuple(item["name"] for item in vertices)
        perimeter_names = tuple(item["name"] for item in perimeter)
        one_name = vertex_names[0]
        two_names = vertex_names[:2]
        arc_names = perimeter_names
        radius = 7.25

        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, page)
        )
        human = {
            "create_one_point_circle": _human_one_point(
                document, view, one_name, radius
            ),
            "create_two_point_circle": _human_direct(
                document,
                view,
                "TechDraw_ExtensionDrawCosmCircle",
                two_names,
            ),
            "create_three_point_circle": _human_direct(
                document,
                view,
                "TechDraw_ExtensionDrawCosmCircle3Points",
                perimeter_names,
            ),
            "create_center_start_end_arc": _human_direct(
                document,
                view,
                "TechDraw_ExtensionDrawCosmArc",
                arc_names,
            ),
        }
        _human_one_point_arc(document, view, one_name, radius)
        assert drawing_cosmetic_curve_inventory_state(view)["curve_count"] == 0

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-cosmetic-curve-gui")

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

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view, one_name)
        selection_before = _selection()
        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        assert (
            snapshot["selected_projected_geometry"][0]["selected_elements"][0]["name"]
            == one_name
        )

        requests = (
            ("create_one_point_circle", (one_name,), radius),
            ("create_two_point_circle", two_names, None),
            ("create_three_point_circle", perimeter_names, None),
            ("create_center_start_end_arc", arc_names, None),
        )
        responses = {}
        first_arguments = None
        for index, (operation, names, explicit_radius) in enumerate(requests):
            arguments = _arguments(
                page,
                view,
                operation,
                names,
                explicit_radius,
            )
            if first_arguments is None:
                first_arguments = arguments
            revision = state_store.current_revision(str(document.Uid))
            response = dispatcher.call(
                DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
                json.dumps(arguments),
                f"native-drawing-cosmetic-curve-{index}",
            )
            assert response["ok"] is True, {
                "operation": operation,
                "response": response,
            }
            assert response["operation"] == operation
            result = response["cosmetic_curve"]
            assert _curve_signature(result["curve"]) == human[operation]
            assert [item["subelement"] for item in result["sources"]] == list(names)
            assert state_store.current_revision(str(document.Uid)) == revision + 1
            assert _selection() == selection_before
            assert not Gui.Control.activeDialog()
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 4096
            responses[operation] = response

        assert first_arguments is not None
        repeated = dispatcher.call(
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            json.dumps(first_arguments),
            "native-drawing-cosmetic-curve-0",
        )
        assert repeated == responses["create_one_point_circle"]
        inventory = drawing_cosmetic_curve_inventory_state(view)
        assert inventory["curve_count"] == 4
        all_tags = {item["tag"] for item in inventory["curves"]}

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        stale = dispatcher.call(
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            json.dumps(first_arguments),
            "native-drawing-cosmetic-curve-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] in {
            "NATIVE_DRAWING_COSMETIC_CURVE_VIEW_STALE",
            "NATIVE_DRAWING_COSMETIC_CURVE_PROJECTION_STALE",
        }
        duplicate = _arguments(
            page,
            view,
            "create_two_point_circle",
            (one_name, one_name),
        )
        duplicate_response = dispatcher.call(
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            json.dumps(duplicate),
            "native-drawing-cosmetic-curve-duplicate",
        )
        assert duplicate_response["ok"] is False
        assert (
            duplicate_response["error_code"]
            == "NATIVE_DRAWING_COSMETIC_CURVE_REFERENCES_INVALID"
        )
        wrong_type = _arguments(
            page,
            view,
            "create_one_point_circle",
            (one_name,),
            radius,
        )
        wrong_type["center_vertex"]["subelement"] = "Edge1"
        wrong_response = dispatcher.call(
            DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
            json.dumps(wrong_type),
            "native-drawing-cosmetic-curve-wrong-type",
        )
        assert wrong_response["ok"] is False
        assert wrong_response["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo
        assert (
            drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )

        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = CurveRuntimeModule.verify_drawing_cosmetic_curve

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected cosmetic-curve verification failure")

        CurveRuntimeModule.verify_drawing_cosmetic_curve = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
                json.dumps(
                    _arguments(
                        page,
                        view,
                        "create_one_point_circle",
                        (one_name,),
                        radius + 2.0,
                    )
                ),
                "native-drawing-cosmetic-curve-rollback",
            )
        finally:
            CurveRuntimeModule.verify_drawing_cosmetic_curve = original_verify
        _events(16)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert (
            drawing_cosmetic_curve_inventory_state(view)["inventory_state_sha256"]
            == inventory["inventory_state_sha256"]
        )
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == tuple(
            bool(obj.ViewObject.Visibility) for obj in (source, view, page)
        )

        document.undo()
        _events(16)
        assert drawing_cosmetic_curve_inventory_state(view)["curve_count"] == 3
        document.redo()
        _events(16)
        redone = drawing_cosmetic_curve_inventory_state(view)
        assert redone["inventory_state_sha256"] == inventory["inventory_state_sha256"]

        names = {"page": page.Name, "view": view.Name}
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject(names["page"])
        view = document.getObject(names["view"])
        assert page is not None and view is not None
        page.ViewObject.show()
        assert document.recompute([view, page], True, True) is not False
        _events(24)
        reopened = drawing_cosmetic_curve_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone["inventory_state_sha256"]
        assert {item["tag"] for item in reopened["curves"]} == all_tags
        for tag in all_tags:
            assert TechDrawGui.drawingPersistentCosmeticCurve(view, tag)["tag"] == tag

        print(
            "VIBECAD_NATIVE_DRAWING_COSMETIC_CURVE_GUI_OK operations=4 "
            "one_point=true two_point=true three_point=true arc=true "
            "human_oracle=true shared_host_builder=true task_accept=true "
            "task_reject=true task_arc=true exact_page=true exact_view=true "
            "projection_hash=true "
            "element_hash=true named_roles=true explicit_radius=true derived_center=true "
            "derived_radius=true derived_angles=true host_style=true persistent_tags=true "
            "selection=true visibility=true history=true duplicate=true wrong_type=true "
            "stale=true rollback=true revision=true idempotency=true undo=true redo=true "
            "snapshot=true reopen=true low_noise=true native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if Gui.Control.activeDialog():
                Gui.Control.activeTaskDialog().reject()
                _events(8)
        except Exception:
            pass
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
