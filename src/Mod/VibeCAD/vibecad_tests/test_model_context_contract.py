# SPDX-License-Identifier: LGPL-2.1-or-later

"""Model-facing context, inspection, and one-shot attachment contracts."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from VibeCADCore import VibeCADService
from VibeCADInspection import (
    MAX_INSPECT_RESULT_BYTES,
    _bounded_page,
    _encoded_bytes,
    capture_inspection,
    complete_inspection,
)
from VibeCADProject import _validated_conversation_turns
import VibeCADProvider as provider
import VibeCADSession as session
from VibeCADTools import ToolSpec
from tool_impl.service import core_inspect


def _prompt_payload(prompt: str, context: dict) -> tuple[dict, str]:
    rendered = session._provider_prompt(prompt, context)
    prefix = "VIBECAD_CONTEXT_JSON\n"
    marker = "\nEND_VIBECAD_CONTEXT_JSON\n\n"
    assert rendered.startswith(prefix)
    encoded, remainder = rendered[len(prefix) :].split(marker, 1)
    return json.loads(encoded), remainder


def _active_state(object_count: int = 10) -> dict:
    return {
        "workbench": "AssemblyWorkbench",
        "modeling_surface": {
            "workbench": "AssemblyWorkbench",
            "engine": "native",
            "domain": "assemblies",
            "surface_id": "vibecad/surface/assembly/native",
            "available": True,
        },
        "document": {
            "name": "Mechanism",
            "uid": "doc-1",
            "object_count": object_count,
            "edit_object": None,
        },
        "selection": {"selection_count": 0, "selection": []},
    }


def test_turn_prompt_contains_only_the_approved_exact_facts() -> None:
    current = "Move the crank through one full revolution."
    context = {
        **_active_state(),
        "conversation": {
            "conversation": [
                {"role": "user", "content": "obsolete question"},
                {"role": "assistant", "content": "obsolete answer"},
                {"role": "user", "content": "Build the crank assembly."},
                {"role": "assistant", "content": "The crank assembly is built."},
                {"role": "user", "content": current},
            ]
        },
        "cad_state": {"huge": "must not leak"},
        "assembly": {"objects": ["must not leak"]},
        "working_set": ["must not leak"],
        "intent_memory": {"must": "not leak"},
        "tool_trace": [{"result": "must not leak"}],
        "provider_tool_schemas": [{"name": "core.inspect"}],
    }

    payload, remainder = _prompt_payload(current, context)

    assert set(payload) == {"active_state"}
    assert set(payload["active_state"]) == {
        "workbench",
        "modeling_surface",
        "document",
        "selection",
    }
    assert remainder == f"CURRENT_USER_MESSAGE\n{current}"
    assert current not in json.dumps(payload)
    serialized = json.dumps(payload)
    assert "must not leak" not in serialized
    for forbidden in ("cad_state", "working_set", "intent_memory", "tool_trace"):
        assert forbidden not in serialized


def test_turn_history_is_never_copied_into_model_context() -> None:
    prior_user = "u" * 6000
    prior_assistant = "a" * 6000
    context = {
        **_active_state(),
        "conversation": {
            "conversation": [
                {"role": "user", "content": prior_user},
                {"role": "assistant", "content": prior_assistant},
                {"role": "user", "content": "follow up"},
            ]
        },
    }

    payload, _ = _prompt_payload("follow up", context)
    assert payload == {"active_state": _active_state()}
    serialized = json.dumps(payload)
    assert prior_user not in serialized
    assert prior_assistant not in serialized
    assert "previous_completed_exchange" not in serialized


@pytest.mark.parametrize("object_count", (10, 100, 1000))
def test_turn_context_size_does_not_scale_with_document_objects(object_count: int) -> None:
    payload, _ = _prompt_payload("Continue.", _active_state(object_count))
    encoded = json.dumps(payload, separators=(",", ":")).encode()

    assert len(encoded) < 2048
    assert payload["active_state"]["document"]["object_count"] == object_count
    assert "objects" not in payload["active_state"]["document"]


def test_document_count_does_not_iterate_the_document_objects() -> None:
    class _LenOnlyObjects:
        def __len__(self) -> int:
            return 1000

        def __iter__(self):
            raise AssertionError("turn-start context must not enumerate document objects")

    service = object.__new__(VibeCADService)
    service._active_document = lambda: SimpleNamespace(
        Name="LargeDocument", Uid="doc-large", Objects=_LenOnlyObjects()
    )
    service.provider_edit_object_summary = lambda: None

    assert service.provider_turn_document_summary() == {
        "name": "LargeDocument",
        "uid": "doc-large",
        "object_count": 1000,
        "edit_object": None,
    }


def test_oversized_selection_is_rejected_before_object_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from VibeCADCore import MAX_PROVIDER_SELECTION_ITEMS

    class _UninspectableSelection:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"oversized selection item was inspected: {name}")

    selected = [
        _UninspectableSelection()
        for _index in range(MAX_PROVIDER_SELECTION_ITEMS + 1)
    ]
    gui = ModuleType("FreeCADGui")
    gui.Selection = SimpleNamespace(getSelectionEx=lambda: selected)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    service = object.__new__(VibeCADService)

    summary = service.provider_turn_selection_summary()

    assert summary["selection_count"] == len(selected)
    assert summary["selection_omitted"] is True
    assert summary["selection_item_limit"] == MAX_PROVIDER_SELECTION_ITEMS
    assert "selection" not in summary
    assert "sample" not in summary


def test_provider_context_does_not_copy_conversation_cache() -> None:
    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service.modeling_engine = lambda: "native"
    service.provider_turn_document_summary = lambda: _active_state()["document"]
    service.provider_turn_selection_summary = lambda: _active_state()["selection"]
    service.view_screenshot_summary = lambda: {"captured": False}
    service.pending_reference_image_attachments = lambda: []
    service._conversation_cache = [
        {"role": "user", "content": f"must not leak {index}"}
        for index in range(1000)
    ]

    context = service.provider_context_summary()

    assert "conversation" not in context
    assert "must not leak" not in json.dumps(context)
    assert not hasattr(VibeCADService, "provider_conversation_cache_snapshot")


def test_legacy_tool_trace_is_removed_during_conversation_validation() -> None:
    validated = _validated_conversation_turns(
        [
            {
                "role": "assistant",
                "content": "done",
                "sequence": 1,
                "turn_id": "1" * 32,
                "tool_trace": [{"arguments": "x" * 10000, "result": "y" * 10000}],
            }
        ],
        source="test",
    )

    assert validated == [
        {
            "role": "assistant",
            "content": "done",
            "sequence": 1,
            "turn_id": "1" * 32,
        }
    ]


def test_session_conversation_artifact_write_stays_off_document_thread() -> None:
    in_document_callback = False
    events: list[str] = []

    class _Service:
        def prepare_conversation_turn(self, *args, **kwargs):
            assert in_document_callback is True
            events.append("capture")
            return {"entry": {"role": args[0], "content": args[1]}}

        def persist_prepared_conversation_turn(self, prepared):
            assert in_document_callback is False
            events.append("persist")
            return {
                "conversation_id": "1" * 32,
                "conversation": [dict(prepared["entry"])],
            }

        def accept_persisted_conversation_turn(self, history, prepared):
            assert in_document_callback is True
            assert history["conversation_id"] == "1" * 32
            assert prepared["entry"]["content"] == "hello"
            events.append("accept")

    def dispatch(operation):
        nonlocal in_document_callback
        assert in_document_callback is False
        in_document_callback = True
        try:
            return operation()
        finally:
            in_document_callback = False

    history = session._persist_session_conversation_turn(
        _Service(), "user", "hello", dispatch=dispatch
    )

    assert history["conversation_id"] == "1" * 32
    assert events == ["capture", "persist", "accept"]


def test_session_modeling_engine_manifest_read_stays_off_document_thread() -> None:
    in_document_callback = False
    events: list[str] = []

    class _Service:
        def prepare_modeling_engine_read(self):
            assert in_document_callback is True
            events.append("capture")
            return {"manifest_path": "/project/project.vibecad.json"}

        def complete_modeling_engine_read(self, prepared):
            assert in_document_callback is False
            assert prepared["manifest_path"].endswith("project.vibecad.json")
            events.append("read")
            return "native"

        def accept_modeling_engine_read(self, prepared, engine):
            assert in_document_callback is True
            assert engine == "native"
            events.append("accept")
            return {"accepted": True, "engine": engine}

    def dispatch(operation):
        nonlocal in_document_callback
        assert in_document_callback is False
        in_document_callback = True
        try:
            return operation()
        finally:
            in_document_callback = False

    engine = session._prime_modeling_engine_for_session(_Service(), dispatch)

    assert engine == "native"
    assert events == ["capture", "read", "accept"]


def test_non_partdesign_engine_state_does_not_probe_optional_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADBuild123d as build123d
    import VibeCADOpenSCAD as openscad

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("optional runtime probe must not run outside Part Design")

    monkeypatch.setattr(build123d, "runtime_health", unexpected_probe)
    monkeypatch.setattr(openscad, "runtime_health", unexpected_probe)
    service = object.__new__(VibeCADService)
    service.build123d_enabled = lambda: True
    service.openscad_enabled = lambda: True
    service.vibescript_enabled = lambda: True
    service.modeling_engine = lambda: "vibescript"

    state = service.modeling_engine_state("AssemblyWorkbench")

    assert state["selected"] == "vibescript"
    assert state["build123d"]["ready"] is False
    assert state["openscad"]["ready"] is False
    assert state["available_engines"] == ["native", "vibescript"]


def test_core_inspect_schema_is_one_low_friction_read_interface() -> None:
    spec = ToolSpec.from_mapping(core_inspect.TOOL_SPEC)

    spec.validate_arguments({"scope": "document"})
    assert spec.safety.value == "read"
    assert spec.parameters["required"] == ["scope"]
    assert spec.parameters["properties"]["limit"]["default"] == 20
    assert set(spec.parameters["properties"]["scope"]["enum"]) == {
        "document",
        "selection",
        "object",
        "domain",
        "program",
        "api",
        "image",
    }


def test_turn_start_rejects_oversized_exact_tool_schemas() -> None:
    schema = {
        "name": "core.inspect",
        "description": "x" * session.MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }

    with pytest.raises(ValueError, match="exact turn-start provider schemas exceed"):
        session._turn_start_tool_surface(
            "PartWorkbench",
            [schema],
            engine="native",
        )


def test_core_inspect_pages_are_deterministic_and_exactly_size_accounted() -> None:
    raw = {
        "objects": [
            {"name": f"Object{index:04d}", "label": "x" * 200}
            for index in range(1000)
        ]
    }
    captured = {
        "scope": "document",
        "target": "",
        "path": "/objects",
        "offset": 100,
        "limit": 50,
        "surface": {"workbench": "PartWorkbench", "engine": "native"},
        "document": {"name": "Large", "uid": "doc", "object_count": 1000},
    }

    first = _bounded_page(raw, captured)
    second = _bounded_page(raw, captured)

    assert first == second
    assert first["page"]["offset"] == 100
    assert first["page"]["returned"] == 50
    assert first["page"]["next_offset"] == 150
    assert first["result_json_bytes"] == _encoded_bytes(first)
    assert first["result_json_bytes"] <= MAX_INSPECT_RESULT_BYTES


def test_core_inspect_shrinks_large_string_pages_below_the_hard_limit() -> None:
    captured = {
        "scope": "program",
        "target": "program-id",
        "path": "/source",
        "offset": 0,
        "limit": 50,
        "surface": {"workbench": "PartWorkbench", "engine": "vibescript"},
        "document": {"name": "Large", "uid": "doc", "object_count": 1},
    }

    result = _bounded_page({"source": "x" * 100000}, captured)

    assert result["page"]["requested_limit"] == 50
    assert result["page"]["effective_limit"] < 50
    assert result["page"]["next_offset"] == len(result["value"])
    assert result["result_json_bytes"] == _encoded_bytes(result)
    assert result["result_json_bytes"] <= MAX_INSPECT_RESULT_BYTES


def test_document_inspection_is_explicit_and_paged() -> None:
    objects = [
        SimpleNamespace(Name=f"Object{index:04d}", Label=f"Object {index}", TypeId="Part::Feature")
        for index in range(1000)
    ]

    class _Service:
        def active_workbench_name(self) -> str:
            return "PartWorkbench"

        def modeling_engine(self) -> str:
            return "native"

        def _active_document(self):
            return SimpleNamespace(Name="Large", Uid="doc", Objects=objects)

    captured = capture_inspection(
        _Service(),
        {"scope": "document", "path": "/objects", "offset": 500, "limit": 20},
    )
    result = complete_inspection(captured)

    assert result["ok"] is True
    assert result["page"]["total"] == 1000
    assert result["page"]["returned"] == 20
    assert result["value"][0]["name"] == "Object0500"
    assert result["result_json_bytes"] <= MAX_INSPECT_RESULT_BYTES


def test_document_inspection_captures_only_the_requested_object_page() -> None:
    accessed: list[int] = []

    class _Objects:
        def __len__(self) -> int:
            return 1000

        def __getitem__(self, index: int):
            accessed.append(index)
            return SimpleNamespace(
                Name=f"Object{index:04d}",
                Label=f"Object {index}",
                TypeId="Part::Feature",
            )

        def __iter__(self):
            raise AssertionError("inspection capture must not enumerate every object")

    class _Service:
        def active_workbench_name(self) -> str:
            return "PartWorkbench"

        def modeling_engine(self) -> str:
            return "native"

        def _active_document(self):
            return SimpleNamespace(Name="Large", Uid="doc", Objects=_Objects())

    captured = capture_inspection(
        _Service(),
        {"scope": "document", "path": "/objects", "offset": 500, "limit": 20},
    )
    result = complete_inspection(captured)

    assert accessed == list(range(500, 520))
    assert result["page"]["total"] == 1000
    assert result["page"]["next_offset"] == 520


def test_reference_attachments_are_queued_and_consumed_by_exact_id() -> None:
    service = object.__new__(VibeCADService)
    service._reference_cache_document_uid = "doc"
    service._active_document_uid = lambda: "doc"
    service._pending_reference_image_ids = ["new-a", "new-b"]
    service._reference_images = [
        {"id": "old", "name": "old.png", "path": "/refs/old.png"},
        {"id": "new-a", "name": "a.png", "path": "/refs/a.png"},
        {"id": "new-b", "name": "b.png", "path": "/refs/b.png"},
    ]

    pending = service.pending_reference_image_attachments()
    consumed = service.consume_reference_image_attachments(
        {"images": [{"id": "new-a"}]}
    )

    assert [item["id"] for item in pending["images"]] == ["new-a", "new-b"]
    assert consumed == {"consumed": True, "ids": ["new-a"]}
    assert service._pending_reference_image_ids == ["new-b"]


def test_explicit_inspected_image_transport_metadata_is_never_model_text() -> None:
    result = {
        "ok": True,
        "value": {"attached": True},
        "_vibecad_image_attachment": {
            "path": "/project/references/exact.png",
            "name": "exact.png",
        },
    }

    visible = provider._provider_visible_tool_result(result)
    image_context = provider._tool_result_image_context(result)

    assert "_vibecad_image_attachment" not in visible
    assert image_context == {
        "reference_images": {
            "count": 1,
            "images": [
                {
                    "id": "explicit-inspection",
                    "name": "exact.png",
                    "path": "/project/references/exact.png",
                }
            ],
        }
    }


def test_oversized_tool_result_omits_whole_values_without_sampling() -> None:
    result = {
        "ok": True,
        "program_id": "program-1",
        "working_revision": "revision-1",
        "diagnostics": [{"index": index, "payload": "x" * 1000} for index in range(1000)],
        "_vibecad_image_attachment": {"path": "/private/image.png"},
    }

    visible = provider._provider_visible_tool_result(result)
    encoded_bytes = provider._provider_json_bytes(visible)

    assert encoded_bytes <= provider.MAX_PROVIDER_TOOL_RESULT_BYTES
    assert visible["ok"] is True
    assert visible["program_id"] == "program-1"
    assert visible["working_revision"] == "revision-1"
    assert visible["diagnostics"] == {
        "_vibecad_value_omitted": True,
        "reason": "provider_tool_result_byte_limit",
        "json_bytes": provider._provider_json_bytes(result["diagnostics"]),
        "value_type": "array",
        "item_count": 1000,
    }
    assert visible["vibecad_result_boundary"]["original_json_bytes"] > encoded_bytes
    assert "_vibecad_image_attachment" not in visible
    assert "payload" not in json.dumps(visible)
