# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Analyze fluid tools."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeFluidSchema import ANALYZE_FLUID_CAPABILITY_NAME
from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


CREATE_OPERATIONS = (
    "create_initial_flow_velocity",
    "create_initial_pressure",
    "create_flow_velocity",
    "create_fluid_boundary",
)
UPDATE_OPERATIONS = (
    "update_initial_flow_velocity",
    "update_initial_pressure",
    "update_flow_velocity",
    "update_fluid_boundary",
)


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _select_analyze_ribbon(main_window):
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _process_events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    assert {
        "FEM_ConstraintInitialFlowVelocity",
        "FEM_ConstraintInitialPressure",
        "FEM_ConstraintFlowVelocity",
        "FEM_ConstraintFluidBoundary",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    fluid = registry.definition(ANALYZE_FLUID_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and fluid is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintInitialFlowVelocity": "create_initial_flow_velocity",
        "FEM_ConstraintInitialPressure": "create_initial_pressure",
        "FEM_ConstraintFlowVelocity": "create_flow_velocity",
        "FEM_ConstraintFluidBoundary": "create_fluid_boundary",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_FLUID_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in fluid.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadFluidConstraint": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "fluid_constraint",
        ),
        "VibeCAD_AnalyzeUpdateInitialFlowVelocity": (
            ANALYZE_FLUID_CAPABILITY_NAME,
            "update_initial_flow_velocity",
        ),
        "VibeCAD_AnalyzeUpdateInitialPressure": (
            ANALYZE_FLUID_CAPABILITY_NAME,
            "update_initial_pressure",
        ),
        "VibeCAD_AnalyzeUpdateFlowVelocity": (
            ANALYZE_FLUID_CAPABILITY_NAME,
            "update_flow_velocity",
        ),
        "VibeCAD_AnalyzeUpdateFluidBoundary": (
            ANALYZE_FLUID_CAPABILITY_NAME,
            "update_fluid_boundary",
        ),
    }
    for action_id, expected in expected_contexts.items():
        action = contexts[action_id]
        assert (action.capability_family, action.operation_variant) == expected
        definition = registry.definition(expected[0])
        assert any(
            variant.operation == expected[1] and action_id in variant.action_ids
            for variant in definition.variants
        )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_FLUID_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                fluid.provider_schema((*CREATE_OPERATIONS, *UPDATE_OPERATIONS)),
                inspect.provider_schema(("fluid_constraint",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(state: dict) -> dict:
    if "expected_state_sha256" in state:
        return dict(state)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": state["member_count"],
    }


def _constraint_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _reference(source, *subelements: str) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelements": list(subelements),
    }


