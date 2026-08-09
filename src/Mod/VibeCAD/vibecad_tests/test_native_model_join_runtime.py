# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelJoinRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelJoinRuntime import NativeModelJoinRuntime
from VibeCADNativePartJoin import prepare_part_join
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-join"
    Name = "DocumentJoin"


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-join-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
    )
    return NativeModelJoinRuntime(context), state, document


def _arguments(operation: str):
    definition = {
        "sources": [{"object_name": "First"}, {"object_name": "Second"}],
        "refine": True,
        "tolerance_mm": 0.025,
    }
    if operation != "connect":
        definition = {
            "base": {"object_name": "First"},
            "tool": {"object_name": "Second"},
            "refine": False,
            "tolerance_mm": 0.0,
        }
    return {
        "operation": operation,
        "label": f"Exact {operation.title()}",
        "definition": definition,
    }


@pytest.mark.parametrize("operation", ("connect", "embed", "cutout"))
def test_join_runtime_prepares_preflights_and_routes_one_mutation(monkeypatch, operation) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(
        runtime_module,
        "prepare_part_join",
        lambda uid, selected, value: (
            captured.update(uid=uid, operation=selected, definition=value) or spec
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_join",
        lambda target_document, value: (
            captured.update(preflight_document=target_document, spec=value) or prepared
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_join",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    arguments = _arguments(operation)
    result = runtime.mutate_join(
        arguments,
        ticket=state.begin_call(document.Uid, "model.join"),
    )

    assert result == {"routed": True}
    assert captured["uid"] == document.Uid
    assert captured["operation"] == operation
    assert captured["definition"] is arguments["definition"]
    assert captured["preflight_document"] is document
    assert captured["spec"] is spec
    assert captured["transaction_name"] == f"Create Native Part Join {operation.title()}"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == f"Exact {operation.title()}"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_join


def test_join_preparation_preserves_order_and_exact_controls() -> None:
    connect = prepare_part_join("doc", "connect", _arguments("connect")["definition"])
    embed = prepare_part_join("doc", "embed", _arguments("embed")["definition"])

    assert tuple(item.object_name for item in connect.operand_refs) == ("First", "Second")
    assert connect.refine is True
    assert connect.tolerance_mm == 0.025
    assert tuple(item.object_name for item in embed.operand_refs) == ("First", "Second")
    assert embed.refine is False
    assert embed.tolerance_mm == 0.0


@pytest.mark.parametrize(
    ("operation", "definition", "message"),
    (
        ("connect", {"sources": [], "refine": True, "tolerance_mm": 0.0}, "1 to 32"),
        (
            "connect",
            {
                "sources": [{"object_name": "Same"}, {"object_name": "Same"}],
                "refine": True,
                "tolerance_mm": 0.0,
            },
            "distinct",
        ),
        (
            "embed",
            {
                "base": {"object_name": "Base"},
                "tool": {"object_name": "Tool"},
                "refine": 1,
                "tolerance_mm": 0.0,
            },
            "true or false",
        ),
        (
            "cutout",
            {
                "base": {"object_name": "Base"},
                "tool": {"object_name": "Tool"},
                "refine": False,
                "tolerance_mm": float("inf"),
            },
            "outside",
        ),
    ),
)
def test_join_preparation_rejects_ambiguous_or_unbounded_definitions(
    operation,
    definition,
    message,
) -> None:
    with pytest.raises(NativeModelError, match=message):
        prepare_part_join("doc", operation, definition)


def test_join_runtime_rejects_retired_or_cross_family_operations() -> None:
    runtime, state, document = _runtime()
    arguments = _arguments("connect")
    arguments["operation"] = "fuse"

    with pytest.raises(NativeArgumentError, match="operation is unavailable"):
        runtime.mutate_join(
            arguments,
            ticket=state.begin_call(document.Uid, "model.join"),
        )
