# SPDX-License-Identifier: LGPL-2.1-or-later
"""Cancellation and cleanup must not hold the caller behind network work."""
import asyncio
import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

import VibeCADMCPToolServers as module


def test_active_loop_operation_is_cancelled_promptly():
    loop = module._LoopThread()
    started, cancelled, request = threading.Event(), threading.Event(), threading.Event()
    result = []

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    def call():
        try:
            loop.run_cancellable(operation(), 30, request.is_set)
        except Exception as exc:
            result.append(exc)

    # Missing API must fail before creating a coroutine/thread in the red test.
    assert callable(getattr(loop, "run_cancellable", None))
    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert started.wait(2)
        request.set()
        assert cancelled.wait(2)
        worker.join(2)
        assert not worker.is_alive()
        assert isinstance(result[0], module._ToolCallCancelled)
    finally:
        request.set()
        loop.stop()
        worker.join(2)


@pytest.mark.parametrize("operation", ["close_server_async", "shutdown_async"])
def test_async_cleanup_returns_while_manager_lock_is_held(tmp_path, operation):
    manager = module.MCPToolServerManager(runtime_directory=tmp_path)
    action = getattr(manager, operation, None)
    assert callable(action)
    held, release, returned = threading.Event(), threading.Event(), threading.Event()
    results = []

    def hold():
        with manager._lock:
            held.set()
            release.wait()

    def submit():
        results.append(action("fake") if operation == "close_server_async" else action())
        returned.set()

    locker = threading.Thread(target=hold)
    caller = threading.Thread(target=submit)
    locker.start()
    try:
        assert held.wait(2)
        caller.start()
        assert returned.wait(1), "Cleanup submission waited for the network-owned lock"
        assert not results[0].done()
    finally:
        release.set()
        locker.join(2)
        caller.join(2)
        manager.shutdown()
    results[0].result(2)


def test_short_tool_timeout_does_not_shorten_connection_startup(tmp_path, monkeypatch):
    observed = []

    class Connection:
        def __init__(self, server, **kwargs):
            self.signature = server.signature
            self.error = ""

        async def open(self, timeout):
            observed.append(timeout)

        async def close(self):
            pass

    monkeypatch.setattr(module, "_ServerConnection", Connection)
    manager = module.MCPToolServerManager(runtime_directory=tmp_path)
    try:
        server = module.MCPToolServer(name="fast", command="unused", timeout_seconds=1)
        connection, error = manager._ensure_connection(server)
        assert connection is not None, error
        assert observed == [module.MCP_CONNECT_TIMEOUT_SECONDS]
    finally:
        manager.shutdown()


def test_delayed_removal_does_not_close_a_server_reused_by_a_new_turn(tmp_path):
    manager = module.MCPToolServerManager(runtime_directory=tmp_path)
    assert callable(getattr(manager, "close_server_async", None))
    closed = []
    server = module.MCPToolServer(name="same", command="unused")

    async def refresh():
        return []

    async def close():
        closed.append(True)

    connection = SimpleNamespace(signature=server.signature, alive=True,
                                 refresh_tools=refresh, close=close)
    manager._connections[server.key] = connection
    try:
        with manager._lock:
            completion = manager.close_server_async(server.name)
            assert manager._ensure_connection(server)[0] is connection
        completion.result(2)
        assert closed == []
        assert manager._connections[server.key] is connection
    finally:
        manager.shutdown()


def test_preferences_removal_uses_nonblocking_cleanup(monkeypatch):
    tree = ast.parse((Path(module.__file__).with_name("VibeCADPreferences.py")).read_text())
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_save_tool_servers")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "preferences", "exec"), namespace)
    calls = []
    manager = SimpleNamespace(close_server=lambda name: calls.append("blocking"),
                              close_server_async=lambda name: calls.append("async"))
    monkeypatch.setattr(module, "get_mcp_tool_server_manager", lambda: manager)
    monkeypatch.setattr(module, "load_mcp_tool_servers", lambda: [
        module.MCPToolServer(name="removed", command="unused")])
    monkeypatch.setattr(module, "save_mcp_tool_servers", lambda servers: None)
    namespace["_save_tool_servers"](SimpleNamespace(_tool_server_drafts=[]))
    assert calls == ["async"]


def test_gui_shutdown_uses_nonblocking_cleanup(monkeypatch):
    tree = ast.parse((Path(module.__file__).with_name("VibeCADGui.py")).read_text())
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_shutdown_internal_assistant")
    calls = []
    monkeypatch.setattr(module, "shutdown_mcp_tool_servers", lambda: calls.append("blocking"))
    monkeypatch.setattr(module, "shutdown_mcp_tool_servers_async", lambda: calls.append("async"))
    monkeypatch.setitem(sys.modules, "VibeCADCodex", SimpleNamespace(
        shutdown_managed_codex_sessions=lambda: None))
    namespace = dict(threading=threading,
        _engineering_brief_dialog=None,
        _application_shutting_down=threading.Event(),
        _persist_session_recovery_before_shutdown=lambda: None,
        _assistant_run_controller=SimpleNamespace(request_cancel=lambda: None),
        _intent_memory_rebuild_cancel_event=threading.Event(),
        _cancel_question_round=lambda: None, _warn=lambda msg: None,
        _assistant_run_thread=None, _intent_memory_rebuild_thread=None,
        _analyze_context_prewarm_thread=None)
    exec(compile(ast.Module(body=[method], type_ignores=[]), "gui", "exec"), namespace)
    namespace["_shutdown_internal_assistant"]()
    assert calls == ["async"]
