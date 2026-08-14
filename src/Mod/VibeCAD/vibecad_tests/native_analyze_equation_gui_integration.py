# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native Elmer equation creation and reads."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import ObjectsFem
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeEquationSchema import ANALYZE_EQUATION_CAPABILITY_NAME
from VibeCADNativeAnalyzeEquationState import equation_state
from VibeCADNativeAnalyzeHistory import creation_boundary, publish_operation
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


EQUATION_OPERATIONS = (
    "create_elasticity",
    "create_deformation",
    "create_electrostatic",
    "create_electric_force",
    "create_magnetodynamic",
    "create_magnetodynamic_2d",
    "create_static_current",
    "create_flow",
    "create_flux",
    "create_heat",
)


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(i for i in range(tabs.count()) if str(tabs.tabData(i)) == "FemWorkbench")
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    expected = {
        "FEM_EquationElasticity": "create_elasticity",
        "FEM_EquationDeformation": "create_deformation",
        "FEM_EquationElectrostatic": "create_electrostatic",
        "FEM_EquationElectricforce": "create_electric_force",
        "FEM_EquationMagnetodynamic": "create_magnetodynamic",
        "FEM_EquationMagnetodynamic2D": "create_magnetodynamic_2d",
        "FEM_EquationStaticCurrent": "create_static_current",
        "FEM_EquationFlow": "create_flow",
        "FEM_EquationFlux": "create_flux",
        "FEM_EquationHeat": "create_heat",
    }
    assert set(expected) <= set(surface.command_ids)
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected
    }
    assert set(plans) == set(expected)
    for action_id, operation in expected.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_EQUATION_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert plan.transaction_behavior == "document"
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    equation = registry.definition(ANALYZE_EQUATION_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert equation is not None and inspect is not None
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(ANALYZE_EQUATION_CAPABILITY_NAME, ANALYZE_INSPECT_CAPABILITY_NAME),
            schemas=(
                equation.provider_schema(EQUATION_OPERATIONS),
                inspect.provider_schema(("equation",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _solver_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _equation_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _setup(document):
    document.openTransaction("Create Elmer equation gate setup")
    try:
        analysis_boundary = creation_boundary(document)
        analysis = ObjectsFem.makeAnalysis(document, "EquationGateAnalysis")
        analysis.Label = "Equation Gate Analysis"
        publish_operation(document, analysis_boundary, analysis)

        solver_boundary = creation_boundary(document)
        from femcommands.commands import createDefaultSolverFeature

        solver = createDefaultSolverFeature(document, "Elmer")
        solver.Label = "Equation Gate Elmer"
        analysis.addObject(solver)
        publish_operation(document, solver_boundary, solver)
        assert document.recompute([analysis, solver], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return analysis, solver


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-equation-")
        path = Path(temporary.name) / "native-analyze-equation.FCStd"
        document = App.newDocument("NativeAnalyzeEquationGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        _analysis, solver = _setup(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-equation-gui")

        def authorize() -> None:
            require_frozen_native_surface(frozen, controller)

        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state_store,
            undo_ledger=ledger,
            reauthorize_turn=authorize,
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
            reauthorize_turn=authorize,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-equation-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        initial_solver = solver_state(solver)
        current_solver = initial_solver
        expected_states = {}
        for index, operation in enumerate(EQUATION_OPERATIONS):
            result = call(
                ANALYZE_EQUATION_CAPABILITY_NAME,
                {
                    "operation": operation,
                    "solver": _solver_target(current_solver),
                    "label": operation.removeprefix("create_").replace("_", " ").title(),
                },
            )
            created = result["created_equation"]
            expected_states[operation] = created
            current_solver = result["solver"]
            assert created["equation_kind"] == operation.removeprefix("create_")
            assert created["solver"] == solver.Name
            assert created["priority"] == 255 - index
            assert created["timeline_role"] == "resource"
            assert created["timeline_owner"] == solver.Name
            assert created["settings"]
            assert result["assistant_undo_available"]

        stale = call(
            ANALYZE_EQUATION_CAPABILITY_NAME,
            {
                "operation": "create_heat",
                "solver": _solver_target(initial_solver),
                "label": "Must Not Exist",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert len(tuple(solver.Group)) == len(EQUATION_OPERATIONS)

        revision_before_read = state_store.current_revision(str(document.Uid))
        elasticity = expected_states["create_elasticity"]
        read = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {
                "operation": "equation",
                "target": _equation_target(elasticity),
            },
        )
        assert read["equation"] == elasticity
        assert state_store.current_revision(str(document.Uid)) == revision_before_read

        heat = expected_states["create_heat"]
        document.undo()
        assert document.getObject(heat["object_name"]) is None
        assert len(tuple(solver.Group)) == len(EQUATION_OPERATIONS) - 1
        document.redo()
        restored_heat = document.getObject(heat["object_name"])
        assert equation_state(restored_heat)["state_sha256"] == heat["state_sha256"]

        document.recompute()
        document.save()
        solver_name = str(solver.Name)
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        _events(20)
        reopened_solver = document.getObject(solver_name)
        assert len(tuple(reopened_solver.Group)) == len(EQUATION_OPERATIONS)
        for expected in expected_states.values():
            reopened = document.getObject(expected["object_name"])
            assert equation_state(reopened)["state_sha256"] == expected["state_sha256"]

        print(
            "VIBECAD_NATIVE_ANALYZE_EQUATION_GUI_OK "
            "actions=10 variants=10 exact_elmer=true defaults=true priorities=true "
            "owned_resources=true stale_rejection=true inspect=true "
            "read_revision_stable=true history=true undo_redo=true reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None:
            try:
                App.closeDocument(document.Name)
            except Exception:
                pass
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
