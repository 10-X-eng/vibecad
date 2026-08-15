# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for Native Assembly fastener insertion and editing."""

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


def _definition(
    *,
    length_mm: float | None = 25.0,
    model_thread: bool = False,
    standard: str = "ISO4762",
) -> dict[str, object]:
    return {
        "standard": standard,
        "nominal_thread": "M6",
        "length_mm": length_mm,
        "model_thread": model_thread,
        "left_handed": False,
        "options": {},
    }


def _dialog_values(label: str, *, length_mm: float = 25.0) -> dict[str, object]:
    definition = _definition(length_mm=length_mm)
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


def _human_edit(document, assembly, graph):
    values = _dialog_values("Human edited M6 socket bolt", length_mm=30.0)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(graph.occurrence)
    _process_events(12)
    command = Gui.Command.get("VibeCAD_EditStandardFastener")
    assert command is not None and command.isActive()
    with mock.patch.object(VibeCADFastenersGui, "_FastenerDialog") as dialog:
        dialog.return_value.exec.return_value = values
        Gui.runCommand("VibeCAD_EditStandardFastener")
    _process_events(24)
    updated, identity = _assert_graph(
        document,
        assembly,
        graph.occurrence,
        label=values["label"],
        canonical_key=values["identity"]["canonical_key"],
    )
    assert updated.occurrence is graph.occurrence
    assert updated.source is graph.source
    assert identity == values["identity"]
    return updated, _shape_signature(updated.source.Shape)


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
            fastener.provider_schema(
                ("insert_standard_fastener", "edit_standard_fastener")
            ),
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


