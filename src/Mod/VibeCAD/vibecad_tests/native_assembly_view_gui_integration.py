# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compiled-GUI lifecycle gate for exact Native Assembly view creation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import Part
import UtilsAssembly
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyStructureBindings import (
    ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyStructureSchema import (
    assembly_structure_capability_definition,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
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
    structure = assembly_structure_capability_definition()
    assert state is not None
    provider = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_STRUCTURE_CAPABILITY_NAME),
        schemas=(
            state.provider_schema(("active", "selection")),
            structure.provider_schema(("create_view",)),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider)


def _placement(x: float, y: float, z: float, angle: float = 0.0) -> dict:
    return {
        "origin_mm": {"x": x, "y": y, "z": z},
        "rotation": {
            "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
            "angle_degrees": angle,
        },
    }


def _assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def _view_arguments(
    summary: dict,
    *,
    label: str,
    parts_as_single_solid: bool,
    moves: list[dict],
) -> dict:
    view_state = summary["view_state"]
    assert view_state["available"] is True, view_state
    return {
        "operation": "create_view",
        "assembly": {"object_name": summary["object_name"]},
        "label": label,
        "parts_as_single_solid": parts_as_single_solid,
        "moves": moves,
        "expected_view_state_sha256": view_state["state_sha256"],
        "expected_component_count": view_state["component_count"],
        "expected_target_count": view_state[
            "solid_target_count" if parts_as_single_solid else "individual_target_count"
        ],
        "expected_view_count": view_state["view_count"],
    }


def _move_visibility_state(document, names: list[str]) -> list[tuple[str, bool, bool]]:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    return [
        (
            name,
            bool(document.getObject(name).Visibility),
            bool(timeline.VisibilityAtEnd[operations.index(document.getObject(name))]),
        )
        for name in names
    ]


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("AssemblyWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-assembly-view-")
        path = Path(temporary.name) / "native-assembly-view.FCStd"
        document = App.newDocument("NativeAssemblyViewGate")
        document.UndoMode = 1

        sources = []
        for index in range(2):
            source = document.addObject("Part::Box", f"ViewSource{index + 1}")
            source.Length = 12.0 + index
            source.Width = 10.0
            source.Height = 8.0
            sources.append(source)
        document.recompute()
        document.saveAs(str(path))

        Gui.runCommand("Assembly_CreateAssembly")
        _process_events(24)
        assembly = next(
            obj for obj in document.Objects if obj.TypeId == "Assembly::AssemblyObject"
        )
        assert Gui.activeDocument().getInEdit() is assembly.ViewObject

        document.openTransaction("Prepare Assembly view targets")
        links = []
        for index, source in enumerate(sources):
            link = assembly.newObject("App::Link", f"ViewOccurrence{index + 1}")
            link.LinkedObject = source
            link.Placement.Base.x = -45.0 if index == 0 else 45.0
            UtilsAssembly.finalizeInsertedComponentTimeline(link)
            links.append(link)
        direct_part = assembly.newObject("App::Part", "DirectPart")
        direct_part.Label = "Direct part target"
        direct_part.Placement.Base.y = 35.0
        direct_inner = direct_part.newObject("Part::Feature", "DirectInner")
        direct_inner.Label = "Inner movable target"
        direct_inner.Shape = Part.makeBox(9.0, 7.0, 6.0)
        UtilsAssembly.markTimelineOperation(direct_part)
        UtilsAssembly.markTimelineResource(direct_inner, direct_part)
        document.finalizeProvisionalTimelineOperationBlock(
            direct_part,
            [direct_inner, direct_part],
        )
        document.recompute()
        document.commitTransaction()
        _process_events(16)
        baseline = {
            obj.Name: App.Placement(obj.Placement)
            for obj in (*links, direct_part, direct_inner)
        }

        VibeGui._connect_document_observer()
        main_window = Gui.getMainWindow()
        controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
        assert controller is not None
        _select_assemble_ribbon(main_window)
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == "assemble"
        assert "Assembly_CreateView" in surface.command_ids
        frozen = NativeSurfaceSnapshot.from_surface(surface)

        registry = build_native_capability_registry()
        structure = registry.definition(ASSEMBLY_STRUCTURE_CAPABILITY_NAME)
        assert structure is not None
        variant = next(
            value for value in structure.variants if value.operation == "create_view"
        )
        assert variant.action_ids == frozenset({"Assembly_CreateView"})
        assert variant.transaction_behavior == "document"
        assert registry.implementation(ASSEMBLY_STRUCTURE_CAPABILITY_NAME) is not None

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-assembly-view-gui")

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

        call_number = 0

        def call(arguments: dict, *, succeeds: bool = True, call_id: str = "") -> dict:
            nonlocal call_number
            call_number += 1
            task_before = Gui.Control.activeTaskDialog()
            result = dispatcher.call(
                ASSEMBLY_STRUCTURE_CAPABILITY_NAME,
                json.dumps(arguments, separators=(",", ":")),
                call_id or f"assembly-view-call-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert Gui.activeDocument().getInEdit() is assembly.ViewObject
            assert Gui.Control.activeTaskDialog() is task_before
            return result

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(links[1])
        assembly.ViewObject.EnableMovement = False
        assembly.ViewObject.DraggerVisibility = True
        document.clearUndos()

        initial = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-view-state-initial",
        )
        assert initial["ok"] is True, initial
        summary = _assembly_summary(initial, assembly.Name)
        view_state = summary["view_state"]
        assert view_state["component_count"] == 3
        assert view_state["individual_target_count"] == 4
        assert view_state["solid_target_count"] == 3
        assert view_state["view_count"] == 0
        assert any(
            item["object_name"] == direct_inner.Name
            and item["target_modes"] == ["individual_objects"]
            for item in view_state["movable_targets"]
        )

        malformed = _view_arguments(
            summary,
            label="Malformed",
            parts_as_single_solid=False,
            moves=[
                {
                    "kind": "normal",
                    "targets": [{"object_name": direct_inner.Name}],
                    "transform": _placement(0.0, 0.0, 5.0),
                }
            ],
        )
        malformed["unexpected"] = True
        before_objects = tuple(document.Objects)
        failure = call(malformed, succeeds=False)
        assert failure["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(document.Objects) == before_objects
        assert int(document.UndoCount) == 0

        first_arguments = _view_arguments(
            summary,
            label="Native service sequence",
            parts_as_single_solid=False,
            moves=[
                {
                    "kind": "normal",
                    "targets": [{"object_name": direct_inner.Name}],
                    "transform": _placement(0.0, 0.0, 24.0),
                },
                {
                    "kind": "normal",
                    "targets": [{"object_name": links[0].Name}],
                    "transform": _placement(0.0, 14.0, 0.0, 12.0),
                },
            ],
        )
        first_call_id = "assembly-view-create-first"
        first = call(first_arguments, call_id=first_call_id)
        assert first["label"] == "Native service sequence"
        assert first["view_count"] == 1
        assert first["move_count"] == 2
        assert first["normal_move_count"] == 2
        assert first["radial_move_count"] == 0
        assert first["target_reference_count"] == 2
        assert first["explosion_line_count"] == 2
        assert first["selection_unchanged"] is True
        assert first["assembly_placements_restored"] is True
        assert first["assistant_undo_available"] is True
        assert Gui.Selection.getSelection() == [links[1]]
        assert not assembly.ViewObject.EnableMovement
        assert assembly.ViewObject.DraggerVisibility
        assert all(
            obj.Placement.isSame(baseline[obj.Name], 1.0e-9)
            for obj in (*links, direct_part, direct_inner)
        )
        assert int(document.UndoCount) == 1

        view_group_name = first["view_group"]["object_name"]
        first_view_name = first["view"]["object_name"]
        first_view = document.getObject(first_view_name)
        first_step_names = [step.Name for step in first_view.Group]
        assert [step.MoveType for step in first_view.Group] == ["Normal", "Normal"]
        assert first_view.Group[0].References[0] is assembly
        assert list(first_view.Group[0].References[1]) == [
            f"{direct_part.Name}.{direct_inner.Name}."
        ]
        assert first_view.Group[1].References[0] is assembly
        assert list(first_view.Group[1].References[1]) == [f"{links[0].Name}."]
        assert all(not step.Visibility for step in first_view.Group)
        visibility_state = _move_visibility_state(document, first_step_names)
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-create",
            visibility_state,
        )

        replay = call(first_arguments, call_id=first_call_id)
        assert replay == first
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events(20)
        assert document.getObject(view_group_name) is None
        assert document.getObject(first_view_name) is None
        assert all(document.getObject(name) is None for name in first_step_names)
        assert Gui.Selection.getSelection() == [links[1]]
        document.redo()
        _process_events(20)
        first_view = document.getObject(first_view_name)
        assert first_view is not None
        assert [step.Name for step in first_view.Group] == first_step_names
        assert all(
            document.getObject(name).VibeCADTimelineOwner is first_view
            for name in first_step_names
        )
        visibility_state = _move_visibility_state(document, first_step_names)
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-redo",
            visibility_state,
        )

        after_first = dispatcher.call(
            "state.read",
            '{"operation":"active"}',
            "assembly-view-state-after-first",
        )
        assert after_first["ok"] is True, after_first
        after_summary = _assembly_summary(after_first, assembly.Name)
        stale_arguments = _view_arguments(
            after_summary,
            label="Stale radial view",
            parts_as_single_solid=True,
            moves=[
                {
                    "kind": "radial",
                    "targets": [{"object_name": links[0].Name}],
                    "radial_distance_mm": 18.0,
                }
            ],
        )
        stale_arguments["expected_view_state_sha256"] = view_state["state_sha256"]
        stale = call(stale_arguments, succeeds=False)
        assert stale["error_code"] == "NATIVE_ASSEMBLY_VIEW_FAILED"
        assert int(document.UndoCount) == 1

        second_arguments = _view_arguments(
            after_summary,
            label="Native radial service view",
            parts_as_single_solid=True,
            moves=[
                {
                    "kind": "radial",
                    "targets": [{"object_name": links[0].Name}],
                    "radial_distance_mm": 18.0,
                }
            ],
        )
        second = call(second_arguments)
        assert second["view_group"]["object_name"] == view_group_name
        assert second["view_count"] == 2
        assert second["move_count"] == 1
        assert second["normal_move_count"] == 0
        assert second["radial_move_count"] == 1
        assert second["explosion_line_count"] == 1
        assert int(document.UndoCount) == 2
        second_view_name = second["view"]["object_name"]
        second_view = document.getObject(second_view_name)
        second_step_name = second_view.Group[0].Name
        assert second_view.Group[0].MoveType == "Radial"
        assert abs(second_view.Group[0].MovementTransform.Base.x - 18.0) < 1.0e-9
        visibility_state = _move_visibility_state(
            document,
            [*first_step_names, second_step_name],
        )
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-second-create",
            visibility_state,
        )
        assert all(
            obj.Placement.isSame(baseline[obj.Name], 1.0e-9)
            for obj in (*links, direct_part, direct_inner)
        )

        document.undo()
        _process_events(16)
        assert document.getObject(second_view_name) is None
        assert document.getObject(second_step_name) is None
        assert document.getObject(first_view_name) is not None
        document.redo()
        _process_events(16)
        assert document.getObject(second_view_name) is not None
        visibility_state = _move_visibility_state(
            document,
            [*first_step_names, second_step_name],
        )
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-second-redo",
            visibility_state,
        )

        assembly_name = assembly.Name
        target_names = [obj.Name for obj in (*links, direct_part, direct_inner)]
        Gui.Selection.clearSelection()
        Gui.activeDocument().resetEdit()
        _process_events(16)
        visibility_state = _move_visibility_state(
            document,
            [*first_step_names, second_step_name],
        )
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "before-save",
            visibility_state,
        )
        document.save()
        visibility_state = _move_visibility_state(
            document,
            [*first_step_names, second_step_name],
        )
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-save",
            visibility_state,
        )
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        App.setActiveDocument(document.Name)
        _process_events(24)

        assembly = document.getObject(assembly_name)
        view_group = document.getObject(view_group_name)
        first_view = document.getObject(first_view_name)
        second_view = document.getObject(second_view_name)
        assert assembly is not None and view_group in list(assembly.Group)
        assert list(view_group.Group) == [first_view, second_view]
        assert type(first_view.Proxy).__name__ == "ExplodedView"
        assert type(second_view.Proxy).__name__ == "ExplodedView"
        assert [type(step.Proxy).__name__ for step in first_view.Group] == [
            "ExplodedViewStep",
            "ExplodedViewStep",
        ]
        assert type(second_view.Group[0].Proxy).__name__ == "ExplodedViewStep"
        assert all(
            document.getObject(name).Placement.isSame(baseline[name], 1.0e-9)
            for name in target_names
        )
        visibility_state = _move_visibility_state(
            document,
            [*first_step_names, second_step_name],
        )
        assert all(
            not live and not accepted for _name, live, accepted in visibility_state
        ), (
            "after-reopen",
            visibility_state,
        )
        timeline = document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        for current_view in (first_view, second_view):
            index = operations.index(current_view)
            assert operations[index - len(current_view.Group) : index] == list(
                current_view.Group
            )
            assert all(
                step.VibeCADTimelineOwner is current_view and not step.Visibility
                for step in current_view.Group
            ), [
                (
                    step.Name,
                    getattr(step.VibeCADTimelineOwner, "Name", None),
                    current_view.Name,
                    bool(step.Visibility),
                )
                for step in current_view.Group
            ]

        print(
            "VIBECAD_NATIVE_ASSEMBLY_VIEW_GUI_OK "
            "views=2 normal_moves=2 radial_moves=1 nested_target=true "
            "stale_noop=true undo_redo=true reopen=true placements_restored=true",
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
