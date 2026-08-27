# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human/Native parity and lifecycle gate for component-interface publication."""

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
import PartDesign  # noqa: F401
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADComponentCatalog import (
    capture_component_catalog,
    prepare_captured_component_catalog,
)
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeComponentInterface import (
    NativeComponentInterfaceError,
    prepare_component_interface,
    publish_component_interface,
)
import VibeCADNativeComponentInterfaceRuntime as runtime_module
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelSnapshot import build_model_snapshot
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import (
    NativeSurfaceSnapshot,
    require_frozen_native_surface,
)
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADReferenceContracts import (
    PROP_NATIVE_INTERFACE,
    connector_frame_placement,
    native_interface_definitions,
    publish_native_interface,
)


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> None:
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert tabs is not None
    index = next(
        (
            candidate
            for candidate in range(tabs.count())
            if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
        ),
        -1,
    )
    assert index >= 0
    tabs.setCurrentIndex(index)
    _process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def _new_component(document, name: str, placement, *, scripted: bool = False):
    body = document.addObject("PartDesign::Body", name)
    feature = body.newObject("PartDesign::Feature", f"{name}Solid")
    feature.Shape = Part.makeBox(12.0, 8.0, 4.0)
    lcs = body.newObject("PartDesign::CoordinateSystem", f"{name}LCS")
    lcs.Placement = placement
    body.Tip = feature
    if scripted:
        body.addProperty(
            "App::PropertyString",
            "VibeCADVibeScriptProgramId",
            "VibeCAD",
        )
        body.VibeCADVibeScriptProgramId = "scripted-component-program"
    return body, feature, lcs


def _setup(document):
    placement = App.Placement(
        App.Vector(3.0, 4.0, 5.0),
        App.Rotation(App.Vector(0.0, 1.0, 0.0), 32.0),
    )
    document.openTransaction("Create component interface inputs")
    human = _new_component(document, "HumanBracket", placement)
    native = _new_component(document, "NativeBracket", placement)
    rollback = _new_component(document, "RollbackBracket", placement)
    stale = _new_component(document, "StaleBracket", placement)
    scripted = _new_component(document, "ScriptedBracket", placement, scripted=True)
    duplicate = _new_component(document, "DuplicateBracket", placement)
    duplicate_lcs = duplicate[0].newObject(
        "PartDesign::CoordinateSystem",
        "DuplicateBracketSecondLCS",
    )
    duplicate_lcs.Placement = placement
    duplicate[0].Tip = duplicate[1]
    unowned = _new_component(document, "UnownedBracket", placement)
    publish_native_interface(
        duplicate[0],
        duplicate[2],
        name="TakenAxis",
        kind="axis",
        allowed_joints=["fixed"],
        compatibility="taken-v1",
    )
    document.recompute()
    document.commitTransaction()
    return {
        "human": human,
        "native": native,
        "rollback": rollback,
        "stale": stale,
        "scripted": scripted,
        "duplicate": (*duplicate, duplicate_lcs),
        "unowned": unowned,
    }


def _definition(component, name: str):
    value = native_interface_definitions(component)
    assert name in value, value
    return value[name]


def _assert_frame_same(actual, expected) -> None:
    assert actual["schema"] == expected["schema"]
    assert connector_frame_placement(actual).isSame(
        connector_frame_placement(expected),
        1.0e-12,
    )


def _assert_definition_same(actual, expected) -> None:
    assert actual["selection"] == expected["selection"]
    assert actual["connector"] == expected["connector"]
    assert {
        key: value
        for key, value in actual["resolved"].items()
        if key != "connector_frame"
    } == {
        key: value
        for key, value in expected["resolved"].items()
        if key != "connector_frame"
    }
    _assert_frame_same(
        actual["resolved"]["connector_frame"],
        expected["resolved"]["connector_frame"],
    )


def _assert_not_published(component, lcs) -> None:
    assert native_interface_definitions(component) == {}
    assert PROP_NATIVE_INTERFACE not in set(lcs.PropertiesList)


def _fill_human_dialog(name: str, kind: str, joints: tuple[str, ...], compatibility: str):
    outcome = {"completed": False, "error": None}

    def complete() -> None:
        dialog = QtWidgets.QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QtWidgets.QDialog), dialog
            assert dialog.windowTitle() == "Publish Interface"
            edits = dialog.findChildren(QtWidgets.QLineEdit)
            combos = dialog.findChildren(QtWidgets.QComboBox)
            lists = dialog.findChildren(QtWidgets.QListWidget)
            assert len(edits) == 2 and len(combos) == 1 and len(lists) == 1
            edits[0].setText(name)
            combos[0].setCurrentText(kind)
            for joint in joints:
                matches = lists[0].findItems(joint, QtCore.Qt.MatchExactly)
                assert len(matches) == 1
                matches[0].setSelected(True)
            edits[1].setText(compatibility)
            outcome["completed"] = True
            dialog.accept()
        except Exception as exc:
            outcome["error"] = exc
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.reject()

    QtCore.QTimer.singleShot(0, complete)
    return outcome


