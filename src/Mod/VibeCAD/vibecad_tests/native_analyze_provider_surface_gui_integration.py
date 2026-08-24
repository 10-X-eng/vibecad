# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production-surface closure gate for the complete Native Analyze ribbon."""

from __future__ import annotations

import json
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADSession as Session
from VibeCADAnalyzeGeometryGui import active_geometry_sources
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import (
    _definition_covers,
    _provider_schema_operations,
    _required_actions,
    _shared_requirements,
    resolve_native_provider_surface,
)
from VibeCADNativeProviderContext import resolve_production_native_surface
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADRibbonSurface import read_active_ribbon_surface


REQUIRED_DOMAIN_TOOLS = {
    "analyze.model",
    "analyze.faces",
    "analyze.inspect",
    "analyze.material_catalog",
    "analyze.geometry",
    "analyze.electromagnetic",
    "analyze.fluid",
    "analyze.initial_velocity",
    "analyze.initial_pressure",
    "analyze.boundary_velocity",
    "analyze.fluid_boundary",
    "analyze.edit_fluid_boundary",
    "analyze.fluid_material",
    "analyze.openfoam_solver",
    "analyze.flow_results",
    "analyze.flow_performance",
    "analyze.show_flow",
    "analyze.solid_material",
    "analyze.solid_region_material",
    "analyze.catalog_material",
    "analyze.custom_material",
    "analyze.fixed_support",
    "analyze.edit_fixed_support",
    "analyze.rigid_coupling",
    "analyze.edit_rigid_coupling",
    "analyze.displacement_support",
    "analyze.edit_displacement_support",
    "analyze.spring_support",
    "analyze.edit_spring_support",
    "analyze.force",
    "analyze.edit_force",
    "analyze.pressure",
    "analyze.edit_pressure",
    "analyze.gravity",
    "analyze.edit_gravity",
    "analyze.centrifugal_load",
    "analyze.edit_centrifugal_load",
    "analyze.mechanical_results",
    "analyze.show_mechanical",
    "analyze.temperature_results",
    "analyze.show_temperature",
    "analyze.geometrical",
    "analyze.support",
    "analyze.connection",
    "analyze.load",
    "analyze.thermal",
    "analyze.mesh",
    "analyze.gmsh_mesh",
    "analyze.solid_mesh",
    "analyze.flow_mesh",
    "analyze.edit_gmsh_mesh",
    "analyze.generate_gmsh",
    "analyze.mesh_field",
    "analyze.mesh_output",
    "analyze.mesh_refinement",
    "analyze.local_mesh_size",
    "analyze.edit_local_mesh_size",
    "analyze.structured_mesh",
    "analyze.solver",
    "analyze.solver_control",
    "analyze.solver_execution",
    "analyze.run_solver",
    "analyze.equation",
    "analyze.results",
    "analyze.presentation",
    "analyze.post",
    "analyze.post_function",
    "analyze.visualization",
}


