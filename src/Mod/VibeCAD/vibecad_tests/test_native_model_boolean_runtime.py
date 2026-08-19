# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelBooleanRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelBooleanRuntime import NativeModelBooleanRuntime
from VibeCADNativeDesignCombine import prepare_design_combine
from VibeCADNativeDesignSplit import prepare_design_split
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativePartSection import prepare_part_section
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-boolean"
    Name = "DocumentBoolean"


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-boolean-unit")
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
    return NativeModelBooleanRuntime(context), state, document


def _section_arguments():
    return {
        "operation": "section",
        "label": "Exact Section",
        "definition": {
            "operands": [
                {"object_name": "BaseShape"},
                {"object_name": "ToolShape"},
            ]
        },
    }


def _combine_arguments():
    return {
        "operation": "combine",
        "label": "Exact Combine",
        "definition": {
            "mode": "join",
            "source_body": {"object_name": "ResultBody"},
            "tool_bodies": [
                {"object_name": "ToolBodyA"},
                {"object_name": "ToolBodyB"},
            ],
            "keep_tools": False,
        },
    }


def _split_arguments():
    return {
        "operation": "split",
        "label": "Exact Split",
        "definition": {
            "source_body": {"object_name": "SourceBody"},
            "splitters": [
                {"object_name": "Plane", "subelements": []},
                {
                    "object_name": "ToolBody",
                    "subelements": ["Face1", "Shell2"],
                },
            ],
            "retained_region_index": 1,
        },
    }


