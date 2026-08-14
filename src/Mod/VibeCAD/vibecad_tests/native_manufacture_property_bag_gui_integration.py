# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native CAM Property Bags."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import Path.Base.Gui.PropertyBag as PathPropertyBagGui
import Path.Base.PropertyBag as PathPropertyBag
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeManufacturePropertyBag import (
    property_bag_destination_state,
    property_bag_snapshot,
)
from VibeCADNativeManufacturePropertyBagSchema import (
    MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
)
from VibeCADNativeManufacturePropertyBagValues import (
    is_property_bag,
    property_bag_state,
)
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
    Gui.activateWorkbench("CAMWorkbench")
    _events(24)
    controller = Gui.getMainWindow().findChild(
        QtCore.QObject,
        "VibeCADRibbonController",
    )
    assert controller is not None
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "manufacture"
    setup = next(group for group in surface.groups if group.label == "Setup")
    assert tuple(action.command_id for action in setup.actions) == (
        "CAM_Job",
        "CAM_PropertyBag",
        "CAM_Sanity",
        "CAM_PostTools",
    )
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    definition = registry.definition(MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME)
    assert definition is not None
    schema = definition.provider_schema(("create",))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 6_000
    variant = schema["parameters"]["oneOf"][0]
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {
        "operation",
        "label",
        "destination_body",
        "properties",
    }
    assert set(variant["properties"]) == set(variant["required"])
    item = variant["properties"]["properties"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "name",
        "group",
        "description",
        "typed_value",
    }
    value_variants = item["properties"]["typed_value"]["oneOf"]
    kinds = {branch["properties"]["kind"]["const"] for branch in value_variants}
    assert kinds == {
        "angle_degrees",
        "boolean",
        "distance_mm",
        "enumeration",
        "number",
        "integer",
        "length_mm",
        "percent",
        "string",
    }
    assert (
        "File properties remain available in the human editor"
        in (schema["description"])
    )
    assert not any(
        token in encoded.casefold() for token in ('"file_path"', '"filename"', '"path"')
    )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,),
            schemas=(schema,),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _destination(body) -> dict:
    state = property_bag_destination_state(body)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _property(name: str, kind: str, value, *, group="Machining") -> dict:
    return {
        "name": name,
        "group": group,
        "description": f"Exact {name} reference",
        "typed_value": {"kind": kind, "value": value},
    }


def _arguments(body) -> dict:
    properties = [
        _property("ApproachAngle", "angle_degrees", -12.5),
        _property("Enabled", "boolean", True),
        _property("DatumOffset", "distance_mm", -3.25),
        {
            "name": "ReleaseState",
            "group": "Identity",
            "description": "Controlled release state",
            "typed_value": {
                "kind": "enumeration",
                "options": ["Draft", "Reviewed", "Released"],
                "selected": "Reviewed",
            },
        },
        _property("ScaleFactor", "number", 1.25),
        _property("RevisionNumber", "integer", -7, group="Identity"),
        _property("StockAllowance", "length_mm", 24.5),
        _property("Completion", "percent", 37),
        _property("OperatorNote", "string", "fixture\nverified"),
    ]
    return {
        "operation": "create",
        "label": "Native Manufacturing Attributes",
        "destination_body": _destination(body),
        "properties": properties,
    }


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


def _timeline(document) -> tuple:
    timeline = document.VibeCADTimeline
    return (
        tuple(timeline.Operations),
        tuple(bool(value) for value in timeline.VisibilityAtEnd),
        tuple(bool(value) for value in timeline.SuppressionAtEnd),
        int(timeline.Position),
    )


def _accept_task() -> None:
    button = None
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not button_box.isVisible():
            continue
        candidate = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if candidate is not None and candidate.isVisible() and candidate.isEnabled():
            button = candidate
            break
    assert button is not None
    button.click()
    _events(20)
    assert not Gui.Control.activeDialog()


