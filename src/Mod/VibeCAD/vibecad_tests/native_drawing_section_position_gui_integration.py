# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing section positioning."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from SectionViewPosition import calculate_edge_vertex_alignment
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSectionPositionSchema import (
    DRAWING_SECTION_POSITION_CAPABILITY_NAME,
    DRAWING_SECTION_POSITION_OPERATIONS,
)
from VibeCADNativeDrawingSectionPositionState import (
    drawing_alignment_base_state,
    drawing_section_position_state,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
import VibeCADNativeDrawingSectionPositionRuntime as PositionRuntimeModule
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


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _visibility(document) -> tuple:
    return tuple(
        (obj.Name, bool(obj.ViewObject.Visibility))
        for obj in document.Objects
        if getattr(obj, "ViewObject", None) is not None
    )


def _create_page(document, name: str):
    page = document.addObject("TechDraw::DrawPage", name)
    page.Label = name.replace("Page", " Page")
    template = document.addObject("TechDraw::DrawSVGTemplate", f"{name}Template")
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
    return page


def _wait_for_projection(document, page, *views) -> None:
    deadline = QtCore.QDeadlineTimer(12_000)
    while not deadline.hasExpired():
        _events(4)
        try:
            states = [drawing_projected_geometry_state(view) for view in views]
            if all(state["edge_count"] and state["vertex_count"] for state in states):
                return
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        for view in views:
            view.touch()
        assert document.recompute([*views, page], True, True) is not False
    raise AssertionError("The Drawing fixture did not produce projected geometry")


def _create_fixture(document):
    document.openTransaction("Create section-position fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "SectionPositionSource")
        source.Label = "Section Position Source"
        source.Shape = Part.makeBox(36.0, 24.0, 12.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = _create_page(document, "SectionPositionPage")
        base = document.addObject("TechDraw::DrawViewPart", "SectionPositionBase")
        base.Label = "Section Position Base"
        base.Source = [source]
        base.Direction = App.Vector(0.0, -1.0, 0.0)
        base.XDirection = App.Vector(1.0, 0.0, 0.0)
        base.ScaleType = "Custom"
        base.Scale = 1.0
        document.publishProvisionalTimelineOperationBlock(base, (), ())
        assert int(page.addView(base)) >= 1
        base.X = 70.0
        base.Y = 75.0

        section = document.addObject(
            "TechDraw::DrawViewSection",
            "PositionedSectionView",
        )
        section.Label = "Positioned Section View"
        section.SectionSymbol = "A"
        section.BaseView = base
        section.Source = [source]
        section.SectionOrigin = App.Vector(18.0, 12.0, 6.0)
        section.SectionDirection = "Aligned"
        section.Direction = App.Vector(-1.0, 0.0, 0.0)
        section.SectionNormal = App.Vector(-1.0, 0.0, 0.0)
        section.XDirection = App.Vector(0.0, 0.0, -1.0)
        section.Rotation = -90.0
        section.ScaleType = "Custom"
        section.Scale = 1.0
        document.publishProvisionalTimelineOperationBlock(section, (), ())
        assert int(page.addView(section)) >= 1
        section.X = 180.0
        section.Y = 130.0

        other_page = _create_page(document, "OtherSectionPositionPage")
        assert document.recompute(
            [source, base, section, page, other_page],
            True,
            True,
        ) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    source.ViewObject.Visibility = True
    base.ViewObject.Visibility = True
    section.ViewObject.Visibility = True
    _wait_for_projection(document, page, base, section)
    return source, page, base, section, other_page


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_SECTION_POSITION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_SECTION_POSITION_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024
    branches = {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }
    assert set(branches) == {"align_axis", "align_edge_to_vertex"}
    assert branches["align_axis"]["properties"]["axis"]["enum"] == [
        "horizontal",
        "vertical",
    ]
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_SECTION_POSITION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _page_target(page) -> dict:
    state = drawing_page_state(page)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _section_target(section, *, projection: dict | None = None) -> dict:
    state = drawing_section_position_state(section)
    result = {
        "object_name": state["object_name"],
        "expected_section_position_state_sha256": state[
            "section_position_state_sha256"
        ],
    }
    if projection is not None:
        result["expected_projection_state_sha256"] = projection[
            "projection_state_sha256"
        ]
    return result


def _axis_arguments(page, section, axis: str) -> dict:
    return {
        "operation": "align_axis",
        "page": _page_target(page),
        "section_view": _section_target(section),
        "axis": axis,
    }


def _base_target(base, projection: dict) -> dict:
    state = drawing_view_state(base)
    alignment = drawing_alignment_base_state(base)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_projection_state_sha256": projection[
            "projection_state_sha256"
        ],
        "expected_alignment_base_state_sha256": alignment[
            "alignment_base_state_sha256"
        ],
    }


def _element_target(element: dict) -> dict:
    return {
        "name": element["name"],
        "expected_element_state_sha256": element["element_state_sha256"],
    }


def _alignment_pair(section, base):
    section_projection = drawing_projected_geometry_state(section)
    base_projection = drawing_projected_geometry_state(base)
    edges = [
        item
        for item in section_projection["elements"]
        if item["element_type"] == "edge"
        and not item["closed"]
        and "line" in item["geometry_type"].casefold()
    ]
    vertices = [
        item
        for item in base_projection["elements"]
        if item["element_type"] == "vertex"
    ]
    for edge in edges:
        for vertex in vertices:
            calculation = calculate_edge_vertex_alignment(
                section,
                edge["name"],
                base,
                vertex["name"],
            )
            current = drawing_section_position_state(section)["position_on_page_mm"]
            moved = math.hypot(
                calculation["target_x_mm"] - current["x_mm"],
                calculation["target_y_mm"] - current["y_mm"],
            )
            if moved > 1.0e-5:
                return section_projection, edge, base_projection, vertex, calculation
    raise AssertionError("No nontrivial straight-edge/base-vertex alignment exists")


def _edge_arguments(page, section, base) -> tuple[dict, dict]:
    section_projection, edge, base_projection, vertex, calculation = _alignment_pair(
        section,
        base,
    )
    return (
        {
            "operation": "align_edge_to_vertex",
            "page": _page_target(page),
            "section_view": _section_target(
                section,
                projection=section_projection,
            ),
            "section_edge": _element_target(edge),
            "base_view": _base_target(base, base_projection),
            "base_vertex": _element_target(vertex),
        },
        calculation,
    )


def _position(section) -> tuple[float, float]:
    state = drawing_section_position_state(section)
    value = state["position_on_page_mm"]
    return value["x_mm"], value["y_mm"]


def _assert_position(section, x_mm: float, y_mm: float) -> None:
    x_value, y_value = _position(section)
    assert math.isclose(x_value, x_mm, abs_tol=1.0e-8), (
        (x_value, y_value),
        (x_mm, y_mm),
    )
    assert math.isclose(y_value, y_mm, abs_tol=1.0e-8), (
        (x_value, y_value),
        (x_mm, y_mm),
    )


def _human_oracle(document, section, base) -> None:
    initial = drawing_section_position_state(section)
    undo_before = int(document.UndoCount)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(section)
    axis_selection = _selection()
    Gui.runCommand("TechDraw_ExtensionPositionSectionView")
    _events(12)
    _assert_position(section, 180.0, 75.0)
    assert _selection() == axis_selection
    assert int(document.UndoCount) == undo_before + 1
    document.undo()
    _events(10)
    assert drawing_section_position_state(section) == initial
    document.redo()
    _events(10)
    _assert_position(section, 180.0, 75.0)
    document.undo()
    _events(10)
    assert drawing_section_position_state(section) == initial

    _, edge, _, vertex, calculation = _alignment_pair(section, base)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(section, edge["name"])
    Gui.Selection.addSelection(base, vertex["name"])
    geometry_selection = _selection()
    undo_before = int(document.UndoCount)
    Gui.runCommand("TechDraw_ExtensionPositionSectionView")
    _events(12)
    _assert_position(
        section,
        calculation["target_x_mm"],
        calculation["target_y_mm"],
    )
    assert _selection() == geometry_selection
    assert int(document.UndoCount) == undo_before + 1
    document.undo()
    _events(10)
    assert drawing_section_position_state(section) == initial
    document.redo()
    _events(10)
    _assert_position(
        section,
        calculation["target_x_mm"],
        calculation["target_y_mm"],
    )
    document.undo()
    _events(10)
    assert drawing_section_position_state(section) == initial


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-section-position-"
        )
        save_path = Path(temporary.name) / "drawing-section-position.FCStd"
        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["TechDraw_ExtensionPositionSectionView"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.transaction_behavior,
            plan.background_required,
        ) == (
            DRAWING_SECTION_POSITION_CAPABILITY_NAME,
            "align_axis",
            "ExactDrawingSectionViewAndExplicitBaseAxis",
            "document",
            False,
        )

        document = App.newDocument("NativeDrawingSectionPositionGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, base, section, other_page = _create_fixture(document)
        _human_oracle(document, section, base)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-section-position-gui")

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

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_SECTION_POSITION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"native-drawing-section-position-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        invalid = _axis_arguments(page, section, "horizontal")
        invalid["inferred_axis"] = True
        rejected = call(invalid, succeeds=False)
        assert rejected["error_code"] == "NATIVE_ARGUMENTS_INVALID"

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(section)
        selection_before = _selection()
        visibility_before = _visibility(document)
        objects_before = tuple(document.Objects)
        page_views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        revision0 = state_store.current_revision(str(document.Uid))

        horizontal_args = _axis_arguments(page, section, "horizontal")
        response = call(horizontal_args, call_id="section-position-horizontal")
        assert response["operation"] == "align_axis"
        assert response["section_position"]["axis"] == "horizontal"
        assert response["assistant_undo_available"] is True
        _assert_position(section, 180.0, 75.0)
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 6 * 1024
        assert call(
            horizontal_args,
            call_id="section-position-horizontal",
        ) == response
        assert state_store.current_revision(str(document.Uid)) == revision0 + 1

        stale = call(horizontal_args, succeeds=False)
        assert stale["error_code"] == "NATIVE_DRAWING_SECTION_POSITION_TARGET_STALE"

        rollback_state = drawing_section_position_state(section)
        rollback_undo = int(document.UndoCount)
        rollback_revision = state_store.current_revision(str(document.Uid))
        original_verify = PositionRuntimeModule.verify_drawing_section_position

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected section-position verification failure")

        PositionRuntimeModule.verify_drawing_section_position = fail_verify
        try:
            rollback = call(
                _axis_arguments(page, section, "vertical"),
                succeeds=False,
            )
        finally:
            PositionRuntimeModule.verify_drawing_section_position = original_verify
        _events(10)
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert drawing_section_position_state(section) == rollback_state
        assert int(document.UndoCount) == rollback_undo
        assert state_store.current_revision(str(document.Uid)) == rollback_revision

        vertical = call(_axis_arguments(page, section, "vertical"))
        assert vertical["section_position"]["axis"] == "vertical"
        _assert_position(section, 70.0, 75.0)

        no_op_undo = int(document.UndoCount)
        no_op_revision = state_store.current_revision(str(document.Uid))
        no_op = call(
            _axis_arguments(page, section, "vertical"),
            succeeds=False,
        )
        assert no_op["error_code"] == "NATIVE_DRAWING_SECTION_POSITION_NO_CHANGE"
        assert int(document.UndoCount) == no_op_undo
        assert state_store.current_revision(str(document.Uid)) == no_op_revision

        mismatch_args = _axis_arguments(page, section, "horizontal")
        mismatch_args["page"] = _page_target(other_page)
        mismatch = call(mismatch_args, succeeds=False)
        assert mismatch["error_code"] == "NATIVE_DRAWING_SECTION_POSITION_PAGE_MISMATCH"

        edge_args, calculation = _edge_arguments(page, section, base)
        stale_projection = json.loads(json.dumps(edge_args))
        stale_projection["section_view"]["expected_projection_state_sha256"] = (
            "0" * 64
        )
        rejected = call(stale_projection, succeeds=False)
        assert rejected["error_code"] == (
            "NATIVE_DRAWING_SECTION_POSITION_PROJECTION_STALE"
        )
        stale_element = json.loads(json.dumps(edge_args))
        stale_element["section_edge"]["expected_element_state_sha256"] = "0" * 64
        rejected = call(stale_element, succeeds=False)
        assert rejected["error_code"] == (
            "NATIVE_DRAWING_SECTION_POSITION_ELEMENT_STALE"
        )

        edge_response = call(edge_args)
        assert edge_response["operation"] == "align_edge_to_vertex"
        assert edge_response["section_position"]["section_edge"] == (
            edge_args["section_edge"]["name"]
        )
        assert edge_response["section_position"]["base_vertex"] == (
            edge_args["base_vertex"]["name"]
        )
        _assert_position(
            section,
            calculation["target_x_mm"],
            calculation["target_y_mm"],
        )
        final_state = drawing_section_position_state(section)
        assert _selection() == selection_before
        assert _visibility(document) == visibility_before
        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == page_views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert not Gui.Control.activeDialog()

        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        page_summary = next(
            item for item in snapshot["pages"] if item["object_name"] == page.Name
        )
        section_summary = next(
            item
            for item in page_summary["views"]
            if item["object_name"] == section.Name
        )
        position_summary = section_summary["section_position"]
        assert position_summary["section_position_state_sha256"] == (
            final_state["section_position_state_sha256"]
        )
        assert position_summary["alignment_base"][
            "alignment_base_state_sha256"
        ] == final_state["alignment_base"]["alignment_base_state_sha256"]
        assert "label" not in position_summary["alignment_base"]
        assert len(json.dumps(snapshot, separators=(",", ":")).encode()) < 96 * 1024

        document.undo()
        _events(12)
        _assert_position(section, 70.0, 75.0)
        document.redo()
        _events(12)
        assert drawing_section_position_state(section) == final_state

        names = {
            "page": page.Name,
            "base": base.Name,
            "section": section.Name,
        }
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        _events(24)
        page = document.getObject(names["page"])
        base = document.getObject(names["base"])
        section = document.getObject(names["section"])
        assert all(obj is not None for obj in (page, base, section))
        assert section in tuple(page.Views)
        assert drawing_section_position_state(section) == final_state

        print(
            "VIBECAD_NATIVE_DRAWING_SECTION_POSITION_GUI_OK operations=2 "
            "human_axis=true human_edge_vertex=true shared_host_primitive=true "
            "explicit_axis=true exact_page=true exact_section=true "
            "exact_projection=true exact_elements=true exact_base=true "
            "closed_schema=true stale_target=true stale_projection=true "
            "stale_element=true no_op=true cross_page=true rollback=true "
            "selection=true visibility=true history=true revision=true "
            "undo=true redo=true snapshot=true reopen=true low_noise=true "
            "no_task=true",
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
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
