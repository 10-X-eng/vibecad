# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for exact Native FEM solver-control edits."""

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
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeSolverControlSchema import (
    ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
)
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


OPERATIONS = ("update_calculix", "update_elmer", "update_z88")


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
    assert "FEM_SolverControl" in surface.command_ids
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    control = registry.definition(ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert control is not None and inspect is not None
    plan = next(
        plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id == "FEM_SolverControl"
    )
    assert plan.capability_family == ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME
    assert plan.operation_variant == "update_calculix"
    assert plan.classification.mutation and not plan.classification.interactive
    assert plan.transaction_behavior == "document"
    schema = control.provider_schema(OPERATIONS)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.lower()
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(schema, inspect.provider_schema(("solver",))),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _create_model(document):
    from femcommands import manager
    from femcommands.commands import createDefaultSolverFeature

    document.openTransaction("Create solver control gate model")
    try:
        analysis = ObjectsFem.makeAnalysis(document, "SolverControlAnalysis")
        analysis.Label = "Solver Control Analysis"
        document.publishProvisionalTimelineOperationBlock(analysis, (), ())
        solvers = {}
        for kind, factory in (
            ("calculix", "CalculiX"),
            ("elmer", "Elmer"),
            ("z88", "Z88"),
        ):
            solver = createDefaultSolverFeature(document, factory)
            solver.Label = f"{factory} Control Gate"
            analysis.addObject(solver)
            manager._mark_timeline_operation(solver)
            document.publishProvisionalTimelineOperationBlock(solver, (), ())
            solvers[kind] = solver
        document.recompute([analysis, *solvers.values()], True, True)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return analysis, solvers


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-solver-control-"
        )
        path = Path(temporary.name) / "native-analyze-solver-control.FCStd"
        document = App.newDocument("NativeAnalyzeSolverControlGate")
        document.UndoMode = 1
        document.saveAs(str(path))
        VibeGui._connect_document_observer()
        controller, surface = _surface(Gui.getMainWindow())
        _analysis, solvers = _create_model(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state_store = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-solver-control-gui")

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
                f"native-analyze-solver-control-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            return result

        states_before = {kind: solver_state(solver) for kind, solver in solvers.items()}
        history_before = tuple(document.VibeCADTimeline.Operations)
        calculix_changes = {
            "analysis_type": "frequency",
            "geometrical_nonlinearity": True,
            "material_nonlinearity": False,
            "eigenmodes_count": 7,
            "eigenmode_low_hz": 5.0,
            "eigenmode_high_hz": 500.0,
            "increments_maximum": 77,
            "buckling_factors": 3,
            "time_initial_s": 0.1,
            "time_period_s": 2.0,
            "time_minimum_s": 0.01,
            "time_maximum_s": 0.5,
            "thermo_mech_steady_state": False,
            "use_iteration_control": True,
            "split_input_writer": True,
            "iteration_control_iterations": "5,9,10,17,11,5,,6,,",
            "iteration_control_cutbacks": "0.2,0.4,0.7,0.8,,,1.4,",
            "iteration_control_field": "0.004,0.02,0.02, ,0.03,0.00002,0.002,0.00000002",
            "automatic_incrementation": False,
            "matrix_solver": "spooles",
            "output_3d": False,
            "reduced_integration": False,
            "output_frequency": 4,
            "model_space": "plane strain",
            "thermo_mech_type": "uncoupled",
            "buckling_accuracy": 0.02,
            "exclude_bending_stiffness": True,
            "pastix_mixed_precision": True,
            "displace_mesh": True,
        }
        elmer_changes = {
            "coordinate_system": "Cartesian 2D",
            "bdf_order": 3,
            "output_intervals": [2, 4],
            "timestep_intervals": [10, 20],
            "timestep_sizes_s": [0.05, 0.1],
            "simulation_type": "Transient",
            "steady_state_max_iterations": 9,
            "steady_state_min_iterations": 2,
            "binary_output": True,
            "save_geometry_index": True,
        }
        z88_changes = {
            "analysis_type": "test",
            "displace_mesh": True,
            "solver_type": "siccg",
            "model_space": "plate",
            "integration_order_quad": 4,
            "integration_order_hexa": 3,
            "integration_order_tria": 7,
            "integration_order_tetra": 5,
            "relaxation_factor": 1.25,
            "shift_factor": 0.02,
            "iteration_maximum": 2500,
            "residual_limit": 1.0e-8,
            "shell_flag": 3,
            "matrix_maximum": 456789,
            "vector_maximum": 234567,
        }
        states_after = {}
        for operation, kind, changes in (
            ("update_calculix", "calculix", calculix_changes),
            ("update_elmer", "elmer", elmer_changes),
            ("update_z88", "z88", z88_changes),
        ):
            result = call(
                ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
                {
                    "operation": operation,
                    "target": _target(states_before[kind]),
                    **changes,
                },
            )
            states_after[kind] = result["updated_solver"]
            assert result["changed_settings"] == changes
            assert result["assistant_undo_available"]
            assert tuple(document.VibeCADTimeline.Operations) == history_before

        assert states_after["calculix"]["settings"]["MatrixSolverType"] == "spooles"
        assert states_after["calculix"]["settings"]["TimeInitialIncrement"] == 0.1
        assert states_after["elmer"]["settings"]["TimestepIntervals"] == [10, 20]
        assert states_after["elmer"]["settings"]["TimestepSizes"] == [0.05, 0.1]
        assert states_after["z88"]["settings"]["IntegrationOrderTria"] == "7"
        assert states_after["z88"]["settings"]["MatrixMaximum"] == 456789

        invalid_relationship = call(
            ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
            {
                "operation": "update_calculix",
                "target": _target(states_after["calculix"]),
                "time_minimum_s": 1.0,
            },
            succeeds=False,
        )
        assert "minimum <= initial <= maximum <= period" in invalid_relationship["error"]
        assert solver_state(solvers["calculix"]) == states_after["calculix"]
        wrong_backend = call(
            ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
            {
                "operation": "update_elmer",
                "target": _target(states_after["calculix"]),
                "binary_output": True,
            },
            succeeds=False,
        )
        assert wrong_backend["error_code"] == "NATIVE_ANALYZE_TARGET_TYPE_INVALID"
        no_change = call(
            ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
            {
                "operation": "update_elmer",
                "target": _target(states_after["elmer"]),
                "binary_output": True,
            },
            succeeds=False,
        )
        assert "already have those values" in no_change["error"]
        stale = call(
            ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
            {
                "operation": "update_calculix",
                "target": _target(states_before["calculix"]),
                "output_frequency": 8,
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert tuple(document.VibeCADTimeline.Operations) == history_before

        revision_before_read = state_store.current_revision(str(document.Uid))
        read = call(
            ANALYZE_INSPECT_CAPABILITY_NAME,
            {"operation": "solver", "target": _target(states_after["elmer"])},
        )
        assert read["solver"] == states_after["elmer"]
        assert state_store.current_revision(str(document.Uid)) == revision_before_read

        z88_name = str(solvers["z88"].Name)
        z88_before_hash = states_before["z88"]["state_sha256"]
        z88_after_hash = states_after["z88"]["state_sha256"]
        document.undo()
        assert solver_state(document.getObject(z88_name))["state_sha256"] == z88_before_hash
        document.redo()
        assert solver_state(document.getObject(z88_name))["state_sha256"] == z88_after_hash

        document.recompute()
        document.save()
        names = {kind: str(solver.Name) for kind, solver in solvers.items()}
        App.closeDocument(document.Name)
        document = App.openDocument(str(path))
        _events(20)
        assert tuple(document.VibeCADTimeline.Operations)[-3:] == tuple(
            document.getObject(names[kind]) for kind in ("calculix", "elmer", "z88")
        )
        for kind, expected in states_after.items():
            assert solver_state(document.getObject(names[kind]))["state_sha256"] == expected[
                "state_sha256"
            ]

        print(
            "VIBECAD_NATIVE_ANALYZE_SOLVER_CONTROL_GUI_OK "
            "actions=1 variants=3 typed_settings=54 exact_backend=true "
            "cross_field_validation=true wrong_backend_rejection=true no_op_rejection=true "
            "stale_rejection=true history_stable=true inspect=true "
            "read_revision_stable=true undo_redo=true reopen=true",
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