def _human_parity(document, body, lcs):
    VibeGui.ensure_commands_registered()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(body)
    Gui.Selection.addSelection(lcs)
    _process_events()
    command = Gui.Command.get("VibeCAD_PublishInterface")
    selected_names = [obj.Name for obj in Gui.Selection.getSelection()]
    assert command is not None, selected_names
    assert command.isActive(), (
        selected_names,
        VibeGui.PublishComponentInterfaceCommand._selection(),
        Gui.Control.activeDialog(),
    )
    outcome = _fill_human_dialog(
        "MountAxis",
        "axis",
        ("revolute", "fixed"),
        "mount-v1",
    )
    Gui.runCommand("VibeCAD_PublishInterface")
    _process_events()
    assert outcome["error"] is None, outcome["error"]
    assert outcome["completed"] is True
    expected = _definition(body, "MountAxis")

    document.undo()
    _process_events()
    _assert_not_published(body, lcs)
    document.redo()
    _process_events()
    assert _definition(body, "MountAxis") == expected
    Gui.Selection.clearSelection()
    return expected


def _arguments(
    component,
    lcs,
    *,
    name: str = "MountAxis",
    kind: str = "axis",
    joints: tuple[str, ...] = ("revolute", "fixed"),
    compatibility: str = "mount-v1",
):
    return {
        "operation": "publish_interface",
        "component": {"object_name": component.Name},
        "lcs": {"object_name": lcs.Name},
        "name": name,
        "kind": kind,
        "allowed_joints": list(joints),
        "compatibility": compatibility,
    }


def _assert_response(document, response, component, lcs, name: str):
    assert set(response) == {
        "ok",
        "verified",
        "component",
        "interface",
        "receipt",
        "assistant_undo_available",
    }
    assert response["verified"] is True
    assert response["component"]["object_name"] == component.Name
    interface = response["interface"]
    assert interface["name"] == name
    assert interface["lcs"]["object_name"] == lcs.Name
    assert interface["kind"] == str(lcs.VibeCADInterfaceKind)
    assert interface["allowed_joints"] == json.loads(
        str(lcs.VibeCADInterfaceAllowedJoints)
    )
    assert interface["compatibility"] == str(lcs.VibeCADInterfaceCompatibility)
    definition = _definition(component, name)
    frame = definition["resolved"]["connector_frame"]
    assert interface["origin_mm"] == frame["origin_mm"]
    assert interface["axis_direction"] == frame["axis_direction"]
    assert interface["x_direction"] == frame["x_direction"]
    receipt = response["receipt"]
    assert receipt["created"] == []
    assert receipt["changed"] == [
        {
            "document_uid": str(document.Uid),
            "object_name": lcs.Name,
            "type_id": lcs.TypeId,
        }
    ]
    assert receipt["deleted"] == []
    assert receipt["replaced"] == []
    assert response["assistant_undo_available"] is True
    return definition


