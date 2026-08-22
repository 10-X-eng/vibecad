# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Assembly BOM creation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import CommandCreateBom
import Part
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyBomState import (
    capture_assembly_bom_state,
    read_bom_table,
)
from VibeCADNativeAssemblyBomBindings import (
    ASSEMBLY_BOM_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyBomSchema import (
    assembly_bom_capability_definition,
)
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
from VibeCADNativeSessionFactory import _edit_or_task_active
from VibeCADRibbonSurface import read_active_ribbon_surface


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


def _focused_turn(surface, registry) -> NativeTurnSnapshot:
    state = registry.definition("state.read")
    bom = assembly_bom_capability_definition()
    assert state is not None
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_BOM_CAPABILITY_NAME),
        schemas=(
            state.provider_schema(("active", "selection")),
            bom.provider_schema(("create",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)


def _assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _bom_arguments(
    *,
    label: str,
    columns: list[str],
    detail_subassemblies: bool,
    detail_parts: bool,
    only_parts: bool,
) -> dict:
    return {
        "label": label,
        "columns": columns,
        "detail_subassemblies": detail_subassemblies,
        "detail_parts": detail_parts,
        "only_parts": only_parts,
    }


def _timeline_accepts(document, bom) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    visibility = list(timeline.VisibilityAtEnd)
    assert bom in operations
    assert bool(visibility[operations.index(bom)]) == bool(bom.Visibility)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-assembly-bom-")
        path = Path(temporary.name) / "native-assembly-bom.FCStd"
        document = App.newDocument("NativeAssemblyBomGate")
        document.UndoMode = 1

        source_part = document.addObject("App::Part", "DriveModule")
        source_part.Label = "Drive module"
        source_part.addProperty("App::PropertyString", "PartNumber", "BOM")
        source_part.PartNumber = "DM-100"
        source_part.addProperty("App::PropertyFloat", "UnitCost", "BOM")
        source_part.UnitCost = 125.5
        nested = source_part.newObject("Part::Feature", "DriveHousing")
        nested.Label = "Drive housing"
        nested.Shape = Part.makeBox(20.0, 14.0, 8.0)
        nested.addProperty("App::PropertyString", "PartNumber", "BOM")
        nested.PartNumber = "DH-110"
        nested.addProperty("App::PropertyFloat", "UnitCost", "BOM")
        nested.UnitCost = 48.25

        source_solid = document.addObject("Part::Feature", "ServiceBolt")
        source_solid.Label = "Service bolt"
        source_solid.Shape = Part.makeCylinder(3.0, 12.0)
        source_solid.addProperty("App::PropertyString", "PartNumber", "BOM")
        source_solid.PartNumber = "SB-220"
        source_solid.addProperty("App::PropertyFloat", "UnitCost", "BOM")
        source_solid.UnitCost = 2.75
        document.recompute()

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.openTransaction("Prepare Assembly BOM sources")
        occurrences = []
        for index, source in enumerate((source_part, source_part, source_solid)):
            occurrence = assembly.newObject("App::Link", f"BomOccurrence{index + 1}")
            occurrence.LinkedObject = source
            occurrence.Placement.Base.x = float(index * 35)
            UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)
            occurrences.append(occurrence)
        document.recompute()
        document.commitTransaction()
        document.saveAs(str(path))

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateBom" in surface.command_ids
        frozen = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        full_provider_surface = resolve_native_provider_surface(surface, registry)
        assert full_provider_surface.available, full_provider_surface.summary()
        assert ASSEMBLY_BOM_CAPABILITY_NAME in full_provider_surface.tool_names
        assert next(
            schema
            for schema in full_provider_surface.schemas
            if schema["name"] == ASSEMBLY_BOM_CAPABILITY_NAME
        )["parameters"]["oneOf"][0]["required"] == []
        bom_definition = registry.definition(ASSEMBLY_BOM_CAPABILITY_NAME)
        assert bom_definition is not None
        variant = bom_definition.variants[0]
        assert variant.operation == "create"
        assert variant.action_ids == frozenset({"Assembly_CreateBom"})
        assert variant.transaction_behavior == "document"
        assert registry.implementation(ASSEMBLY_BOM_CAPABILITY_NAME) is not None

        service = get_service()
        service.select_modeling_engine("native")
        task_summary = service.task_panel_summary()
        assert _edit_or_task_active(service) is False, task_summary
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-bom-gui")

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
            edit_or_task_active=lambda: _edit_or_task_active(service),
        )
        turn = _focused_turn(surface, registry)
        def new_dispatcher() -> NativeTurnDispatcher:
            return NativeTurnDispatcher(
                document=document,
                state=state,
                registry=registry,
                turn=turn,
                runtimes=build_native_runtime_bindings(context, turn.tool_names),
                reauthorize_turn=reauthorize,
                active_document=lambda: App.ActiveDocument,
            )

        dispatcher = new_dispatcher()

        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_number
            call_number += 1
            task_before = Gui.Control.activeTaskDialog()
            subwindow_before = mdi_area.activeSubWindow()
            result = dispatcher.call(
                ASSEMBLY_BOM_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-bom-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert Gui.Control.activeTaskDialog() is task_before
            assert mdi_area.activeSubWindow() is subwindow_before
            return result

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrences[2])
        baseline = {
            occurrence.Name: App.Placement(occurrence.Placement)
            for occurrence in occurrences
        }
        document.clearUndos()
        assert not Gui.Control.activeDialog()

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-bom-state-initial",
        )
        assert initial["ok"] is True, initial
        summary = _assembly_summary(initial, assembly.Name)
        assert summary["counts"] == {
            "components": 3,
            "joints": 0,
            "grounded": 0,
        }
        assert summary["artifacts"]["boms"] == 0

        malformed = _bom_arguments(
            label="Malformed",
            columns=["Name"],
            detail_subassemblies=False,
            detail_parts=False,
            only_parts=False,
        )
        malformed["unexpected"] = True
        before_objects = tuple(document.Objects)
        failure = call(malformed, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(document.Objects) == before_objects
        assert int(document.UndoCount) == 0

        first_arguments = {
            "columns": [
                "Index",
                "Name",
                ".PartNumber",
                ".UnitCost",
                "Quantity",
                "File Name",
            ],
        }
        first_call_id = "assembly-bom-create-first"
        first = call(first_arguments, call_id=first_call_id)
        assert first["operation"] == "create"
        assert first["verified"] is True
        assert "table_sha256" not in first
        assert "bom_state_sha256" not in first
        assert first["label"] == "Bill of Materials"
        assert first["component_count"] == 3
        assert first["bom_count"] == 1
        assert first["row_count"] == 3
        assert first["assistant_undo_available"] is True
        assert Gui.Selection.getSelection() == [occurrences[2]]
        assert all(
            occurrence.Placement.isSame(baseline[occurrence.Name], 1.0e-9)
            for occurrence in occurrences
        )
        assert int(document.UndoCount) == 1

        group_name = first["bom_group"]["object_name"]
        first_name = first["bom"]["object_name"]
        group = document.getObject(group_name)
        first_bom = document.getObject(first_name)
        assert group.TypeId == "Assembly::BomGroup"
        assert list(group.Group) == [first_bom]
        assert CommandCreateBom._findBomAssembly(first_bom) is assembly
        assert first_bom.ViewObject.TypeId == "AssemblyGui::ViewProviderBom"
        assert list(first_bom.columnsNames) == first_arguments["columns"]
        assert first_bom.detailParts and first_bom.detailSubAssemblies
        assert not first_bom.onlyParts and first_bom.autoGenerate
        _timeline_accepts(document, first_bom)
        first_table = read_bom_table(first_bom)
        assert first_table["headers"] == first_arguments["columns"]
        assert first_table["row_count"] == 3
        rows = first_table["row_preview"]
        assert [row["Name"] for row in rows] == [
            "Drive module",
            "Drive housing",
            "Service bolt",
        ]
        assert [row[".PartNumber"] for row in rows] == [
            "DM-100",
            "DH-110",
            "SB-220",
        ]
        assert [row["Quantity"] for row in rows] == ["2", "1", "1"]

        replay = call(first_arguments, call_id=first_call_id)
        assert replay == first
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events(20)
        assert document.getObject(group_name) is None
        assert document.getObject(first_name) is None
        assert Gui.Selection.getSelection() == [occurrences[2]]
        document.redo()
        _process_events(20)
        group = document.getObject(group_name)
        first_bom = document.getObject(first_name)
        assert group is not None and list(group.Group) == [first_bom]
        assert CommandCreateBom._findBomAssembly(first_bom) is assembly
        assert read_bom_table(first_bom) == first_table
        _timeline_accepts(document, first_bom)

        changed_turn = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-bom-state-after-undo-redo",
        )
        assert changed_turn["error_code"] == "NATIVE_REVISION_CONFLICT"
        dispatcher = new_dispatcher()

        after_first = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-bom-state-after-first",
        )
        assert after_first["ok"] is True, after_first
        after_summary = _assembly_summary(after_first, assembly.Name)
        assert after_summary["artifacts"]["boms"] == 1

        second_arguments = _bom_arguments(
            label="Native parts-only BOM",
            columns=["Name", "Quantity"],
            detail_subassemblies=True,
            detail_parts=False,
            only_parts=True,
        )
        second = call(second_arguments)
        assert second["bom_group"]["object_name"] == group_name
        assert second["bom_count"] == 2
        assert second["row_count"] == 1
        assert int(document.UndoCount) == 2
        second_name = second["bom"]["object_name"]
        second_bom = document.getObject(second_name)
        assert list(group.Group) == [first_bom, second_bom]
        assert second_bom.detailSubAssemblies and not second_bom.detailParts
        assert second_bom.onlyParts and second_bom.autoGenerate
        second_table = read_bom_table(second_bom)
        assert second_table["headers"] == ["Name", "Quantity"]
        assert second_table["row_preview"] == [
            {"Name": "Drive module", "Quantity": "2"}
        ]
        _timeline_accepts(document, second_bom)

        document.undo()
        _process_events(16)
        assert document.getObject(second_name) is None
        assert document.getObject(first_name) is not None
        document.redo()
        _process_events(16)
        group = document.getObject(group_name)
        second_bom = document.getObject(second_name)
        assert list(group.Group) == [document.getObject(first_name), second_bom]
        assert read_bom_table(second_bom) == second_table
        _timeline_accepts(document, second_bom)

        assembly_name = assembly.Name
        occurrence_names = [occurrence.Name for occurrence in occurrences]
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        group = document.getObject(group_name)
        first_bom = document.getObject(first_name)
        second_bom = document.getObject(second_name)
        assert assembly is not None and group in list(assembly.Group)
        assert list(group.Group) == [first_bom, second_bom]
        assert CommandCreateBom._findBomAssembly(first_bom) is assembly
        assert CommandCreateBom._findBomAssembly(second_bom) is assembly
        assert list(first_bom.columnsNames) == first_arguments["columns"]
        assert list(second_bom.columnsNames) == second_arguments["columns"]
        assert read_bom_table(first_bom) == first_table
        assert read_bom_table(second_bom) == second_table
        assert all(
            document.getObject(name).Placement.isSame(baseline[name], 1.0e-9)
            for name in occurrence_names
        )
        _timeline_accepts(document, first_bom)
        _timeline_accepts(document, second_bom)
        restored = capture_assembly_bom_state(assembly)
        assert restored.bom_group is group
        assert restored.boms == (first_bom, second_bom)
        assert len(restored.components) == 3
        assert len(restored.source_records) == 4

        print(
            "VIBECAD_NATIVE_ASSEMBLY_BOM_GUI_OK "
            f"tools={len(full_provider_surface.tool_names)} "
            f"schema_bytes={len(json.dumps(full_provider_surface.schemas, separators=(',', ':')).encode('utf-8'))} "
            "boms=2 rows=4 properties=true quantity_aggregation=true "
            "parts_filter=true stale_turn=true idempotent=true undo_redo=true "
            "reopen=true owner=true no_sheet_opened=true placements_unchanged=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
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