def _create_source(document):
    document.openTransaction("Create fluid geometry source")
    try:
        source = document.addObject("Part::Box", "FluidGeometry")
        source.Label = "Fluid Geometry"
        source.Length = 30.0
        source.Width = 20.0
        source.Height = 10.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-fluid-")
        save_path = Path(temporary.name) / "native-analyze-fluid.FCStd"
        document = App.newDocument("NativeAnalyzeFluidGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        source = _create_source(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-fluid-gui")

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

        def call(tool: str, arguments: dict, *, succeeds: bool = True) -> dict:
            nonlocal call_number
            call_number += 1
            result = dispatcher.call(
                tool,
                json.dumps(arguments, separators=(",", ":")),
                f"native-analyze-fluid-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Fluid Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(
            analysis_result["created_analysis"]["object_name"]
        )
        current_analysis = analysis_result["created_analysis"]
        formula = 'Variable Coordinate 2; Real MATC "10*(tx+0.05)*(0.05-tx)"'

        initial_velocity_result = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "create_initial_flow_velocity",
                "analysis": _analysis_target(current_analysis),
                "label": "Global Initial Velocity",
                "references": [],
                "constraint": {
                    "components": {
                        "x": {"kind": "formula", "expression": formula},
                        "y": {"kind": "value", "value_m_s": 0.25},
                    }
                },
            },
        )
        initial_velocity = document.getObject(
            initial_velocity_result["created_constraint"]["object_name"]
        )
        current_analysis = initial_velocity_result["analysis_target"]
        ambiguous = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "create_initial_flow_velocity",
                "analysis": _analysis_target(current_analysis),
                "label": "Must Not Be Added",
                "references": [_reference(source, "Solid1")],
                "constraint": {
                    "components": {"z": {"kind": "value", "value_m_s": 1.0}}
                },
            },
            succeeds=False,
        )
        assert "already contains a global initial flow velocity" in ambiguous["error"]
        assert tuple(analysis.Group) == (initial_velocity,)

        initial_pressure_result = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "create_initial_pressure",
                "analysis": _analysis_target(current_analysis),
                "label": "Global Initial Pressure",
                "references": [],
                "constraint": {"pressure_pa": 101325.0},
            },
        )
        initial_pressure = document.getObject(
            initial_pressure_result["created_constraint"]["object_name"]
        )
        current_analysis = initial_pressure_result["analysis_target"]
        flow_result = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "create_flow_velocity",
                "analysis": _analysis_target(current_analysis),
                "label": "Inlet Velocity",
                "references": [_reference(source, "Face1", "Edge1")],
                "constraint": {
                    "components": {
                        "x": {"kind": "value", "value_m_s": 3.5},
                        "z": {"kind": "value", "value_m_s": 0.0},
                    },
                    "normal_to_boundary": False,
                },
            },
        )
        flow = document.getObject(flow_result["created_constraint"]["object_name"])
        current_analysis = flow_result["analysis_target"]

        boundary_result = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "create_fluid_boundary",
                "analysis": _analysis_target(current_analysis),
                "label": "Cooling Inlet",
                "references": [_reference(source, "Face3")],
                "constraint": {
                    "condition": {
                        "kind": "inlet_velocity",
                        "velocity_m_s": 12.5,
                    },
                    "turbulence": {
                        "kind": "intensity_length_scale",
                        "intensity_ratio": 0.05,
                        "length_scale_m": 0.02,
                    },
                    "thermal": {
                        "kind": "fixed_temperature",
                        "temperature_k": 300.0,
                    },
                },
            },
        )
        boundary = document.getObject(
            boundary_result["created_constraint"]["object_name"]
        )
        assert boundary_result["created_constraint"]["definition"] == {
            "condition": {"kind": "inlet_velocity", "velocity_m_s": 12.5},
            "turbulence": {
                "kind": "intensity_length_scale",
                "intensity_ratio": 0.05,
                "length_scale_m": 0.02,
            },
            "thermal": {"kind": "fixed_temperature", "temperature_k": 300.0},
        }
        assert boundary.Reversed

        flow_before_invalid = fluid_constraint_state(flow)
        invalid_formula = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_flow_velocity",
                "target": _constraint_target(flow_before_invalid),
                "constraint": {
                    "components": {
                        "x": {
                            "kind": "formula",
                            "expression": "Variable Coordinate 1\nEnd",
                        }
                    },
                    "normal_to_boundary": False,
                },
            },
            succeeds=False,
        )
        assert invalid_formula["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert fluid_constraint_state(flow) == flow_before_invalid

        initial_velocity_update = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_initial_flow_velocity",
                "target": _constraint_target(fluid_constraint_state(initial_velocity)),
                "label": "Body Initial Velocity",
                "references": [_reference(source, "Solid1")],
                "constraint": {
                    "components": {
                        "z": {"kind": "formula", "expression": "Real 1.75"}
                    },
                },
            },
        )
        pressure_update = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_initial_pressure",
                "target": _constraint_target(fluid_constraint_state(initial_pressure)),
                "references": [_reference(source, "Face2")],
                "constraint": {"pressure_pa": 95000.0},
            },
        )
        boundary_update = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_fluid_boundary",
                "target": _constraint_target(fluid_constraint_state(boundary)),
                "label": "Pressure Outlet",
                "references": [_reference(source, "Face4")],
                "constraint": {
                    "condition": {
                        "kind": "outlet_static_pressure",
                        "pressure_pa": 101325.0,
                    },
                    "turbulence": {"kind": "none"},
                    "thermal": {"kind": "adiabatic"},
                },
            },
        )
        assert boundary_update["updated_constraint"]["definition"] == {
            "condition": {
                "kind": "outlet_static_pressure",
                "pressure_pa": 101325.0,
            },
            "turbulence": {"kind": "none"},
            "thermal": {"kind": "adiabatic"},
        }
        assert not boundary.Reversed
        flow_before_update = fluid_constraint_state(flow)
        flow_update = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_flow_velocity",
                "target": _constraint_target(flow_before_update),
                "label": "Profiled Inlet Velocity",
                "references": [_reference(source, "Edge2")],
                "constraint": {
                    "components": {
                        "x": {"kind": "formula", "expression": formula},
                        "y": {"kind": "value", "value_m_s": -0.5},
                    },
                    "normal_to_boundary": True,
                },
            },
        )
        assert initial_velocity_update["updated_constraint"]["definition"] == {
            "components": {"z": {"kind": "formula", "expression": "Real 1.75"}}
        }
        assert (
            pressure_update["updated_constraint"]["definition"]["pressure_pa"]
            == 95000.0
        )
        assert (
            flow_update["updated_constraint"]["definition"]["normal_to_boundary"]
            is True
        )
        assert flow.VelocityXHasFormula and flow.VelocityXFormula == formula
        assert not flow.VelocityXUnspecified and not flow.VelocityYUnspecified
        assert flow.VelocityZUnspecified

        stale = call(
            ANALYZE_FLUID_CAPABILITY_NAME,
            {
                "operation": "update_flow_velocity",
                "target": _constraint_target(flow_before_update),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(flow.Label) == "Profiled Inlet Velocity"

        constraints = (initial_velocity, initial_pressure, flow, boundary)
        read_revision = state.current_revision(str(document.Uid))
        for constraint in constraints:
            current = fluid_constraint_state(constraint)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "fluid_constraint",
                    "target": _constraint_target(current),
                },
            )
            assert read["fluid_constraint"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["fluid_constraint_count"] == 4
        assert not snapshot["fluid_constraints_truncated"]
        assert {item["constraint_kind"] for item in snapshot["fluid_constraints"]} == {
            "initial_flow_velocity",
            "initial_pressure",
            "flow_velocity",
            "fluid_boundary",
        }
        assert tuple(analysis.Group) == constraints
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            source.Name,
            analysis.Name,
            *(obj.Name for obj in constraints),
        )

        document.undo()
        flow_after_undo = fluid_constraint_state(flow)
        assert flow_after_undo["state_sha256"] == flow_before_update["state_sha256"]
        assert flow_after_undo["definition"] == flow_before_update["definition"]
        assert flow_after_undo["references"] == flow_before_update["references"]
        document.redo()
        assert (
            fluid_constraint_state(flow)["definition"]
            == flow_update["updated_constraint"]["definition"]
        )

        expected = {obj.Name: fluid_constraint_state(obj) for obj in constraints}
        analysis_name = analysis.Name
        constraint_names = tuple(obj.Name for obj in constraints)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened_analysis = document.getObject(analysis_name)
        assert tuple(obj.Name for obj in reopened_analysis.Group) == constraint_names
        assert (
            tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
            == operation_names
        )
        for name, old_state in expected.items():
            new_state = fluid_constraint_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_FLUID_GUI_OK "
            "actions=4 edits=4 reads=1 exact_references=true typed_boundaries=true "
            "history=true undo_redo=true reopen=true read_revision_stable=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
