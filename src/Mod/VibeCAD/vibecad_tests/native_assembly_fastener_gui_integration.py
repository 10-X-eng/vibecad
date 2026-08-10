# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Assembly fastener insertion."""

from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import traceback
from unittest import mock

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import UtilsAssembly
from VibeCADCore import get_service
from VibeCADFastenerAssembly import (
    assembly_fastener_graph_from_occurrence,
    validate_assembly_fastener_graph,
)
from VibeCADFasteners import resolve_fastener
import VibeCADFastenersGui
import VibeCADGui as VibeGui
from VibeCADNativeAssemblyFastener import NativeAssemblyFastenerError
from VibeCADNativeAssemblyFastenerSchema import (
    ASSEMBLY_FASTENER_CAPABILITY_NAME,
    assembly_fastener_capability_definition,
)
from VibeCADNativeAssemblySnapshot import build_assembly_snapshot
from VibeCADNativeCapabilityRegistry import (
    NativeProviderSurface,
    resolve_native_provider_surface,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface
import VibeCADNativeAssemblyFastenerRuntime as runtime_module


def _process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_assemble_ribbon(main_window) -> None:
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "AssemblyWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def _definition(*, model_thread: bool = False) -> dict[str, object]:
    return {
        "standard": "ISO4762",
        "nominal_thread": "M6",
        "length_mm": 25.0,
        "model_thread": model_thread,
        "left_handed": False,
        "options": {},
    }


def _dialog_values(label: str) -> dict[str, object]:
    definition = _definition()
    return {
        **definition,
        "label": label,
        "identity": resolve_fastener(**definition),
    }


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return math.isclose(
        float(left),
        float(right),
        rel_tol=1.0e-9,
        abs_tol=tolerance,
    )


def _shape_signature(shape) -> tuple[object, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Solids),
        len(shape.Faces),
        len(shape.Edges),
        len(shape.Vertexes),
        float(shape.Volume),
        float(shape.Area),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _assert_signature(actual, expected, *, restored: bool = False) -> None:
    assert actual[:5] == expected[:5], (actual, expected)
    tolerance = 1.0e-2 if restored else 1.0e-7
    for left, right in zip(actual[5:], expected[5:], strict=True):
        assert _close(left, right, tolerance), (actual, expected)


def _graph_contract(graph) -> dict[str, object]:
    return {
        "occurrence_type": str(graph.occurrence.TypeId),
        "source_type": str(graph.source.TypeId),
        "occurrence_role": str(graph.occurrence.VibeCADTimelineRole),
        "source_role": str(graph.source.VibeCADTimelineRole),
        "edit_command": str(graph.occurrence.VibeCADTimelineEditCommand),
        "source_hidden": bool(not graph.source.ViewObject.Visibility),
        "source_tree_hidden": bool(not graph.source.ViewObject.ShowInTree),
        "occurrence_tree_visible": bool(graph.occurrence.ViewObject.ShowInTree),
    }


def _assert_graph(
    document,
    assembly,
    occurrence,
    *,
    label: str,
    canonical_key: str,
):
    graph = assembly_fastener_graph_from_occurrence(assembly, occurrence)
    identity = validate_assembly_fastener_graph(
        document,
        graph,
        label=label,
        canonical_key=canonical_key,
    )
    assert occurrence in tuple(assembly.Group)
    assert graph.source not in tuple(assembly.Group)
    assert occurrence.LinkedObject is graph.source
    assert graph.source.VibeCADTimelineOwner is occurrence
    assert occurrence.VibeCADTimelineEditor is graph.source
    assert occurrence.VibeCADTimelineOwner is None
    assert UtilsAssembly.isTimelineOperationActive(graph.source)
    assert UtilsAssembly.isTimelineOperationActive(occurrence)
    timeline = document.getObject("VibeCADTimeline")
    operations = tuple(timeline.Operations)
    assert operations.index(graph.source) + 1 == operations.index(occurrence)
    assert identity["canonical_key"] == canonical_key
    assert len(graph.source.Shape.Solids) == 1
    assert len(occurrence.Shape.Solids) == 1
    return graph, identity


def _human_insert(document, assembly):
    values = _dialog_values("Human M6 socket bolt")
    VibeCADFastenersGui.ensure_commands_registered()
    command = Gui.Command.get("VibeCAD_InsertStandardFastener")
    assert command is not None and command.isActive()
    with mock.patch.object(VibeCADFastenersGui, "_FastenerDialog") as dialog:
        dialog.return_value.exec.return_value = values
        Gui.runCommand("VibeCAD_InsertStandardFastener")
    _process_events(24)
    selected = Gui.Selection.getSelection()
    assert len(selected) == 1
    occurrence = selected[0]
    graph, identity = _assert_graph(
        document,
        assembly,
        occurrence,
        label=values["label"],
        canonical_key=values["identity"]["canonical_key"],
    )
    assert identity == values["identity"]
    return graph


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    assert state is not None
    fastener = assembly_fastener_capability_definition()
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_FASTENER_CAPABILITY_NAME),
        schemas=(
            state.provider_schema(("active", "selection")),
            fastener.provider_schema(("insert_standard_fastener",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)


def _assembly_summary(response: dict, assembly_name: str) -> dict:
    return next(
        value
        for value in response["domain"]["assemblies"]
        if value["object_name"] == assembly_name
    )


def _insert_arguments(summary: dict, *, label: str) -> dict[str, object]:
    diagnosis = summary["diagnosis_state"]
    assert diagnosis["available"] is True, diagnosis
    return {
        "operation": "insert_standard_fastener",
        "assembly": {"object_name": summary["object_name"]},
        "label": label,
        "definition": _definition(),
        "expected_state_sha256": diagnosis["state_sha256"],
        "expected_component_count": diagnosis["component_count"],
        "expected_grounded_count": diagnosis["grounded_count"],
        "expected_joint_count": diagnosis["joint_count"],
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-assembly-fastener-"
        )
        path = Path(temporary.name) / "native-assembly-fastener.FCStd"
        document = App.newDocument("NativeAssemblyFastenerGate")
        document.UndoMode = 1
        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        human_graph = _human_insert(document, assembly)
        human_contract = _graph_contract(human_graph)
        human_signature = _shape_signature(human_graph.source.Shape)
        human_names = (human_graph.occurrence.Name, human_graph.source.Name)
        human_graph.occurrence.Placement = App.Placement(
            App.Vector(18.0, -4.0, 7.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 31.0),
        )
        document.recompute()
        human_placement = App.Placement(human_graph.occurrence.Placement)
        document.saveAs(str(path))

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "VibeCAD_InsertStandardFastener" in surface.command_ids
        frozen = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        fastener_definition = registry.definition(ASSEMBLY_FASTENER_CAPABILITY_NAME)
        assert fastener_definition is not None
        variant = next(
            value
            for value in fastener_definition.variants
            if value.operation == "insert_standard_fastener"
        )
        assert variant.action_ids == frozenset({"VibeCAD_InsertStandardFastener"})
        assert variant.surface_ids == frozenset({"assemble"})
        assert registry.implementation(ASSEMBLY_FASTENER_CAPABILITY_NAME) is not None
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is False
        assert ASSEMBLY_FASTENER_CAPABILITY_NAME in (
            production.incomplete_definition_names
        )

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-fastener-gui")

        def reauthorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()),
        )
        turn = _focused_turn(surface, registry)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_number
            call_number += 1
            task_before = Gui.Control.activeTaskDialog()
            subwindow_before = mdi_area.activeSubWindow()
            result = dispatcher.call(
                ASSEMBLY_FASTENER_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-fastener-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert Gui.Control.activeTaskDialog() is task_before
            assert mdi_area.activeSubWindow() is subwindow_before
            assert Gui.Selection.getSelection() == [human_graph.occurrence]
            return result

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(human_graph.occurrence)
        document.clearUndos()
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-fastener-state-initial",
        )
        assert initial["ok"] is True, initial
        summary = _assembly_summary(initial, assembly.Name)
        arguments = _insert_arguments(summary, label="Native M6 socket bolt")
        assert arguments["expected_component_count"] == 1
        before_objects = tuple(document.Objects)
        before_timeline = tuple(document.VibeCADTimeline.Operations)

        malformed = {**arguments, "placement": {"x": 0.0}}
        failure = call(malformed, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(document.Objects) == before_objects
        assert int(document.UndoCount) == 0

        stale_digest = {**arguments, "expected_state_sha256": "0" * 64}
        failure = call(stale_digest, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        stale_count = {
            **arguments,
            "expected_component_count": arguments["expected_component_count"] + 1,
        }
        failure = call(stale_count, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"

        invalid_catalog = {
            **arguments,
            "definition": {**arguments["definition"], "standard": "NOT_A_STANDARD"},
        }
        failure = call(invalid_catalog, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        assert tuple(document.Objects) == before_objects
        assert tuple(document.VibeCADTimeline.Operations) == before_timeline
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        call_id = "assembly-fastener-create-first"
        result = call(arguments, call_id=call_id)
        assert set(result) == {
            "ok",
            "operation",
            "assembly",
            "occurrence",
            "definition_source",
            "label",
            "fastener",
            "component_count",
            "grounded_count",
            "joint_count",
            "assembly_state_sha256",
            "initial_placement_identity",
            "receipt",
            "assistant_undo_available",
        }
        assert result["operation"] == "insert_standard_fastener"
        assert result["assembly"]["object_name"] == assembly.Name
        assert result["label"] == arguments["label"]
        assert result["component_count"] == 2
        assert result["grounded_count"] == 0
        assert result["joint_count"] == 0
        assert result["initial_placement_identity"] is True
        assert result["assistant_undo_available"] is True
        occurrence_name = result["occurrence"]["object_name"]
        source_name = result["definition_source"]["object_name"]
        occurrence = document.getObject(occurrence_name)
        source = document.getObject(source_name)
        assert occurrence is not None and source is not None
        graph, identity = _assert_graph(
            document,
            assembly,
            occurrence,
            label=arguments["label"],
            canonical_key=result["fastener"]["canonical_key"],
        )
        assert graph.source is source
        assert _graph_contract(graph) == human_contract
        _assert_signature(_shape_signature(source.Shape), human_signature)
        assert occurrence.Placement.isIdentity()
        assert human_graph.occurrence.Placement.isSame(human_placement, 1.0e-9)
        assert {item["object_name"] for item in result["receipt"]["created"]} == {
            occurrence_name,
            source_name,
        }
        assert {item["object_name"] for item in result["receipt"]["changed"]} == {
            assembly.Name
        }
        assert int(document.UndoCount) == 1

        replay = call(arguments, call_id=call_id)
        assert replay == result
        assert int(document.UndoCount) == 1
        assert tuple(obj.Name for obj in document.Objects).count(occurrence_name) == 1

        after = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-fastener-state-after",
        )
        assert after["ok"] is True, after
        after_summary = _assembly_summary(after, assembly.Name)
        component = next(
            value
            for value in after_summary["components"]
            if value["object_name"] == occurrence_name
        )
        assert component["standard_fastener"] == {
            "source": {"object_name": source_name},
            **result["fastener"],
        }

        rollback_arguments = _insert_arguments(
            after_summary,
            label="Rolled back M6 socket bolt",
        )
        names_before_rollback = tuple(obj.Name for obj in document.Objects)
        timeline_before_rollback = tuple(document.VibeCADTimeline.Operations)
        original_verifier = runtime_module.verify_inserted_assembly_fastener

        def reject_verification(_document, _draft):
            raise NativeAssemblyFastenerError("Forced verifier rejection.")

        runtime_module.verify_inserted_assembly_fastener = reject_verification
        try:
            failure = call(rollback_arguments, succeeds=False)
        finally:
            runtime_module.verify_inserted_assembly_fastener = original_verifier
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        assert tuple(obj.Name for obj in document.Objects) == names_before_rollback
        assert tuple(document.VibeCADTimeline.Operations) == timeline_before_rollback
        assert int(document.UndoCount) == 1
        assert not document.HasPendingTransaction

        document.undo()
        _process_events(20)
        assert document.getObject(occurrence_name) is None
        assert document.getObject(source_name) is None
        assert document.getObject(human_names[0]) is not None
        assert document.getObject(human_names[1]) is not None
        assert int(document.UndoCount) == 0
        document.redo()
        _process_events(20)
        assembly = document.getObject(assembly.Name)
        human_graph = assembly_fastener_graph_from_occurrence(
            assembly,
            document.getObject(human_names[0]),
        )
        occurrence = document.getObject(occurrence_name)
        graph, _identity = _assert_graph(
            document,
            assembly,
            occurrence,
            label=arguments["label"],
            canonical_key=identity["canonical_key"],
        )
        _assert_signature(_shape_signature(graph.source.Shape), human_signature)
        assert human_graph.occurrence.Placement.isSame(human_placement, 1.0e-9)
        assert int(document.UndoCount) == 1

        assembly_name = assembly.Name
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        assert document.recompute(None, True, True) is not False
        _process_events(24)

        assembly = document.getObject(assembly_name)
        human_graph, _human_identity = _assert_graph(
            document,
            assembly,
            document.getObject(human_names[0]),
            label="Human M6 socket bolt",
            canonical_key=identity["canonical_key"],
        )
        graph, restored_identity = _assert_graph(
            document,
            assembly,
            document.getObject(occurrence_name),
            label=arguments["label"],
            canonical_key=identity["canonical_key"],
        )
        assert graph.source.Name == source_name
        assert _graph_contract(graph) == human_contract
        _assert_signature(
            _shape_signature(graph.source.Shape),
            human_signature,
            restored=True,
        )
        assert human_graph.occurrence.Placement.isSame(human_placement, 1.0e-9)
        assert restored_identity["canonical_key"] == identity["canonical_key"]
        snapshot = build_assembly_snapshot(document)
        restored_summary = next(
            value
            for value in snapshot["assemblies"]
            if value["object_name"] == assembly_name
        )
        restored_component = next(
            value
            for value in restored_summary["components"]
            if value["object_name"] == occurrence_name
        )
        assert restored_component["standard_fastener"]["source"] == {
            "object_name": source_name
        }
        assert (
            restored_component["standard_fastener"]["canonical_key"]
            == (identity["canonical_key"])
        )

        print(
            "VIBECAD_NATIVE_ASSEMBLY_FASTENER_GUI_OK "
            "human_parity=true hidden_definition=true visible_occurrence=true "
            "exact_history=true stale_noop=true invalid_catalog_noop=true "
            "rollback=true idempotent=true undo_redo=true reopen=true "
            "snapshot=true placements_unchanged=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        Gui.Selection.clearSelection()
        if document is not None:
            try:
                Gui.activeDocument().resetEdit()
            except (AttributeError, RuntimeError):
                pass
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
