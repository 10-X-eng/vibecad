# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native mechanical support-condition tools."""

from __future__ import annotations

import json
import math
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
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeAnalyzeSupportSchema import ANALYZE_SUPPORT_CAPABILITY_NAME
from VibeCADNativeAnalyzeSupportState import support_condition_state
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


KINDS = ("fixed", "rigid_body", "displacement", "spring")
CREATE_OPERATIONS = tuple(f"create_{kind}" for kind in KINDS)
UPDATE_OPERATIONS = tuple(f"update_{kind}" for kind in KINDS)


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
        "FEM_ConstraintFixed",
        "FEM_ConstraintRigidBody",
        "FEM_ConstraintDisplacement",
        "FEM_ConstraintSpring",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    support = registry.definition(ANALYZE_SUPPORT_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and support is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintFixed": "create_fixed",
        "FEM_ConstraintRigidBody": "create_rigid_body",
        "FEM_ConstraintDisplacement": "create_displacement",
        "FEM_ConstraintSpring": "create_spring",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_SUPPORT_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in support.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadSupportCondition": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "support_condition",
        ),
        **{
            f"VibeCAD_AnalyzeUpdate{''.join(part.title() for part in kind.split('_'))}": (
                ANALYZE_SUPPORT_CAPABILITY_NAME,
                f"update_{kind}",
            )
            for kind in KINDS
        },
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
                ANALYZE_SUPPORT_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                support.provider_schema((*CREATE_OPERATIONS, *UPDATE_OPERATIONS)),
                inspect.provider_schema(("support_condition",)),
            ),
            human_only_action_ids=(),
            missing_definition_names=(),
            missing_implementation_names=(),
            incomplete_definition_names=(),
        )
    )


def _analysis_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": state["member_count"],
    }


def _condition_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _references(source, *subelements: str) -> list[dict]:
    return [
        {
            "object_name": source.Name,
            "expected_state_sha256": mesh_object_state(source)["state_sha256"],
            "subelements": list(subelements),
        }
    ]


def _free_axes() -> dict[str, dict[str, str]]:
    return {axis: {"kind": "free"} for axis in "xyz"}


