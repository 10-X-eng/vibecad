# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-GUI lifecycle gate for Native Geometrical Analysis Features tools."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeAnalyzeGeometricalSchema import (
    ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
)
from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot
from VibeCADNativeAnalyzeState import analysis_state
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
    "create_plane_rotation",
    "create_section_print",
    "create_transform",
)
UPDATE_OPERATIONS = (
    "update_plane_rotation",
    "update_section_print",
    "update_transform",
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
        "FEM_ConstraintPlaneRotation",
        "FEM_ConstraintSectionPrint",
        "FEM_ConstraintTransform",
    } <= set(surface.command_ids)
    return controller, surface


def _turn(surface, registry) -> NativeTurnSnapshot:
    model = registry.definition(ANALYZE_MODEL_CAPABILITY_NAME)
    geometrical = registry.definition(ANALYZE_GEOMETRICAL_CAPABILITY_NAME)
    inspect = registry.definition(ANALYZE_INSPECT_CAPABILITY_NAME)
    assert model is not None and geometrical is not None and inspect is not None
    expected_actions = {
        "FEM_ConstraintPlaneRotation": "create_plane_rotation",
        "FEM_ConstraintSectionPrint": "create_section_print",
        "FEM_ConstraintTransform": "create_transform",
    }
    plans = {
        plan.command_id: plan
        for plan in resolve_native_action_inventory(surface).plans
        if plan.command_id in expected_actions
    }
    assert set(plans) == set(expected_actions)
    for action_id, operation in expected_actions.items():
        plan = plans[action_id]
        assert plan.capability_family == ANALYZE_GEOMETRICAL_CAPABILITY_NAME
        assert plan.operation_variant == operation
        assert plan.classification.mutation and not plan.classification.interactive
        assert any(
            variant.operation == operation and action_id in variant.action_ids
            for variant in geometrical.variants
        )
    contexts = {
        action.action_id: action
        for action in provider_context_actions_for_surface("analyze")
    }
    expected_contexts = {
        "VibeCAD_AnalyzeReadGeometricalFeature": (
            ANALYZE_INSPECT_CAPABILITY_NAME,
            "geometrical_feature",
        ),
        "VibeCAD_AnalyzeUpdatePlaneRotation": (
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            "update_plane_rotation",
        ),
        "VibeCAD_AnalyzeUpdateSectionPrint": (
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            "update_section_print",
        ),
        "VibeCAD_AnalyzeUpdateTransform": (
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            "update_transform",
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
                ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
                ANALYZE_INSPECT_CAPABILITY_NAME,
            ),
            schemas=(
                model.provider_schema(("create_analysis",)),
                geometrical.provider_schema((*CREATE_OPERATIONS, *UPDATE_OPERATIONS)),
                inspect.provider_schema(("geometrical_feature",)),
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


def _feature_target(state: dict) -> dict:
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def _face(source, subelement: str) -> dict:
    return {
        "object_name": source.Name,
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": subelement,
    }


def _publish_source(document, type_id: str, name: str):
    document.openTransaction(f"Create {name}")
    try:
        source = document.addObject(type_id, name)
        if type_id == "Part::Box":
            source.Length = 30.0
            source.Width = 20.0
            source.Height = 10.0
        else:
            source.Radius = 8.0
            source.Height = 25.0
        assert document.recompute([source], True, True) is not False
        assert not source.Shape.isNull() and source.Shape.isValid()
        document.publishProvisionalTimelineOperationBlock(source, (), ())
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return source


def _cylindrical_face(source) -> str:
    return next(
        f"Face{index}"
        for index, face in enumerate(source.Shape.Faces, 1)
        if isinstance(face.Surface, Part.Cylinder)
    )


def _add_transform_conditions(document, analysis, box, cylinder, cylinder_face):
    import ObjectsFem
    from femcommands import manager

    document.openTransaction("Create transform support conditions")
    try:
        displacement = ObjectsFem.makeConstraintDisplacement(
            document,
            document.getUniqueObjectName("Displacement"),
        )
        displacement.Label = "Planar Transform Support"
        displacement.References = [(box, ("Face1",))]
        analysis.addObject(displacement)
        manager._mark_timeline_operation(displacement)
        document.publishProvisionalTimelineOperationBlock(displacement, (), ())

        force = ObjectsFem.makeConstraintForce(
            document,
            document.getUniqueObjectName("Force"),
        )
        force.Label = "Cylindrical Transform Support"
        force.References = [(cylinder, (cylinder_face,))]
        force.Force = "125 N"
        analysis.addObject(force)
        manager._mark_timeline_operation(force)
        document.publishProvisionalTimelineOperationBlock(force, (), ())
        assert document.recompute([displacement, force, analysis], True, True) is not False
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return displacement, force


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-geometrical-"
        )
        save_path = Path(temporary.name) / "native-analyze-geometrical.FCStd"
        document = App.newDocument("NativeAnalyzeGeometricalGate")
        document.UndoMode = 1
        document.saveAs(str(save_path))
        VibeGui._connect_document_observer()
        controller, surface = _select_analyze_ribbon(Gui.getMainWindow())
        box = _publish_source(document, "Part::Box", "PlanarGeometry")
        cylinder = _publish_source(document, "Part::Cylinder", "CylindricalGeometry")
        cylinder_face = _cylindrical_face(cylinder)
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        registry = build_native_capability_registry()
        turn = _turn(surface, registry)
        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-geometrical-gui")

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
                f"native-analyze-geometrical-{call_number}",
            )
            assert result.get("ok") is succeeds, result
            assert not Gui.Control.activeDialog()
            return result

        analysis_result = call(
            ANALYZE_MODEL_CAPABILITY_NAME,
            {
                "operation": "create_analysis",
                "label": "Geometrical Features Analysis",
                "default_solver_policy": "none",
            },
        )
        analysis = document.getObject(
            analysis_result["created_analysis"]["object_name"]
        )
        displacement, force = _add_transform_conditions(
            document,
            analysis,
            box,
            cylinder,
            cylinder_face,
        )
        current_analysis = analysis_state(analysis)

        invalid_transform = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "create_transform",
                "analysis": _analysis_target(current_analysis),
                "label": "Unsupported Transform",
                "face": _face(box, "Face3"),
                "coordinate_system": {
                    "kind": "rectangular",
                    "rotation": {
                        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                        "angle_degrees": 0.0,
                    },
                },
            },
            succeeds=False,
        )
        assert "already used by a displacement" in invalid_transform["error"]
        assert "PlanarGeometry.Face1" in invalid_transform["error"]

        plane_result = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "create_plane_rotation",
                "analysis": _analysis_target(current_analysis),
                "label": "Planar MPC",
                "face": _face(box, "Face2"),
            },
        )
        plane = document.getObject(plane_result["created_feature"]["object_name"])
        current_analysis = analysis_state(analysis)
        section_result = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "create_section_print",
                "analysis": _analysis_target(current_analysis),
                "label": "Section Forces",
                "face": _face(box, "Face4"),
                "variable": "section_force",
            },
        )
        section = document.getObject(
            section_result["created_feature"]["object_name"]
        )
        current_analysis = analysis_state(analysis)
        transform_result = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "create_transform",
                "analysis": _analysis_target(current_analysis),
                "label": "Rectangular Local Frame",
                "face": _face(box, "Face1"),
                "coordinate_system": {
                    "kind": "rectangular",
                    "rotation": {
                        "axis": {"x": 0.0, "y": 1.0, "z": 0.0},
                        "angle_degrees": 35.0,
                    },
                },
            },
        )
        transform = document.getObject(
            transform_result["created_feature"]["object_name"]
        )
        assert str(transform.TransformType) == "Rectangular"
        assert math.isclose(math.degrees(transform.Rotation.Angle), 35.0, abs_tol=1e-10)

        plane_update = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "update_plane_rotation",
                "target": _feature_target(geometrical_feature_state(plane)),
                "label": "Updated Planar MPC",
                "face": _face(box, "Face5"),
            },
        )
        section_update = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "update_section_print",
                "target": _feature_target(geometrical_feature_state(section)),
                "face": _face(box, "Face6"),
                "variable": "heat_flux",
            },
        )
        transform_before_update = geometrical_feature_state(transform)
        transform_update = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "update_transform",
                "target": _feature_target(transform_before_update),
                "label": "Cylindrical Local Frame",
                "face": _face(cylinder, cylinder_face),
                "coordinate_system": {"kind": "cylindrical"},
            },
        )
        assert plane_update["updated_feature"]["face"]["subelement"] == "Face5"
        assert section_update["updated_feature"]["definition"] == {
            "variable": "heat_flux"
        }
        assert str(section.Variable) == "Heat Flux"
        assert str(transform.TransformType) == "Cylindrical"
        derived_axis = transform_update["updated_feature"]["derived_frame"]["axis"]
        assert math.isclose(derived_axis["x"], 0.0, abs_tol=1e-12)
        assert math.isclose(derived_axis["y"], 0.0, abs_tol=1e-12)
        assert math.isclose(abs(derived_axis["z"]), 1.0, abs_tol=1e-12)

        stale = call(
            ANALYZE_GEOMETRICAL_CAPABILITY_NAME,
            {
                "operation": "update_transform",
                "target": _feature_target(transform_before_update),
                "label": "Must Not Apply",
            },
            succeeds=False,
        )
        assert stale["error_code"] == "NATIVE_ANALYZE_STATE_STALE"
        assert str(transform.Label) == "Cylindrical Local Frame"

        features = (plane, section, transform)
        read_revision = state.current_revision(str(document.Uid))
        for feature in features:
            current = geometrical_feature_state(feature)
            read = call(
                ANALYZE_INSPECT_CAPABILITY_NAME,
                {
                    "operation": "geometrical_feature",
                    "target": _feature_target(current),
                },
            )
            assert read["geometrical_feature"] == current
        assert state.current_revision(str(document.Uid)) == read_revision

        snapshot = build_analyze_snapshot(document)
        assert snapshot["geometrical_feature_count"] == 3
        assert not snapshot["geometrical_features_truncated"]
        assert {item["feature_kind"] for item in snapshot["geometrical_features"]} == {
            "plane_rotation",
            "section_print",
            "transform",
        }
        assert tuple(analysis.Group) == (
            displacement,
            force,
            plane,
            section,
            transform,
        )
        operation_names = tuple(obj.Name for obj in document.VibeCADTimeline.Operations)
        assert operation_names == (
            box.Name,
            cylinder.Name,
            analysis.Name,
            displacement.Name,
            force.Name,
            *(obj.Name for obj in features),
        )

        document.undo()
        transform_after_undo = geometrical_feature_state(transform)
        assert transform_after_undo["state_sha256"] == transform_before_update["state_sha256"]
        assert transform_after_undo["definition"] == transform_before_update["definition"]
        assert transform_after_undo["face"] == transform_before_update["face"]
        document.redo()
        assert (
            geometrical_feature_state(transform)["definition"]
            == transform_update["updated_feature"]["definition"]
        )

        expected = {obj.Name: geometrical_feature_state(obj) for obj in features}
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
            new_state = geometrical_feature_state(document.getObject(name))
            assert new_state["state_sha256"] == old_state["state_sha256"]
            assert new_state["definition"] == old_state["definition"]
            assert new_state["face"] == old_state["face"]

        print(
            "VIBECAD_NATIVE_ANALYZE_GEOMETRICAL_GUI_OK "
            "actions=3 edits=3 reads=1 exact_faces=true typed_frames=true "
            "eligibility=true history=true undo_redo=true reopen=true "
            "read_revision_stable=true",
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
