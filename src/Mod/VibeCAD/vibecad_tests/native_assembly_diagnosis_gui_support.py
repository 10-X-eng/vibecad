# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared real-GUI harness for exact Native Assembly diagnosis gates."""

from __future__ import annotations

from typing import Iterable

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import JointObject
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeAssemblyDiagnosisBindings import (
    ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME,
)
from VibeCADNativeAssemblyDiagnosisSchema import (
    assembly_diagnosis_capability_definition,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


def process_events(rounds: int = 20) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def select_assemble_ribbon(main_window) -> None:
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
    process_events(24)
    assert Gui.activeWorkbench().name() == "AssemblyWorkbench"


def joint_group(assembly):
    groups = [
        child for child in assembly.Group if child.TypeId == "Assembly::JointGroup"
    ]
    assert len(groups) == 1
    return groups[0]


def create_fixed_joint(group, first, second, name: str):
    joint = group.newObject("App::FeaturePython", name)
    JointObject.Joint(joint, 0)
    JointObject.ensureViewProviderJoint(joint)
    joint.Proxy.setJointConnectors(
        joint,
        [
            [first, ["", ""]],
            [second, ["", ""]],
        ],
    )
    return joint


def assembly_summary(state: dict, assembly_name: str) -> dict:
    return next(
        item
        for item in state["domain"]["assemblies"]
        if item["object_name"] == assembly_name
    )


def focused_turn(surface, registry, operations: Iterable[str]) -> NativeTurnSnapshot:
    state_definition = registry.definition("state.read")
    assert state_definition is not None
    diagnosis = assembly_diagnosis_capability_definition()
    selected_operations = tuple(operations)
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot.from_surface(surface),
        available=True,
        unavailable_reason="",
        tool_names=("state.read", ASSEMBLY_DIAGNOSIS_CAPABILITY_NAME),
        schemas=(
            state_definition.provider_schema(("active", "selection")),
            diagnosis.provider_schema(selected_operations),
        ),
        human_only_action_ids=("Assembly_ActivateAssembly",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(provider_surface)


def dispatcher(document, surface, registry, controller, run_id, operations):
    frozen_surface = NativeSurfaceSnapshot.from_surface(surface)
    service = get_service()
    service.select_modeling_engine("native")
    state = service.native_document_state_store()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run(run_id)

    def reauthorize() -> None:
        require_frozen_native_surface(frozen_surface, controller)

    context = NativeRuntimeContext(
        service=service,
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
        active_surface_id=lambda: read_active_ribbon_surface(controller).surface_id,
        edit_or_task_active=lambda: Gui.Control.activeDialog() is not None,
    )
    turn = focused_turn(surface, registry, operations)
    return NativeTurnDispatcher(
        document=document,
        state=state,
        registry=registry,
        turn=turn,
        runtimes=build_native_runtime_bindings(context, turn.tool_names),
        reauthorize_turn=reauthorize,
        active_document=lambda: App.ActiveDocument,
    )


def active_assemble_surface():
    VibeGui._connect_document_observer()
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    assert controller is not None
    select_assemble_ribbon(main_window)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "assemble"
    return controller, surface