def _publish_box(document):
    document.openTransaction("Create support geometry")
    try:
        source = document.addObject("Part::Box", "SupportGeometry")
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
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-analyze-support-")
        save_path = Path(temporary.name) / "native-analyze-support.FCStd"
        document = App.newDocument("NativeAnalyzeSupportGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        source = _publish_box(document)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-support-gui")

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
                f"native-analyze-support-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Mechanical Support Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(analysis_result["created_analysis"]["object_name"])
        current_analysis = analysis_state(analysis)

        mixed = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_fixed",
                "analysis": _analysis_target(current_analysis),
                "label": "Mixed References Must Fail",
                "references": _references(source, "Vertex1", "Edge1"),
            },
            succeeds=False,
        )
        assert "one subelement type" in mixed["error"].lower(), mixed
        invalid_flow = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_displacement",
                "analysis": _analysis_target(current_analysis),
                "label": "Invalid Flow Condition",
                "references": _references(source, "Face1"),
                "condition": {
                    "translation": {
                        "x": {"kind": "value", "displacement_mm": 1.0},
                        "y": {"kind": "free"},
                        "z": {"kind": "free"},
                    },
                    "rotation": _free_axes(),
                    "flow_surface_force": True,
                },
            },
            succeeds=False,
        )
        assert "every translation and rotation axis" in invalid_flow["error"]
        assert analysis_state(analysis) == current_analysis

        fixed_result = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_fixed",
                "analysis": _analysis_target(current_analysis),
                "label": "Fixed Vertices",
                "references": _references(source, "Vertex1", "Vertex2"),
            },
        )
        fixed = document.getObject(fixed_result["created_condition"]["object_name"])
        current_analysis = analysis_state(analysis)
        rigid_result = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_rigid_body",
                "analysis": _analysis_target(current_analysis),
                "label": "Mixed Rigid Coupling",
                "references": _references(source, "Face1"),
                "condition": {
                    "reference_node_mm": {"x": 1.0, "y": 2.0, "z": 3.0},
                    "translation": {
                        "x": {"kind": "prescribed", "displacement_mm": 0.25},
                        "y": {"kind": "load", "force_n": 125.0},
                        "z": {"kind": "free"},
                    },
                    "rotation": {
                        "x": {"kind": "prescribed", "rotation_degrees": -10.0},
                        "y": {"kind": "free"},
                        "z": {"kind": "load", "moment_n_mm": 400.0},
                    },
                },
            },
        )
        rigid = document.getObject(rigid_result["created_condition"]["object_name"])
        current_analysis = analysis_state(analysis)
        displacement_result = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_displacement",
                "analysis": _analysis_target(current_analysis),
                "label": "Driven Displacement",
                "references": _references(source, "Edge1", "Edge2"),
                "condition": {
                    "translation": {
                        "x": {"kind": "formula", "expression": "0.05 * z"},
                        "y": {"kind": "value", "displacement_mm": -0.1},
                        "z": {"kind": "free"},
                    },
                    "rotation": {
                        "x": {"kind": "free"},
                        "y": {"kind": "value", "rotation_degrees": 2.0},
                        "z": {"kind": "free"},
                    },
                    "flow_surface_force": False,
                },
            },
        )
        displacement = document.getObject(
            displacement_result["created_condition"]["object_name"]
        )
        current_analysis = analysis_state(analysis)
        spring_result = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "create_spring",
                "analysis": _analysis_target(current_analysis),
                "label": "Surface Spring",
                "references": _references(source, "Face2"),
                "condition": {
                    "normal_stiffness_n_m": 1500.0,
                    "tangential_stiffness_n_m": 400.0,
                    "elmer_component": "normal",
                },
            },
        )
        spring = document.getObject(spring_result["created_condition"]["object_name"])

        assert str(rigid.TranslationalModeX) == "Constraint"
        assert str(rigid.TranslationalModeY) == "Load"
        assert str(rigid.RotationalModeX) == "Constraint"
        assert str(rigid.RotationalModeZ) == "Load"
        assert math.isclose(rigid.ForceY.getValueAs("N").Value, 125.0)
        assert math.isclose(rigid.MomentZ.getValueAs("N*mm").Value, 400.0)
        assert bool(displacement.hasXFormula)
        assert str(displacement.xDisplacementFormula) == "0.05 * z"
        assert str(spring.ElmerStiffness) == "Normal Stiffness"

        fixed_before = support_condition_state(fixed)
        fixed_update = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "update_fixed",
                "target": _condition_target(fixed_before),
                "label": "Fixed Edges",
                "references": _references(source, "Edge3", "Edge4"),
            },
        )
        rigid_update = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "update_rigid_body",
                "target": _condition_target(support_condition_state(rigid)),
                "references": _references(source, "Face3"),
                "condition": {
                    "reference_node_mm": {"x": 5.0, "y": 0.0, "z": 1.0},
                    "translation": {
                        "x": {"kind": "free"},
                        "y": {"kind": "prescribed", "displacement_mm": -0.5},
                        "z": {"kind": "load", "force_n": 60.0},
                    },
                    "rotation": {
                        "x": {"kind": "free"},
                        "y": {"kind": "prescribed", "rotation_degrees": 15.0},
                        "z": {"kind": "free"},
                    },
                },
            },
        )
        displacement_update = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "update_displacement",
                "target": _condition_target(support_condition_state(displacement)),
                "condition": {
                    "translation": {
                        "x": {"kind": "value", "displacement_mm": 0.2},
                        "y": {"kind": "free"},
                        "z": {"kind": "value", "displacement_mm": -0.3},
                    },
                    "rotation": _free_axes(),
                    "flow_surface_force": False,
                },
            },
        )
        spring_before = support_condition_state(spring)
        spring_update = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "update_spring",
                "target": _condition_target(spring_before),
                "label": "Updated Surface Spring",
                "references": _references(source, "Face4"),
                "condition": {
                    "normal_stiffness_n_m": 1750.0,
                    "tangential_stiffness_n_m": 900.0,
                    "elmer_component": "tangential",
                },
            },
        )
        assert fixed_update["updated_condition"]["references"][0]["subelements"] == [
            "Edge3",
            "Edge4",
        ]
        assert rigid_update["updated_condition"]["definition"]["translation"]["z"] == {
            "kind": "load",
            "force_n": 60.0,
        }
        assert displacement_update["updated_condition"]["definition"]["translation"]["z"] == {
            "kind": "value",
            "displacement_mm": -0.3,
        }
        assert spring_update["updated_condition"]["definition"]["elmer_component"] == (
            "tangential"
        )

        stale = call(
            ANALYZE_SUPPORT_CAPABILITY_NAME,
            {
                "operation": "update_spring",
                "target": _condition_target(spring_before),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(spring.Label) == "Updated Surface Spring"

        conditions = (fixed, rigid, displacement, spring)
        read_revision = state.current_revision(str(document.Uid))
        for condition in conditions:
            current = support_condition_state(condition)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "support_condition",
                    "target": _condition_target(current),
                },
            )
            assert read["support_condition"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["support_condition_count"] == 4
        assert not snapshot["support_conditions_truncated"]
        assert {item["condition_kind"] for item in snapshot["support_conditions"]} == set(
            KINDS
        )
        assert tuple(analysis.Group) == conditions
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (source.Name, analysis.Name, *(obj.Name for obj in conditions))

        document.undo()
        assert support_condition_state(spring)["state_sha256"] == spring_before["state_sha256"]
        document.redo()
        assert support_condition_state(spring)["definition"] == spring_update[
            "updated_condition"
        ]["definition"]

        expected = {obj.Name: support_condition_state(obj) for obj in conditions}
        analysis_name = analysis.Name
        member_names = tuple(obj.Name for obj in analysis.Group)
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened_analysis = document.getObject(analysis_name)
        assert tuple(obj.Name for obj in reopened_analysis.Group) == member_names
        assert tuple(obj.Name for obj in document.VibeCADTimeline.Operations) == operation_names
        for name, old_state in expected.items():
            new_state = support_condition_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_SUPPORT_GUI_OK "
            "actions=4 edits=4 reads=1 exact_references=true typed_dofs=true "
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
