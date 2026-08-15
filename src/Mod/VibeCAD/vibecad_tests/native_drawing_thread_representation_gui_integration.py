# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for all four Drawing thread actions."""

from __future__ import annotations

import itertools
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
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingDimensionSupport import drawing_selection_state
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingLineAttributeState import (
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineLengthState import (
    drawing_line_length_inventory_state,
)
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state
import VibeCADNativeDrawingThreadRepresentationRuntime as ThreadRuntimeModule
from VibeCADNativeDrawingThreadRepresentationSchema import (
    DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
    DRAWING_THREAD_REPRESENTATION_OPERATIONS,
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


def _create_fixture(document):
    document.openTransaction("Create Drawing thread fixture")
    transaction = int(document.getBookedTransactionID())
    try:
        source = document.addObject("Part::Feature", "ThreadSource")
        source.Label = "Thread Representation Source"
        first = Part.makeLine(
            App.Vector(-25.0, -10.0, 0.0),
            App.Vector(25.0, -10.0, 0.0),
        )
        second = Part.makeLine(
            App.Vector(-25.0, 10.0, 0.0),
            App.Vector(25.0, 10.0, 0.0),
        )
        diagonal = Part.makeLine(
            App.Vector(-20.0, -28.0, 0.0),
            App.Vector(18.0, -20.0, 0.0),
        )
        circle_one = Part.makeCircle(
            6.0,
            App.Vector(-20.0, 31.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
        )
        circle_two = Part.makeCircle(
            8.0,
            App.Vector(20.0, 31.0, 0.0),
            App.Vector(0.0, 0.0, 1.0),
        )
        source.Shape = Part.makeCompound(
            [first, second, diagonal, circle_one, circle_two]
        )
        document.publishProvisionalTimelineOperationBlock(source, (), ())

        page = document.addObject("TechDraw::DrawPage", "ThreadPage")
        page.Label = "Thread Representation Page"
        template = document.addObject("TechDraw::DrawSVGTemplate", "ThreadTemplate")
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

        view = document.addObject("TechDraw::DrawViewPart", "ThreadView")
        view.Label = "Thread Representation View"
        view.Source = [source]
        view.Direction = App.Vector(0.0, 0.0, 1.0)
        view.XDirection = App.Vector(1.0, 0.0, 0.0)
        view.ScaleType = "Custom"
        view.Scale = 1.35
        view.Rotation = 7.0
        view.X = 108.0
        view.Y = 78.0
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
    projection = drawing_projected_geometry_state(view)
    circles = tuple(
        item
        for item in projection["elements"]
        if item["element_type"] == "edge"
        and item.get("closed") is True
        and "center_in_view_mm" in item
    )
    straight = tuple(
        item
        for item in projection["elements"]
        if item["element_type"] == "edge" and "center_in_view_mm" not in item
    )
    assert len(circles) == 2, projection
    assert len(straight) == 3, projection
    parallel_names = None
    for first_item, second_item in itertools.combinations(straight, 2):
        names = [first_item["name"], second_item["name"]]
        try:
            TechDrawGui.validateDrawingThreadSide(view, "hole_side", names)
        except Exception:
            continue
        parallel_names = tuple(names)
        break
    assert parallel_names is not None
    diagonal_name = next(
        item["name"] for item in straight if item["name"] not in parallel_names
    )
    return (
        source,
        page,
        view,
        parallel_names,
        tuple(item["name"] for item in circles),
        diagonal_name,
    )


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(DRAWING_THREAD_REPRESENTATION_OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024
    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_THREAD_REPRESENTATION_OPERATIONS
    )
    assert all(branch["additionalProperties"] is False for branch in branches)
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _elements(view, names):
    by_name = {
        item["name"]: item
        for item in drawing_projected_geometry_state(view)["elements"]
    }
    return tuple(by_name[name] for name in names)


def _arguments(page, view, operation, names) -> dict:
    page_state = drawing_page_state(page)
    view_state = drawing_view_state(view)
    projection = drawing_projected_geometry_state(view)
    by_name = {item["name"]: item for item in projection["elements"]}
    field = "boundary_edges" if operation.endswith("side") else "circles"
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
        field: [
            {
                "subelement": name,
                "expected_element_state_sha256": by_name[name]["element_state_sha256"],
            }
            for name in names
        ],
    }


def _selection():
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _side_signatures(attributes, lengths, tags):
    attribute_by_tag = {line["tag"]: line for line in attributes["lines"]}
    length_by_tag = {line["tag"]: line for line in lengths["lines"]}
    return sorted(
        json.dumps(
            {
                "format": attribute_by_tag[tag]["format"],
                "start": length_by_tag[tag]["start_in_view_mm"],
                "end": length_by_tag[tag]["end_in_view_mm"],
                "length_mm": length_by_tag[tag]["length_mm"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for tag in tags
    )


def _bottom_signatures(view, tags):
    result = []
    for tag in tags:
        arc = dict(TechDrawGui.drawingPersistentCosmeticArc(view, tag))
        result.append(
            json.dumps(
                {
                    key: value
                    for key, value in arc.items()
                    if key not in {"tag", "subelement"}
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return sorted(result)


def _human_oracle(document, view, operation, names):
    actions = {
        "create_hole_side": "TechDraw_ExtensionThreadHoleSide",
        "create_hole_bottom": "TechDraw_ExtensionThreadHoleBottom",
        "create_bolt_side": "TechDraw_ExtensionThreadBoltSide",
        "create_bolt_bottom": "TechDraw_ExtensionThreadBoltBottom",
    }
    before_attributes = drawing_line_attribute_inventory_state(view)
    before_lengths = drawing_line_length_inventory_state(view)
    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(view, name)
    Gui.runCommand(actions[operation])
    _events(20)
    assert not Gui.Control.activeDialog()
    assert not Gui.Selection.getSelectionEx()
    attributes = drawing_line_attribute_inventory_state(view)
    lengths = drawing_line_length_inventory_state(view)
    old_tags = {line["tag"] for line in before_attributes["lines"]}
    tags = {line["tag"] for line in attributes["lines"] if line["tag"] not in old_tags}
    expected = (
        3
        if operation == "create_hole_side"
        else (2 if operation == "create_bolt_side" else len(names))
    )
    assert len(tags) == expected
    if operation.endswith("side"):
        assert lengths["line_count"] == before_lengths["line_count"] + expected
        signature = _side_signatures(attributes, lengths, tags)
    else:
        assert (
            lengths["inventory_state_sha256"]
            == before_lengths["inventory_state_sha256"]
        )
        signature = _bottom_signatures(view, tags)
    document.undo()
    _events(16)
    assert (
        drawing_line_attribute_inventory_state(view)["inventory_state_sha256"]
        == before_attributes["inventory_state_sha256"]
    )
    assert (
        drawing_line_length_inventory_state(view)["inventory_state_sha256"]
        == before_lengths["inventory_state_sha256"]
    )
    return signature


def _native_signature(view, result):
    if result["kind"].endswith("side"):
        return sorted(
            json.dumps(
                {
                    "format": line["line_format"],
                    "start": line["start_in_view_mm"],
                    "end": line["end_in_view_mm"],
                    "length_mm": line["length_mm"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for line in result["lines"]
        )
    return _bottom_signatures(
        view,
        {item["arc"]["tag"] for item in result["threads"]},
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-drawing-thread-representation-"
        )
        save_path = Path(temporary.name) / "thread-representations.FCStd"
        controller, surface = _surface()
        action_plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        expected_actions = {
            "TechDraw_ExtensionThreadHoleSide": (
                "create_hole_side",
                "ExactDrawingParallelHoleBoundariesAndSideThreadLines",
            ),
            "TechDraw_ExtensionThreadHoleBottom": (
                "create_hole_bottom",
                "ExactDrawingFullHoleCirclesAndBottomThreadArcs",
            ),
            "TechDraw_ExtensionThreadBoltSide": (
                "create_bolt_side",
                "ExactDrawingParallelBoltBoundariesAndSideThreadLines",
            ),
            "TechDraw_ExtensionThreadBoltBottom": (
                "create_bolt_bottom",
                "ExactDrawingFullBoltCirclesAndBottomThreadArcs",
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
                DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
                operation,
                target_type,
                "document",
                False,
            )

        document = App.newDocument("NativeDrawingThreadGate")
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        (
            source,
            page,
            view,
            side_names,
            circle_names,
            diagonal_name,
        ) = _create_fixture(document)
        objects_before = tuple(document.Objects)
        views_before = tuple(page.Views)
        history_before = tuple(document.VibeCADTimeline.Operations)
        visibility_before = (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )
        operation_sources = {
            "create_hole_side": side_names,
            "create_hole_bottom": circle_names,
            "create_bolt_side": side_names,
            "create_bolt_bottom": circle_names,
        }
        human = {
            operation: _human_oracle(
                document,
                view,
                operation,
                names,
            )
            for operation, names in operation_sources.items()
        }
        assert drawing_line_attribute_inventory_state(view)["line_count"] == 0

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-thread-representation-gui")

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
        Gui.Selection.addSelection(view, circle_names[0])
        selection_before = _selection()
        snapshot = build_drawing_snapshot(
            document,
            selection=drawing_selection_state(document),
        )
        selected = snapshot["selected_projected_geometry"]
        assert len(selected) == 1
        assert selected[0]["selected_elements"][0]["name"] == circle_names[0]

        first_arguments = None
        all_tags = set()
        for operation, names in operation_sources.items():
            arguments = _arguments(page, view, operation, names)
            if first_arguments is None:
                first_arguments = arguments
            revision_before = state_store.current_revision(str(document.Uid))
            call_id = f"native-drawing-thread-{operation}"
            response = dispatcher.call(
                DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
                json.dumps(arguments),
                call_id,
            )
            assert response["ok"] is True, response
            assert response["operation"] == operation
            result = response["thread_representation"]
            assert result["kind"] == operation.removeprefix("create_")
            assert _native_signature(view, result) == human[operation]
            assert len(json.dumps(response, separators=(",", ":")).encode()) < 14 * 1024
            assert (
                state_store.current_revision(str(document.Uid)) == revision_before + 1
            )
            assert _selection() == selection_before
            assert not Gui.Control.activeDialog()
            repeated = dispatcher.call(
                DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
                json.dumps(arguments),
                call_id,
            )
            assert repeated == response
            if operation.endswith("side"):
                all_tags.update(line["tag"] for line in result["lines"])
            else:
                all_tags.update(item["arc"]["tag"] for item in result["threads"])

        attributes = drawing_line_attribute_inventory_state(view)
        lengths = drawing_line_length_inventory_state(view)
        assert attributes["line_count"] == 9
        assert attributes["cosmetic_edge_count"] == 9
        assert lengths["line_count"] == 5
        assert {line["tag"] for line in attributes["lines"]} == all_tags

        refusal_revision = state_store.current_revision(str(document.Uid))
        refusal_undo = int(document.UndoCount)
        assert first_arguments is not None
        stale = dispatcher.call(
            DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
            json.dumps(first_arguments),
            "native-drawing-thread-stale",
        )
        assert stale["ok"] is False
        assert stale["error_code"] in {
            "NATIVE_DRAWING_THREAD_VIEW_STALE",
            "NATIVE_DRAWING_THREAD_PROJECTION_STALE",
        }
        wrong_type = dispatcher.call(
            DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
            json.dumps(
                _arguments(
                    page,
                    view,
                    "create_bolt_bottom",
                    (side_names[0],),
                )
            ),
            "native-drawing-thread-wrong-bottom-type",
        )
        assert wrong_type["ok"] is False
        assert wrong_type["error_code"] == "NATIVE_DRAWING_THREAD_REFERENCES_INVALID"
        nonparallel = dispatcher.call(
            DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
            json.dumps(
                _arguments(
                    page,
                    view,
                    "create_hole_side",
                    (side_names[0], diagonal_name),
                )
            ),
            "native-drawing-thread-nonparallel",
        )
        assert nonparallel["ok"] is False
        assert nonparallel["error_code"] == "NATIVE_DRAWING_THREAD_REFERENCES_INVALID"
        assert state_store.current_revision(str(document.Uid)) == refusal_revision
        assert int(document.UndoCount) == refusal_undo
        assert (
            drawing_line_attribute_inventory_state(view)["inventory_state_sha256"]
            == attributes["inventory_state_sha256"]
        )

        rollback_revision = state_store.current_revision(str(document.Uid))
        rollback_undo = int(document.UndoCount)
        original_verify = ThreadRuntimeModule.verify_drawing_thread_representation

        def fail_verify(_document, _draft):
            raise RuntimeError("Injected thread-representation verification failure")

        ThreadRuntimeModule.verify_drawing_thread_representation = fail_verify
        try:
            rollback = dispatcher.call(
                DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
                json.dumps(
                    _arguments(
                        page,
                        view,
                        "create_bolt_bottom",
                        circle_names,
                    )
                ),
                "native-drawing-thread-rollback",
            )
        finally:
            ThreadRuntimeModule.verify_drawing_thread_representation = original_verify
        _events(16)
        assert rollback["ok"] is False
        assert rollback["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        assert (
            drawing_line_attribute_inventory_state(view)["inventory_state_sha256"]
            == attributes["inventory_state_sha256"]
        )
        assert state_store.current_revision(str(document.Uid)) == rollback_revision
        assert int(document.UndoCount) == rollback_undo
        assert _selection() == selection_before

        assert tuple(document.Objects) == objects_before
        assert tuple(page.Views) == views_before
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        assert visibility_before == (
            bool(source.ViewObject.Visibility),
            bool(view.ViewObject.Visibility),
            bool(page.ViewObject.Visibility),
        )

        document.undo()
        _events(16)
        undone = drawing_line_attribute_inventory_state(view)
        assert undone["line_count"] == 7
        document.redo()
        _events(16)
        redone = drawing_line_attribute_inventory_state(view)
        assert redone["inventory_state_sha256"] == attributes["inventory_state_sha256"]

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
        reopened = drawing_line_attribute_inventory_state(view)
        assert reopened["inventory_state_sha256"] == redone["inventory_state_sha256"]
        assert {line["tag"] for line in reopened["lines"]} == all_tags
        for tag in all_tags:
            if tag not in {
                line["tag"]
                for line in drawing_line_length_inventory_state(view)["lines"]
            }:
                assert TechDrawGui.drawingPersistentCosmeticArc(view, tag)["tag"] == tag

        print(
            "VIBECAD_NATIVE_DRAWING_THREAD_REPRESENTATION_GUI_OK operations=4 "
            "hole_side=true hole_bottom=true bolt_side=true bolt_bottom=true "
            "human_oracle=true shared_host_builder=true exact_page=true "
            "exact_view=true projection_hash=true element_hash=true "
            "parallel_validation=true full_circle_validation=true factors=true "
            "arc_span=true persistent_tags=true host_style=true selection=true "
            "visibility=true history=true wrong_type=true nonparallel=true "
            "stale=true rollback=true revision=true undo=true redo=true "
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
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
