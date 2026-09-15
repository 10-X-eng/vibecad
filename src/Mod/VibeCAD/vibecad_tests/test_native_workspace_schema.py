# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeWorkspaceSchema import (
    NATIVE_SURFACE_BY_WORKSPACE,
    NATIVE_WORKSPACES,
    NATIVE_WORKSPACE_SURFACES,
    workspace_capability_definition,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADNativeWorkspaceRuntime import (
    NativeWorkspaceRuntime,
    WORKBENCH_BY_NATIVE_WORKSPACE,
)


class _Document:
    Uid = "workspace-document"


def test_workspace_switch_is_one_shared_inter_turn_surface_control() -> None:
    definition = workspace_capability_definition()
    variant = definition.variants[0]
    branch = definition.provider_schema(("switch",))["parameters"]["oneOf"][0]

    assert definition.name == "workspace.switch"
    assert definition.primary_classification == "view"
    assert variant.transaction_behavior == "surface_control"
    assert variant.surface_ids == frozenset(NATIVE_WORKSPACE_SURFACES)
    assert "sketch.edit" not in variant.surface_ids
    assert set(branch["required"]) == {"workspace"}
    assert branch["properties"]["workspace"]["enum"] == list(NATIVE_WORKSPACES)
    assert branch["additionalProperties"] is False
    assert NATIVE_SURFACE_BY_WORKSPACE["aerodynamics"] == "aero"
    assert WORKBENCH_BY_NATIVE_WORKSPACE["aerodynamics"] == "VibeCADAeroWorkbench"


def test_provider_visible_switch_explains_how_to_obtain_missing_tools():
    from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
    from VibeCADNativeProviderContext import provider_visible_native_state

    schema = provider_visible_native_schema(
        workspace_capability_definition().provider_schema(("switch",)))
    # Variant descriptions are compacted away. The model must receive this
    # before calling switch, including when no sheet inspection is available.
    assert "tools" in schema["description"].lower()
    state = {"surface_id": "assemble"}
    visible = provider_visible_native_state(state)
    assert state == {"surface_id": "assemble"}
    assert visible["surface_id"] == "assemble"
    guidance = visible["workspace_navigation"]
    assert guidance["tool"] == "workspace.switch"
    text = guidance["message"].lower()
    assert "next turn" in text
    assert "automatically" in text
    assert "end this turn" in text


def test_workspace_navigation_does_not_offer_switching_during_sketch_edit():
    from VibeCADNativeProviderContext import provider_visible_native_state

    state = {"surface_id": "sketch.edit", "edit_active": True}
    assert provider_visible_native_state(state) == state


def test_sheet_metal_can_leave_for_assembly_and_return_to_its_tools():
    from jsonschema import Draft202012Validator
    from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
    from VibeCADNativeRegistry import build_native_capability_registry
    from vibecad_tests.test_native_sheetmetal_surface import surface

    registry = build_native_capability_registry()
    provider = resolve_native_provider_surface(surface(), registry)
    assert provider.available
    from VibeCADNativeProviderContext import provider_authorized_native_surface
    provider = provider_authorized_native_surface(provider)
    assert "workspace.switch" in provider.tool_names
    assert NATIVE_SURFACE_BY_WORKSPACE["sheet_metal"] == "sheet_metal"
    assert WORKBENCH_BY_NATIVE_WORKSPACE["sheet_metal"] == "SMWorkbench"
    validator = Draft202012Validator(workspace_capability_definition().provider_schema(("switch",))["parameters"])
    for workspace in ("sheet_metal", "assembly", "modeling"):
        assert not list(validator.iter_errors({"workspace": workspace}))


def test_workspace_runtime_activates_exact_workbench_on_document_thread() -> None:
    document = _Document()
    surface = {"id": "model"}
    activated = []

    def activate(workbench: str) -> None:
        activated.append(workbench)
        from VibeCADNativeWorkspaceSchema import NATIVE_SURFACE_BY_WORKSPACE

        workspace = next(
            name for name, candidate in WORKBENCH_BY_NATIVE_WORKSPACE.items()
            if candidate == workbench
        )
        surface["id"] = NATIVE_SURFACE_BY_WORKSPACE[workspace]

    context = NativeRuntimeContext(
        service=object(),
        document=document,
        state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: surface["id"],
        edit_or_task_active=lambda: False,
        document_thread_dispatch=lambda operation: operation(),
    )
    runtime = NativeWorkspaceRuntime(context, activate_workbench=activate)

    result = runtime.switch({"operation": "switch", "workspace": "assembly"})

    assert activated == ["AssemblyWorkbench"]
    assert result == {
        "workspace": "assembly",
        "next_turn_required": True,
        "message": "The assembly workspace is active. End this turn; VibeCAD continues automatically with its tools on the next turn.",
    }


def test_switch_destinations_cover_every_ribbon_domain_and_explain_tool_refresh():
    import re
    from pathlib import Path
    ribbon = (Path(__file__).resolve().parents[3] / "Gui" / "VibeCADRibbon.cpp").read_text()
    table = ribbon.split(" domains = {{", 1)[1].split("}};", 1)[0]
    domains = re.findall(r'\{"[^"\n]+", "([^"\n]+)", "([^"\n]+)"\}', table)
    assert len(domains) >= 10
    for workbench, surface in domains:
        workspace = next((name for name, value in NATIVE_SURFACE_BY_WORKSPACE.items()
                          if value == surface), None)
        assert workspace is not None, surface
        assert WORKBENCH_BY_NATIVE_WORKSPACE[workspace] == workbench
    definition = workspace_capability_definition()
    text = definition.description + " " + definition.variants[0].description
    assert "after the switch" in text.lower()
    assert "next turn" in text.lower()
    assert "tools" in text.lower()