def test_part_section_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}

    monkeypatch.setattr(runtime_module, "prepare_part_section", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_section",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Part Section preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_part_section",
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

    result = runtime.mutate_boolean(
        _section_arguments(),
        ticket=state.begin_call(document.Uid, "model.boolean"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Part Section"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Section"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_part_section


def test_part_section_preparation_preserves_ordered_exact_operands() -> None:
    spec = prepare_part_section(
        "document-boolean",
        _section_arguments()["definition"],
    )

    assert tuple(reference.document_uid for reference in spec.operands) == (
        "document-boolean",
        "document-boolean",
    )
    assert tuple(reference.object_name for reference in spec.operands) == (
        "BaseShape",
        "ToolShape",
    )


def test_design_combine_runtime_preflights_before_one_immediate_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(runtime_module, "prepare_design_combine", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_combine",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Design Combine preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_combine",
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

    result = runtime.mutate_boolean(
        _combine_arguments(),
        ticket=state.begin_call(document.Uid, "model.boolean"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Combine"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Combine"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_design_combine


def test_design_combine_preparation_preserves_exact_roles_and_mode() -> None:
    spec = prepare_design_combine(
        "document-boolean",
        _combine_arguments()["definition"],
    )

    assert spec.mode == "join"
    assert spec.native_mode == "Join"
    assert spec.result_ref.object_name == "ResultBody"
    assert tuple(reference.object_name for reference in spec.tool_refs) == (
        "ToolBodyA",
        "ToolBodyB",
    )
    assert spec.keep_tools is False


def test_design_split_runtime_preflights_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    spec = object()
    prepared = object()
    captured = {}
    monkeypatch.setattr(runtime_module, "prepare_design_split", lambda uid, value: spec)
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_split",
        lambda target_document, target_spec: (
            prepared
            if target_document is document and target_spec is spec
            else pytest.fail("wrong Design Split preflight target")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_design_split",
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

    result = runtime.mutate_boolean(
        _split_arguments(),
        ticket=state.begin_call(document.Uid, "model.boolean"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Design Split"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "Exact Split"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_design_split


def test_design_split_preparation_preserves_ordered_exact_definitions() -> None:
    spec = prepare_design_split(
        "document-boolean",
        _split_arguments()["definition"],
    )

    assert spec.source_ref.object_name == "SourceBody"
    assert tuple(item.reference.object_name for item in spec.splitters) == (
        "Plane",
        "ToolBody",
    )
    assert tuple(item.subelements for item in spec.splitters) == (
        (),
        ("Face1", "Shell2"),
    )
    assert spec.retained_region_index == 1


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "source_body": {"object_name": "SourceBody"},
                "splitters": [],
                "retained_region_index": 0,
            },
            "1 to 32",
        ),
        (
            {
                "source_body": {"object_name": "SourceBody"},
                "splitters": [
                    {"object_name": "Same", "subelements": []},
                    {"object_name": "Same", "subelements": ["Face1"]},
                ],
                "retained_region_index": 0,
            },
            "must appear once",
        ),
        (
            {
                "source_body": {"object_name": "SourceBody"},
                "splitters": [
                    {"object_name": "Plane", "subelements": ["Face1", "Face1"]}
                ],
                "retained_region_index": 0,
            },
            "nonempty and distinct",
        ),
        (
            {
                "source_body": {"object_name": "SourceBody"},
                "splitters": [{"object_name": "Plane", "subelements": []}],
                "retained_region_index": 256,
            },
            "0 to 255",
        ),
    ),
)
def test_design_split_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _split_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_split",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_boolean(
            arguments,
            ticket=state.begin_call(document.Uid, "model.boolean"),
        )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        (
            {
                "mode": "union",
                "source_body": {"object_name": "ResultBody"},
                "tool_bodies": [{"object_name": "ToolBody"}],
                "keep_tools": False,
            },
            "join, cut, or intersect",
        ),
        (
            {
                "mode": "cut",
                "source_body": {"object_name": "ResultBody"},
                "tool_bodies": [],
                "keep_tools": False,
            },
            "1 to 15",
        ),
        (
            {
                "mode": "intersect",
                "source_body": {"object_name": "SameBody"},
                "tool_bodies": [{"object_name": "SameBody"}],
                "keep_tools": False,
            },
            "must be distinct",
        ),
        (
            {
                "mode": "join",
                "source_body": {"object_name": "ResultBody"},
                "tool_bodies": [{"object_name": "ToolBody"}],
                "keep_tools": 1,
            },
            "true or false",
        ),
        (
            {
                "mode": "join",
                "source_body": {"object_name": "ResultBody"},
                "tool_bodies": [{"object_name": "ToolBody"}],
                "keep_tools": False,
                "refine": True,
            },
            "incorrect fields",
        ),
    ),
)
def test_design_combine_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _combine_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_design_combine",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_boolean(
            arguments,
            ticket=state.begin_call(document.Uid, "model.boolean"),
        )


@pytest.mark.parametrize(
    ("definition", "message"),
    (
        ({"operands": [{"object_name": "OnlyOne"}]}, "exactly two ordered"),
        (
            {
                "operands": [
                    {"object_name": "Same"},
                    {"object_name": "Same"},
                ]
            },
            "must be distinct",
        ),
        (
            {
                "operands": [
                    {"object_name": "Base", "subelement": "Face1"},
                    {"object_name": "Tool"},
                ]
            },
            "target is invalid",
        ),
        (
            {
                "operands": [
                    {"object_name": "Base"},
                    {"object_name": "Tool"},
                ],
                "approximation": True,
            },
            "exact operands",
        ),
    ),
)
def test_part_section_definition_failures_precede_preflight_and_transaction(
    monkeypatch,
    definition,
    message,
) -> None:
    runtime, state, document = _runtime()
    arguments = _section_arguments()
    arguments["definition"] = definition
    monkeypatch.setattr(
        runtime_module,
        "preflight_part_section",
        lambda *_args: pytest.fail("preflight started"),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda *_args, **_kwargs: pytest.fail("mutation started"),
    )

    with pytest.raises(NativeModelError, match=message):
        runtime.mutate_boolean(
            arguments,
            ticket=state.begin_call(document.Uid, "model.boolean"),
        )


def test_retired_or_cross_family_operation_is_rejected_without_compatibility() -> None:
    runtime, state, document = _runtime()
    arguments = _section_arguments()
    arguments["operation"] = "legacy_common"

    with pytest.raises(NativeArgumentError, match="operation is unavailable"):
        runtime.mutate_boolean(
            arguments,
            ticket=state.begin_call(document.Uid, "model.boolean"),
        )
