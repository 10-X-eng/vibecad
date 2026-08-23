# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production-surface closure gate for the complete Native Analyze ribbon."""

from __future__ import annotations

import json
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADSession as Session
from VibeCADCore import get_service
from VibeCADNativeActionManifest import resolve_native_action_inventory
from VibeCADNativeCapabilityRegistry import (
    _definition_covers,
    _required_actions,
    _shared_requirements,
    resolve_native_provider_surface,
)
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
    "analyze.inspect",
    "analyze.geometry",
    "analyze.electromagnetic",
    "analyze.fluid",
    "analyze.geometrical",
    "analyze.support",
    "analyze.connection",
    "analyze.load",
    "analyze.thermal",
    "analyze.mesh",
    "analyze.mesh_field",
    "analyze.mesh_output",
    "analyze.mesh_refinement",
    "analyze.structured_mesh",
    "analyze.solver",
    "analyze.solver_control",
    "analyze.solver_execution",
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
        assert len(provider.tool_names) <= 32
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
        assert schema_bytes <= 120 * 1024
        assert schema_bytes <= 128 * 1024 - 8 * 1024
        assert "unknown" not in encoded_schemas.lower()

        service = get_service()
        service.select_modeling_engine("native")
        initial_context = Session._context_for_provider(service)
        initial_names = {
            str(schema.get("name") or "")
            for schema in initial_context["provider_tool_schemas"]
        }
        assert {"analyze.model", "analyze.inspect"} <= initial_names
        assert "analyze.fluid" not in initial_names
        assert "analyze.mesh" not in initial_names
        assert "workspace.switch" not in initial_names
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
            document_thread_dispatch=lambda operation: operation(),
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
            "analyze.inspect",
            "analyze.geometry",
            "analyze.fluid",
            "analyze.mesh",
            "analyze.solver",
        } <= fluid_names
        assert not {
            "analyze.load",
            "analyze.support",
            "analyze.thermal",
            "analyze.electromagnetic",
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

        print(
            "VIBECAD_NATIVE_ANALYZE_PROVIDER_SURFACE_GUI_OK "
            f"actions={len(inventory.plans)} contexts={len(context_actions)} "
            f"tools={len(provider.tool_names)} schemas={schema_bytes}B "
            f"fluid_scoped={fluid_schema_bytes}B "
            f"state={initial_state_bytes}B->{fluid_state_bytes}B "
            "exact_targets=true runtimes=true full_surface_call=true "
            "automatic_scope_loop=true",
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
