# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibeCADEngineeringBrief import (
    add_active_conversation_context,
    EngineeringBriefStore,
    build_engineering_brief_prompt,
    engineering_brief_handoff,
    new_engineering_brief,
    parse_engineering_brief_result,
    render_engineering_brief,
    run_engineering_brief_turn,
    update_engineering_brief_draft,
)


def _identity() -> dict[str, str]:
    return {
        "project_root": "test-project-root",
        "document_uid": "document-uid",
        "conversation_id": "a" * 32,
    }


def _context() -> dict[str, object]:
    return {
        "workbench": "PartDesignWorkbench",
        "units": {"schema": 0, "label": "Standard (mm/kg/s/degree)"},
        "document": {
            "name": "Bracket",
            "uid": "document-uid",
            "object_count": 4,
        },
        "selection": {
            "selection_count": 1,
            "selection": [{"object": "Body", "label": "Mounting Bracket"}],
        },
    }


def _provider_payload(*, ready: bool = False) -> dict[str, object]:
    return {
        "assistant_message": (
            "I have enough information to prepare the brief."
            if ready
            else "What load must the bracket support?"
        ),
        "next_question": "" if ready else "What load must the bracket support?",
        "ready": ready,
        "brief": {
            "objective": "Create a wall-mounted equipment bracket.",
            "deliverables": ["Editable 3D model", "Manufacturing drawing"],
            "existing_geometry": ["Use the selected Mounting Bracket body"],
            "units": "mm, N, MPa",
            "dimensions": ["Fit within a 120 mm by 80 mm envelope"],
            "materials": ["6061-T6 aluminum"],
            "interfaces": ["Four M6 wall fasteners"],
            "loads": [] if not ready else ["1.5 kN vertical service load"],
            "manufacturing": ["3-axis CNC milling"],
            "tolerances": ["General tolerance +/-0.2 mm"],
            "analyses": ["Static FEA with factor of safety"],
            "acceptance_criteria": ["Factor of safety at least 2.0"],
            "requirements": ["Remain editable"],
            "preferences": ["Minimize mass"],
        },
        "assumptions": ["Room-temperature indoor service"],
        "open_questions": [] if ready else ["Required service load"],
    }


def test_new_brief_keeps_request_identity_and_safe_document_context() -> None:
    state = new_engineering_brief(
        "Make this bracket strong enough for a motor",
        identity=_identity(),
        context=_context(),
    )

    assert state["schema"] == "vibecad-engineering-brief-v1"
    assert state["original_request"] == "Make this bracket strong enough for a motor"
    assert state["document_uid"] == "document-uid"
    assert state["conversation_id"] == "a" * 32
    assert state["context"]["selection"]["selection"][0]["object"] == "Body"
    assert state["transcript"] == []
    assert state["ready"] is False


def test_provider_prompt_requests_one_question_and_forbids_cad_mutation() -> None:
    state = new_engineering_brief(
        "Make this bracket strong enough for a motor",
        identity=_identity(),
        context=_context(),
    )

    prompt = build_engineering_brief_prompt(state, "It carries a 1.5 kN motor")

    assert "exactly one highest-value question" in prompt
    assert "Do not call or request CAD tools" in prompt
    assert "ENGINEERING_BRIEF_STATE_JSON" in prompt
    assert "It carries a 1.5 kN motor" in prompt
    assert "PartDesignWorkbench" in prompt


def test_request_edits_update_an_unreviewed_brief_without_losing_human_edits() -> None:
    state = new_engineering_brief("Make a bracket", _identity(), _context())
    original_render = render_engineering_brief(state)

    updated = update_engineering_brief_draft(
        state,
        original_request="Make a lightweight motor bracket",
        editable_text=original_render,
    )
    human_edited = update_engineering_brief_draft(
        updated,
        original_request="Make a lightweight motor bracket for a wall",
        editable_text=render_engineering_brief(updated)
        + "\n\nHuman note: reuse M6 bolts.",
    )

    assert updated["brief"]["objective"] == "Make a lightweight motor bracket"
    assert "Make a lightweight motor bracket" in render_engineering_brief(updated)
    assert "Human note: reuse M6 bolts." in render_engineering_brief(human_edited)


