# SPDX-License-Identifier: LGPL-2.1-or-later
import json

import pytest

import VibeCADConversationContext as history


def event(sequence, role, content, **metadata):
    return {"sequence": sequence, "turn_id": f"{sequence:032x}", "role": role,
            "content": content, "metadata": metadata}


def test_workspace_return_delivers_unseen_instructions_and_tool_outcomes():
    initial = [event(1, "user", "Make a bracket; keep the mounting holes.")]
    cursor = history.cursor(initial)
    records = initial + [event(2, "assistant", "Opening its sketch.", provider_thread_id="sheet"),
                         event(3, "user", "Do not change the outer dimensions."),
                         event(4, "system", "CAD tool activity.", provider_thread_id="sketch",
                               tool_activity={"calls": [{"tool_name": "sketch.finish",
                                                          "result": {"ok": True}}]})]
    payload = history.window(records, cursor, thread_id="sheet")
    assert [row["sequence"] for row in payload["events"]] == [3, 4]
    assert "mounting holes" in payload["original_request"]["content"]
    assert "sketch.finish" in json.dumps(payload)
    assert history.window(records, history.cursor(records), thread_id="sheet")["events"] == []


def test_changed_boundary_replays_instead_of_losing_history():
    records = [event(1, "user", "Original request")]
    old = history.cursor(records)
    records[0]["content"] = "Corrected request"
    assert history.window(records, old)["events"][0]["content"] == "Corrected request"


def test_window_is_bounded_and_omitted_content_is_retrievable():
    records = [event(i, "user", "engineering requirements " * 2000) for i in range(1, 45)]
    payload = history.window(records, {})
    assert len(json.dumps(payload).encode("utf-8")) < 24000
    assert payload["omitted_event_count"] > 0
    assert payload["read_tool"] == "conversation.read"
    chunks = []
    sequence, offset = 1, 0
    while sequence == 1:
        page = history.read_page(records, sequence=sequence, offset=offset, max_chars=1200)
        chunks.append(page["text"])
        sequence, offset = page["next"]["sequence"], page["next"]["offset"]
    recovered = json.loads("".join(chunks))
    assert recovered["content"] == records[0]["content"]


def test_stop_controls_are_not_design_requirements():
    records = [event(1, "user", "Build it"), event(2, "user", "Stop", source="stop")]
    payload = history.window(records, {})
    assert [row["sequence"] for row in payload["events"]] == [1]


@pytest.mark.parametrize("arguments", [{"sequence": 0}, {"offset": -1},
                                      {"max_chars": 200000}, {"sequence": True}])
def test_history_read_rejects_invalid_paging(arguments):
    with pytest.raises(ValueError):
        history.read_page([event(1, "user", "Build it")], **arguments)


def test_managed_history_cursor_is_thread_scoped_and_invalidated():
    import threading
    import VibeCADCodex as codex
    runtime = codex._ManagedCodexRuntime(threading.RLock(), threading.RLock())
    sheet = codex.ManagedCodexSession(None, "sheet", runtime, "sheet-schema")
    sketch = codex.ManagedCodexSession(None, "sketch", runtime, "sketch-schema")
    delivered = history.cursor([event(1, "user", "Preserve the design")])
    generation = sheet.context_reuse_generation
    assert sheet.remember_conversation_cursor(delivered, generation=generation)
    assert sheet.previous_conversation_cursor == delivered
    assert sketch.previous_conversation_cursor == {}
    sheet.invalidate_context_reuse()
    assert sheet.previous_conversation_cursor == {}
    assert not sheet.remember_conversation_cursor(delivered, generation=generation)
    assert sheet.previous_conversation_cursor == {}


@pytest.mark.parametrize("interruption", [None, "failure", "compaction", "cancelled"])
def test_actual_provider_resume_gets_other_workspace_events(monkeypatch, interruption):
    import VibeCADCodex as codex
    import VibeCADProvider as provider
    import VibeCADSession as session

    class Client:
        alive = True
        requests = []
        count = 0
        interrupt = None
        def __init__(self, *, notification_handler, **_):
            self.notification_handler = notification_handler
        def start(self):
            pass
        def set_handlers(self, *, notification_handler, **_):
            self.notification_handler = notification_handler
        def close(self):
            self.alive = False
        def request(self, method, params, **_):
            self.requests.append((method, params))
            if method == "thread/start":
                Client.count += 1
                return {"thread": {"id": f"thread-{self.count}"}}
            if method == "thread/resume":
                return {"thread": {"id": params["threadId"]}}
            if method == "turn/start":
                if self.interrupt == "failure":
                    Client.interrupt = None
                    raise provider.ProviderUnavailable("Connection lost before delivery")
                if self.interrupt == "compaction":
                    Client.interrupt = None
                    self.notification_handler("thread/compacted", {"threadId": params["threadId"]})
                if self.interrupt == "cancelled":
                    Client.interrupt = None
                    self.notification_handler("turn/completed", {"turn": {"status": "interrupted"}})
                    return {"turn": {"id": "turn"}}
                self.notification_handler("item/completed", {
                    "item": {"type": "agentMessage", "text": "Done"}})
                self.notification_handler("turn/completed", {"turn": {"status": "completed"}})
                return {"turn": {"id": "turn"}}
            raise AssertionError(method)

    records = [event(1, "user", "Make the sheet without changing its outer dimensions.")]
    def run(name):
        schemas = [{"name": name, "description": "Test", "parameters": {
            "type": "object", "properties": {}, "additionalProperties": False}}]
        context = {"provider_tool_schemas": schemas,
                   "provider_tool_surface": session._turn_start_tool_surface("PartWorkbench", schemas),
                   "_vibecad_codex_session": {"conversation_id": "a"*32},
                   history.RECORDS_KEY: list(records), history.SCHEMAS_KEY: [history.TOOL_SCHEMA]}
        context["modeling_surface"] = {key: context["provider_tool_surface"][key] for key in (
            "workbench", "engine", "domain", "surface_id", "available", "unavailable_reason")}
        prompt = session._provider_prompt("Continue", context, prompt_section="CURRENT_SESSION_EVENT")
        return provider.CodexProvider(model="test", api_key="test", auth_mode="api_key").run(prompt, context)

    codex.reset_managed_codex_sessions()
    monkeypatch.setattr(codex, "CodexAppServerClient", Client)
    try:
        run("core.set_view")
        records.append(event(2, "user", "Keep all mounting holes too."))
        run("core.capture_view_screenshot")
        Client.interrupt = interruption
        if interruption in ("failure", "cancelled"):
            with pytest.raises(provider.ProviderUnavailable):
                run("core.set_view")
        else:
            run("core.set_view")
        run("core.set_view")
    finally:
        codex.reset_managed_codex_sessions()
    inputs = [params["input"][0]["text"] for method, params in Client.requests if method == "turn/start"]
    payloads = [json.loads(text.split(history.START, 1)[1].split(history.END, 1)[0]) for text in inputs]
    assert [row["sequence"] for row in payloads[2]["events"]] == [2]
    assert bool(payloads[3]["events"]) == bool(interruption)
    assert "outer dimensions" in payloads[2]["original_request"]["content"]


