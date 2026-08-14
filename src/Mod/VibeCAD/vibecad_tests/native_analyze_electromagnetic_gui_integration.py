# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Analyze electromagnetic tools."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
from VibeCADNativeAnalyzeElectromagneticSchema import (
    ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
)
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
from femsolver.elmer.equations.magnetodynamic_writer import MgDynwriter


CREATE_OPERATIONS = (
    "constraint_electromagnetic",
    "constraint_current_density",
    "constraint_magnetization",
    "constraint_electric_charge_density",
)
UPDATE_OPERATIONS = (
    "update_electromagnetic",
    "update_current_density",
    "update_magnetization",
    "update_electric_charge_density",
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
        "FEM_CompEmConstraints",
        "FEM_ConstraintElectromagnetic",
        "FEM_ConstraintCurrentDensity",
        "FEM_ConstraintMagnetization",
        "FEM_ConstraintElectricChargeDensity",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    electromagnetic = registry.definition(ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and electromagnetic is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintElectromagnetic": "constraint_electromagnetic",
        "FEM_ConstraintCurrentDensity": "constraint_current_density",
        "FEM_ConstraintMagnetization": "constraint_magnetization",
        "FEM_ConstraintElectricChargeDensity": "constraint_electric_charge_density",
    }
    inventory = resolve_native_action_inventory(surface)
    parent = next(
        plan for plan in inventory.plans if plan.command_id == "FEM_CompEmConstraints"
    )
    assert parent.classification.parent_only and parent.operation_variant is None
    plans = {
        plan.command_id: plan
        for plan in inventory.plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in electromagnetic.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadElectromagneticConstraint": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "electromagnetic_constraint",
        ),
        "VibeCAD_AnalyzeUpdateElectromagnetic": (
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            "update_electromagnetic",
        ),
        "VibeCAD_AnalyzeUpdateCurrentDensity": (
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            "update_current_density",
        ),
        "VibeCAD_AnalyzeUpdateMagnetization": (
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            "update_magnetization",
        ),
        "VibeCAD_AnalyzeUpdateElectricChargeDensity": (
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            "update_electric_charge_density",
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
                ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                electromagnetic.provider_schema(
                    (*CREATE_OPERATIONS, *UPDATE_OPERATIONS)
                ),
                inspect.provider_schema(("electromagnetic_constraint",)),
            ),
            human_only_action_ids=("FEM_CompEmConstraints",),
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
    document.openTransaction("Create electromagnetic geometry source")
    try:
        source = document.addObject("Part::Box", "ElectromagneticGeometry")
        source.Label = "Electromagnetic Geometry"
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
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-electromagnetic-"
        )
        save_path = Path(temporary.name) / "native-analyze-electromagnetic.FCStd"
        document = App.newDocument("NativeAnalyzeElectromagneticGate")
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
        ledger.begin_run("native-analyze-electromagnetic-gui")

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
                f"native-analyze-electromagnetic-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Electromagnetic Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(
            analysis_result["created_analysis"]["object_name"]
        )
        current_analysis = analysis_result["created_analysis"]

        em_result = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "constraint_electromagnetic",
                "analysis": _analysis_target(current_analysis),
                "label": "Driven Boundary",
                "references": [_reference(source, "Face1", "Edge1")],
                "constraint": {
                    "kind": "dirichlet",
                    "electric_potential_v": 24.0,
                    "scalar_potential": {"real_v": 1.5, "imaginary_v": -0.25},
                    "vector_potential": {
                        "x": {"real_wb_m": 0.01, "imaginary_wb_m": 0.002},
                        "z": {"real_wb_m": -0.03, "imaginary_wb_m": 0.004},
                    },
                    "potential_constant": True,
                    "far_field": False,
                    "capacitance_body": 2,
                },
            },
        )
        electromagnetic = document.getObject(
            em_result["created_constraint"]["object_name"]
        )
        current_analysis = em_result["analysis_target"]
        current_result = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "constraint_current_density",
                "analysis": _analysis_target(current_analysis),
                "label": "Global Coil Current",
                "references": [],
                "constraint": {
                    "kind": "cartesian",
                    "components": {
                        "x": {"real_a_m2": 1200.0, "imaginary_a_m2": 25.0},
                        "y": {"real_a_m2": -400.0, "imaginary_a_m2": 10.0},
                    },
                },
            },
        )
        current = document.getObject(
            current_result["created_constraint"]["object_name"]
        )
        current_analysis = current_result["analysis_target"]

        class _BodyForceCapture:
            def __init__(self) -> None:
                self.names = []

            def bodyForce(self, _body, name, _value) -> None:
                self.names.append(name)

        capture = _BodyForceCapture()
        MgDynwriter(capture, None)._outputMagnetodynamicBodyForce(
            current,
            "Solid1",
            SimpleNamespace(IsHarmonic=True),
        )
        assert capture.names == [
            "Current Density 1",
            "Current Density 2",
            "Current Density Im 1",
            "Current Density Im 2",
        ]

        ambiguous = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "constraint_current_density",
                "analysis": _analysis_target(current_analysis),
                "label": "Must Not Be Added",
                "references": [_reference(source, "Solid1")],
                "constraint": {
                    "kind": "cartesian",
                    "components": {"z": {"real_a_m2": 1.0, "imaginary_a_m2": 0.0}},
                },
            },
            succeeds=False,
        )
        assert "already contains a global current density" in ambiguous["error"]
        assert tuple(analysis.Group) == (electromagnetic, current)

        magnetization_result = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "constraint_magnetization",
                "analysis": _analysis_target(current_analysis),
                "label": "Permanent Magnet",
                "references": [_reference(source, "Solid1", "Face2")],
                "constraint": {
                    "components": {"z": {"real_a_m": 7500.0, "imaginary_a_m": 125.0}}
                },
            },
        )
        magnetization = document.getObject(
            magnetization_result["created_constraint"]["object_name"]
        )
        current_analysis = magnetization_result["analysis_target"]
        charge_result = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "constraint_electric_charge_density",
                "analysis": _analysis_target(current_analysis),
                "label": "Charged Interface",
                "references": [_reference(source, "Face3")],
                "constraint": {
                    "kind": "interface",
                    "surface_charge_density_c_m2": 7.94e-9,
                },
            },
        )
        charge = document.getObject(charge_result["created_constraint"]["object_name"])

        charge_before_invalid = electromagnetic_constraint_state(charge)
        invalid_charge = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "update_electric_charge_density",
                "target": _constraint_target(charge_before_invalid),
                "references": [_reference(source, "Edge2")],
                "constraint": {
                    "kind": "source",
                    "volume_charge_density_c_m3": 2.5,
                },
            },
            succeeds=False,
        )
        assert "requires Face or Solid references" in invalid_charge["error"]
        assert electromagnetic_constraint_state(charge) == charge_before_invalid

        em_update = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "update_electromagnetic",
                "target": _constraint_target(
                    electromagnetic_constraint_state(electromagnetic)
                ),
                "references": [_reference(source, "Face1")],
                "constraint": {
                    "kind": "neumann",
                    "electric_flux_density_c_m2": -3.2e-6,
                    "magnetic_flux_density": {
                        "x": {"real_wb_m2": 0.8, "imaginary_wb_m2": 0.1},
                        "y": {"real_wb_m2": -0.4, "imaginary_wb_m2": 0.05},
                    },
                    "capacitance_body": 3,
                },
            },
        )
        current_update = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "update_current_density",
                "target": _constraint_target(electromagnetic_constraint_state(current)),
                "label": "Normal Surface Current",
                "references": [_reference(source, "Face4")],
                "constraint": {
                    "kind": "normal",
                    "real_a_m2": 2400.0,
                    "imaginary_a_m2": -15.0,
                },
            },
        )
        magnetization_update = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "update_magnetization",
                "target": _constraint_target(
                    electromagnetic_constraint_state(magnetization)
                ),
                "references": [],
                "constraint": {
                    "components": {
                        "x": {"real_a_m": -5000.0, "imaginary_a_m": 20.0},
                        "y": {"real_a_m": 2500.0, "imaginary_a_m": -10.0},
                    }
                },
            },
        )
        charge_modes = (
            {
                "references": [_reference(source, "Solid1")],
                "constraint": {
                    "kind": "source",
                    "volume_charge_density_c_m3": 1.2e-3,
                },
            },
            {
                "references": [_reference(source, "Edge3")],
                "constraint": {"kind": "total_interface", "total_charge_c": 2.0e-8},
            },
            {
                "references": [_reference(source, "Solid1")],
                "constraint": {
                    "kind": "total_source",
                    "total_charge_c": 3.0e-8,
                    "concentrated": False,
                },
            },
            {
                "references": [_reference(source, "Vertex1")],
                "constraint": {
                    "kind": "total_source",
                    "total_charge_c": 4.0e-8,
                    "concentrated": True,
                },
            },
        )
        charge_update = None
        charge_before_final = None
        for changes in charge_modes:
            charge_before_final = electromagnetic_constraint_state(charge)
            charge_update = call(
                ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
                {
                    "operation": "update_electric_charge_density",
                    "target": _constraint_target(charge_before_final),
                    **changes,
                },
            )

        assert em_update["updated_constraint"]["definition"]["kind"] == "neumann"
        assert current_update["updated_constraint"]["definition"]["kind"] == "normal"
        assert set(
            magnetization_update["updated_constraint"]["definition"]["components"]
        ) == {"x", "y"}
        assert charge_update["updated_constraint"]["definition"] == {
            "kind": "total_source",
            "total_charge_c": 4.0e-8,
            "concentrated": True,
        }

        stale = call(
            ANALYZE_ELECTROMAGNETIC_CAPABILITY_NAME,
            {
                "operation": "update_electric_charge_density",
                "target": _constraint_target(charge_before_final),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(charge.Label) == "Charged Interface"

        read_revision = state.current_revision(str(document.Uid))
        constraints = (electromagnetic, current, magnetization, charge)
        for constraint in constraints:
            current_state = electromagnetic_constraint_state(constraint)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "electromagnetic_constraint",
                    "target": _constraint_target(current_state),
                },
            )
            assert read["electromagnetic_constraint"] == current_state
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["electromagnetic_constraint_count"] == 4
        assert not snapshot["electromagnetic_constraints_truncated"]
        assert {
            item["constraint_kind"] for item in snapshot["electromagnetic_constraints"]
        } == {
            "electromagnetic",
            "current_density",
            "magnetization",
            "electric_charge_density",
        }
        assert tuple(analysis.Group) == constraints
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            source.Name,
            analysis.Name,
            *(obj.Name for obj in constraints),
        )
        assert all(obj.VibeCADTimelineRole == "operation" for obj in constraints)
        assert all(
            getattr(obj, "VibeCADTimelineOwner", None) is None for obj in constraints
        )

        document.undo()
        assert electromagnetic_constraint_state(charge)["definition"] == {
            "kind": "total_source",
            "total_charge_c": 3.0e-8,
            "concentrated": False,
        }
        document.redo()
        assert electromagnetic_constraint_state(charge)["definition"] == {
            "kind": "total_source",
            "total_charge_c": 4.0e-8,
            "concentrated": True,
        }

        expected = {
            obj.Name: electromagnetic_constraint_state(obj) for obj in constraints
        }
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
            new_state = electromagnetic_constraint_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_ELECTROMAGNETIC_GUI_OK "
            "actions=4 edits=4 reads=1 exact_references=true typed_modes=true "
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