def test_provider_result_accepts_fenced_json_and_updates_transcript() -> None:
    state = new_engineering_brief(
        "Make this bracket strong enough for a motor",
        identity=_identity(),
        context=_context(),
    )
    raw = "```json\n" + json.dumps(_provider_payload()) + "\n```"

    updated = parse_engineering_brief_result(
        raw,
        prior_state=state,
        user_response="Start with a 1.5 kN design load.",
    )

    assert updated["brief"]["objective"].startswith("Create a wall-mounted")
    assert updated["next_question"] == "What load must the bracket support?"
    assert updated["open_questions"] == ["Required service load"]
    assert updated["transcript"] == [
        {"role": "user", "content": "Start with a 1.5 kN design load."},
        {"role": "assistant", "content": "What load must the bracket support?"},
    ]


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"ready": False}, "assistant_message"),
        ({**_provider_payload(), "ready": "yes"}, "ready"),
        ({**_provider_payload(), "brief": []}, "brief"),
        ({**_provider_payload(), "assumptions": "none"}, "assumptions"),
    ],
)
def test_provider_result_rejects_ambiguous_contracts(
    payload: dict[str, object], error: str
) -> None:
    state = new_engineering_brief("Make a bracket", _identity(), _context())
    with pytest.raises(ValueError, match=error):
        parse_engineering_brief_result(
            json.dumps(payload),
            prior_state=state,
            user_response="",
        )


def test_turn_runner_exposes_no_cad_tools_and_uses_brief_instructions() -> None:
    requests: list[dict[str, object]] = []

    class Provider:
        def run(self, prompt, context, **kwargs):
            requests.append(
                {
                    "prompt": prompt,
                    "context": context,
                    "tool_runner": kwargs.get("tool_runner"),
                }
            )
            return SimpleNamespace(final_output=json.dumps(_provider_payload()))

    state = new_engineering_brief("Make a bracket", _identity(), _context())
    updated = run_engineering_brief_turn(
        state,
        user_response="",
        provider=Provider(),
    )

    assert updated["next_question"] == "What load must the bracket support?"
    assert requests[0]["tool_runner"] is None
    assert requests[0]["context"]["provider_tool_schemas"] == []
    assert requests[0]["context"]["_vibecad_toolless_task"] is True
    assert (
        "non-mutating Engineering Brief assistant"
        in requests[0]["context"]["_vibecad_task_instructions"]
    )


def test_active_conversation_context_is_available_to_the_brief_assistant() -> None:
    state = new_engineering_brief("Finish the robot", _identity(), _context())
    conversation = {
        "turns": [
            {"role": "user", "content": "The robot must fit through a doorway."},
            {
                "role": "assistant",
                "content": "I will keep the shoulder width below 800 mm.",
            },
        ],
        "omitted_turn_count": 3,
        "truncated_turn_count": 0,
    }

    updated = add_active_conversation_context(state, conversation)
    prompt = build_engineering_brief_prompt(updated, "")

    assert updated["context"]["active_conversation"] == conversation
    assert "The robot must fit through a doorway." in prompt
    assert "shoulder width below 800 mm" in prompt
    assert updated["conversation_id"] == state["conversation_id"]


def test_readable_brief_and_agent_handoff_preserve_edits_and_assumptions() -> None:
    state = new_engineering_brief("Make a bracket", _identity(), _context())
    state = parse_engineering_brief_result(
        json.dumps(_provider_payload(ready=True)),
        prior_state=state,
        user_response="Use a 1.5 kN service load.",
    )
    readable = render_engineering_brief(state)
    edited = readable.replace("Minimize mass", "Prefer simple machining")

    handoff = engineering_brief_handoff(state, approved_text=edited)

    assert "approved engineering brief" in handoff.lower()
    assert "Prefer simple machining" in handoff
    assert "Room-temperature indoor service" in handoff
    assert "ENGINEERING_BRIEF_JSON" not in handoff
    assert "END_ENGINEERING_BRIEF" not in handoff
    assert '"objective"' not in handoff
    assert "Complete the work in the active VibeCAD document" in handoff


def test_store_round_trip_is_scoped_to_document_and_conversation(
    tmp_path: Path,
) -> None:
    identity = {
        "project_root": str(tmp_path),
        "document_uid": "document-uid",
        "conversation_id": "b" * 32,
    }
    state = new_engineering_brief("Make a bracket", identity, _context())
    store = EngineeringBriefStore(tmp_path)

    written = store.write(state)
    loaded = store.load(
        document_uid="document-uid",
        conversation_id="b" * 32,
    )
    other_conversation = store.load(
        document_uid="document-uid",
        conversation_id="c" * 32,
    )

    assert written["written"] is True
    assert Path(written["path"]).is_file()
    assert loaded["available"] is True
    assert loaded["state"]["original_request"] == "Make a bracket"
    assert other_conversation == {
        "available": False,
        "reason": "missing",
        "path": str(tmp_path / "engineering-briefs" / ("c" * 32 + ".json")),
    }