def _assert_snapshot(document, component, lcs, name: str) -> None:
    snapshot = build_model_snapshot(document)
    summary = next(
        item for item in snapshot["bodies"] if item["object_name"] == component.Name
    )
    assert len(summary["local_coordinate_systems"]) == 1
    lcs_summary = summary["local_coordinate_systems"][0]
    assert lcs_summary["document_uid"] == str(document.Uid)
    assert lcs_summary["object_name"] == lcs.Name
    assert lcs_summary["type_id"] == lcs.TypeId
    assert lcs_summary["published_interface"] == name
    interface = summary["published_interfaces"][0]
    assert interface["name"] == name
    assert interface["lcs"]["object_name"] == lcs.Name
    assert interface["kind"] == str(lcs.VibeCADInterfaceKind)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeComponentInterfaceGate")
        document.UndoMode = True
        VibeGui._connect_document_observer()
        setup = _setup(document)
        _process_events()
        model_workbench = Gui.activeWorkbench().name()

        human_body, _human_feature, human_lcs = setup["human"]
        human = _human_parity(document, human_body, human_lcs)
        assert Gui.activeWorkbench().name() == model_workbench

        main_window = Gui.getMainWindow()
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is True, production.debug_summary()
        assert "component.interface" in production.tool_names
        assert "component.interfaces" in production.tool_names
        assert "VibeCAD_PublishInterface" in surface.command_ids
        assert "AssemblyContextToggleActive" in production.human_only_action_ids
        workbench = Gui.activeWorkbench().name()

        def reauthorize() -> None:
            require_frozen_native_surface(frozen_surface, controller)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-component-interface-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: False,
        )
        turn = NativeTurnSnapshot.from_provider_surface(production)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        Gui.Selection.clearSelection()
        call_number = 0

        def native_call(arguments, *, succeeds=True, name="component.interface"):
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                name,
                json.dumps(arguments, separators=(",", ":")),
                f"component-interface-call-{call_number}",
            )
            assert result.get("ok") is succeeds, (arguments, result)
            assert Gui.activeWorkbench().name() == workbench
            assert not Gui.Control.activeDialog()
            assert QtWidgets.QApplication.activeModalWidget() is None
            return result

        native_body, native_feature, native_lcs = setup["native"]
        targets = native_call({}, name="component.interfaces")
        native_target = next(
            target
            for target in targets["targets"]
            if target["component"]["object_name"] == native_body.Name
            and target["lcs"]["object_name"] == native_lcs.Name
        )
        assert native_target["component"] == {"object_name": native_body.Name}
        assert native_target["lcs"] == {"object_name": native_lcs.Name}
        arguments = _arguments(native_body, native_lcs)
        before_objects = tuple(obj.Name for obj in document.Objects)
        duplicate_body, _duplicate_feature, _taken_lcs, duplicate_lcs = setup[
            "duplicate"
        ]
        scripted_body, _scripted_feature, scripted_lcs = setup["scripted"]
        unowned_body, _unowned_feature, unowned_lcs = setup["unowned"]
        invalid_cases = (
            ({**arguments, "selection": []}, "NATIVE_ARGUMENTS_INVALID"),
            (
                {**arguments, "component": {"object_name": "DeletedComponent"}},
                "NATIVE_TARGET_INVALID",
            ),
            (
                {**arguments, "lcs": {"object_name": "DeletedLCS"}},
                "NATIVE_TARGET_INVALID",
            ),
            (
                {**arguments, "lcs": {"object_name": native_feature.Name}},
                "NATIVE_TARGET_INVALID",
            ),
            (
                {**arguments, "lcs": {"object_name": unowned_lcs.Name}},
                "NATIVE_COMPONENT_INVALID",
            ),
            (
                _arguments(scripted_body, scripted_lcs),
                "NATIVE_COMPONENT_INVALID",
            ),
            (
                _arguments(
                    duplicate_body,
                    duplicate_lcs,
                    name="TakenAxis",
                    joints=("fixed",),
                    compatibility="taken-v1",
                ),
                "NATIVE_COMPONENT_INVALID",
            ),
            ({**arguments, "name": "1bad"}, "NATIVE_ARGUMENTS_INVALID"),
            ({**arguments, "kind": "line"}, "NATIVE_ARGUMENTS_INVALID"),
            (
                {**arguments, "allowed_joints": ["fixed", "fixed"]},
                "NATIVE_ARGUMENTS_INVALID",
            ),
            (
                {**arguments, "compatibility": "bad token"},
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for invalid, error_code in invalid_cases:
            response = native_call(invalid, succeeds=False)
            assert response["error_code"] == error_code, response
            assert tuple(obj.Name for obj in document.Objects) == before_objects
            assert not document.HasPendingTransaction
        _assert_not_published(native_body, native_lcs)

        stale_body, _stale_feature, stale_lcs = setup["stale"]
        stale_values = _arguments(stale_body, stale_lcs)
        stale_values.pop("operation")
        prepared = prepare_component_interface(document, stale_values)
        publish_native_interface(
            stale_body,
            stale_lcs,
            name="ExternalAxis",
            kind="axis",
            allowed_joints=["fixed"],
            compatibility="external-v1",
        )
        stale_state = tuple(
            (name, getattr(stale_lcs, name))
            for name in stale_lcs.PropertiesList
            if name.startswith("VibeCADInterface")
            or name == PROP_NATIVE_INTERFACE
        )
        try:
            publish_component_interface(document, prepared=prepared)
            raise AssertionError("stale preflight unexpectedly mutated")
        except NativeComponentInterfaceError as exc:
            assert "changed after preflight" in str(exc)
        assert stale_state == tuple(
            (name, getattr(stale_lcs, name))
            for name in stale_lcs.PropertiesList
            if name.startswith("VibeCADInterface")
            or name == PROP_NATIVE_INTERFACE
        )

        turn = NativeTurnSnapshot.from_provider_surface(production)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )

        rollback_body, _rollback_feature, rollback_lcs = setup["rollback"]
        original_verifier = runtime_module.verify_component_interface

        def reject_verification(_document, _draft):
            raise NativeComponentInterfaceError(
                "Forced component-interface verifier failure."
            )

        runtime_module.verify_component_interface = reject_verification
        try:
            response = native_call(
                _arguments(rollback_body, rollback_lcs),
                succeeds=False,
            )
        finally:
            runtime_module.verify_component_interface = original_verifier
        assert response["error_code"] == "NATIVE_COMPONENT_INVALID", response
        _assert_not_published(rollback_body, rollback_lcs)
        assert tuple(obj.Name for obj in document.Objects) == before_objects
        assert not document.HasPendingTransaction

        response = native_call(arguments)
        native = _assert_response(
            document,
            response,
            native_body,
            native_lcs,
            "MountAxis",
        )
        assert native["connector"] == human["connector"]
        assert (
            native["resolved"]["connector_frame"]
            == human["resolved"]["connector_frame"]
        )
        _assert_snapshot(document, native_body, native_lcs, "MountAxis")

        response = native_call(arguments, succeeds=False)
        assert response["error_code"] == "NATIVE_COMPONENT_INVALID"
        assert _definition(native_body, "MountAxis") == native

        updated_arguments = _arguments(
            native_body,
            native_lcs,
            name="MountFrame",
            kind="frame",
            joints=("fixed", "gears"),
            compatibility="mount-v2",
        )
        updated_arguments["coupling_parameters"] = {
            "schema": "vibecad-interface-coupling-parameters-v1",
            "values": {"gears": {"pitch_radius_mm": 24.0}},
        }
        response = native_call(updated_arguments)
        updated = _assert_response(
            document,
            response,
            native_body,
            native_lcs,
            "MountFrame",
        )
        assert "MountAxis" not in native_interface_definitions(native_body)
        assert updated["connector"]["coupling_parameters"] == (
            updated_arguments["coupling_parameters"]
        )
        _assert_snapshot(document, native_body, native_lcs, "MountFrame")

        document.undo()
        _process_events()
        assert _definition(native_body, "MountAxis") == native
        document.redo()
        _process_events()
        assert _definition(native_body, "MountFrame") == updated

        for _index in range(5):
            assert document.recompute([native_lcs, native_body], True, True) is not False
            assert _definition(native_body, "MountFrame") == updated
        assert Gui.activeWorkbench().name() == workbench

        captured = capture_component_catalog(service)
        candidate = next(
            item
            for item in captured["open_candidates"]
            if item["object_name"] == native_body.Name
        )
        assert candidate["published_interfaces"] == ["MountFrame"]
        assert candidate["local_coordinate_systems"][0]["object_name"] == native_lcs.Name
        assert candidate["local_coordinate_systems"][0]["published_interface"] == (
            "MountFrame"
        )

        native_body_name = native_body.Name
        native_lcs_name = native_lcs.Name
        save_directory = tempfile.mkdtemp(prefix="vibecad-native-interface-")
        save_path = Path(save_directory) / "native-component-interface.FCStd"
        document.saveAs(str(save_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = None

        saved_catalog = prepare_captured_component_catalog(
            {
                "owner_document_uid": "assembly-owner",
                "project_directory": save_directory,
                "owner_file": "",
                "open_document_files": [],
                "open_candidates": [],
            }
        )
        saved_candidate = next(
            item
            for item in saved_catalog["candidates"]
            if item["object_name"] == native_body_name
        )
        assert saved_candidate["published_interfaces"] == ["MountFrame"]
        _assert_frame_same(
            saved_candidate["interfaces"][0]["frame"],
            updated["resolved"]["connector_frame"],
        )
        assert saved_candidate["local_coordinate_systems"][0][
            "published_interface"
        ] == "MountFrame"

        document = App.openDocument(str(save_path))
        document.UndoMode = True
        assert document.recompute(None, True, True) is not False
        _process_events()
        reopened_body = document.getObject(native_body_name)
        reopened_lcs = document.getObject(native_lcs_name)
        _assert_definition_same(_definition(reopened_body, "MountFrame"), updated)
        _assert_snapshot(document, reopened_body, reopened_lcs, "MountFrame")
        assert Gui.activeWorkbench().name() == workbench

        print("VIBECAD_NATIVE_COMPONENT_INTERFACE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        modal = QtWidgets.QApplication.activeModalWidget()
        if isinstance(modal, QtWidgets.QDialog):
            modal.reject()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