def _edit_arguments(
    summary: dict,
    component: dict,
    *,
    label: str,
) -> dict[str, object]:
    diagnosis = summary["diagnosis_state"]
    fastener = component["standard_fastener"]
    assert diagnosis["available"] is True, diagnosis
    return {
        "operation": "edit_standard_fastener",
        "assembly": {"object_name": summary["object_name"]},
        "occurrence": {"object_name": component["object_name"]},
        "definition_source": dict(fastener["source"]),
        "label": label,
        "definition": _definition(length_mm=30.0),
        "expected_fastener_state_sha256": fastener["state_sha256"],
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
        human_graph, human_edit_signature = _human_edit(
            document,
            assembly,
            human_graph,
        )
        human_edit_key = str(human_graph.identity["canonical_key"])
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
        assert "VibeCAD_EditStandardFastener" in surface.command_ids
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
        edit_variant = next(
            value
            for value in fastener_definition.variants
            if value.operation == "edit_standard_fastener"
        )
        assert edit_variant.action_ids == frozenset({"VibeCAD_EditStandardFastener"})
        assert edit_variant.surface_ids == frozenset({"assemble"})
        assert registry.implementation(ASSEMBLY_FASTENER_CAPABILITY_NAME) is not None
        production = resolve_native_provider_surface(surface, registry)
        assert production.available is True, production.summary()
        assert ASSEMBLY_FASTENER_CAPABILITY_NAME not in (
            production.missing_definition_names
        )
        assert ASSEMBLY_FASTENER_CAPABILITY_NAME not in (
            production.missing_implementation_names
        )
        assert ASSEMBLY_FASTENER_CAPABILITY_NAME not in (
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
            selection_before = tuple(
                (entry.Object, tuple(entry.SubElementNames))
                for entry in Gui.Selection.getSelectionEx("", 0)
            )
            result = dispatcher.call(
                ASSEMBLY_FASTENER_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-fastener-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert Gui.Control.activeTaskDialog() is task_before
            assert mdi_area.activeSubWindow() is subwindow_before
            assert (
                tuple(
                    (entry.Object, tuple(entry.SubElementNames))
                    for entry in Gui.Selection.getSelectionEx("", 0)
                )
                == selection_before
            )
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
        graph, insert_identity = _assert_graph(
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
        assert component["standard_fastener"]["source"] == {"object_name": source_name}
        assert component["standard_fastener"]["state_sha256"]
        for name, value in result["fastener"].items():
            assert component["standard_fastener"][name] == value

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
            canonical_key=insert_identity["canonical_key"],
        )
        _assert_signature(_shape_signature(graph.source.Shape), human_signature)
        assert human_graph.occurrence.Placement.isSame(human_placement, 1.0e-9)
        assert int(document.UndoCount) == 1

        occurrence.Placement = App.Placement(
            App.Vector(-11.0, 6.0, 4.0),
            App.Rotation(App.Vector(1.0, 0.0, 0.0), 17.0),
        )
        document.recompute()
        edited_placement = App.Placement(occurrence.Placement)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        _process_events(12)
        document.clearUndos()
        assert int(document.UndoCount) == 0

        before_edit_state = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-fastener-state-before-edit",
        )
        assert before_edit_state["ok"] is True, before_edit_state
        before_edit_summary = _assembly_summary(before_edit_state, assembly.Name)
        edit_component = next(
            value
            for value in before_edit_summary["components"]
            if value["object_name"] == occurrence_name
        )
        edit_arguments = _edit_arguments(
            before_edit_summary,
            edit_component,
            label="Native edited M6 socket bolt",
        )
        pre_edit_signature = _shape_signature(graph.source.Shape)
        pre_edit_timeline = tuple(document.VibeCADTimeline.Operations)
        pre_edit_names = tuple(obj.Name for obj in document.Objects)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(human_graph.occurrence)
        wrong_selection = call(edit_arguments, succeeds=False)
        assert wrong_selection["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)

        stale_fastener = {
            **edit_arguments,
            "expected_fastener_state_sha256": "0" * 64,
        }
        failure = call(stale_fastener, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        wrong_source = {
            **edit_arguments,
            "definition_source": {"object_name": human_names[1]},
        }
        failure = call(wrong_source, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        incompatible = {
            **edit_arguments,
            "definition": _definition(length_mm=None, standard="ISO4032"),
        }
        failure = call(incompatible, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        shared_definition = document.addObject(
            "App::Link",
            "ManuallySharedFastenerDefinition",
        )
        shared_definition.LinkedObject = source
        document.recompute()
        failure = call(edit_arguments, succeeds=False)
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        document.removeObject(shared_definition.Name)
        document.recompute()
        document.clearUndos()
        assert tuple(obj.Name for obj in document.Objects) == pre_edit_names
        assert tuple(document.VibeCADTimeline.Operations) == pre_edit_timeline
        assert occurrence.Placement.isSame(edited_placement, 1.0e-9)
        assert int(document.UndoCount) == 0

        original_edit_verifier = runtime_module.verify_edited_assembly_fastener
        runtime_module.verify_edited_assembly_fastener = reject_verification
        try:
            failure = call(edit_arguments, succeeds=False)
        finally:
            runtime_module.verify_edited_assembly_fastener = original_edit_verifier
        assert failure["error_code"] == "NATIVE_ASSEMBLY_FASTENER_FAILED"
        graph, restored_after_rollback = _assert_graph(
            document,
            assembly,
            occurrence,
            label=arguments["label"],
            canonical_key=insert_identity["canonical_key"],
        )
        assert (
            restored_after_rollback["canonical_key"]
            == (insert_identity["canonical_key"])
        )
        _assert_signature(_shape_signature(graph.source.Shape), pre_edit_signature)
        assert occurrence.Placement.isSame(edited_placement, 1.0e-9)
        assert tuple(obj.Name for obj in document.Objects) == pre_edit_names
        assert tuple(document.VibeCADTimeline.Operations) == pre_edit_timeline
        assert int(document.UndoCount) == 0
        assert not document.HasPendingTransaction

        edit_call_id = "assembly-fastener-edit-first"
        edited = call(edit_arguments, call_id=edit_call_id)
        assert set(edited) == {
            "ok",
            "operation",
            "assembly",
            "occurrence",
            "definition_source",
            "label",
            "fastener",
            "fastener_state_sha256",
            "solid_count",
            "volume_mm3",
            "component_count",
            "grounded_count",
            "joint_count",
            "assembly_state_sha256",
            "receipt",
            "assistant_undo_available",
        }
        assert edited["operation"] == "edit_standard_fastener"
        assert edited["occurrence"]["object_name"] == occurrence_name
        assert edited["definition_source"]["object_name"] == source_name
        assert edited["label"] == edit_arguments["label"]
        assert edited["component_count"] == 2
        assert edited["solid_count"] == 1
        assert _close(edited["volume_mm3"], graph.source.Shape.Volume)
        assert edited["assistant_undo_available"] is True
        assert edited["receipt"]["created"] == []
        assert {item["object_name"] for item in edited["receipt"]["changed"]} == {
            occurrence_name,
            source_name,
        }
        assert int(document.UndoCount) == 1
        graph, edited_identity = _assert_graph(
            document,
            assembly,
            occurrence,
            label=edit_arguments["label"],
            canonical_key=edited["fastener"]["canonical_key"],
        )
        assert graph.occurrence.Name == occurrence_name
        assert graph.source.Name == source_name
        assert _graph_contract(graph) == human_contract
        _assert_signature(_shape_signature(graph.source.Shape), human_edit_signature)
        assert occurrence.Placement.isSame(edited_placement, 1.0e-9)

        edit_replay = call(edit_arguments, call_id=edit_call_id)
        assert edit_replay == edited
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events(20)
        occurrence = document.getObject(occurrence_name)
        graph, _restored_insert_identity = _assert_graph(
            document,
            assembly,
            occurrence,
            label=arguments["label"],
            canonical_key=insert_identity["canonical_key"],
        )
        _assert_signature(_shape_signature(graph.source.Shape), pre_edit_signature)
        assert occurrence.Placement.isSame(edited_placement, 1.0e-9)
        document.redo()
        _process_events(20)
        occurrence = document.getObject(occurrence_name)
        graph, edited_identity = _assert_graph(
            document,
            assembly,
            occurrence,
            label=edit_arguments["label"],
            canonical_key=edited_identity["canonical_key"],
        )
        _assert_signature(_shape_signature(graph.source.Shape), human_edit_signature)
        assert occurrence.Placement.isSame(edited_placement, 1.0e-9)

        after_edit_state = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-fastener-state-after-edit",
        )
        assert after_edit_state["ok"] is True, after_edit_state
        after_edit_summary = _assembly_summary(after_edit_state, assembly.Name)
        edited_component = next(
            value
            for value in after_edit_summary["components"]
            if value["object_name"] == occurrence_name
        )
        assert (
            edited_component["standard_fastener"]["state_sha256"]
            == (edited["fastener_state_sha256"])
        )
        for name, value in edited["fastener"].items():
            assert edited_component["standard_fastener"][name] == value

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
            label="Human edited M6 socket bolt",
            canonical_key=human_edit_key,
        )
        graph, restored_identity = _assert_graph(
            document,
            assembly,
            document.getObject(occurrence_name),
            label=edit_arguments["label"],
            canonical_key=edited_identity["canonical_key"],
        )
        assert graph.source.Name == source_name
        assert _graph_contract(graph) == human_contract
        _assert_signature(
            _shape_signature(graph.source.Shape),
            human_edit_signature,
            restored=True,
        )
        assert human_graph.occurrence.Placement.isSame(human_placement, 1.0e-9)
        assert graph.occurrence.Placement.isSame(edited_placement, 1.0e-9)
        assert restored_identity["canonical_key"] == edited_identity["canonical_key"]
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
            == (edited_identity["canonical_key"])
        )

        print(
            "VIBECAD_NATIVE_ASSEMBLY_FASTENER_GUI_OK "
            "human_parity=true hidden_definition=true visible_occurrence=true "
            "exact_history=true edit_in_place=true exact_selected_target=true "
            "compatible_guard=true shared_definition_guard=true stale_noop=true "
            "invalid_catalog_noop=true "
            "rollback=true idempotent=true undo_redo=true reopen=true snapshot=true "
            "placements_unchanged=true",
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
