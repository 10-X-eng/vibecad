# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Drawing page operations."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

import VibeCADGui as VibeGui
import VibeCADNativeDrawingPageRuntime as DrawingPageRuntimeModule
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeDrawingPageSchema import DRAWING_PAGE_CAPABILITY_NAMES
from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
from VibeCADNativeDrawingState import drawing_page_state, template_content_state
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSnapshot import build_active_snapshot
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


PAGE_TOOLS = (
    DRAWING_PAGE_CAPABILITY_NAMES[0],
    DRAWING_PAGE_CAPABILITY_NAMES[1],
    DRAWING_PAGE_CAPABILITY_NAMES[2],
    DRAWING_PAGE_CAPABILITY_NAMES[4],
)
PAGE_TOOL_BY_OPERATION = {
    "page_default": PAGE_TOOLS[0],
    "page_template": PAGE_TOOLS[1],
    "fill_template_fields": PAGE_TOOLS[2],
    "set_keep_updated": PAGE_TOOLS[3],
}


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
    return controller, surface


def _selection() -> tuple:
    return tuple(
        (item.Object.Name, tuple(item.SubElementNames))
        for item in Gui.Selection.getSelectionEx()
    )


def _create_anchor(document):
    document.openTransaction("Create anchor")
    transaction = int(document.getBookedTransactionID())
    try:
        anchor = document.addObject("Part::Feature", "Anchor")
        anchor.Shape = Part.makeBox(10.0, 8.0, 4.0)
        document.publishProvisionalTimelineOperationBlock(anchor, (), ())
        assert document.recompute([anchor], True, True) is not False
    except Exception:
        App.closeActiveTransaction(True, transaction)
        raise
    App.closeActiveTransaction(False, transaction)
    return anchor


def _human_default_page_hash() -> str:
    document = App.newDocument("NativeDrawingHumanParity")
    try:
        document.UndoMode = 1
        Gui.runCommand("TechDraw_PageDefault")
        _events(16)
        pages = document.findObjects(Type="TechDraw::DrawPage")
        assert len(pages) == 1
        page = pages[0]
        template = page.Template
        assert template is not None
        assert str(page.VibeCADTimelineRole) == "operation"
        assert str(template.VibeCADTimelineRole) == "resource"
        assert template.VibeCADTimelineOwner is page
        content = template_content_state(template)
        assert content["available"] is True
        return content["sha256"]
    finally:
        App.closeDocument(document.Name)
        _events(8)


