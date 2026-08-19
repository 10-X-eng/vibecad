# SPDX-License-Identifier: LGPL-2.1-or-later

"""Model-facing context, inspection, and one-shot attachment contracts."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from VibeCADCore import (
    MAX_PROVIDER_INTENT_DISPOSITIONS,
    MAX_PROVIDER_RECEIPT_FIELD_CHARS,
    MAX_PROVIDER_RECEIPT_IDENTITIES,
    VibeCADService,
    provider_intent_disposition_list,
    provider_last_native_receipt,
)
from VibeCADProject import VibeCADConversationStore, _validated_conversation_turns
import VibeCADProvider as provider
import VibeCADSession as session


def _prompt_payload(
    prompt: str,
    context: dict,
    *,
    recent_conversation: list[dict] | None = None,
    current_user_message: str | None = None,
) -> tuple[dict, dict, str]:
    rendered = session._provider_prompt(
        prompt,
        context,
        recent_conversation=recent_conversation,
        current_user_message=current_user_message,
    )
    prefix = "VIBECAD_CONTEXT_JSON\n"
    marker = "\nEND_VIBECAD_CONTEXT_JSON\n\nRECENT_CONVERSATION_JSON\n"
    conversation_marker = "\nEND_RECENT_CONVERSATION_JSON\n\n"
    assert rendered.startswith(prefix)
    encoded, conversation_and_remainder = rendered[len(prefix) :].split(marker, 1)
    encoded_conversation, remainder = conversation_and_remainder.split(
        conversation_marker, 1
    )
    return json.loads(encoded), json.loads(encoded_conversation), remainder


def _active_state(object_count: int = 10) -> dict:
    return {
        "workbench": "AssemblyWorkbench",
        "modeling_surface": {
            "workbench": "AssemblyWorkbench",
            "engine": "vibescript",
            "domain": "assemblies",
            "surface_id": "vibecad/surface/assembly/vibescript",
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
        "provider_tool_schemas": [{"name": "core.set_view"}],
    }

    payload, conversation, remainder = _prompt_payload(current, context)

    assert set(payload) == {"active_state"}
    assert set(payload["active_state"]) == {
        "workbench",
        "modeling_surface",
        "document",
        "selection",
    }
    assert conversation == {
        "turns": [],
        "omitted_turn_count": 0,
        "truncated_turn_count": 0,
    }
    assert remainder == f"CURRENT_USER_MESSAGE\n{current}"
    assert current not in json.dumps(payload)
    serialized = json.dumps(payload)
    assert "must not leak" not in serialized
    for forbidden in ("cad_state", "working_set", "intent_memory", "tool_trace"):
        assert forbidden not in serialized


def test_first_prompt_context_json_includes_toplevel_aero() -> None:
    """VIBECAD_CONTEXT_JSON is the first-prompt path, not steering.

    ``_provider_state_payload`` is the last allowlist before that JSON is
    serialized. Aero must sit next to document/selection, not inside
    ``provider_turn_document_summary``.
    """

    aero = {
        "available": True,
        "CL": 1.516,
        "CD": 0.242,
        "Cmalpha": 4.68,
        "PitchUnstable": True,
        "corrections": [
            "PitchUnstable: Cmα > 0. Increase decalage, add tail volume, "
            "or move CG forward until Cmα < 0."
        ],
    }
    context = {
        **_active_state(),
        "aero": aero,
        "human_steering": {"must": "not be the first-prompt path"},
    }

    state = session._provider_state_payload(context)
    payload, conversation, remainder = _prompt_payload("Continue.", context)

    assert "aero" in state
    assert state["aero"]["CL"] == 1.516
    assert state["aero"]["PitchUnstable"] is True
    assert "aero" not in (state.get("document") or {})
    assert set(payload) == {"active_state"}
    assert payload["active_state"]["aero"] == aero
    assert "aero" not in (payload["active_state"].get("document") or {})
    assert set(payload["active_state"]) == {
        "workbench",
        "modeling_surface",
        "document",
        "selection",
        "aero",
    }
    assert conversation["turns"] == []
    assert remainder == "CURRENT_USER_MESSAGE\nContinue."
    assert "human_steering" not in json.dumps(payload)


def _constraint_memory() -> dict:
    return {
        "schema": "vibecad-intent-memory-v1",
        "revision": "rev-must-not-leak",
        "entries": [
            {
                "id": "keep-halves",
                "category": "constraint",
                "statement": "Keep the two-handle-half architecture.",
                "authority": "user_explicit",
                "source_turn_ids": ["a" * 32],
                "status": "active",
            },
            {
                "id": "hole-note",
                "category": "requirement",
                "statement": "Mounting hole is 6 mm.",
                "authority": "user_confirmed",
                "source_turn_ids": ["b" * 32],
                "status": "active",
            },
            {
                "id": "old-single-piece",
                "category": "constraint",
                "statement": "Use a single-piece handle.",
                "authority": "model_assumption",
                "source_turn_ids": ["c" * 32],
                "status": "superseded",
            },
        ],
    }


def test_turn_start_context_includes_intent_dispositions() -> None:
    """Constraints stay visible as a short {text, status} list, not raw memory."""

    memory = _constraint_memory()
    dispositions = provider_intent_disposition_list(memory)
    assert dispositions == [
        {"text": "Keep the two-handle-half architecture.", "status": "active"},
        {"text": "Mounting hole is 6 mm.", "status": "active"},
        {"text": "Use a single-piece handle.", "status": "superseded"},
    ]
    assert provider_intent_disposition_list({"entries": []}) == []
    assert provider_intent_disposition_list(None) == []

    oversized = {
        "entries": [
            {
                "statement": f"constraint-{index} " + ("x" * 400),
                "status": "active",
            }
            for index in range(MAX_PROVIDER_INTENT_DISPOSITIONS + 8)
        ]
    }
    bounded = provider_intent_disposition_list(oversized)
    assert len(bounded) == MAX_PROVIDER_INTENT_DISPOSITIONS + 8
    assert all(set(item) == {"text", "status"} for item in bounded)
    assert all(len(item["text"]) < 400 for item in bounded)

    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service.modeling_engine = lambda: "vibescript"
    service.provider_turn_document_summary = lambda: _active_state()["document"]
    service.provider_turn_selection_summary = lambda: _active_state()["selection"]
    service.view_screenshot_summary = lambda: {"captured": False}
    service.provider_reference_image_attachments = lambda: {
        "count": 0,
        "images": [],
    }
    service.aero_summary = lambda: {"available": False}
    service.intent_memory = lambda: memory

    context = service.provider_context_summary()
    assert context["intent"] == dispositions
    assert "rev-must-not-leak" not in json.dumps(context["intent"])
    assert "source_turn_ids" not in json.dumps(context["intent"])

    service.intent_memory = lambda: {"entries": []}
    assert service.provider_context_summary()["intent"] == []

    aero = {"available": True, "CL": 1.516}
    references = {"count": 1, "images": [{"id": "ref-1", "name": "ref.png"}]}
    payload_context = {
        **_active_state(),
        "intent": dispositions,
        "aero": aero,
        "reference_images": references,
        "intent_memory": {"must": "not leak"},
    }
    state = session._provider_state_payload(payload_context)
    assert state["intent"] == dispositions
    assert state["aero"] == aero
    assert "intent_memory" not in state
    payload, _conversation, _remainder = _prompt_payload("Continue.", payload_context)
    assert payload["active_state"]["intent"] == dispositions
    assert payload["active_state"]["aero"] == aero
    serialized = json.dumps(payload)
    assert "must not leak" not in serialized
    assert "intent_memory" not in serialized
    assert "rev-must-not-leak" not in serialized

    empty_intent_context = {**_active_state(), "intent": []}
    empty_state = session._provider_state_payload(empty_intent_context)
    assert empty_state["intent"] == []
    empty_payload, _conversation, _remainder = _prompt_payload(
        "Continue.", empty_intent_context
    )
    assert empty_payload["active_state"]["intent"] == []


def test_active_user_explicit_wall_survives_superseded_truncation() -> None:
    """A live '2 mm wall' stays in the turn-start list when superseded fill it."""

    wall = {
        "id": "wall-thickness",
        "category": "constraint",
        "statement": "2 mm wall",
        "authority": "user_explicit",
        "source_turn_ids": ["d" * 32],
        "status": "active",
    }
    memory = {
        "entries": [
            {
                "id": f"old-{index}",
                "category": "constraint",
                "statement": f"obsolete constraint {index}",
                "authority": "model_assumption",
                "status": "superseded",
            }
            for index in range(20)
        ]
        + [wall]
    }

    dispositions = provider_intent_disposition_list(memory)
    assert {"text": "2 mm wall", "status": "active"} in dispositions
    assert dispositions[0] == {"text": "2 mm wall", "status": "active"}
    assert len(dispositions) == MAX_PROVIDER_INTENT_DISPOSITIONS
    assert all(
        item["status"] == "superseded" for item in dispositions[1:]
    )

    crowded = {
        "entries": [
            {
                "id": f"other-active-{index}",
                "category": "constraint",
                "statement": f"other active {index}",
                "authority": "model_assumption",
                "status": "active",
            }
            for index in range(MAX_PROVIDER_INTENT_DISPOSITIONS)
        ]
        + [wall]
    }
    crowded_list = provider_intent_disposition_list(crowded)
    assert {"text": "2 mm wall", "status": "active"} in crowded_list
    assert crowded_list[0] == {"text": "2 mm wall", "status": "active"}

    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service.modeling_engine = lambda: "vibescript"
    service.provider_turn_document_summary = lambda: _active_state()["document"]
    service.provider_turn_selection_summary = lambda: _active_state()["selection"]
    service.view_screenshot_summary = lambda: {"captured": False}
    service.provider_reference_image_attachments = lambda: {
        "count": 0,
        "images": [],
    }
    service.aero_summary = lambda: {"available": False}
    service.intent_memory = lambda: memory

    context = service.provider_context_summary()
    assert {"text": "2 mm wall", "status": "active"} in context["intent"]
    assert context["intent"][0]["text"] == "2 mm wall"
    visible = provider._model_visible_context(context)
    assert visible["intent"][0]["text"] == "2 mm wall"


def test_all_active_constraints_survive_the_turn_cap() -> None:
    memory = {
        "entries": [
            {
                "id": f"active-{index}",
                "statement": f"active constraint {index}",
                "status": "active",
                "authority": "user_confirmed",
            }
            for index in range(MAX_PROVIDER_INTENT_DISPOSITIONS + 5)
        ]
    }
    dispositions = provider_intent_disposition_list(memory)
    assert len(dispositions) == MAX_PROVIDER_INTENT_DISPOSITIONS + 5
    assert all(item["status"] == "active" for item in dispositions)


def test_intent_store_failure_does_not_look_like_no_constraints() -> None:
    service = object.__new__(VibeCADService)

    def _raise() -> dict:
        raise RuntimeError("intent store down")

    service.intent_memory = _raise
    dispositions = service.provider_intent_dispositions()
    assert dispositions
    assert dispositions[0]["status"] == "evidence_waiting"
    assert "do not assume constraints were dropped" in dispositions[0]["text"]
    state = session._provider_state_payload({"intent": dispositions})
    assert state["intent"] == dispositions


def test_turn_context_screenshot_is_presentation_only_not_measured() -> None:
    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service.modeling_engine = lambda: "vibescript"
    service.provider_turn_document_summary = lambda: _active_state()["document"]
    service.provider_turn_selection_summary = lambda: _active_state()["selection"]
    service._last_view_screenshot = {
        "captured": True,
        "path": "/project/screenshots/current.png",
        "pending_attachment": True,
    }
    service.provider_reference_image_attachments = lambda: {
        "count": 0,
        "images": [],
    }
    service.aero_summary = lambda: {"available": False}
    service.intent_memory = lambda: _constraint_memory()

    context = service.provider_context_summary()
    screenshot = context["view_screenshot"]
    assert screenshot["captured"] is True
    assert screenshot["path"] == "/project/screenshots/current.png"
    assert screenshot["presentation_only"] is True
    assert screenshot["artifact_class"] == "presentation"
    assert screenshot["claim_ceiling"] == "not_measured"
    assert context["intent"] == [
        {"text": "Keep the two-handle-half architecture.", "status": "active"},
        {"text": "Mounting hole is 6 mm.", "status": "active"},
        {"text": "Use a single-piece handle.", "status": "superseded"},
    ]

    visible = provider._model_visible_context(context)
    assert visible["view_screenshot"]["presentation_only"] is True
    assert visible["view_screenshot"]["claim_ceiling"] == "not_measured"
    assert visible["intent"] == context["intent"]


def test_session_capture_keeps_intent_with_reference_images_and_aero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispositions = [
        {"text": "Keep the two-handle-half architecture.", "status": "active"},
    ]
    aero = {"available": True, "CL": 0.77}
    references = {"count": 1, "images": [{"id": "ref-1", "name": "ref.png"}]}

    class _Service:
        def provider_context_summary(self):
            return {
                "document": {"name": "Mechanism", "uid": "doc-1", "object_count": 2},
                "selection": {"selection_count": 0, "selection": []},
                "view_screenshot": {"captured": False},
                "reference_images": references,
                "aero": aero,
                "intent": dispositions,
                "cad_state": {"must": "not leak"},
                "intent_memory": {"must": "not leak"},
            }

        def active_workbench_name(self):
            return "PartWorkbench"

        def modeling_engine(self):
            return "vibescript"

        def provider_debug_config(self):
            return {"enabled": False}

        def provider_name(self):
            return "grok"

    monkeypatch.setattr(session, "provider_tool_schemas", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        session,
        "_capture_editable_sources_for_workbench",
        lambda *_args, **_kwargs: {"sources": []},
    )

    context = session._capture_context_for_provider(_Service())
    assert context["intent"] == dispositions
    assert context["aero"] == aero
    assert context["reference_images"] == references
    assert "cad_state" not in context
    assert "intent_memory" not in context

    state = session._provider_state_payload(context)
    assert state["intent"] == dispositions
    assert state["aero"] == aero


def _receipt_summary(
    capability: str,
    *,
    revision_before: int = 0,
    revision_after: int = 1,
    created: list[dict[str, str]] | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "capability": capability,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "claim_ceiling": "geometry_applied",
        "evidence_state": "pass",
        "created": list(created or []),
        "changed": [],
        "deleted": [],
        "replaced": [],
    }
    if extra:
        payload.update(extra)
    return payload


def test_provider_last_native_receipt_is_the_bounded_ceiling_summary() -> None:
    """Turn-start Grok only gets the last receipt, with honesty fields kept."""

    created = [
        {
            "document_uid": "doc-1",
            "object_name": f"Pad{index}",
            "type_id": "PartDesign::Pad",
            "secret": "must not leak",
        }
        for index in range(MAX_PROVIDER_RECEIPT_IDENTITIES + 4)
    ]
    oversized = "x" * (MAX_PROVIDER_RECEIPT_FIELD_CHARS + 40)
    last = _receipt_summary(
        oversized,
        revision_before=4,
        revision_after=5,
        created=created,
        extra={"verified_result": {"ok": True, "blob": "must not leak"}},
    )
    last.pop("claim_ceiling")
    last.pop("evidence_state")
    native_state = {
        "document_uid": "doc-1",
        "structural_revision": 5,
        "recent_receipts": [
            _receipt_summary("model.sketch", revision_after=1),
            last,
        ],
    }

    bounded = provider_last_native_receipt(native_state)

    assert bounded is not None
    assert bounded["capability"] == oversized[:MAX_PROVIDER_RECEIPT_FIELD_CHARS]
    assert bounded["revision_before"] == 4
    assert bounded["revision_after"] == 5
    assert bounded["claim_ceiling"] == "geometry_applied"
    assert bounded["evidence_state"] == "pass"
    assert len(bounded["created"]) == MAX_PROVIDER_RECEIPT_IDENTITIES
    assert bounded["created"][0]["object_name"] == "Pad0"
    assert "secret" not in json.dumps(bounded)
    assert "verified_result" not in bounded
    assert "must not leak" not in json.dumps(bounded)
    assert provider_last_native_receipt({"recent_receipts": []}) is None
    assert provider_last_native_receipt(None) is None


def test_turn_start_native_state_includes_last_receipt_ceiling() -> None:
    """VIBECAD_CONTEXT_JSON must carry the last Native receipt ceiling."""

    last_receipt = provider_last_native_receipt(
        {
            "recent_receipts": [
                _receipt_summary("model.sketch"),
                _receipt_summary(
                    "model.pad",
                    revision_before=1,
                    revision_after=2,
                    created=[
                        {
                            "document_uid": "doc-1",
                            "object_name": "Pad",
                            "type_id": "PartDesign::Pad",
                        }
                    ],
                ),
            ]
        }
    )
    dispositions = [{"text": "2 mm wall", "status": "active"}]
    aero = {"available": False, "claim_ceiling": "not_airworthy"}
    context = {
        "workbench": "PartDesignWorkbench",
        "modeling_surface": {
            "workbench": "PartDesignWorkbench",
            "engine": "native",
            "domain": "model",
            "surface_id": "vibecad/surface/native/model",
            "available": True,
        },
        "native_state": {
            "surface_id": "model",
            "structural_revision": 2,
            "last_receipt": last_receipt,
        },
        "intent": dispositions,
        "aero": aero,
        "cad_state": {"must": "not leak"},
        "intent_memory": {"must": "not leak"},
    }

    state = session._provider_state_payload(context)
    assert state["native_state"]["last_receipt"]["claim_ceiling"] == "geometry_applied"
    assert state["native_state"]["last_receipt"]["evidence_state"] == "pass"
    assert state["native_state"]["last_receipt"]["capability"] == "model.pad"
    assert state["intent"] == dispositions
    assert state["aero"] == aero
    assert "intent_memory" not in state

    payload, _conversation, remainder = _prompt_payload("Continue.", context)
    active = payload["active_state"]
    assert active["native_state"]["last_receipt"]["claim_ceiling"] == "geometry_applied"
    assert active["native_state"]["last_receipt"]["evidence_state"] == "pass"
    assert active["intent"] == dispositions
    assert active["aero"] == aero
    serialized = json.dumps(payload)
    assert "must not leak" not in serialized
    assert remainder == "CURRENT_USER_MESSAGE\nContinue."


def test_native_active_snapshot_attaches_last_receipt_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADNativeSnapshot as snapshot_mod
    import VibeCADRibbonSurface as ribbon_mod

    last = _receipt_summary(
        "model.pad",
        revision_before=1,
        revision_after=2,
        created=[
            {
                "document_uid": "doc-1",
                "object_name": "Pad",
                "type_id": "PartDesign::Pad",
            }
        ],
        extra={"verified_result": {"must": "not leak"}},
    )
    native_state = {
        "document_uid": "doc-1",
        "structural_revision": 2,
        "recent_receipts": [_receipt_summary("model.sketch"), last],
    }

    monkeypatch.setattr(
        ribbon_mod,
        "read_active_ribbon_surface",
        lambda: SimpleNamespace(surface_id="model"),
    )
    monkeypatch.setattr(
        snapshot_mod,
        "build_active_snapshot",
        lambda _document, surface_id, state, **_kwargs: {
            "surface_id": surface_id,
            "structural_revision": state.get("structural_revision"),
            "domain": {"kind": "model"},
            "working_set": [],
        },
    )

    service = object.__new__(VibeCADService)
    service.modeling_engine = lambda: "native"
    service._active_document = lambda: SimpleNamespace(Uid="doc-1", Name="Doc")
    service.native_document_state = lambda: native_state

    snapshot = service.native_active_snapshot()
    assert snapshot["last_receipt"]["capability"] == "model.pad"
    assert snapshot["last_receipt"]["claim_ceiling"] == "geometry_applied"
    assert snapshot["last_receipt"]["evidence_state"] == "pass"
    assert "verified_result" not in snapshot["last_receipt"]
    assert "must not leak" not in json.dumps(snapshot["last_receipt"])

    service.native_document_state = lambda: {
        "document_uid": "doc-1",
        "structural_revision": 0,
        "recent_receipts": [],
    }
    assert "last_receipt" not in service.native_active_snapshot()


def test_session_payload_keeps_native_last_receipt_with_intent() -> None:
    last_receipt = provider_last_native_receipt(
        {
            "recent_receipts": [
                _receipt_summary(
                    "model.pad",
                    revision_before=1,
                    revision_after=2,
                    created=[
                        {
                            "document_uid": "doc-1",
                            "object_name": "Pad",
                            "type_id": "PartDesign::Pad",
                        }
                    ],
                )
            ]
        }
    )
    dispositions = [{"text": "2 mm wall", "status": "active"}]
    aero = {"available": True, "CL": 0.77}
    references = {"count": 1, "images": [{"id": "ref-1", "name": "ref.png"}]}
    context = {
        **_active_state(),
        "intent": dispositions,
        "aero": aero,
        "reference_images": references,
        "native_state": {
            "surface_id": "model",
            "structural_revision": 2,
            "last_receipt": last_receipt,
        },
        "cad_state": {"must": "not leak"},
    }
    state = session._provider_state_payload(context)
    assert state["native_state"]["last_receipt"]["claim_ceiling"] == "geometry_applied"
    assert state["intent"] == dispositions
    assert state["aero"] == aero
    assert "cad_state" not in state
    payload, _conversation, _remainder = _prompt_payload("Continue.", context)
    assert payload["active_state"]["native_state"]["last_receipt"][
        "claim_ceiling"
    ] == "geometry_applied"
    assert payload["active_state"]["intent"] == dispositions


def test_turn_history_is_supplied_separately_from_model_state_packet() -> None:
    prior_user = "What hole diameter did I specify?"
    prior_assistant = "You specified a 6 mm through-hole."
    current = "What was the last question I asked?"
    turns = [
        {"role": "user", "content": "obsolete question"},
        {"role": "assistant", "content": "obsolete answer"},
        {"role": "user", "content": prior_user},
        {"role": "assistant", "content": prior_assistant},
        {"role": "system", "content": "internal status must not become dialogue"},
        {"role": "user", "content": current},
    ]
    context = {
        **_active_state(),
        "conversation": {"conversation": turns},
    }

    payload, conversation, remainder = _prompt_payload(
        current,
        context,
        recent_conversation=turns,
        current_user_message=current,
    )
    assert payload == {"active_state": _active_state()}
    assert conversation["turns"] == [
        {"role": "user", "content": "obsolete question"},
        {"role": "assistant", "content": "obsolete answer"},
        {"role": "user", "content": prior_user},
        {"role": "assistant", "content": prior_assistant},
    ]
    assert conversation["omitted_turn_count"] == 0
    assert conversation["truncated_turn_count"] == 0
    assert current not in json.dumps(conversation)
    assert "internal status" not in json.dumps(conversation)
    assert remainder == f"CURRENT_USER_MESSAGE\n{current}"
    assert prior_user not in json.dumps(payload)


def test_recent_conversation_window_keeps_newest_turns_within_hard_limits() -> None:
    turns = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index:02d} " + ("é" * 10000),
            "tool_trace": [{"must": "not leak"}],
        }
        for index in range(40)
    ]

    payload = session._recent_conversation_payload(turns)
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()

    assert len(encoded) <= session.MAX_RECENT_CONVERSATION_JSON_BYTES
    assert 0 < len(payload["turns"]) <= session.MAX_RECENT_CONVERSATION_TURNS
    assert payload["turns"][-1]["content"].startswith("turn-39")
    assert all(set(turn) == {"role", "content"} for turn in payload["turns"])
    assert payload["omitted_turn_count"] == len(turns) - len(payload["turns"])
    assert payload["truncated_turn_count"] > 0
    assert b"must not leak" not in encoded


def test_stop_button_control_is_not_reinjected_as_a_design_instruction() -> None:
    turns = [
        {"role": "user", "content": "Move only the lock pin."},
        {"role": "assistant", "content": "Working on the lock pin."},
        {
            "role": "user",
            "content": "Stop.",
            "metadata": {"source": "stop"},
        },
        {
            "role": "user",
            "content": "Keep the two-handle-half architecture and continue.",
        },
        {
            "role": "assistant",
            "content": "The two exterior handle halves remain unchanged.",
        },
    ]

    payload = session._recent_conversation_payload(turns)

    assert [turn["content"] for turn in payload["turns"]] == [
        "Move only the lock pin.",
        "Working on the lock pin.",
        "Keep the two-handle-half architecture and continue.",
        "The two exterior handle halves remain unchanged.",
    ]
    assert payload["omitted_turn_count"] == 0

    literal_user_request = session._recent_conversation_payload(
        [{"role": "user", "content": "Stop."}]
    )
    assert literal_user_request["turns"] == [{"role": "user", "content": "Stop."}]


def test_recent_conversation_keeps_aero_analyze_as_assistant() -> None:
    report = (
        "Aero Analyze (AeroBuildup)\n"
        "CL=1.516  CD=0.242  CM=0.733\n"
        "CLα=7.3  Cmα=4.68  PITCH UNSTABLE (Cmα > 0)\n"
        "Corrections:\n"
        "- PitchUnstable: Cmα > 0. Increase decalage, add tail volume, "
        "or move CG forward until Cmα < 0."
    )
    turns = [
        {"role": "user", "content": "Analyze the voider."},
        {
            "role": "assistant",
            "content": report,
            "metadata": {"source": "aero"},
        },
        {
            "role": "system",
            "content": "internal status must not become dialogue",
        },
    ]

    payload = session._recent_conversation_payload(turns)

    assert payload["turns"] == [
        {"role": "user", "content": "Analyze the voider."},
        {"role": "assistant", "content": report},
    ]
    assert "PITCH UNSTABLE" in payload["turns"][-1]["content"]
    assert "internal status" not in json.dumps(payload)


@pytest.mark.parametrize("object_count", (10, 100, 1000))
def test_turn_context_size_does_not_scale_with_document_objects(object_count: int) -> None:
    payload, _conversation, _ = _prompt_payload(
        "Continue.", _active_state(object_count)
    )
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


def test_selected_object_includes_copy_ready_geometry_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_object = SimpleNamespace(
        Name="ImportedMotor",
        Label="Imported STEP Motor",
        TypeId="Part::Feature",
    )
    selection = SimpleNamespace(
        Object=selected_object,
        SubElementNames=("Face2",),
    )
    gui = ModuleType("FreeCADGui")
    gui.Selection = SimpleNamespace(getSelectionEx=lambda: [selection])
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    service = object.__new__(VibeCADService)
    service._active_document = lambda: SimpleNamespace(Uid="document-uid")

    assert service.provider_turn_selection_summary() == {
        "selection_count": 1,
        "selection": [
            {
                "object": "ImportedMotor",
                "label": "Imported STEP Motor",
                "type": "Part::Feature",
                "reference": {
                    "document_uid": "document-uid",
                    "object_name": "ImportedMotor",
                },
                "subelements": ["Face2"],
            }
        ],
    }


def test_provider_context_does_not_copy_conversation_cache() -> None:
    service = object.__new__(VibeCADService)
    service.active_workbench_name = lambda: "AssemblyWorkbench"
    service.modeling_engine = lambda: "vibescript"
    service.provider_turn_document_summary = lambda: _active_state()["document"]
    service.provider_turn_selection_summary = lambda: _active_state()["selection"]
    service.view_screenshot_summary = lambda: {"captured": False}
    service.provider_reference_image_attachments = lambda: {
        "count": 0,
        "images": [],
    }
    service.aero_summary = lambda: {"available": False}
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


def test_session_conversation_history_read_stays_off_document_thread() -> None:
    in_document_callback = False
    events: list[str] = []

    class _Service:
        def prepare_conversation_history_read(self):
            assert in_document_callback is True
            events.append("capture")
            return {"conversation_id": "1" * 32}

        def complete_conversation_history_read(self, prepared):
            assert in_document_callback is False
            assert prepared["conversation_id"] == "1" * 32
            events.append("read")
            return {
                "conversation_id": "1" * 32,
                "conversation": [{"role": "user", "content": "Earlier request"}],
            }

        def accept_conversation_history_read(self, prepared, history):
            assert in_document_callback is True
            assert history["conversation_id"] == prepared["conversation_id"]
            events.append("accept")
            return {"accepted": True}

    def dispatch(operation):
        nonlocal in_document_callback
        assert in_document_callback is False
        in_document_callback = True
        try:
            return operation()
        finally:
            in_document_callback = False

    history = session._load_conversation_for_session(_Service(), dispatch)

    assert history["conversation"] == [
        {"role": "user", "content": "Earlier request"}
    ]
    assert events == ["capture", "read", "accept"]


def test_conversation_history_read_is_scoped_to_the_selected_thread(
    tmp_path,
) -> None:
    store = VibeCADConversationStore(tmp_path)
    first = store.active_history()
    first_id = first["conversation_id"]
    store.write_conversation(
        first_id,
        [{"role": "user", "content": "Question from the selected thread."}],
    )
    second = store.create_conversation()
    second_id = second["conversation_id"]
    store.write_conversation(
        second_id,
        [{"role": "user", "content": "Question from another thread."}],
    )
    store.activate_conversation(first_id)

    service = object.__new__(VibeCADService)
    service._conversation_cache = []
    service._conversation_cache_key = None
    service._conversation_cache_document_uid = None
    service.project_scope_snapshot = lambda: {"root": str(tmp_path)}
    service._active_document_uid = lambda: "document-uid"

    prepared = service.prepare_conversation_history_read()
    history = service.complete_conversation_history_read(prepared)

    assert history["conversation_id"] == first_id
    assert [turn["content"] for turn in history["conversation"]] == [
        "Question from the selected thread."
    ]
    assert service.accept_conversation_history_read(prepared, history) == {
        "accepted": True,
        "conversation_id": first_id,
        "turn_count": 1,
    }

    stale_read = service.prepare_conversation_history_read()
    service._set_conversation_cache(store.activate_conversation(second_id))
    stale_history = service.complete_conversation_history_read(stale_read)

    assert service.accept_conversation_history_read(
        stale_read, stale_history
    ) == {
        "accepted": False,
        "reason": "active_conversation_changed",
    }


def test_unsaved_run_prompt_reaches_provider_and_includes_active_thread_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = "What was the last question I asked?"
    active_history = [
        {"role": "user", "content": "Make the mounting hole 6 mm."},
        {"role": "assistant", "content": "The mounting hole is now 6 mm."},
        {"role": "user", "content": "What diameter is the mounting hole?"},
        {"role": "assistant", "content": "It is 6 mm."},
    ]

    class _Service:
        def __init__(self):
            self.conversation = [dict(item) for item in active_history]
            self.other_thread_text = "This belongs to a different conversation."
            self.persisted_conversation_ids: list[str | None] = []

        def assistant_document_state(self):
            return {
                "enabled": True,
                "turn_enabled": True,
                "saved": False,
            }

        def active_workbench_name(self):
            return "AssemblyWorkbench"

        def prepare_conversation_turn(
            self,
            role,
            content,
            *,
            provider=None,
            metadata=None,
            conversation_id=None,
        ):
            entry = {"role": role, "content": content}
            if provider:
                entry["provider"] = provider
            if metadata:
                entry["metadata"] = dict(metadata)
            return {
                "conversation_id": conversation_id,
                "entry": entry,
            }

        def persist_prepared_conversation_turn(self, prepared):
            requested = prepared.get("conversation_id")
            self.persisted_conversation_ids.append(requested)
            self.conversation.append(dict(prepared["entry"]))
            return {
                "path": "/active/conversation.json",
                "conversation_id": "a" * 32,
                "conversation": [dict(item) for item in self.conversation],
                "written": True,
            }

        def accept_persisted_conversation_turn(self, history, prepared):
            return {"accepted": True}

    service = _Service()
    active_provider = provider.CodexProvider(
        model="gpt-test",
        auth_mode="chatgpt",
        reasoning_effort="xhigh",
    )
    active_provider.prompt = ""

    def fake_run(prompt, context, **kwargs):
        active_provider.prompt = prompt
        return provider.ProviderResult("I can see the prior question.")

    monkeypatch.setattr(active_provider, "run", fake_run)
    context = {
        **_active_state(),
        "provider_tool_schemas": [],
    }
    monkeypatch.setattr(
        session,
        "_build_context_for_provider",
        lambda active_service, trigger, interaction_mode, dispatch: dict(context),
    )
    monkeypatch.setattr(
        session,
        "make_provider_tool_runner",
        lambda *args, **kwargs: lambda *tool_args: {"ok": True},
    )

    progress_events: list[dict] = []
    response = session.run_prompt(
        current,
        service=service,
        provider=active_provider,
        prefer_online=False,
        progress_callback=lambda event: progress_events.append(dict(event)),
    )

    assert response.error is None
    assert active_provider.prompt.count(current) == 1
    assert "What diameter is the mounting hole?" in active_provider.prompt
    assert "It is 6 mm." in active_provider.prompt
    assert service.other_thread_text not in active_provider.prompt
    assert active_provider.prompt.endswith(f"CURRENT_USER_MESSAGE\n{current}")
    assert service.persisted_conversation_ids == [None, "a" * 32]
    assert service.conversation[-1] == {
        "role": "assistant",
        "content": "I can see the prior question.",
        "provider": "CodexProvider",
        "metadata": {
            "provider_runtime": {
                "provider_id": "chatgpt",
                "provider_label": "ChatGPT subscription via Codex",
                "adapter": "CodexProvider",
                "requested_model": "gpt-test",
                "model_selection": "explicit",
                "reasoning_effort": "xhigh",
                "model_fallback_allowed": False,
                "interaction_mode": "build",
            }
        },
    }
    started = next(
        event
        for event in progress_events
        if event.get("event") == "provider_turn_started"
    )
    assert started["provider_runtime"] == service.conversation[-1]["metadata"][
        "provider_runtime"
    ]


def test_turn_start_rejects_oversized_exact_tool_schemas() -> None:
    schema = {
        "name": "vibescript.read_api",
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
            engine="vibescript",
        )


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
        "program": "Design/partdesign/Program One",
        "working_revision": "revision-1",
        "diagnostics": [{"index": index, "payload": "x" * 1000} for index in range(1000)],
        "_vibecad_image_attachment": {"path": "/private/image.png"},
    }

    visible = provider._provider_visible_tool_result(result)
    encoded_bytes = provider._provider_json_bytes(visible)

    assert encoded_bytes <= provider.MAX_PROVIDER_TOOL_RESULT_BYTES
    assert visible["ok"] is True
    assert visible["program"] == "Design/partdesign/Program One"
    assert "program_id" not in visible
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


def test_terminal_source_operation_never_omits_its_verdict_or_recovery() -> None:
    result = {
        "ok": True,
        "operation": {
            "operation_id": "operation-9",
            "status": "failed",
            "tool": "vibescript.edit_source",
        },
        "operation_succeeded": False,
        "result": {
            "ok": False,
            "failure_code": "DOMAIN_CPU_LIMIT_EXCEEDED",
            "failure_stage": "external_process",
            "error": "The native solve exhausted its CPU limit.",
            "program": "Clamp/assembly/Mechanism",
            "working_revision": "a" * 64,
            "next_write_expected_revision": "a" * 64,
            "observed": {
                "stdout": "noise\n" * 30_000,
                "stderr": "solver iteration\n" * 30_000,
                "termination_reason": "cpu_time_limit",
                "limit_reached": "cpu_seconds",
                "worker_progress": {
                    "domain": "assembly",
                    "phase": "simulation_native_solve",
                    "phase_elapsed_seconds": 238.4,
                    "item_progress": {
                        "kind": "joint",
                        "completed": 8,
                        "total": 12,
                        "current": "HandlePivot",
                    },
                    "graph_timings": [{"payload": "x" * 1000}] * 200,
                },
            },
            "required_changes": [
                "Correct HandlePivot and rebuild against the returned revision."
            ],
            "_vibecad_source_lifecycle_result": True,
        },
    }

    visible = provider._provider_visible_tool_result(result)

    assert provider._provider_json_bytes(visible) <= (
        provider.MAX_PROVIDER_TOOL_RESULT_BYTES
    )
    assert visible["operation_succeeded"] is False
    terminal = visible["result"]
    assert terminal["failure_code"] == "DOMAIN_CPU_LIMIT_EXCEEDED"
    assert terminal["failure_stage"] == "external_process"
    assert terminal["revision"] == "a" * 64
    assert terminal["observed"]["termination_reason"] == "cpu_time_limit"
    assert terminal["observed"]["worker_progress"]["phase"] == (
        "simulation_native_solve"
    )
    assert "stdout" not in json.dumps(visible)
    assert "_vibecad_value_omitted" not in json.dumps(terminal)


def test_pathological_terminal_error_keeps_stable_verdict_envelope() -> None:
    result = {
        "ok": True,
        "operation": {
            "operation_id": "operation-10",
            "status": "failed",
            "tool": "vibescript.edit_source",
        },
        "operation_succeeded": False,
        "result": {
            "ok": False,
            "failure_code": "NATIVE_SOLVER_FAILED",
            "failure_stage": "native_solve",
            "error": "native solver detail\n" * 100_000,
            "program": "Clamp/assembly/Mechanism",
            "revision": "b" * 64,
            "next_actions": [
                {
                    "tool": "vibescript.read_source",
                    "arguments": {
                        "program": "Clamp/assembly/Mechanism",
                        "include_logs": False,
                    },
                }
            ],
        },
    }

    visible = provider._provider_visible_tool_result(result)

    assert provider._provider_json_bytes(visible) <= (
        provider.MAX_PROVIDER_TOOL_RESULT_BYTES
    )
    assert visible["operation"] == {
        "status": "failed",
        "tool": "vibescript.edit_source",
    }
    assert visible["operation_succeeded"] is False
    terminal = visible["result"]
    assert terminal["ok"] is False
    assert terminal["failure_code"] == "NATIVE_SOLVER_FAILED"
    assert terminal["failure_stage"] == "native_solve"
    assert terminal["revision"] == "b" * 64
    assert terminal["next_actions"][0]["tool"] == "vibescript.read_source"
    assert terminal["error_boundary"]["utf8_bytes"] > 2048
    assert "_vibecad_value_omitted" not in json.dumps(terminal)


def test_concise_source_read_states_current_revision_directly() -> None:
    result = {
        "ok": True,
        "program": "Audit/partdesign/Part",
        "current_revision": "c" * 64,
        "accepted_revision": "c" * 64,
        "source": "result = {}\n",
        "source_range": {
            "line_start": 1,
            "line_end": 1,
            "total_lines": 1,
            "complete": True,
        },
        "model_state": {"status": "accepted_current"},
        "_vibecad_source_read_result": True,
    }

    visible = provider._provider_visible_tool_result(result)

    assert visible["program"] == "Audit/partdesign/Part"
    assert visible["revision"] == "c" * 64
    assert visible["state"] == {"status": "accepted_current"}


def test_successful_assembly_lifecycle_result_states_solver_scope() -> None:
    validation_scope = {
        "scope": "joint_constraint_consistency",
        "constraints_consistent": True,
        "mechanical_operation_verified": False,
        "advisory": "A solved joint graph does not prove proper mechanism operation.",
        "required_evidence": [
            "collision_and_clearance",
            "motion_over_operating_range",
        ],
    }
    result = {
        "ok": True,
        "program": "Audit/assembly/Mechanism",
        "working_revision": "d" * 64,
        "outputs": [
            {
                "name": "Diagnostics",
                "output_type": "solver_diagnostics",
            }
        ],
        "live_outputs": {
            "Diagnostics": {
                "label": "Diagnostics",
                "output_type": "solver_diagnostics",
                "assembly_data": {"validation_scope": validation_scope},
            }
        },
        "_vibecad_source_lifecycle_result": True,
    }

    visible = provider._provider_visible_tool_result(result)

    assert visible["validation_scope"] == validation_scope
    assert visible["outputs"][0]["validation_scope"] == validation_scope
    assert visible["validation_scope"]["mechanical_operation_verified"] is False