def test_task_specific_provider_instructions_are_additive() -> None:
    from VibeCADProvider import (
        VIBECAD_SYSTEM_INSTRUCTIONS,
        _system_instruction_sections,
    )

    default_sections = _system_instruction_sections({"provider_tool_schemas": []})
    brief_sections = _system_instruction_sections(
        {
            "provider_tool_schemas": [],
            "_vibecad_task_instructions": "Brief-specific contract.",
        }
    )

    assert default_sections == [VIBECAD_SYSTEM_INSTRUCTIONS]
    assert brief_sections == [VIBECAD_SYSTEM_INSTRUCTIONS, "Brief-specific contract."]


def test_codex_supports_explicit_toolless_brief_tasks(monkeypatch) -> None:
    import VibeCADCodex as codex
    import VibeCADCodexResponses as codex_responses
    import VibeCADOllama as ollama
    from VibeCADProvider import CodexProvider

    monkeypatch.setattr(
        codex_responses,
        "codex_responses_base_url",
        lambda value: value,
    )
    monkeypatch.setattr(
        ollama,
        "inspect_model",
        lambda *_args, **_kwargs: {"detected": False, "ok": True},
    )
    thread_requests: list[dict[str, object]] = []

    class Client:
        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            del server_request_handler, environment
            self.notification_handler = notification_handler
            self.alive = True

        @property
        def stderr_tail(self):
            return []

        def start(self):
            return None

        def request(self, method, params, timeout):
            del timeout
            if method == "thread/start":
                thread_requests.append(params)
                return {"thread": {"id": "brief-thread"}, "model": "gpt-test"}
            if method == "turn/start":
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": "brief-thread",
                        "item": {
                            "type": "agentMessage",
                            "text": json.dumps(_provider_payload()),
                        },
                    },
                )
                self.notification_handler(
                    "turn/completed",
                    {
                        "threadId": "brief-thread",
                        "turn": {"id": "brief-turn", "status": "completed"},
                    },
                )
                return {"turn": {"id": "brief-turn"}}
            if method == "thread/delete":
                return {}
            raise AssertionError(method)

        def close(self):
            self.alive = False

    monkeypatch.setattr(codex, "CodexAppServerClient", Client)
    active_provider = CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
    )

    result = active_provider.run(
        "Develop the brief.",
        {
            "workbench": "PartDesignWorkbench",
            "provider_tool_schemas": [],
            "_vibecad_toolless_task": True,
            "_vibecad_task_instructions": "Return the engineering brief JSON.",
        },
        tool_runner=None,
    )

    assert json.loads(result.final_output)["next_question"]
    assert thread_requests[0]["dynamicTools"] == []
    assert (
        "non-mutating text-only VibeCAD task"
        in thread_requests[0]["developerInstructions"]
    )


def test_assistant_composer_exposes_the_engineering_brief_window() -> None:
    root = Path(__file__).resolve().parents[4]
    gui_source = (root / "src/Mod/VibeCAD/VibeCADGui.py").read_text(encoding="utf-8")
    brief_gui_source = (
        root / "src/Mod/VibeCAD/VibeCADEngineeringBriefGui.py"
    ).read_text(encoding="utf-8")
    cmake_source = (root / "src/Mod/VibeCAD/CMakeLists.txt").read_text(encoding="utf-8")

    assert 'setObjectName("VibeEngineeringBrief")' in gui_source
    assert "_open_engineering_brief_from_panel" in gui_source
    assert 'setWindowTitle("VibeCAD Engineering Brief")' in brief_gui_source
    assert 'setObjectName("VibeEngineeringBriefPreview")' in brief_gui_source
    assert 'setObjectName("VibeEngineeringBriefTranscript")' in brief_gui_source
    assert 'setObjectName("VibeEngineeringBriefPrimary")' in brief_gui_source
    assert 'QPushButton("Build My Brief"' in brief_gui_source
    assert '"Finish with Assumptions"' in brief_gui_source
    assert "complete_conversation_history_read" in gui_source
    assert "VibeCADEngineeringBriefGui.py" in cmake_source
    assert "vibecad-engineering-brief.svg" in cmake_source
