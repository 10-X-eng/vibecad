# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Analyze element-definition tools."""

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
from VibeCADNativeAnalyzeElementState import element_definition_state
from VibeCADNativeAnalyzeGeometrySchema import ANALYZE_GEOMETRY_CAPABILITY_NAME
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


GEOMETRY_OPERATIONS = (
    "create_beam_section",
    "create_beam_rotation",
    "create_shell_thickness",
    "create_fluid_section",
    "update_beam_section",
    "update_beam_rotation",
    "update_shell_thickness",
    "update_fluid_section",
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
    expected = {
        "FEM_ElementGeometry1D",
        "FEM_ElementRotation1D",
        "FEM_ElementGeometry2D",
        "FEM_ElementFluid1D",
    }
    assert expected <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    geometry = registry.definition(ANALYZE_GEOMETRY_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and geometry is not None and inspect is not None
    expected_actions = {
        "FEM_ElementGeometry1D": "create_beam_section",
        "FEM_ElementRotation1D": "create_beam_rotation",
        "FEM_ElementGeometry2D": "create_shell_thickness",
        "FEM_ElementFluid1D": "create_fluid_section",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_GEOMETRY_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert plan.transaction_behavior == "document"
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in geometry.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadElementDefinition": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "element_definition",
        ),
        "VibeCAD_AnalyzeUpdateBeamSection": (
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            "update_beam_section",
        ),
        "VibeCAD_AnalyzeUpdateBeamRotation": (
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            "update_beam_rotation",
        ),
        "VibeCAD_AnalyzeUpdateShellThickness": (
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            "update_shell_thickness",
        ),
        "VibeCAD_AnalyzeUpdateFluidSection": (
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            "update_fluid_section",
        ),
    }
    assert expected_contexts.keys() <= contexts.keys()
    for action_id, (capability, operation) in expected_contexts.items():
        action = contexts[action_id]
        assert (action.capability_family, action.operation_variant) == (
            capability,
            operation,
        )
        definition = registry.definition(capability)
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in definition.variants
        )
    return NativeTurnSnapshot.from_provider_surface(
        NativeProviderSurface(
            snapshot=NativeSurfaceSnapshot.from_surface(surface),
            available=True,
            unavailable_reason="",
            tool_names=(
                ANALYZE_MODEL_CAPABILITY_NAME,
                ANALYZE_GEOMETRY_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                geometry.provider_schema(GEOMETRY_OPERATIONS),
                inspect.provider_schema(("element_definition",)),
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


def _element_target(state: dict) -> dict:
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
    document.openTransaction("Create FEM geometry source")
    try:
        source = document.addObject("Part::Box", "MemberGeometry")
        source.Label = "Member Geometry"
        source.Length = 40.0
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
            prefix="vibecad-native-analyze-geometry-"
        )
        save_path = Path(temporary.name) / "native-analyze-geometry.FCStd"
        document = App.newDocument("NativeAnalyzeGeometryGate")
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
        ledger.begin_run("native-analyze-geometry-gui")

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
                f"native-analyze-geometry-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Element Definition Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(
            analysis_result["created_analysis"]["object_name"]
        )
        current_analysis = analysis_result["created_analysis"]

        beam_result = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "create_beam_section",
                "analysis": _analysis_target(current_analysis),
                "label": "Main Beam Section",
                "references": [_reference(source, "Edge1", "Edge2")],
                "section": {"kind": "rectangular", "width_mm": 12.0, "height_mm": 24.0},
            },
        )
        beam = document.getObject(
            beam_result["created_element_definition"]["object_name"]
        )
        current_analysis = beam_result["analysis_target"]
        rotation_result = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "create_beam_rotation",
                "analysis": _analysis_target(current_analysis),
                "label": "Main Beam Orientation",
                "references": [_reference(source, "Edge1", "Edge2")],
                "rotation_degrees": 15.0,
            },
        )
        rotation = document.getObject(
            rotation_result["created_element_definition"]["object_name"]
        )
        current_analysis = rotation_result["analysis_target"]
        shell_result = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "create_shell_thickness",
                "analysis": _analysis_target(current_analysis),
                "label": "Outer Shell",
                "references": [_reference(source, "Face1", "Face2")],
                "thickness_mm": 1.5,
            },
        )
        shell = document.getObject(
            shell_result["created_element_definition"]["object_name"]
        )
        current_analysis = shell_result["analysis_target"]
        fluid_result = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "create_fluid_section",
                "analysis": _analysis_target(current_analysis),
                "label": "Hydraulic Run",
                "references": [_reference(source, "Edge3")],
                "section": {
                    "kind": "pipe_manning",
                    "area_mm2": 314.159,
                    "hydraulic_radius_mm": 5.0,
                    "manning_coefficient": 0.015,
                },
            },
        )
        fluid = document.getObject(
            fluid_result["created_element_definition"]["object_name"]
        )

        beam_before = element_definition_state(beam)
        invalid = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "update_beam_section",
                "target": _element_target(beam_before),
                "section": {
                    "kind": "pipe",
                    "outer_diameter_mm": 20.0,
                    "wall_thickness_mm": 10.0,
                },
            },
            succeeds=False,
        )
        assert invalid["error_code"] == "NATIVE_ANALYZE_OPERATION_FAILED"
        assert "smaller than the pipe radius" in invalid["error"]
        assert element_definition_state(beam) == beam_before

        beam_sections = (
            {"kind": "circular", "diameter_mm": 18.0},
            {"kind": "elliptical", "axis_1_mm": 12.0, "axis_2_mm": 24.0},
            {
                "kind": "box",
                "width_mm": 24.0,
                "height_mm": 30.0,
                "t1_mm": 2.0,
                "t2_mm": 3.0,
                "t3_mm": 2.0,
                "t4_mm": 3.0,
            },
            {
                "kind": "pipe",
                "outer_diameter_mm": 20.0,
                "wall_thickness_mm": 2.0,
            },
        )
        beam_update = None
        for section in beam_sections:
            changes = {"section": section}
            if section["kind"] == "pipe":
                changes["label"] = "Tubular Main Beam"
            beam_update = call(
                ANALYZE_GEOMETRY_CAPABILITY_NAME,
                {
                    "operation": "update_beam_section",
                    "target": _element_target(element_definition_state(beam)),
                    **changes,
                },
            )
            assert (
                beam_update["updated_element_definition"]["definition"]["kind"]
                == section["kind"]
            )
        rotation_update = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "update_beam_rotation",
                "target": _element_target(element_definition_state(rotation)),
                "rotation_degrees": 22.5,
            },
        )
        shell_update = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "update_shell_thickness",
                "target": _element_target(element_definition_state(shell)),
                "thickness_mm": 2.5,
            },
        )
        fluid_sections = (
            {
                "kind": "pipe_enlargement",
                "initial_area_mm2": 20.0,
                "enlarged_area_mm2": 40.0,
            },
            {
                "kind": "pipe_contraction",
                "initial_area_mm2": 40.0,
                "contracted_area_mm2": 20.0,
            },
            {
                "kind": "pipe_inlet",
                "pressure_mpa": 0.13,
                "mass_flow_rate_kg_s": 0.012,
                "pressure_active": True,
                "mass_flow_rate_active": True,
            },
            {
                "kind": "pipe_outlet",
                "pressure_mpa": 0.10,
                "mass_flow_rate_kg_s": -0.012,
                "pressure_active": True,
                "mass_flow_rate_active": False,
            },
            {"kind": "pipe_entrance", "pipe_area_mm2": 20.0, "entrance_area_mm2": 40.0},
            {"kind": "pipe_diaphragm", "pipe_area_mm2": 20.0, "aperture_area_mm2": 8.0},
            {
                "kind": "pipe_bend",
                "pipe_area_mm2": 20.0,
                "bend_radius_to_diameter": 1.5,
                "angle_degrees": 90.0,
                "loss_coefficient": 0.2,
            },
            {
                "kind": "pipe_gate_valve",
                "pipe_area_mm2": 20.0,
                "closing_coefficient": 0.5,
            },
            {
                "kind": "pipe_white_colebrook",
                "pipe_area_mm2": 20.0,
                "hydraulic_radius_mm": 2.0,
                "grain_diameter_mm": 0.0025,
                "form_factor": 0.9,
            },
        )
        for section in fluid_sections:
            result = call(
                ANALYZE_GEOMETRY_CAPABILITY_NAME,
                {
                    "operation": "update_fluid_section",
                    "target": _element_target(element_definition_state(fluid)),
                    "section": section,
                },
            )
            assert (
                result["updated_element_definition"]["definition"]["kind"]
                == section["kind"]
            )
        fluid_before = element_definition_state(fluid)
        fluid_update = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "update_fluid_section",
                "target": _element_target(fluid_before),
                "section": {
                    "kind": "liquid_pump",
                    "curve": [
                        {"flow_rate_mm3_s": 0.0, "head_loss_mm": 30.0},
                        {"flow_rate_mm3_s": 125.0, "head_loss_mm": 24.0},
                        {"flow_rate_mm3_s": 250.0, "head_loss_mm": 14.0},
                    ],
                },
            },
        )
        assert beam_update["updated_element_definition"]["definition"]["kind"] == "pipe"
        assert (
            rotation_update["updated_element_definition"]["definition"][
                "rotation_degrees"
            ]
            == 22.5
        )
        assert (
            shell_update["updated_element_definition"]["definition"]["thickness_mm"]
            == 2.5
        )
        assert (
            len(fluid_update["updated_element_definition"]["definition"]["curve"]) == 3
        )

        stale = call(
            ANALYZE_GEOMETRY_CAPABILITY_NAME,
            {
                "operation": "update_fluid_section",
                "target": _element_target(fluid_before),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(fluid.Label) == "Hydraulic Run"

        read_revision = state.current_revision(str(document.Uid))
        for element in (beam, rotation, shell, fluid):
            current = element_definition_state(element)
            result = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "element_definition",
                    "target": _element_target(current),
                },
            )
            assert result["element_definition"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["element_definition_count"] == 4
        assert not snapshot["element_definitions_truncated"]
        assert {
            item["element_definition_kind"] for item in snapshot["element_definitions"]
        } == {
            "beam_section",
            "beam_rotation",
            "shell_thickness",
            "fluid_section",
        }

        assert tuple(analysis.Group) == (beam, rotation, shell, fluid)
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            source.Name,
            analysis.Name,
            beam.Name,
            rotation.Name,
            shell.Name,
            fluid.Name,
        )
        assert all(obj.VibeCADTimelineRole == "operation" for obj in analysis.Group)
        assert all(
            getattr(obj, "VibeCADTimelineOwner", None) is None for obj in analysis.Group
        )

        document.undo()
        assert (
            element_definition_state(fluid)["definition"]["kind"]
            == "pipe_white_colebrook"
        )
        document.redo()
        assert element_definition_state(fluid)["definition"]["kind"] == "liquid_pump"

        expected = {
            obj.Name: element_definition_state(obj)
            for obj in (beam, rotation, shell, fluid)
        }
        analysis_name = analysis.Name
        element_names = tuple(obj.Name for obj in (beam, rotation, shell, fluid))
        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        App.setActiveDocument(document.Name)
        _process_events(12)
        reopened_analysis = document.getObject(analysis_name)
        assert tuple(obj.Name for obj in reopened_analysis.Group) == element_names
        assert (
            tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
            == operation_names
        )
        for name, old_state in expected.items():
            new_state = element_definition_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["references"] == old_state["references"]

        print(
            "VIBECAD_NATIVE_ANALYZE_GEOMETRY_GUI_OK "
            "actions=4 edits=4 reads=1 exact_references=true typed_sections=true "
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