def _turn(surface, registry) -> NativeTurnSnapshot:
    schemas = []
    for operation, tool_name in PAGE_TOOL_BY_OPERATION.items():
        definition = registry.definition(tool_name)
        assert definition is not None
        schemas.append(definition.provider_schema((operation,)))
    encoded = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "expected_state_sha256" in encoded
    assert "expected_value" in encoded
    assert "path" not in encoded.casefold()
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=PAGE_TOOLS,
            schemas=tuple(schemas),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-page-")
        temporary_path = Path(temporary.name)
        custom_template = temporary_path / "human-template.svg"
        drift_template = temporary_path / "drift-template.svg"
        source_template = Path(App.getResourceDir()) / (
            "Mod/TechDraw/Templates/ISO/A4_Landscape_ISO5457_minimal.svg"
        )
        shutil.copyfile(source_template, custom_template)
        shutil.copyfile(source_template, drift_template)
        save_path = temporary_path / "native-drawing-pages.FCStd"

        controller, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        assert {
            command_id: (
                plans[command_id].capability_family,
                plans[command_id].operation_variant,
                plans[command_id].exact_target_type,
            )
            for command_id in (
                "TechDraw_PageDefault",
                "TechDraw_PageTemplate",
                "TechDraw_FillTemplateFields",
            )
        } == {
            "TechDraw_PageDefault": (
                PAGE_TOOL_BY_OPERATION["page_default"],
                "page_default",
                "NewDrawingPageWithConfiguredTemplate",
            ),
            "TechDraw_PageTemplate": (
                PAGE_TOOL_BY_OPERATION["page_template"],
                "page_template",
                "HumanAuthorizedSvgTemplateForNewDrawingPage",
            ),
            "TechDraw_FillTemplateFields": (
                PAGE_TOOL_BY_OPERATION["fill_template_fields"],
                "fill_template_fields",
                "ExactDrawingPageAndEditableTemplateFields",
            ),
        }
        human_default_hash = _human_default_page_hash()
        controller, surface = _surface()

        document = App.newDocument("NativeDrawingPageGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        anchor = _create_anchor(document)
        anchor.ViewObject.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(anchor, "Edge1")
        selection_before = _selection()
        visibility_before = bool(anchor.ViewObject.Visibility)
        names_before = tuple(obj.Name for obj in document.Objects)
        history_before = tuple(document.VibeCADTimeline.Operations)

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-drawing-page-gui")
        input_mode = {"kind": "custom"}
        authorizations = {"count": 0}

        def authorize(request):
            authorizations["count"] += 1
            path = custom_template
            if input_mode["kind"] == "drift":
                path = drift_template
                authorization = authorize_native_input_path(request, path)
                path.write_bytes(path.read_bytes() + b"\n")
                return authorization
            if input_mode["kind"] == "cancel":
                return None
            return authorize_native_input_path(request, path)

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
            authorize_input=authorize,
        )
        def refresh_dispatcher() -> NativeTurnDispatcher:
            nonlocal turn, frozen
            turn = _turn(surface, registry)
            frozen = turn.surface
            return NativeTurnDispatcher(
                document=document,
                state=state_store,
                registry=registry,
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = refresh_dispatcher()
        call_index = 0

        def call(arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                PAGE_TOOL_BY_OPERATION[str(arguments.get("operation") or "")],
                json.dumps(arguments, separators=(",", ":")),
                f"native-drawing-page-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        invalid = call(
            {"operation": "page_default", "path": "/tmp/not-authorized.svg"},
            succeeds=False,
        )
        assert invalid["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == names_before

        undo_before = int(document.UndoCount)
        default_result = call({"operation": "page_default"})
        _events(12)
        default_page_name = default_result["page"]["object_name"]
        default_page = document.getObject(default_page_name)
        assert default_page is not None
        assert default_result["page"]["template_content"]["sha256"] == human_default_hash
        assert "path" not in json.dumps(default_result).casefold()
        assert authorizations["count"] == 0
        assert int(document.UndoCount) == undo_before + 1
        assert _selection() == selection_before
        assert bool(anchor.ViewObject.Visibility) is visibility_before
        assert not Gui.Control.activeDialog()
        default_state = drawing_page_state(default_page)

        document.undo()
        _events(10)
        assert document.getObject(default_page_name) is None
        assert tuple(document.VibeCADTimeline.Operations) == history_before
        document.redo()
        _events(10)
        default_page = document.getObject(default_page_name)
        assert default_page is not None
        assert drawing_page_state(default_page)["state_sha256"] == default_state["state_sha256"]
        dispatcher = refresh_dispatcher()

        input_mode["kind"] = "cancel"
        cancelled = call({"operation": "page_template"}, succeeds=False)
        assert cancelled["error_code"] == "NATIVE_DRAWING_TEMPLATE_INPUT_CANCELLED", cancelled
        pages_before_custom = tuple(document.findObjects(Type="TechDraw::DrawPage"))

        input_mode["kind"] = "drift"
        drifted = call({"operation": "page_template"}, succeeds=False)
        assert drifted["error_code"] == "NATIVE_INPUT_AUTHORIZATION_FAILED"
        assert tuple(document.findObjects(Type="TechDraw::DrawPage")) == pages_before_custom

        input_mode["kind"] = "custom"
        custom_result = call({"operation": "page_template"})
        _events(12)
        custom_page_name = custom_result["page"]["object_name"]
        custom_page = document.getObject(custom_page_name)
        assert custom_page is not None and custom_page is not default_page
        assert custom_result["template_input"]["source"] == "human_authorized"
        assert custom_result["page"]["editable_field_count"] >= 8
        assert "path" not in json.dumps(custom_result).casefold()
        assert _selection() == selection_before
        custom_state = drawing_page_state(custom_page)
        fields = {item["field_name"]: item["value"] for item in custom_state["editable_fields"]}
        selected_fields = tuple(sorted(fields)[:2])

        snapshot = build_drawing_snapshot(
            document,
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 1,
                "items": [
                    {
                        "object": {
                            "document_uid": str(document.Uid),
                            "object_name": custom_page.Name,
                            "type_id": custom_page.TypeId,
                        }
                    }
                ],
            },
        )
        assert snapshot["active_page_resolution"] == "selection"
        assert snapshot["active_page"]["object_name"] == custom_page.Name
        assert snapshot["active_page"]["state_sha256"] == custom_state["state_sha256"]

        stale_arguments = {
            "operation": "fill_template_fields",
            "page": {
                "object_name": custom_page.Name,
                "expected_state_sha256": "0" * 64,
            },
            "updates": [
                {
                    "field_name": selected_fields[0],
                    "expected_value": fields[selected_fields[0]],
                    "value": "Native stale value",
                }
            ],
        }
        stale = call(stale_arguments, succeeds=False)
        assert stale["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        updates = [
            {
                "field_name": name,
                "value": f"Native {index + 1}",
            }
            for index, name in enumerate(selected_fields)
        ]
        edit_arguments = {
            "operation": "fill_template_fields",
            "page": {
                "object_name": custom_page.Name,
                "expected_state_sha256": custom_state["state_sha256"],
            },
            "updates": updates,
        }
        original_verify = DrawingPageRuntimeModule.verify_template_field_edit

        def fail_verify(_document, _draft):
            raise RuntimeError("injected Drawing postcondition failure")

        DrawingPageRuntimeModule.verify_template_field_edit = fail_verify
        try:
            rolled_back = call(edit_arguments, succeeds=False)
        finally:
            DrawingPageRuntimeModule.verify_template_field_edit = original_verify
        assert rolled_back["error_code"] == "NATIVE_POSTCONDITION_FAILED"
        custom_page = document.getObject(custom_page_name)
        assert drawing_page_state(custom_page)["state_sha256"] == custom_state["state_sha256"]

        edited = call(edit_arguments)
        _events(12)
        assert edited["changed_field_count"] == 2
        assert edited["changed_fields"] == list(selected_fields)
        assert edited["assistant_undo_available"] is True
        edited_state = drawing_page_state(custom_page)
        edited_fields = {
            item["field_name"]: item["value"]
            for item in edited_state["editable_fields"]
        }
        assert [edited_fields[name] for name in selected_fields] == [
            "Native 1",
            "Native 2",
        ]
        operations_after_edit = tuple(document.VibeCADTimeline.Operations)

        document.undo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert drawing_page_state(custom_page)["state_sha256"] == custom_state["state_sha256"]
        document.redo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert drawing_page_state(custom_page)["state_sha256"] == edited_state["state_sha256"]
        assert tuple(document.VibeCADTimeline.Operations) == operations_after_edit

        custom_page.ViewObject.show()
        _events(16)
        keep_updated_action = Gui.getMainWindow().findChild(
            QtGui.QAction,
            "TechDrawContextToggleKeepUpdated",
        )
        assert keep_updated_action is not None
        keep_updated_before = bool(custom_page.KeepUpdated)
        human_undo_before = int(document.UndoCount)
        keep_updated_action.trigger()
        _events(12)
        assert bool(custom_page.KeepUpdated) is not keep_updated_before
        assert int(document.UndoCount) == human_undo_before + 1
        document.undo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert bool(custom_page.KeepUpdated) is keep_updated_before
        document.redo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert bool(custom_page.KeepUpdated) is not keep_updated_before
        keep_updated_action.trigger()
        _events(12)
        assert bool(custom_page.KeepUpdated) is keep_updated_before
        dispatcher = refresh_dispatcher()

        keep_updated_state = drawing_page_state(custom_page)
        update_arguments = {
            "operation": "set_keep_updated",
            "page": {
                "object_name": custom_page.Name,
                "expected_state_sha256": keep_updated_state["state_sha256"],
            },
            "keep_updated": not keep_updated_before,
        }
        update_result = call(update_arguments)
        _events(12)
        assert update_result["operation"] == "set_keep_updated"
        assert update_result["previous_keep_updated"] is keep_updated_before
        assert update_result["keep_updated"] is not keep_updated_before
        assert update_result["changed"] is True
        assert update_result["page"]["keep_updated"] is not keep_updated_before
        updated_page_state = drawing_page_state(custom_page)
        assert update_result["page"]["state_sha256"] == updated_page_state[
            "state_sha256"
        ]
        stale_update = call(update_arguments, succeeds=False)
        assert stale_update["error_code"] == "NATIVE_DRAWING_PAGE_STALE"

        document.undo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert bool(custom_page.KeepUpdated) is keep_updated_before
        document.redo()
        _events(10)
        custom_page = document.getObject(custom_page_name)
        assert bool(custom_page.KeepUpdated) is not keep_updated_before
        assert drawing_page_state(custom_page)["state_sha256"] == updated_page_state[
            "state_sha256"
        ]

        native_state = state_store.snapshot(context.document_uid)
        active = build_active_snapshot(
            document,
            "drawing",
            native_state,
            selection={
                "document_uid": str(document.Uid),
                "selected_count": 1,
                "items": [
                    {
                        "object": {
                            "document_uid": str(document.Uid),
                            "object_name": custom_page.Name,
                            "type_id": custom_page.TypeId,
                        }
                    }
                ],
            },
        )
        assert active["domain"]["active_page"]["state_sha256"] == updated_page_state[
            "state_sha256"
        ]
        assert active["domain"]["active_page"]["keep_updated"] is not keep_updated_before
        assert len(json.dumps(active, separators=(",", ":")).encode()) < 64 * 1024

        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(save_path))
        _events(16)
        reopened = document.getObject(custom_page_name)
        assert reopened is not None
        assert drawing_page_state(reopened)["state_sha256"] == updated_page_state[
            "state_sha256"
        ]
        assert bool(reopened.KeepUpdated) is not keep_updated_before
        assert str(reopened.VibeCADTimelineRole) == "operation"
        assert str(reopened.Template.VibeCADTimelineRole) == "resource"
        assert reopened.Template.VibeCADTimelineOwner is reopened

        print(
            "VIBECAD_NATIVE_DRAWING_PAGE_GUI_OK "
            "default=true human_parity=true custom=true human_authorized=true "
            "path_private=true exact_fields=true active_page=true closed_schema=true "
            "keep_updated=true explicit_state=true human_context_oracle=true "
            "cancel=true file_drift=true stale=true rollback=true selection=true "
            "visibility=true history=true undo=true redo=true reopen=true low_noise=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
