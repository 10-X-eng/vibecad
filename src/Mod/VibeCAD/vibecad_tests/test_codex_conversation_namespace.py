# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation history and CAD questions must share one Codex namespace."""

import copy
import os
from pathlib import Path

import pytest

import VibeCADCodex as codex
import VibeCADConversationContext as history
import VibeCADProvider as provider
from vibecad_tests.test_codex_subscription import _surface_context, _tool_schema


@pytest.mark.parametrize("namespaced", [True, False])
def test_conversation_tools_share_namespace_and_remain_callable(monkeypatch, namespaced):
    requests = []
    calls = []
    context = _surface_context("conversation.ask_user", "core.set_view")
    context[history.SCHEMAS_KEY] = [history.TOOL_SCHEMA]
    monkeypatch.setattr(provider, "_external_tool_schemas", lambda _: [_tool_schema("mcp_test.echo")])
    original = copy.deepcopy(context)

    class Client:
        alive = True
        stderr_tail = []

        def __init__(self, *, notification_handler, server_request_handler, **kwargs):
            self.notify = notification_handler
            self.call = server_request_handler

        def start(self):
            pass

        def close(self):
            self.alive = False

        def request(self, method, params, **kwargs):
            requests.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "thread-test"}}
            if method == "thread/delete":
                return {}
            assert method == "turn/start"
            for name in ("conversation.ask_user", "conversation.read", "mcp_test.echo"):
                namespace, function = name.split(".")
                result = self.call("item/tool/call", {
                    "namespace": namespace if namespaced else "",
                    "tool": function if namespaced else provider._codex_flat_function_name(namespace, function),
                    "arguments": {}, "callId": name,
                })
                assert result["success"]
            self.notify("item/completed", {"item": {"type": "agentMessage", "text": "Done"}})
            self.notify("turn/completed", {"turn": {"status": "completed"}})
            return {"turn": {"id": "turn-test"}}

    def run_tool(name, arguments, call_id):
        calls.append(name)
        return {"ok": True}

    run_tool.provider_update = lambda: context
    monkeypatch.setattr(codex, "CodexAppServerClient", Client)
    result = provider.CodexProvider(
        model="test", api_key="test", auth_mode="api_key",
        base_url=None if namespaced else "https://api.example.test/v1",
    ).run("Continue", context, tool_runner=run_tool)
    tools = next(params["dynamicTools"] for method, params in requests if method == "thread/start")
    assert len({tool["name"] for tool in tools}) == len(tools)
    if namespaced:
        conversation = next(tool for tool in tools if tool["name"] == "conversation")
        assert [tool["name"] for tool in conversation["tools"]] == ["ask_user", "read"]
        assert conversation["tools"][1]["inputSchema"] == history.TOOL_SCHEMA["parameters"]
        assert {tool["name"] for tool in tools} == {"conversation", "core", "mcp_test"}
    else:
        assert all(tool["type"] == "function" for tool in tools)
        assert {tool["name"] for tool in tools} == {
            "conversation__ask_user", "conversation__read", "core__set_view", "mcp_test__echo",
        }
    assert calls == ["conversation.ask_user", "conversation.read", "mcp_test.echo"]
    assert result.final_output == "Done"
    assert context == original


@pytest.mark.parametrize("namespaced", [True, False])
def test_duplicate_conversation_function_is_still_rejected(monkeypatch, namespaced):
    context = _surface_context("conversation.ask_user")
    monkeypatch.setattr(provider, "_codex_external_dynamic_tools", provider._codex_dynamic_tool_surface)
    with pytest.raises(provider.ProviderUnavailable, match="collides"):
        provider.CodexProvider(
            model="test", api_key="test", auth_mode="api_key",
            base_url=None if namespaced else "https://api.example.test/v1",
        ).run("Continue", context)


@pytest.mark.skipif(not os.environ.get("VIBECAD_TEST_CODEX_APP_SERVER"),
                    reason="Set VIBECAD_TEST_CODEX_APP_SERVER to test the shipped runtime")
def test_bundled_runtime_accepts_combined_conversation_namespace(monkeypatch, tmp_path):
    """Validate registration only: no model turn, credentials, or CAD commands."""
    requests = []

    def capture(*args, **kwargs):
        if kwargs.get("sdk_call") == "codex-app-server.thread/start":
            requests.append(copy.deepcopy(kwargs["request"]))

    monkeypatch.setattr(codex, "codex_home", lambda: tmp_path)
    for namespaced in (True, False):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(provider, "_capture_outbound_request", capture)
            test_conversation_tools_share_namespace_and_remain_callable(patch, namespaced)

    executable = Path(os.environ["VIBECAD_TEST_CODEX_APP_SERVER"]).resolve()
    command = codex.CodexRuntimeCommand(
        argv=(str(executable), "--listen", "stdio://"),
        executable=executable, source="test",
    )
    with codex.CodexAppServerClient(command=command, environment={
        "CODEX_HOME": str(tmp_path),
        codex.CODEX_OPENAI_API_KEY_ENV: "registration-only-no-model-turn",
    }) as client:
        broken = copy.deepcopy(requests[0])
        conversation = next(t for t in broken["dynamicTools"] if t["name"] == "conversation")
        history_namespace = copy.deepcopy(conversation)
        history_namespace["tools"] = [conversation["tools"].pop()]
        broken["dynamicTools"].append(history_namespace)
        with pytest.raises(codex.CodexAppServerError, match="duplicate dynamic tool namespace: conversation"):
            client.request("thread/start", broken, timeout=30)
        for request in requests:
            result = client.request("thread/start", request, timeout=30)
            assert result["thread"]["id"]
        # Threads are ephemeral: closing the private server disposes of them.