def _human_editor_gate(document, secret_path: str):
    Gui.Selection.clearSelection()
    before = tuple(document.Objects)
    before_undo = int(document.UndoCount)
    assert Gui.isCommandActive("CAM_PropertyBag")
    Gui.runCommand("CAM_PropertyBag")
    _events(20)
    assert Gui.Control.activeDialog()
    created = [obj for obj in document.Objects if obj not in before]
    assert len(created) == 1
    bag = created[0]
    assert isinstance(bag.Proxy, PathPropertyBag.PropertyBag)
    assert isinstance(bag.ViewObject.Proxy, PathPropertyBagGui.ViewProvider)
    assert "File" in PathPropertyBag.SupportedPropertyType
    bag.Proxy.addCustomProperty(
        "App::PropertyFile",
        "HumanFileReference",
        "Human",
        "Human-authorized file reference",
    )
    bag.HumanFileReference = secret_path
    _accept_task()
    assert document.getObject(bag.Name) is bag
    assert int(document.UndoCount) == before_undo + 1
    snapshot_json = json.dumps(property_bag_snapshot(document), sort_keys=True)
    assert secret_path not in snapshot_json
    assert "path_sha256" not in snapshot_json
    return bag


def _assert_property_bag(bag, body) -> None:
    assert isinstance(bag.Proxy, PathPropertyBag.PropertyBag)
    assert isinstance(bag.ViewObject.Proxy, PathPropertyBagGui.ViewProvider)
    assert bag.getParentGeoFeatureGroup() is body
    assert tuple(body.Group)[-1] is bag
    assert str(bag.VibeCADTimelineRole) == "operation"
    assert getattr(bag, "VibeCADTimelineOwner", None) is None
    assert tuple(getattr(bag, "VibeCADTimelineReplacedInputs", ()) or ()) == ()
    state = property_bag_state(bag)
    assert len(state["properties"]) == 9
    assert {item["kind"] for item in state["properties"]} == {
        "angle_degrees",
        "boolean",
        "distance_mm",
        "enumeration",
        "number",
        "integer",
        "length_mm",
        "percent",
        "string",
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-property-bag-")
        save_path = Path(temporary.name) / "native-manufacture-property-bag.FCStd"
        secret_path = str(Path(temporary.name) / "human-only-secret-reference.step")
        document = App.newDocument("NativeManufacturePropertyBagGate")
        document.UndoMode = 1
        VibeGui._connect_document_observer()
        controller_widget, surface = _surface()
        plans = {
            plan.command_id: plan
            for plan in resolve_native_action_inventory(surface).plans
        }
        plan = plans["CAM_PropertyBag"]
        assert (
            plan.capability_family,
            plan.operation_variant,
            plan.exact_target_type,
            plan.classification.mutation,
            plan.classification.human_only,
            plan.background_required,
        ) == (
            MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
            "create",
            "ExactOptionalPartDesignBodyAndTypedPropertySet",
            True,
            False,
            False,
        )

        document.openTransaction("Create Property Bag Body fixture")
        body = document.addObject("PartDesign::Body", "MetadataBody")
        body.Label = "Metadata Body"
        document.commitTransaction()
        document.recompute()
        human_bag = _human_editor_gate(document, secret_path)
        document.saveAs(str(save_path))
        document.clearUndos()

        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        frozen = turn.surface
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-manufacture-property-bag-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller_widget)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: (
                read_active_ribbon_surface(controller_widget).surface_id
            ),
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

        def call(payload: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_index
            call_index += 1
            response = dispatcher.call(
                MANUFACTURE_PROPERTY_BAG_CAPABILITY_NAME,
                json.dumps(payload, separators=(",", ":")),
                f"native-manufacture-property-bag-{call_index}",
            )
            assert response.get("ok") is succeeds, response
            return response

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(body)
        selection_before = _selection()

        stale = _arguments(body)
        stale["destination_body"]["expected_state_sha256"] = "0" * 64
        stale_result = call(stale, succeeds=False)
        assert stale_result["error_code"] == "NATIVE_MANUFACTURE_STATE_STALE"

        file_injection = _arguments(body)
        file_injection["properties"][0]["typed_value"] = {
            "kind": "file",
            "value": "/tmp/provider-controlled.step",
        }
        rejected_file = call(file_injection, succeeds=False)
        assert rejected_file["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert rejected_file["argument_error"]["path"][-1] == "typed_value"

        duplicate = _arguments(body)
        duplicate["properties"][1]["name"] = "approachangle"
        duplicate_result = call(duplicate, succeeds=False)
        assert duplicate_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert duplicate_result["repair"]["field"] == "properties"

        reserved_group = _arguments(body)
        reserved_group["properties"][0]["group"] = "Base"
        reserved_result = call(reserved_group, succeeds=False)
        assert reserved_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert reserved_result["repair"]["field"].endswith(".group")

        document.openTransaction("Caller-owned Property Bag transaction")
        transaction = int(document.getBookedTransactionID())
        blocked = call(_arguments(body), succeeds=False)
        assert blocked["error_code"] == "NATIVE_TRANSACTION_ACTIVE"
        App.closeActiveTransaction(True, transaction)
        assert not document.HasPendingTransaction

        before_objects = tuple(document.Objects)
        before_members = tuple(body.Group)
        before_visibility = _visibility(document)
        before_timeline = _timeline(document)
        before_undo = int(document.UndoCount)
        with patch(
            "VibeCADNativeManufacturePropertyBagRuntime.verify_created_property_bag",
            side_effect=RuntimeError("forced Property Bag postcondition failure"),
        ):
            failed = call(_arguments(body), succeeds=False)
        assert failed["error_code"] == "NATIVE_POSTCONDITION_FAILED", failed
        assert tuple(document.Objects) == before_objects
        assert tuple(body.Group) == before_members
        assert _visibility(document) == before_visibility
        assert _timeline(document) == before_timeline
        assert _selection() == selection_before
        assert int(document.UndoCount) == before_undo

        result = call(_arguments(body))
        bag = document.getObject(result["object"]["object_name"])
        bag_name = str(bag.Name)
        body_name = str(body.Name)
        human_bag_name = str(human_bag.Name)
        _assert_property_bag(bag, body)
        assert result["property_count"] == 9
        assert len(result["properties"]) == 9
        assert result["destination_body"]["object_name"] == body.Name
        assert result["history_position"] == before_timeline[3] + 1
        assert not {
            "values",
            "file",
            "file_path",
            "path",
            "description",
        } & set(result)
        result_json = json.dumps(result, sort_keys=True)
        assert "fixture" not in result_json
        assert secret_path not in result_json
        assert len(result["receipt"]["created"]) == 1
        assert len(result["receipt"]["changed"]) == 1
        assert result["assistant_undo_available"] is True
        assert _selection() == selection_before
        assert _visibility(document)[: len(before_visibility)] == before_visibility
        assert int(document.UndoCount) == before_undo + 1

        document.undo()
        _events(12)
        body = document.getObject(body_name)
        assert document.getObject(bag_name) is None
        assert tuple(body.Group) == before_members
        document.redo()
        _events(12)
        body = document.getObject(body_name)
        bag = document.getObject(bag_name)
        _assert_property_bag(bag, body)

        root_result = call(
            {
                "operation": "create",
                "label": "Root Manufacturing Notes",
                "destination_body": None,
                "properties": [],
            }
        )
        root = document.getObject(root_result["object"]["object_name"])
        root_name = str(root.Name)
        assert root.getParentGeoFeatureGroup() is None
        assert root_result["destination_body"] is None
        assert root_result["property_count"] == 0
        assert root_result["receipt"]["changed"] == []

        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = None
        _events(16)
        document = App.openDocument(str(save_path))
        _events(20)
        body = document.getObject(body_name)
        bag = document.getObject(bag_name)
        root = document.getObject(root_name)
        human_bag = document.getObject(human_bag_name)
        _assert_property_bag(bag, body)
        assert root.getParentGeoFeatureGroup() is None
        assert is_property_bag(root)
        assert is_property_bag(human_bag)
        reopened_snapshot = json.dumps(property_bag_snapshot(document), sort_keys=True)
        assert secret_path not in reopened_snapshot
        assert "path_sha256" not in reopened_snapshot

        print(
            "VIBECAD_NATIVE_MANUFACTURE_PROPERTY_BAG_GUI_OK "
            "ribbon=true exact_body=true root=true closed_schema=true typed_values=true "
            "schema_budget=true no_provider_path=true human_file=true snapshot=true "
            "stale=true duplicate_guard=true reserved_guard=true transaction_guard=true "
            "rollback=true history=true receipt=true low_noise=true selection=true "
            "visibility=true undo=true redo=true reopen=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        try:
            Gui.Control.closeDialog()
        except Exception:
            pass
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        if application is not None:
            application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
