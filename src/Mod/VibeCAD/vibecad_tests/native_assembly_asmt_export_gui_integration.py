# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for human-authorized Native ASMT export."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import CommandCreateJoint
import JointObject
import Part
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyExportSchema import (
    ASSEMBLY_EXPORT_CAPABILITY_NAME,
    assembly_export_capability_definition,
)
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeOutput import authorize_native_output_path
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTargets import read_current_selection
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> tuple[object, object]:
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "assemble"
    return controller, surface


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    export = assembly_export_capability_definition()
    assert state is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=("state.read", ASSEMBLY_EXPORT_CAPABILITY_NAME),
            schemas=(
                state.provider_schema(("active", "selection")),
                export.provider_schema(("asmt",)),
            ),
            human_only_action_ids=("Assembly_ActivateAssembly",),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _assembly_summary(result: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in result["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _arguments(summary: dict) -> dict:
    diagnosis = summary["diagnosis_state"]
    assert diagnosis["available"] is True, diagnosis
    return {
        "operation": "asmt",
        "assembly": {"object_name": summary["object_name"]},
        "expected_state_sha256": diagnosis["state_sha256"],
        "expected_component_count": diagnosis["component_count"],
        "expected_grounded_count": diagnosis["grounded_count"],
        "expected_joint_count": diagnosis["joint_count"],
    }


def _dispatcher(
    document,
    service,
    controller,
    surface,
    registry,
    authorizer,
    run_id: str,
) -> NativeTurnDispatcher:
    frozen = NativeSurfaceSnapshot.from_surface(surface)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run(run_id)

    def reauthorize() -> None:
        require_frozen_native_surface(frozen, controller)

    context = NativeRuntimeContext(
        service=service,
        document=document,
        state=service.native_document_state_store(),
        undo_ledger=ledger,
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
        active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
        edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        authorize_output=authorizer,
    )
    turn = _focused_turn(surface, registry)
    return NativeTurnDispatcher(
        document=document,
        state=context.state,
        registry=registry,
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-asmt-export-"
        )
        root = Path(temporary.name)
        document_path = root / "native-assembly-asmt-export.FCStd"
        human_path = root / "human-reference.asmt"
        native_path = root / "native-export.asmt"
        overwrite_path = root / "overwrite.asmt"
        drift_path = root / "drift.asmt"
        stale_path = root / "stale.asmt"
        reopen_path = root / "reopened.asmt"

        document = App.newDocument("NativeAssemblyAsmtExportGate")
        document.UndoMode = 1
        sources = []
        for index in range(2):
            source = document.addObject("Part::Feature", f"ExportSource{index + 1}")
            source.Shape = (
                Part.makeBox(16.0, 12.0, 8.0)
                if index == 0
                else Part.makeCylinder(5.0, 18.0)
            )
            sources.append(source)
        document.recompute()
        document.saveAs(str(document_path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject
        document.openTransaction("Prepare ASMT export fixture")
        components = []
        for index, source in enumerate(sources):
            component = assembly.newObject("App::Link", f"ExportComponent{index + 1}")
            component.LinkedObject = source
            component.Placement.Base.x = float(index * 35)
            UtilsAssembly.finalizeInsertedComponentTimeline(component)
            components.append(component)
        ground = CommandCreateJoint.createGroundedJointFeature(components[0], assembly)
        JointObject.ensureViewProviderGroundedJoint(ground)
        document.recompute()
        document.commitTransaction()
        document.save()
        document.clearUndos()

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller, surface = _select_assemble_ribbon(main_window)
        assert Gui.isCommandActive("Assembly_ExportASMT")

        registry = build_native_capability_registry()
        definition = registry.definition(ASSEMBLY_EXPORT_CAPABILITY_NAME)
        assert definition is not None
        variant = definition.variants[0]
        assert variant.action_ids == frozenset({"Assembly_ExportASMT"})
        assert variant.transaction_behavior == "output"
        assert registry.implementation(ASSEMBLY_EXPORT_CAPABILITY_NAME) is not None
        production = resolve_native_provider_surface(surface, registry)
        assert (
            ASSEMBLY_EXPORT_CAPABILITY_NAME not in production.missing_definition_names
        )
        assert (
            ASSEMBLY_EXPORT_CAPABILITY_NAME
            not in production.missing_implementation_names
        )
        assert (
            ASSEMBLY_EXPORT_CAPABILITY_NAME
            not in production.incomplete_definition_names
        )

        service = get_service()
        service.select_modeling_engine("native")
        choice = {"path": native_path, "mode": "normal"}
        authorization_requests = []

        def authorize(request):
            authorization_requests.append(request)
            if choice["mode"] == "cancel":
                return None
            authorization = authorize_native_output_path(request, choice["path"])
            if choice["mode"] == "destination_drift":
                choice["path"].write_text(
                    "changed after authorization", encoding="utf-8"
                )
            elif choice["mode"] == "revision_drift":
                service.native_document_state_store().note_structural_change(
                    str(document.Uid)
                )
            return authorization

        dispatcher = _dispatcher(
            document,
            service,
            controller,
            surface,
            registry,
            authorize,
            "native-assembly-asmt-export-gui",
        )
        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-asmt-state-initial",
        )
        assert initial["ok"] is True, initial
        summary = _assembly_summary(initial, assembly.Name)
        arguments = _arguments(summary)
        assert arguments["expected_component_count"] == 2
        assert arguments["expected_grounded_count"] == 1

        assembly.exportAsASMT(str(human_path))
        human_bytes = human_path.read_bytes()
        assert human_bytes.startswith(b"OndselSolver\nAssembly\n")

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(components[1])
        _process_events(10)
        selection_before = read_current_selection(document)
        objects_before = tuple(document.Objects)
        undo_before = int(document.UndoCount)
        transaction_before = int(document.getBookedTransactionID())
        touched_before = bool(document.isTouched())
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        subwindow_before = mdi_area.activeSubWindow()
        edit_before = Gui.activeDocument().getInEdit()
        task_before = Gui.Control.activeTaskDialog()

        malformed = {**arguments, "path": str(root / "provider-chosen.asmt")}
        malformed_result = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(malformed, separators=(",", ":")),
            "assembly-asmt-provider-path-refused",
        )
        assert malformed_result["ok"] is False, malformed_result
        assert malformed_result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert not (root / "provider-chosen.asmt").exists()
        assert authorization_requests == []

        choice.update(path=root / "cancelled.asmt", mode="cancel")
        cancelled = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            "assembly-asmt-cancelled",
        )
        assert cancelled["ok"] is False, cancelled
        assert cancelled["error_code"] == "NATIVE_ASSEMBLY_EXPORT_FAILED"
        assert not choice["path"].exists()

        choice.update(path=native_path, mode="normal")
        first_call_id = "assembly-asmt-native-success"
        first = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            first_call_id,
        )
        assert first["ok"] is True, first
        assert first["operation"] == "asmt"
        assert first["output"]["file_name"] == native_path.name
        assert first["output"]["size_bytes"] == len(human_bytes)
        assert len(first["output"]["sha256"]) == 64
        assert first["output"]["replaced_existing"] is False
        assert "path" not in first["output"]
        assert native_path.read_bytes() == human_bytes
        authorization_count = len(authorization_requests)
        replay = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            first_call_id,
        )
        assert replay == first
        assert len(authorization_requests) == authorization_count

        drift_path.write_text("authorized destination", encoding="utf-8")
        choice.update(path=drift_path, mode="destination_drift")
        drift = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            "assembly-asmt-destination-drift",
        )
        assert drift["ok"] is False, drift
        assert drift["error_code"] == "NATIVE_ASSEMBLY_EXPORT_FAILED"
        assert drift_path.read_text(encoding="utf-8") == "changed after authorization"

        stale_path.write_text("stale sentinel", encoding="utf-8")
        choice.update(path=stale_path, mode="revision_drift")
        stale = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            "assembly-asmt-revision-drift",
        )
        assert stale["ok"] is False, stale
        assert stale["error_code"] == "NATIVE_REVISION_CONFLICT"
        assert stale_path.read_text(encoding="utf-8") == "stale sentinel"

        overwrite_path.write_text("replace me", encoding="utf-8")
        choice.update(path=overwrite_path, mode="normal")
        overwritten = dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(arguments, separators=(",", ":")),
            "assembly-asmt-overwrite",
        )
        assert overwritten["ok"] is True, overwritten
        assert overwritten["output"]["replaced_existing"] is True
        assert overwrite_path.read_bytes() == human_bytes

        assert App.ActiveDocument is document
        assert Gui.activeDocument().getInEdit() is edit_before
        assert Gui.Control.activeTaskDialog() is task_before
        assert mdi_area.activeSubWindow() is subwindow_before
        assert read_current_selection(document) == selection_before
        assert tuple(document.Objects) == objects_before
        assert int(document.UndoCount) == undo_before == 0
        assert int(document.getBookedTransactionID()) == transaction_before == 0
        assert bool(document.isTouched()) is touched_before

        assembly_name = assembly.Name
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(document_path))
        App.setActiveDocument(document.Name)
        assembly = document.getObject(assembly_name)
        assert assembly is not None
        assert Gui.activeDocument().setEdit(assembly.Name)
        _process_events(24)
        controller, surface = _select_assemble_ribbon(main_window)
        service.select_modeling_engine("native")
        choice.update(path=reopen_path, mode="normal")
        reopened_dispatcher = _dispatcher(
            document,
            service,
            controller,
            surface,
            registry,
            authorize,
            "native-assembly-asmt-export-reopen-gui",
        )
        reopened_state = reopened_dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-asmt-state-reopened",
        )
        assert reopened_state["ok"] is True, reopened_state
        reopened_arguments = _arguments(
            _assembly_summary(reopened_state, assembly.Name)
        )
        reopened = reopened_dispatcher.call(
            ASSEMBLY_EXPORT_CAPABILITY_NAME,
            json.dumps(reopened_arguments, separators=(",", ":")),
            "assembly-asmt-reopened-export",
        )
        assert reopened["ok"] is True, reopened
        assert reopen_path.read_bytes().startswith(b"OndselSolver\nAssembly\n")
        assert reopened["output"]["size_bytes"] == reopen_path.stat().st_size

        print(
            "VIBECAD_NATIVE_ASSEMBLY_ASMT_EXPORT_GUI_OK "
            "human_serializer=true explicit_path=true provider_path_refused=true "
            "cancel_noop=true destination_drift_noop=true stale_noop=true "
            "atomic_overwrite=true idempotent=true document_unchanged=true "
            "selection_unchanged=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        if document is not None:
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