def _events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _surface():
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        candidate
        for candidate in range(tabs.count())
        if str(tabs.tabData(candidate)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    surface = read_active_ribbon_surface(controller)
    assert surface.surface_id == "analyze"
    return controller, surface


def _variant_for(definition, action_id: str, operation: str):
    matches = tuple(
        variant
        for variant in definition.variants
        if variant.operation == operation
        and action_id in variant.action_ids
        and "analyze" in variant.surface_ids
    )
    assert len(matches) == 1, (definition.name, action_id, operation)
    return matches[0]


def _coverage_gaps(surface, inventory, registry) -> list[dict]:
    requirements = (
        *_shared_requirements(surface.surface_id, registry),
        *_required_actions(surface, inventory.plans),
    )
    gaps = []
    for requirement in requirements:
        definition = registry.definition(requirement.capability_family)
        if definition is None or _definition_covers(
            definition,
            requirement,
            surface.surface_id,
        ):
            continue
        gaps.append(
            {
                "family": requirement.capability_family,
                "action": requirement.action_id,
                "operation": requirement.operation_variant,
                "target": requirement.exact_target_type,
                "transaction": requirement.transaction_behavior,
                "background": requirement.background_required,
            }
        )
    return gaps


def _schema_diagnostics(surface, inventory, registry) -> dict:
    requirements = (
        *_shared_requirements(surface.surface_id, registry),
        *_required_actions(surface, inventory.plans),
    )
    families = tuple(
        dict.fromkeys(requirement.capability_family for requirement in requirements)
    )
    sizes = {}
    for family in families:
        definition = registry.definition(family)
        operations = tuple(
            requirement.operation_variant
            for requirement in requirements
            if requirement.capability_family == family
        )
        encoded = json.dumps(
            definition.provider_schema(operations),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sizes[family] = len(encoded)
    return {
        "total_bytes": sum(sizes.values()) + max(0, len(sizes) - 1),
        "family_bytes": dict(sorted(sizes.items(), key=lambda item: -item[1])),
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    runner = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        document = App.newDocument("NativeAnalyzeProviderSurfaceGate")
        document.UndoMode = 1
        geometry = document.addObject("Part::Feature", "FluidDomain")
        geometry.Label = "Rectangular Fluid Domain"
        geometry.Shape = Part.makeBox(200.0, 60.0, 40.0)
        body = document.addObject("PartDesign::Body", "CanonicalBody")
        feature = body.newObject("PartDesign::Feature", "ContainedFeature")
        feature.Shape = Part.makeBox(20.0, 15.0, 10.0)
        body.Tip = feature
        document.recompute()
        VibeGui._connect_document_observer()
        controller, surface = _surface()
        frozen = NativeSurfaceSnapshot.from_surface(surface)
        inventory = resolve_native_action_inventory(surface)
        registry = build_native_capability_registry()
        schema_diagnostics = _schema_diagnostics(surface, inventory, registry)
        try:
            provider = resolve_native_provider_surface(surface, registry)
        except Exception as exc:
            raise AssertionError(schema_diagnostics) from exc

        assert provider.available is True, {
            **provider.debug_summary(),
            "coverage_gaps": _coverage_gaps(surface, inventory, registry),
        }
        assert not provider.missing_action_ids
        assert not provider.missing_definition_names
        assert not provider.missing_implementation_names
        assert not provider.incomplete_definition_names
        assert len(surface.command_ids) == len(inventory.plans)
        assert REQUIRED_DOMAIN_TOOLS <= set(provider.tool_names)

        ribbon_human_only = tuple(
            plan.command_id
            for plan in inventory.plans
            if plan.classification.human_only
        )
        assert ribbon_human_only == (
            "VibeCAD_AnalyzeStudySetup",
            "FEM_Examples",
        )

        for plan in inventory.plans:
            if plan.classification.parent_only or plan.classification.human_only:
                continue
            definition = registry.definition(plan.capability_family)
            implementation = registry.implementation(plan.capability_family)
            assert definition is not None and implementation is not None
            variant = _variant_for(
                definition,
                plan.command_id,
                str(plan.operation_variant),
            )
            assert variant.transaction_behavior == plan.transaction_behavior
            assert variant.background_required is plan.background_required

        context_actions = provider_context_actions_for_surface("analyze")
        for plan in context_actions:
            definition = registry.definition(plan.capability_family)
            implementation = registry.implementation(plan.capability_family)
            assert definition is not None and implementation is not None
            variant = _variant_for(
                definition,
                plan.action_id,
                str(plan.operation_variant),
            )
            assert variant.exact_target_type == plan.exact_target_type
            assert variant.transaction_behavior == plan.transaction_behavior
            assert variant.background_required is plan.background_required

        encoded_schemas = json.dumps(
            provider.schemas,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        schema_bytes = len(encoded_schemas.encode("utf-8"))
        assert schema_bytes <= 160 * 1024
        assert "unknown" not in encoded_schemas.lower()

        service = get_service()
        service.select_modeling_engine("native")
        initial_context = Session._context_for_provider(service)
        initial_names = {
            str(schema.get("name") or "")
            for schema in initial_context["provider_tool_schemas"]
        }
        assert {"analyze.model", "analyze.material_catalog"} <= initial_names
        assert "analyze.solid_domain" in initial_names
        assert "analyze.inspect" not in initial_names
        assert "analyze.fluid" not in initial_names
        assert "analyze.mesh" not in initial_names
        assert "workspace.switch" not in initial_names
        initial_domain = service.native_active_snapshot()["domain"]
        geometry_names = {
            value["object_name"] for value in initial_domain["geometry_sources"]
        }
        assert geometry_names == {geometry.Name, body.Name}, geometry_names
        assert {
            value.Name for value in active_geometry_sources(document)
        } == geometry_names
        geometry_state = next(
            value
            for value in initial_domain["geometry_sources"]
            if value["object_name"] == geometry.Name
        )
        initial_state_bytes = len(
            json.dumps(
                initial_context["native_state"],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-analyze-provider-surface-gui")

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
        bindings = build_native_runtime_bindings(context, provider.tool_names)
        assert tuple(bindings) == provider.tool_names
        turn = NativeTurnSnapshot.from_provider_surface(provider)
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=registry,
            turn=turn,
            runtimes=bindings,
            reauthorize_turn=reauthorize,
            active_document=lambda: App.ActiveDocument,
        )
        catalog_response = dispatcher.call(
            "analyze.material_catalog",
            json.dumps(
                {"query": "steel", "category": "solid", "limit": 10},
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-material-catalog",
        )
        assert catalog_response.get("ok") is True, catalog_response
        assert catalog_response["materials"]
        assert all(
            set(material["properties"]) - {"name"}
            for material in catalog_response["materials"]
        ), catalog_response["materials"]
        geometry_response = dispatcher.call(
            "analyze.faces",
            json.dumps(
                {
                    "source_name": geometry.Name,
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-read-geometry",
        )
        assert geometry_response.get("ok") is True, geometry_response
        face_page = geometry_response["face_page"]
        assert face_page["source"] == {
            "object_name": geometry.Name,
            "expected_state_sha256": geometry_state["state_sha256"],
        }
        assert face_page["total"] == 6
        assert face_page["faces"][0]["subelement"] == "Face1"
        assert face_page["faces"][0]["center_mm"] == [0.0, 30.0, 20.0]
        assert face_page["faces"][0]["normal"] == [-1.0, 0.0, 0.0]
        assert face_page["faces"][1]["subelement"] == "Face2"
        assert face_page["faces"][1]["center_mm"] == [200.0, 30.0, 20.0]
        assert face_page["faces"][1]["normal"] == [1.0, 0.0, 0.0]
        response = dispatcher.call(
            "analyze.model",
            json.dumps(
                {
                    "operation": "create_analysis",
                    "label": "Provider Surface Analysis",
                    "default_solver_policy": "none",
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-create-analysis",
        )
        assert response.get("ok") is True, response
        assert response["created_analysis"]["object_name"]
        assert document.getObject(response["created_analysis"]["object_name"])
        ledger.end_run("native-analyze-provider-surface-gui")

        setup_context = Session._context_for_provider(service)
        setup_surface = dict(setup_context["provider_tool_surface"])
        setup_schemas = list(setup_context["provider_tool_schemas"])
        setup_trace = []
        execution = create_native_session_execution(
            service=service,
            expected_surface=setup_surface,
            expected_schemas=setup_schemas,
            controller=controller,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        runner = NativeProviderToolRunner(
            execution=execution,
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=setup_surface,
            frozen_schemas=setup_schemas,
            frozen_modeling_surface=dict(setup_context["modeling_surface"]),
            tool_trace=setup_trace,
        )
        study_response = runner(
            "analyze.model",
            json.dumps(
                {
                    "operation": "update_study",
                    "target": {
                        "object_name": response["created_analysis"]["object_name"],
                        "expected_state_sha256": response["created_analysis"][
                            "state_sha256"
                        ],
                        "expected_member_count": response["created_analysis"][
                            "member_count"
                        ],
                    },
                    "study": {"physics": ["fluid"], "regime": "steady"},
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-update-study",
        )
        assert study_response.get("ok") is True, study_response
        if "provider_surface_changed" not in study_response:
            Session._context_for_provider(service)
        assert study_response["provider_surface_changed"] is True
        assert study_response["next_turn_required"] is True
        assert runner.turn_transition_requested() is True
        continuation = VibeGui._native_surface_continuation_event(
            type(
                "Response",
                (),
                {"error": None, "tool_trace": setup_trace},
            )()
        )
        assert continuation is not None
        assert continuation["type"] == "cad_provider_surface_changed"
        assert continuation["surface_id"] == "analyze"
        runner.close()
        runner = None

        fluid_context = Session._context_for_provider(service)
        assert service.native_active_snapshot()["domain"]["provider_scope"] == {
            "analysis_count": 1,
            "undeclared_analysis_count": 0,
            "physics": ["fluid"],
            "mesh_definition_count": 0,
            "generated_mesh_count": 0,
            "solver_count": 0,
            "result_count": 0,
        }
        fluid_schemas = list(fluid_context["provider_tool_schemas"])
        fluid_names = {str(schema.get("name") or "") for schema in fluid_schemas}
        service_surface = service.provider_tool_surface()
        assert {
            str(schema.get("name") or "") for schema in service_surface["tools"]
        } == fluid_names
        assert {
            "analyze.model",
            "analyze.initial_velocity",
            "analyze.initial_pressure",
            "analyze.fluid_boundary",
            "analyze.fluid_material",
            "analyze.openfoam_solver",
            "analyze.flow_mesh",
        } <= fluid_names
        assert not {
            "analyze.fluid",
            "analyze.boundary_velocity",
            "analyze.geometry",
            "analyze.mesh",
            "analyze.gmsh_mesh",
            "analyze.solid_mesh",
            "analyze.generate_gmsh",
            "analyze.load",
            "analyze.support",
            "analyze.solver",
            "analyze.thermal",
            "analyze.electromagnetic",
            "analyze.inspect",
            "workspace.switch",
        } & fluid_names
        fluid_schema_bytes = len(
            json.dumps(
                fluid_schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert fluid_schema_bytes < 48 * 1024
        fluid_state_bytes = len(
            json.dumps(
                fluid_context["native_state"],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        fluid_trace = []
        runner = NativeProviderToolRunner(
            execution=create_native_session_execution(
                service=service,
                expected_surface=dict(fluid_context["provider_tool_surface"]),
                expected_schemas=fluid_schemas,
                controller=controller,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            ),
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=dict(fluid_context["provider_tool_surface"]),
            frozen_schemas=fluid_schemas,
            frozen_modeling_surface=dict(fluid_context["modeling_surface"]),
            tool_trace=fluid_trace,
        )
        boundary_response = runner(
            "analyze.fluid_boundary",
            json.dumps(
                {
                    "analysis_name": response["created_analysis"]["object_name"],
                    "source_name": geometry.Name,
                    "face_names": ["Face1"],
                    "condition": {"kind": "inlet_velocity", "velocity_m_s": 5.0},
                    "label": "Provider Surface Inlet",
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-create-boundary",
        )
        assert boundary_response.get("ok") is True, boundary_response
        boundary_domain = service.native_active_snapshot()["domain"]
        _live_registry, complete_surface = resolve_production_native_surface()
        complete_edit_schema = next(
            schema
            for name, schema in zip(
                complete_surface.tool_names,
                complete_surface.schemas,
                strict=True,
            )
            if name == "analyze.edit_fluid_boundary"
        )
        assert _provider_schema_operations(complete_edit_schema) == (), (
            complete_edit_schema,
        )
        post_boundary_context = Session._context_for_provider(service)
        post_boundary_names = {
            schema["name"]
            for schema in post_boundary_context["provider_tool_schemas"]
        }
        assert "analyze.edit_fluid_boundary" in post_boundary_names, (
            boundary_domain,
            sorted(post_boundary_names),
        )
        assert boundary_response.get("provider_surface_changed") is True, (
            boundary_response,
            boundary_domain,
            sorted(post_boundary_names),
        )
        assert boundary_response.get("next_turn_required") is True
        assert runner.turn_transition_requested() is True
        runner.close()
        runner = None
        boundary_context = Session._context_for_provider(service)
        boundary_schemas = list(boundary_context["provider_tool_schemas"])
        runner = NativeProviderToolRunner(
            execution=create_native_session_execution(
                service=service,
                expected_surface=dict(boundary_context["provider_tool_surface"]),
                expected_schemas=boundary_schemas,
                controller=controller,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            ),
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=dict(boundary_context["provider_tool_surface"]),
            frozen_schemas=boundary_schemas,
            frozen_modeling_surface=dict(boundary_context["modeling_surface"]),
            tool_trace=[],
        )
        mesh_response = runner(
            "analyze.flow_mesh",
            json.dumps(
                {
                    "analysis_name": response["created_analysis"]["object_name"],
                    "source_name": geometry.Name,
                    "maximum_size_mm": 10.0,
                    "label": "Provider Surface Mesh",
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-create-mesh",
        )
        assert mesh_response.get("ok") is True, mesh_response
        post_mesh_context = Session._context_for_provider(service)
        post_mesh_names = {
            schema["name"] for schema in post_mesh_context["provider_tool_schemas"]
        }
        mesh_domain = service.native_active_snapshot()["domain"]
        assert mesh_response.get("provider_surface_changed") is True, (
            mesh_response,
            mesh_domain,
            sorted(post_mesh_names),
        )
        assert mesh_response.get("next_turn_required") is True
        assert runner.turn_transition_requested() is True
        runner.close()
        runner = None

        generate_context = Session._context_for_provider(service)
        generate_schemas = list(generate_context["provider_tool_schemas"])
        generate_names = {
            schema["name"] for schema in generate_context["provider_tool_schemas"]
        }
        assert "analyze.generate_gmsh" in generate_names
        assert "analyze.local_mesh_size" in generate_names
        assert "analyze.edit_local_mesh_size" not in generate_names
        runner = NativeProviderToolRunner(
            execution=create_native_session_execution(
                service=service,
                expected_surface=dict(generate_context["provider_tool_surface"]),
                expected_schemas=generate_schemas,
                controller=controller,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            ),
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=dict(generate_context["provider_tool_surface"]),
            frozen_schemas=generate_schemas,
            frozen_modeling_surface=dict(generate_context["modeling_surface"]),
            tool_trace=[],
        )
        refinement_response = runner(
            "analyze.local_mesh_size",
            json.dumps(
                {
                    "mesh_name": mesh_response["mesh_name"],
                    "source_name": geometry.Name,
                    "subelement_names": ["Face1"],
                    "element_size_mm": 5.0,
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-create-local-size",
        )
        assert refinement_response.get("ok") is True, refinement_response
        assert refinement_response.get("provider_surface_changed") is True
        runner.close()
        runner = None

        edit_context = Session._context_for_provider(service)
        edit_schemas = list(edit_context["provider_tool_schemas"])
        edit_names = {schema["name"] for schema in edit_schemas}
        assert "analyze.edit_local_mesh_size" in edit_names
        assert "analyze.generate_gmsh" in edit_names
        runner = NativeProviderToolRunner(
            execution=create_native_session_execution(
                service=service,
                expected_surface=dict(edit_context["provider_tool_surface"]),
                expected_schemas=edit_schemas,
                controller=controller,
                document_thread_dispatch=VibeGui._dispatch_to_document_thread,
            ),
            document_dispatch=lambda operation: operation(),
            refresh_context=lambda: Session._context_for_provider(service),
            frozen_surface=dict(edit_context["provider_tool_surface"]),
            frozen_schemas=edit_schemas,
            frozen_modeling_surface=dict(edit_context["modeling_surface"]),
            tool_trace=[],
        )
        edit_response = runner(
            "analyze.edit_local_mesh_size",
            json.dumps(
                {
                    "refinement_name": refinement_response["refinement_name"],
                    "changes": {"element_size_mm": 4.0},
                },
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-edit-local-size",
        )
        assert edit_response.get("ok") is True, edit_response
        generate_response = runner(
            "analyze.generate_gmsh",
            json.dumps(
                {"mesh_name": mesh_response["mesh_name"]},
                separators=(",", ":"),
            ),
            "native-analyze-provider-surface-generate-mesh",
        )
        assert generate_response.get("ok") is True, generate_response
        pending_domain = service.native_active_snapshot()["domain"]
        pending_context = Session._context_for_provider(service)
        pending_names = {
            schema["name"] for schema in pending_context["provider_tool_schemas"]
        }
        job_id = generate_response["job"]["job_id"]
        assert pending_domain["provider_scope"]["generated_mesh_count"] == 0
        assert pending_domain["run_status"]["job_id"] == job_id
        assert pending_domain["run_status"]["terminal"] is False
        assert "analyze.generate_gmsh" not in pending_names
        assert "native.job" in pending_names
        assert generate_response.get("provider_surface_changed") is True, (
            generate_response,
            pending_domain,
            sorted(pending_names),
        )
        assert generate_response.get("next_turn_required") is True
        assert runner.turn_transition_requested() is True
        runner.close()
        runner = None

        deadline = QtCore.QDeadlineTimer(30000)
        while not service.native_background_manager().snapshot(job_id).terminal:
            assert not deadline.hasExpired(), job_id
            _events(2)
        completed = service.native_background_manager().snapshot(job_id)
        assert completed.phase == "completed", completed
        generated_domain = service.native_active_snapshot()["domain"]
        generated_context = Session._context_for_provider(service)
        generated_names = {
            schema["name"] for schema in generated_context["provider_tool_schemas"]
        }
        assert generated_domain["provider_scope"]["generated_mesh_count"] == 1
        assert "analyze.generate_gmsh" not in generated_names
        assert "analyze.mesh_output" in generated_names

        completed_after_capture = create_native_session_execution(
            service=service,
            expected_surface=dict(pending_context["provider_tool_surface"]),
            expected_schemas=list(pending_context["provider_tool_schemas"]),
            controller=controller,
            document_thread_dispatch=VibeGui._dispatch_to_document_thread,
        )
        completed_after_capture.close()

        print(
            "VIBECAD_NATIVE_ANALYZE_PROVIDER_SURFACE_GUI_OK "
            f"actions={len(inventory.plans)} contexts={len(context_actions)} "
            f"tools={len(provider.tool_names)} schemas={schema_bytes}B "
            f"fluid_scoped={fluid_schema_bytes}B "
            f"state={initial_state_bytes}B->{fluid_state_bytes}B "
            "exact_targets=true runtimes=true full_surface_call=true "
            "automatic_scope_loop=true boundary_scope_transition=true "
            "mesh_scope_transition=true generated_scope_transition=true "
            "capture_dispatch_atomic=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if runner is not None:
            runner.close()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
