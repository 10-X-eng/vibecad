# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Drawing rich annotations."""

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
from VibeCADNativeDrawingRichAnnotation import (
    drawing_rich_annotation_defaults_state,
)
from VibeCADNativeDrawingRichAnnotationSchema import (
    DRAWING_NOTE_CAPABILITY_NAMES,
)
from VibeCADNativeDrawingRichAnnotationState import (
    drawing_rich_annotation_owner_state,
    drawing_rich_annotation_state,
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


def _scene_item_state(object_name: str) -> dict:
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    subwindow = mdi.activeSubWindow() if mdi is not None else None
    graphics = subwindow.findChild(QtWidgets.QGraphicsView) if subwindow else None
    assert graphics is not None and graphics.scene() is not None
    matches = [
        item
        for item in graphics.scene().items()
        if str(item.data(1) or "") == object_name
    ]
    if not matches:
        return {"attached": False}
    item = matches[0]
    bounds = item.mapRectToScene(item.boundingRect())
    return {
        "attached": True,
        "visible": bool(item.isVisible()),
        "position": [float(item.scenePos().x()), float(item.scenePos().y())],
        "bounds": [
            float(bounds.x()),
            float(bounds.y()),
            float(bounds.width()),
            float(bounds.height()),
        ],
    }


def _create_fixture(document):
    document.openTransaction("Create Drawing rich annotation fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "AnnotationSource")
        source.Label = "Annotation Source"
        source.Shape = Part.makeBox(40.0, 24.0, 8.0)
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        page = document.addObject("TechDraw::DrawPage", "AnnotationPage")
        page.Label = "Annotation Page"
        template = document.addObject(
            "TechDraw::DrawSVGTemplate", "AnnotationTemplate"
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
        view = document.addObject("TechDraw::DrawViewPart", "AnnotationView")
        view.Label = "Annotation View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.0
        view.X = 100.0
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
    _events(28)
    return source, page, view


def _human_annotation(document, page, view):
    before = tuple(document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(view)
    Gui.runCommand("TechDraw_RichTextAnnotation")
    _events(16)
    assert Gui.Control.activeDialog()
    mdi = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    subwindow = mdi.activeSubWindow() if mdi is not None else None
    graphics = subwindow.findChild(QtWidgets.QGraphicsView) if subwindow else None
    assert graphics is not None
    viewport = graphics.viewport()
    point = QtCore.QPoint(viewport.width() // 2, viewport.height() // 2)
    event = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QPointF(point),
        QtCore.QPointF(viewport.mapToGlobal(point)),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(viewport, event)
    _events(20)
    task = Gui.Control.activeTaskDialog()
    assert task is not None
    task.accept()
    _events(20)
    assert not Gui.Control.activeDialog()
    created = [item for item in document.Objects if item not in before]
    assert len(created) == 1
    annotation = created[0]
    state = drawing_rich_annotation_state(annotation)
    assert state["owner"]["object_name"] == view.Name
    assert annotation in tuple(page.Views)
    assert annotation in tuple(document.VibeCADTimeline.Operations)
    return annotation


def _turn(surface, registry) -> NativeTurnSnapshot:
    definitions = tuple(registry.definition(name) for name in DRAWING_NOTE_CAPABILITY_NAMES)
    assert all(definition is not None for definition in definitions)
    schemas = tuple(
        definition.provider_schema(("create",)) for definition in definitions
    )
    plain, rich = (schema["parameters"]["oneOf"][0] for schema in schemas)
    assert "text" in plain["required"] and "html" not in plain["properties"]
    assert "html" in rich["required"] and "text" not in rich["properties"]
    assert plain["properties"]["owner"]["default"] == "page"
    assert plain["properties"]["width"]["default"] == "automatic"
    encoded = "".join(
        json.dumps(schema, sort_keys=True, separators=(",", ":"))
        for schema in schemas
    )
    assert "unknown" not in encoded.casefold()
    assert "file_path" not in encoded.casefold()
    assert "data_url" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=DRAWING_NOTE_CAPABILITY_NAMES,
            schemas=schemas,
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _arguments(
    page,
    owner,
    defaults: dict,
    *,
    operation: str,
    content: str,
    label: str,
    x_mm: float,
    y_mm: float,
) -> dict:
    owner_target = "page"
    if owner is not None:
        owner_state = drawing_rich_annotation_owner_state(owner, page=page)
        owner_target = {
            "object_name": owner.Name,
            "expected_owner_state_sha256": owner_state["owner_state_sha256"],
        }
    result = {
        "operation": "create",
        "page": {
            "object_name": page.Name,
            "expected_state_sha256": drawing_page_state(page)["state_sha256"],
        },
        "owner": owner_target,
        "label": label,
        "placement_on_page_mm": {"x_mm": x_mm, "y_mm": y_mm},
    }
    if defaults["width"]["mode"] == "automatic":
        result["width"] = "automatic"
    else:
        result["width"] = defaults["width"]["value_mm"]
    result["frame"] = json.loads(json.dumps(defaults["frame"]))
    result["text" if operation == "create_plain_text" else "html"] = content
    return result


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-rich-annotation-"
        )
        save_path = Path(temporary.name) / "drawing-rich-annotation.FCStd"
        controller, surface = _surface()
        plan = next(
            item
            for item in resolve_native_action_inventory(surface).plans
            if item.command_id == "TechDraw_RichTextAnnotation"
        )
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
        ) == (
            "drawing.note",
            "create",
            "ExactDrawingPageOwnerPlainTextPlacementWidthAndFrame",
        )

        document = App.newDocument("NativeDrawingRichAnnotationGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        source, page, view = _create_fixture(document)
        image_before_human = _page_image_sha256()
        human = _human_annotation(document, page, view)
        assert drawing_rich_annotation_state(human)["valid"]
        assert _page_image_sha256() != image_before_human
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(view)
        _events(20)
        selection_before = _selection()
        visibility_before = tuple(
            bool(item.ViewObject.Visibility) for item in (source, page, view, human)
        )

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-rich-annotation-gui")

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
                "drawing.rich_note" if "html" in arguments else "drawing.note",
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-rich-annotation-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        revision_before_defaults = state_store.current_revision(str(document.Uid))
        defaults = drawing_rich_annotation_defaults_state()
        assert defaults == drawing_rich_annotation_defaults_state()
        assert state_store.current_revision(str(document.Uid)) == revision_before_defaults

        outside = _arguments(
            page,
            None,
            defaults,
            operation="create_plain_text",
            content="Outside drawing area",
            label="Outside Note",
            x_mm=-1.0,
            y_mm=52.0,
        )
        outside["width"] = "auto"
        rejected = call(outside, False)
        assert rejected["error_code"] == (
            "NATIVE_DRAWING_RICH_ANNOTATION_PLACEMENT_INVALID"
        )
        assert rejected["repair"]["requested_position_on_page_mm"] == {
            "x_mm": -1.0,
            "y_mm": 52.0,
        }

        image_before_plain = _page_image_sha256()
        plain_arguments = _arguments(
            page,
            None,
            defaults,
            operation="create_plain_text",
            content="CAUTION: deburr all edges before assembly.",
            label="Assembly Caution",
            x_mm=62.0,
            y_mm=52.0,
        )
        for optional in ("owner", "width", "frame"):
            plain_arguments.pop(optional)
        plain_response = call(plain_arguments)
        _events(20)
        plain_page_state = drawing_page_state(page)
        assert plain_response["page"] == {
            "object_name": plain_page_state["object_name"],
            "state_sha256": plain_page_state["state_sha256"],
            "view_count": plain_page_state["view_count"],
        }
        plain_name = plain_response["annotation"]["object_name"]
        assert plain_response["annotation"]["owner"] == {"kind": "page"}
        assert plain_response["annotation"]["content"]["plain_text_preview"].startswith(
            "CAUTION"
        )
        assert _page_image_sha256() != image_before_plain
        assert not Gui.Control.activeDialog()

        rich_arguments = _arguments(
            page,
            view,
            defaults,
            operation="create_rich_text",
            content=(
                '<p><b>Inspection:</b> follow the '
                '<a href="https://example.com/procedure">approved procedure</a>.</p>'
            ),
            label="Inspection Note",
            x_mm=178.0,
            y_mm=72.0,
        )
        rich_arguments["width"] = 54.0
        rich_arguments["frame"] = {
            "visible": True,
            "line_width_mm": 0.7,
            "line_style": "dash_dot",
            "color_rgb": {"red": 0.2, "green": 0.3, "blue": 0.8},
        }
        image_before_rich = _page_image_sha256()
        rich_response = call(rich_arguments)
        _events(20)
        rich_page_state = drawing_page_state(page)
        assert rich_response["page"] == {
            "object_name": rich_page_state["object_name"],
            "state_sha256": rich_page_state["state_sha256"],
            "view_count": rich_page_state["view_count"],
        }
        rich_name = rich_response["annotation"]["object_name"]
        assert rich_response["annotation"]["owner"]["object_name"] == view.Name
        assert rich_response["annotation"]["content"]["link_count"] == 1
        assert rich_response["annotation"]["content"]["has_rich_formatting"]
        assert _page_image_sha256() != image_before_rich, _scene_item_state(rich_name)

        stale = _arguments(
            page,
            view,
            defaults,
            operation="create_plain_text",
            content="Stale owner request",
            label="Stale Note",
            x_mm=40.0,
            y_mm=30.0,
        )
        stale["owner"]["expected_owner_state_sha256"] = "0" * 64
        rejected = call(stale, False)
        assert rejected["error_code"] == "NATIVE_DRAWING_RICH_ANNOTATION_OWNER_STALE"

        for unsafe_html in (
            '<p>Unsafe<img src="file:///tmp/private.png"></p>',
            '<p onclick="alert(1)">Unsafe event handler</p>',
            '<video src="https://example.com/private.mp4">Unsafe media</video>',
        ):
            unsafe = _arguments(
                page,
                None,
                defaults,
                operation="create_rich_text",
                content=unsafe_html,
                label="Unsafe Note",
                x_mm=40.0,
                y_mm=30.0,
            )
            rejected = call(unsafe, False)
            assert (
                rejected["error_code"]
                == "NATIVE_DRAWING_RICH_ANNOTATION_CONTENT_INVALID"
            )

        original_validate = TechDrawGui.validateDrawingRichAnnotation

        def malformed_host_plan(*args):
            raw = dict(original_validate(*args))
            raw["placement_on_page_mm"] = None
            return raw

        TechDrawGui.validateDrawingRichAnnotation = malformed_host_plan
        try:
            malformed = call(
                _arguments(
                    page,
                    None,
                    defaults,
                    operation="create_plain_text",
                    content="Malformed host plan request",
                    label="Malformed Host Note",
                    x_mm=40.0,
                    y_mm=30.0,
                ),
                False,
            )
        finally:
            TechDrawGui.validateDrawingRichAnnotation = original_validate
        assert (
            malformed["error_code"]
            == "NATIVE_DRAWING_RICH_ANNOTATION_RUNTIME_UNAVAILABLE"
        )

        objects_before_rollback = tuple(document.Objects)
        views_before_rollback = tuple(page.Views)
        history_before_rollback = tuple(document.VibeCADTimeline.Operations)
        original_create = TechDrawGui.createDrawingRichAnnotation

        def fail_after_create(*args):
            original_create(*args)
            raise RuntimeError("Injected rich annotation creation failure")

        TechDrawGui.createDrawingRichAnnotation = fail_after_create
        try:
            rolled_back = call(
                _arguments(
                    page,
                    None,
                    defaults,
                    operation="create_plain_text",
                    content="Rollback request",
                    label="Rollback Note",
                    x_mm=30.0,
                    y_mm=30.0,
                ),
                False,
            )
        finally:
            TechDrawGui.createDrawingRichAnnotation = original_create
        assert rolled_back["error_code"] == "NATIVE_DRAWING_RICH_ANNOTATION_CREATE_FAILED"
        assert tuple(document.Objects) == objects_before_rollback
        assert tuple(page.Views) == views_before_rollback
        assert tuple(document.VibeCADTimeline.Operations) == history_before_rollback

        expected_names = {human.Name, plain_name, rich_name}
        assert expected_names <= {item.Name for item in page.Views}
        assert expected_names <= {item.Name for item in document.VibeCADTimeline.Operations}
        claimed_by_page = {item.Name for item in page.ViewObject.claimChildren()}
        claimed_by_view = {item.Name for item in view.ViewObject.claimChildren()}
        assert plain_name in claimed_by_page
        assert {human.Name, rich_name} <= claimed_by_view
        assert {human.Name, rich_name}.isdisjoint(claimed_by_page)
        assert plain_name not in claimed_by_view
        snapshot = build_drawing_snapshot(
            document, selection=drawing_selection_state(document)
        )
        assert snapshot["rich_annotation_defaults"] == defaults
        snapshot_annotations = {
            item["object_name"]
            for item in snapshot["pages"][0]["views"]
            if "rich_annotation" in item
        }
        assert expected_names <= snapshot_annotations
        assert _selection() == selection_before
        assert tuple(
            bool(item.ViewObject.Visibility) for item in (source, page, view, human)
        ) == visibility_before
        assert not Gui.Control.activeDialog()
        assert len(json.dumps(rich_response, separators=(",", ":")).encode()) < 4096

        document.undo()
        _events(16)
        assert document.getObject(rich_name) is None
        document.redo()
        _events(20)
        assert drawing_rich_annotation_state(document.getObject(rich_name))["valid"]

        names = tuple(sorted(expected_names))
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        page = document.getObject("AnnotationPage")
        view = document.getObject("AnnotationView")
        assert page is not None and view is not None
        page.ViewObject.show()
        _events(28)
        assert set(names) <= {item.Name for item in page.Views}
        assert all(
            drawing_rich_annotation_state(document.getObject(name))["valid"]
            for name in names
        )
        assert rich_name in {item.Name for item in view.ViewObject.claimChildren()}

        print(
            "VIBECAD_NATIVE_DRAWING_RICH_ANNOTATION_GUI_OK operations=2 "
            "plain_text=true rich_text=true human_oracle=true "
            "shared_host_builder=true safe_html=true active_content_rejected=true "
            "resources_rejected=true malformed_host_rejected=true "
            "exact_page=true exact_owner=true explicit_placement=true "
            "semantic_width=true auto_alias=true drawing_bounds=true "
            "explicit_frame=true optional_defaults=true "
            "content_hash=true bounded_preview=true visual_hash=true tree=true "
            "history=true snapshot=true stale=true rollback=true undo=true "
            "redo=true reopen=true selection=true visibility=true "
            "closed_schema=true low_noise=true native_no_task=true",
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
