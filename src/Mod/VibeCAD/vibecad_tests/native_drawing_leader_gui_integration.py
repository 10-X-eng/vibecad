# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing Leader Lines."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets
import TechDrawGui

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingLeader import drawing_leader_defaults_state
from VibeCADNativeDrawingLeaderSchema import (
    DRAWING_ANNOTATION_CAPABILITY_NAME,
    DRAWING_LEADER_OPERATIONS,
)
from VibeCADNativeDrawingLeaderState import (
    drawing_leader_owner_state,
    drawing_leader_state,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
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


def _page_image_sha256() -> str:
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    assert mdi is not None and mdi.activeSubWindow() is not None
    image = mdi.activeSubWindow().grab().toImage()
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    assert buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return hashlib.sha256(bytes(data)).hexdigest()


def _graphics_view() -> QtWidgets.QGraphicsView:
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    subwindow = mdi.activeSubWindow() if mdi is not None else None
    graphics = subwindow.findChild(QtWidgets.QGraphicsView) if subwindow else None
    assert graphics is not None and graphics.scene() is not None
    return graphics


def _click(
    viewport: QtWidgets.QWidget,
    point: QtCore.QPoint,
    button: QtCore.Qt.MouseButton,
) -> None:
    press = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QPointF(point),
        QtCore.QPointF(viewport.mapToGlobal(point)),
        button,
        button,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    release = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QPointF(point),
        QtCore.QPointF(viewport.mapToGlobal(point)),
        button,
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(viewport, press)
    QtWidgets.QApplication.sendEvent(viewport, release)
    _events(4)


def _create_fixture(document):
    document.openTransaction("Create projected Leader Line fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "LeaderSource")
        source.Label = "Leader Source"
        source.Shape = Part.makeBox(42.0, 26.0, 9.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "LeaderPage")
        page.Label = "Leader Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "LeaderTemplate")
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

        owner = document.addObject("TechDraw::DrawProjGroupItem", "LeaderOwner")
        owner.Label = "Rotated Projected Owner"
        owner.Type = "Front"
        owner.Source = [source]
        owner.Direction = App.Vector(0.0, 0.0, 1.0)
        owner.XDirection = App.Vector(1.0, 0.0, 0.0)
        owner.ScaleType = "Custom"
        owner.Scale = 1.4
        owner.Rotation = 22.0
        owner.X = 92.0
        owner.Y = 62.0
        owner.CoarseView = False

        group = document.addObject("TechDraw::DrawProjGroup", "LeaderProjection")
        group.Label = "Leader Projection Group"
        group.X = 28.0
        group.Y = 18.0
        group.ScaleType = "Custom"
        group.Scale = 1.4
        group.AutoDistribute = False
        group.ProjectionType = "First angle"
        group.Source = [source]
        assert int(group.addView(owner)) >= 1
        group.Anchor = owner
        assert int(page.addPrecomputedView(group)) >= 1
        document.publishProvisionalTimelineOperationBlock(owner, (), ())
        document.publishProvisionalTimelineOperationBlock(group, (), ())
        assert document.recompute([source, owner, group, page], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    page.ViewObject.show()
    _events(32)
    assert owner.findParentPage() is page
    assert owner not in tuple(page.Views)
    assert owner in tuple(group.Views)
    state = drawing_leader_owner_state(owner, page=page)
    assert state["projection_group_name"] == group.Name
    assert state["position_on_page_mm"] == {"x_mm": 120.0, "y_mm": 80.0}
    assert state["scale"] == 1.4
    assert state["rotation_degrees"] == 22.0
    return source, page, group, owner


def _human_leader(document, owner):
    defaults = drawing_leader_defaults_state()
    before = tuple(document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(owner)
    Gui.runCommand("TechDraw_LeaderLine")
    _events(20)
    assert Gui.Control.activeDialog()
    pick = next(
        button
        for button in Gui.getMainWindow().findChildren(QtWidgets.QPushButton)
        if button.isVisible() and button.text() == "Pick Points"
    )
    pick.click()
    _events(12)
    graphics = _graphics_view()
    viewport = graphics.viewport()
    width = viewport.width()
    height = viewport.height()
    for point in (
        QtCore.QPoint(width * 42 // 100, height * 55 // 100),
        QtCore.QPoint(width * 52 // 100, height * 43 // 100),
        QtCore.QPoint(width * 63 // 100, height * 43 // 100),
    ):
        _click(viewport, point, QtCore.Qt.MouseButton.LeftButton)
    assert pick.text() == "Save Points"
    pick.click()
    _events(12)
    task = Gui.Control.activeTaskDialog()
    assert task is not None
    task.accept()
    _events(24)
    assert not Gui.Control.activeDialog()
    created = [item for item in document.Objects if item not in before]
    assert len(created) == 1
    leader = created[0]
    state = drawing_leader_state(leader)
    assert state["owner"]["object_name"] == owner.Name
    assert state["symbols"] == defaults["symbols"]
    assert state["behavior"] == defaults["behavior"]
    assert state["line"] == defaults["line"]
    assert leader in tuple(leader.findParentPage().Views)
    assert leader in tuple(document.VibeCADTimeline.Operations)
    return leader


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_ANNOTATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_LEADER_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_LEADER_OPERATIONS
    )
    by_operation = {
        branch["properties"]["operation"]["const"]: branch for branch in branches
    }
    create = by_operation["leader_line"]
    assert create["properties"]["points_on_page_mm"]["minItems"] == 2
    assert create["properties"]["points_on_page_mm"]["maxItems"] == 64
    assert by_operation["read_leader_defaults"]["required"] == ["operation"]
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_ANNOTATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(page, owner, defaults: dict) -> dict:
    owner_state = drawing_leader_owner_state(owner, page=page)
    return {
        "operation": "leader_line",
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": drawing_page_state(page)["state_sha256"],
        },
        "owner": {
            "object_name": owner.Name,
            "expected_owner_state_sha256": owner_state["owner_state_sha256"],
        },
        "points_on_page_mm": [
            {"x_mm": 82.0, "y_mm": 61.0},
            {"x_mm": 112.0, "y_mm": 88.0},
            {"x_mm": 151.0, "y_mm": 73.0},
        ],
        "label": "Native Inspection Leader",
        "symbols": {"start": "open_arrow", "end": "dot"},
        "behavior": {
            "scalable": True,
            "auto_horizontal": True,
            "rotates_with_owner": True,
        },
        "line": {
            "line_width_mm": 0.7,
            "line_style": "dash_dot",
            "color_rgb": {"red": 0.2, "green": 0.35, "blue": 0.8},
        },
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-leader-")
        save_path = Path(temporary.name) / "drawing-leader.FCStd"
        controller, surface = _surface()
        plan = next(
            item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id == "TechDraw_LeaderLine"
        )
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
        ) == (
            DRAWING_ANNOTATION_CAPABILITY_NAME,
            "leader_line",
            "ExactDrawingPageOwnerPointsSymbolsBehaviorAndLineStyle",
        )

        document = App.newDocument("NativeDrawingLeaderGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, group, owner = _create_fixture(document)
        image_before_human = _page_image_sha256()
        human = _human_leader(document, owner)
        assert _page_image_sha256() != image_before_human

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-leader-gui")

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
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state_store,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_index = 0

        def call(arguments: dict, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                DRAWING_ANNOTATION_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-leader-{call_index}",
            )
            assert response.get("ok") is succeeds, (response, debug_events[-1:])
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(owner)
        selection_before = _selection()
        visibility_before = tuple(
            bool(item.ViewObject.Visibility)
            for item in (source, page, group, owner, human)
        )
        revision_before_defaults = state_store.current_revision(str(document.Uid))
        defaults_response = call({"operation": "read_leader_defaults"})
        defaults = {
            name: defaults_response[name]
            for name in ("symbols", "behavior", "line")
        }
        assert defaults == drawing_leader_defaults_state()
        assert state_store.current_revision(str(document.Uid)) == revision_before_defaults

        snapshot_before = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        selected_owner = snapshot_before["selected_leader_owners"]
        assert len(selected_owner) == 1
        assert selected_owner[0]["object_name"] == owner.Name
        assert selected_owner[0]["projection_group_name"] == group.Name

        arguments = _arguments(page, owner, defaults)
        image_before_native = _page_image_sha256()
        response = call(arguments)
        _events(24)
        leader_name = response["leader"]["object_name"]
        leader = document.getObject(leader_name)
        assert leader is not None
        state = drawing_leader_state(leader)
        assert state == response["leader"]
        assert state["point_count"] == 3
        assert all(
            abs(
                state["rendered_points_on_page_mm"][0][name]
                - arguments["points_on_page_mm"][0][name]
            )
            < 1.0e-9
            for name in ("x_mm", "y_mm")
        )
        assert abs(
            state["rendered_points_on_page_mm"][-1]["y_mm"]
            - state["rendered_points_on_page_mm"][-2]["y_mm"]
        ) < 1.0e-9
        assert state["rendered_points_on_page_mm"][-1] != arguments[
            "points_on_page_mm"
        ][-1]
        assert _page_image_sha256() != image_before_native
        assert not Gui.Control.activeDialog()

        stale_owner = _arguments(page, owner, defaults)
        stale_owner["owner"]["expected_owner_state_sha256"] = "0" * 64
        rejected = call(stale_owner, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_LEADER_OWNER_STALE"

        stale_page = _arguments(page, owner, defaults)
        stale_page["page"]["expected_state_sha256"] = "0" * 64
        rejected = call(stale_page, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        duplicate = _arguments(page, owner, defaults)
        duplicate["points_on_page_mm"][1] = dict(duplicate["points_on_page_mm"][0])
        rejected = call(duplicate, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_LEADER_PARAMETERS_INVALID"

        outside = _arguments(page, owner, defaults)
        outside["points_on_page_mm"][0]["x_mm"] = (
            drawing_page_state(page)["template_geometry"]["width_mm"] + 1.0
        )
        rejected = call(outside, False)
        assert rejected["error_code"] in {
            "NATIVE_ARGUMENTS_INVALID",
            "NATIVE_DRAWING_LEADER_GEOMETRY_INVALID",
        }

        objects_before_rollback = tuple(document.Objects)
        views_before_rollback = tuple(page.Views)
        history_before_rollback = tuple(document.VibeCADTimeline.Operations)
        original_create = TechDrawGui.createDrawingLeaderLine

        def fail_after_create(*args):
            original_create(*args)
            raise RuntimeError("Injected Leader Line creation failure")

        TechDrawGui.createDrawingLeaderLine = fail_after_create
        try:
            rolled_back = call(_arguments(page, owner, defaults), False)
        finally:
            TechDrawGui.createDrawingLeaderLine = original_create
        assert rolled_back["error_code"] == "NATIVE_DRAWING_LEADER_CREATE_FAILED"
        assert tuple(document.Objects) == objects_before_rollback
        assert tuple(page.Views) == views_before_rollback
        assert tuple(document.VibeCADTimeline.Operations) == history_before_rollback

        expected_names = {human.Name, leader_name}
        assert expected_names <= {item.Name for item in page.Views}
        assert expected_names <= {
            item.Name for item in document.VibeCADTimeline.Operations
        }
        claimed_by_owner = {item.Name for item in owner.ViewObject.claimChildren()}
        claimed_by_page = {item.Name for item in page.ViewObject.claimChildren()}
        assert expected_names <= claimed_by_owner
        assert expected_names.isdisjoint(claimed_by_page)
        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        assert snapshot["leader_defaults"] == defaults
        snapshot_leaders = {
            item["object_name"]
            for item in snapshot["pages"][0]["views"]
            if "leader" in item
        }
        assert expected_names <= snapshot_leaders
        assert _selection() == selection_before
        assert tuple(
            bool(item.ViewObject.Visibility)
            for item in (source, page, group, owner, human)
        ) == visibility_before
        assert len(json.dumps(response, separators=(",", ":")).encode()) < 4096

        document.undo()
        _events(20)
        assert document.getObject(leader_name) is None
        document.redo()
        _events(24)
        assert drawing_leader_state(document.getObject(leader_name))["valid"]

        names = tuple(sorted(expected_names))
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject("LeaderPage")
        owner = document.getObject("LeaderOwner")
        group = document.getObject("LeaderProjection")
        assert page is not None and owner is not None and group is not None
        page.ViewObject.show()
        _events(32)
        assert owner.findParentPage() is page
        assert owner in tuple(group.Views)
        assert set(names) <= {item.Name for item in page.Views}
        assert all(
            drawing_leader_state(document.getObject(name))["valid"]
            for name in names
        )
        assert set(names) <= {
            item.Name for item in owner.ViewObject.claimChildren()
        }

        print(
            "VIBECAD_NATIVE_DRAWING_LEADER_GUI_OK operations=2 "
            "human_oracle=true shared_host_builder=true projected_owner=true "
            "rotated_owner=true scaled_owner=true exact_page=true exact_owner=true "
            "absolute_points=true rendered_points=true auto_horizontal=true "
            "symbols=true behavior=true line_style=true defaults=true visual_hash=true "
            "tree=true history=true snapshot=true stale_page=true stale_owner=true "
            "invalid_geometry=true rollback=true undo=true redo=true reopen=true "
            "selection=true visibility=true closed_schema=true low_noise=true "
            "native_no_task=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
