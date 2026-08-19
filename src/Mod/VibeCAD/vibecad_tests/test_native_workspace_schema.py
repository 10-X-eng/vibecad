# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeWorkspaceSchema import (
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
    assert set(branch["required"]) == {"operation", "workspace"}
    assert branch["properties"]["workspace"]["enum"] == list(NATIVE_WORKSPACES)
    assert branch["additionalProperties"] is False


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
    }