def test_history_tool_is_separate_from_cad_authority_and_does_not_reembed_history():
    from unittest.mock import Mock
    from VibeCADProvider import _model_visible_context, _provider_tool_surface_definitions
    inner = Mock(return_value={"ok": True, "context": {"live": True}})
    inner.provider_update.return_value = {"live": True}
    records = [event(1, "user", "Keep mounting holes")]
    trace = []
    runner = history.ConversationToolRunner(inner, records, tool_trace=trace)
    context = runner.attach({"provider_tool_schemas": []})
    assert "conversation.read" in json.dumps(_provider_tool_surface_definitions(
        context, lambda schema: schema, validate=False))
    assert history.RECORDS_KEY not in _model_visible_context(context)
    result = runner("conversation.read", '{"sequence":1,"max_chars":30}')
    assert result["ok"] and len(result["result"]["text"]) == 30
    assert result["result"]["next"] == {"sequence": 1, "offset": 30}
    inner.assert_not_called()
    assert trace[0]["result"] == {"ok": True}
    assert not runner("conversation.read", '{"offset":true}')["ok"]
    runner("sheet_metal.inspect", "{}", "call-1")
    inner.assert_called_once_with("sheet_metal.inspect", "{}", "call-1")
    assert runner.provider_update()[history.RECORDS_KEY] == records
    runner.close()
    inner.close.assert_called_once()


def test_cancelled_history_read_does_not_access_cad_or_return_data():
    from unittest.mock import Mock
    inner = Mock()
    runner = history.ConversationToolRunner(inner, [event(1, "user", "private")],
        tool_trace=[], cancellation_check=lambda: True)
    result = runner("conversation.read")
    assert not result["ok"] and "private" not in json.dumps(result)
    inner.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_session_persists_tool_only_handoff_and_failed_run_outcomes(monkeypatch, failed):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import VibeCADSession as session
    import VibeCADProvider as provider
    from vibecad_tests.test_model_context_contract import _active_state

    records = [event(1, "user", "Repair clearance; preserve mounting holes.")]
    saved = []
    service = SimpleNamespace(assistant_document_state=lambda: {"enabled": True},
                              active_workbench_name=lambda: "AssemblyWorkbench")
    def persist(service, role, content, **kwargs):
        saved.append({"role": role, "content": content, "metadata": kwargs.get("metadata", {})})
        return {"conversation_id": "a"*32, "conversation": records}
    monkeypatch.setattr(session, "_persist_session_conversation_turn", persist)
    monkeypatch.setattr(session, "_load_conversation_for_session", lambda *_: {
        "conversation_id": "a"*32, "conversation": records})
    monkeypatch.setattr(session, "_build_context_for_provider", lambda *_a, **_k: {
        **_active_state(), "provider_tool_schemas": []})
    def runner(*args, tool_trace, **kwargs):
        def call(name, arguments="{}", call_id=""):
            result = {"ok": True, "next_turn_required": True, "workspace": "sketching",
                      "human_steering": ["Preserve the outer dimensions too."]}
            tool_trace.append({"tool_name": name, "result": result})
            return result
        return call
    monkeypatch.setattr(session, "make_provider_tool_runner", runner)
    active_provider = provider.CodexProvider(model="test", auth_mode="chatgpt")
    def run(prompt, context, **kwargs):
        assert "mounting holes" in prompt
        kwargs["tool_runner"]("workspace.switch", '{"workspace":"sketching"}')
        if failed:
            raise provider.ProviderUnavailable("transport interrupted")
        return provider.ProviderResult("", raw={"thread_id": "sheet-thread"})
    monkeypatch.setattr(active_provider, "run", run)
    response = session.run_native_surface_continuation({"type": "cad_workspace_changed",
        "document_uid": "document-a", "document_name": "Design", "surface_id": "sheet_metal",
        "workspace": "sheet_metal"}, service=service, provider=active_provider, prefer_online=False)
    assert bool(response.error) == failed
    assert len(saved) == 1 and saved[0]["role"] == "system"
    activity = saved[0]["metadata"]["tool_activity"]
    assert activity["calls"][0]["tool_name"] == "workspace.switch"
    assert "outer dimensions" in json.dumps(activity)
    if not failed:
        assert saved[0]["metadata"]["provider_thread_id"] == "sheet-thread"
