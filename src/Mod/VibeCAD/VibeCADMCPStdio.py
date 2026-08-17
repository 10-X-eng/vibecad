# SPDX-License-Identifier: LGPL-2.1-or-later

"""Harness-launched stdio MCP server for one running VibeCAD instance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Callable


_runtime_root = Path(__file__).resolve().parents[2]
for _dependency_path in (_runtime_root / "Ext", Path(__file__).resolve().parent):
    _dependency = str(_dependency_path)
    if _dependency not in sys.path:
        sys.path.insert(0, _dependency)

from VibeCADMCP import (  # noqa: E402
    _ServerOperationStatusCache,
    _attachment_content,
    _mcp_ipc_address,
    _runtime_product_version,
)
from VibeCADMCPToolNames import mcp_wire_tool_schemas  # noqa: E402


class _IPCBrokerProxy:
    """Concurrent request/reply client with asynchronous broker events."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue[Any]] = {}
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self._closed_error: RuntimeError | None = None
        self._reader = threading.Thread(
            target=self._read_messages,
            name="VibeCAD-MCP-stdio-IPC",
            daemon=True,
        )
        self._reader.start()

    def set_event_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        with self._state_lock:
            self._event_callback = callback

    def request(self, method: str, **parameters: Any) -> dict[str, Any]:
        response_queue: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._state_lock:
            if self._closed_error is not None:
                raise self._closed_error
            self._request_id += 1
            request_id = self._request_id
            self._pending[request_id] = response_queue
        try:
            with self._send_lock:
                self._connection.send(
                    {
                        "request_id": request_id,
                        "method": method,
                        "parameters": parameters,
                    }
                )
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._fail(RuntimeError(f"VibeCAD MCP broker disconnected: {exc}"))
        response = response_queue.get()
        if isinstance(response, BaseException):
            raise response
        if not isinstance(response, dict) or response.get("request_id") != request_id:
            raise RuntimeError("VibeCAD MCP broker returned an invalid response.")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "MCP broker failed."))
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("VibeCAD MCP broker returned no payload.")
        return payload

    def _read_messages(self) -> None:
        try:
            while True:
                message = self._connection.recv()
                if not isinstance(message, dict):
                    continue
                if message.get("event"):
                    with self._state_lock:
                        callback = self._event_callback
                    if callback is not None:
                        callback(message)
                    continue
                request_id = message.get("request_id")
                with self._state_lock:
                    response_queue = self._pending.pop(request_id, None)
                if response_queue is not None:
                    response_queue.put(message)
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._fail(RuntimeError(f"VibeCAD MCP broker disconnected: {exc}"))

    def _fail(self, failure: RuntimeError) -> None:
        with self._state_lock:
            if self._closed_error is None:
                self._closed_error = failure
            pending = list(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            try:
                response_queue.put_nowait(failure)
            except queue.Full:
                pass

    def close(self) -> None:
        self._fail(RuntimeError("VibeCAD MCP stdio server closed."))
        try:
            self._connection.close()
        except Exception:
            pass
        if self._reader is not threading.current_thread():
            self._reader.join(timeout=1.0)


def _connect_to_broker() -> Any:
    from multiprocessing.connection import Client

    address, family = _mcp_ipc_address()
    deadline = time.monotonic() + 5.0
    failure: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            return Client(
                address=address,
                family=family,
            )
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            failure = exc
            time.sleep(0.1)
    raise RuntimeError(
        "VibeCAD's MCP broker is unavailable. Enable External MCP control in "
        f"the running VibeCAD instance and try again. ({failure})"
    )


async def _serve() -> None:
    from mcp.server import NotificationOptions, Server
    from mcp.server.stdio import stdio_server
    from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
    from mcp.shared.subscriptions import ToolsListChanged
    from mcp_types import CallToolResult, ListToolsResult, TextContent, Tool

    connection = _connect_to_broker()
    proxy = _IPCBrokerProxy(connection)
    shutdown_event = threading.Event()
    bridge = _ServerOperationStatusCache(proxy, shutdown_event)
    subscriptions = InMemorySubscriptionBus()

    class VibeCADStdioServer(Server):
        def __init__(self) -> None:
            self._client_sessions: dict[int, Any] = {}
            self._canonical_by_wire: dict[str, str] = {}
            self._surface_lock = asyncio.Lock()
            super().__init__(
                "VibeCAD",
                version=_runtime_product_version(),
                instructions=(
                    "Control the live VibeCAD document through the tools on the "
                    "human-selected ribbon. The human controls ribbon changes."
                ),
                on_list_tools=self._list_tools,
                on_call_tool=self._call_tool,
                on_subscriptions_listen=ListenHandler(subscriptions),
            )

        def create_initialization_options(
            self,
            notification_options: Any | None = None,
            experimental_capabilities: dict[str, dict[str, Any]] | None = None,
            extensions: dict[str, dict[str, Any]] | None = None,
        ) -> Any:
            configured_notifications = NotificationOptions(
                prompts_changed=bool(
                    getattr(notification_options, "prompts_changed", False)
                ),
                resources_changed=bool(
                    getattr(notification_options, "resources_changed", False)
                ),
                tools_changed=True,
            )
            return super().create_initialization_options(
                configured_notifications,
                experimental_capabilities,
                extensions,
            )

        def _remember_session(self, session: Any) -> None:
            self._client_sessions[id(session)] = session

        async def _load_tool_surface(self) -> list[dict[str, Any]]:
            async with self._surface_lock:
                payload = await asyncio.to_thread(bridge.request, "list_tools")
                advertised, routing = mcp_wire_tool_schemas(
                    payload.get("tools") or []
                )
                for wire_name, canonical_name in routing.items():
                    previous = self._canonical_by_wire.get(wire_name)
                    if previous is not None and previous != canonical_name:
                        raise RuntimeError(
                            f"MCP name {wire_name!r} changed from {previous!r} "
                            f"to {canonical_name!r} across ribbon surfaces."
                        )
                self._canonical_by_wire.update(routing)
                return advertised

        async def _list_tools(self, ctx: Any, params: Any) -> Any:
            del params
            self._remember_session(ctx.session)
            advertised = await self._load_tool_surface()
            tools = [
                Tool(
                    name=str(schema["name"]),
                    description=str(schema.get("description") or ""),
                    inputSchema=dict(schema.get("parameters") or {}),
                )
                for schema in advertised
            ]
            return ListToolsResult(tools=tools)

        async def _call_tool(self, ctx: Any, params: Any) -> Any:
            self._remember_session(ctx.session)
            requested_name = str(params.name)
            if requested_name not in self._canonical_by_wire:
                await self._load_tool_surface()
            canonical_name = self._canonical_by_wire.get(
                requested_name,
                requested_name,
            )
            payload = await asyncio.to_thread(
                bridge.request,
                "call_tool",
                name=canonical_name,
                arguments=dict(params.arguments or {}),
            )
            result = payload.get("result")
            if not isinstance(result, dict):
                result = {"ok": False, "error": "VibeCAD returned no result."}
            content: list[Any] = [
                TextContent(
                    text=json.dumps(
                        result,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            ]
            image = _attachment_content(payload.get("image_attachment"))
            if image is not None:
                content.append(image)
            return CallToolResult(
                content=content,
                structuredContent=result,
                isError=not bool(result.get("ok")),
            )

        async def notify_tool_list_changed(self) -> None:
            await subscriptions.publish(ToolsListChanged())
            stale_sessions = []
            for identity, session in list(self._client_sessions.items()):
                try:
                    await session.send_tool_list_changed()
                except Exception:
                    stale_sessions.append(identity)
            for identity in stale_sessions:
                self._client_sessions.pop(identity, None)

    server = VibeCADStdioServer()
    loop = asyncio.get_running_loop()

    def broker_event(event: dict[str, Any]) -> None:
        if str(event.get("event") or "") != "tool_list_changed":
            return

        def schedule() -> None:
            asyncio.create_task(server.notify_tool_list_changed())

        if not loop.is_closed():
            loop.call_soon_threadsafe(schedule)

    proxy.set_event_callback(broker_event)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        shutdown_event.set()
        proxy.set_event_callback(None)
        proxy.close()


def main() -> int:
    try:
        asyncio.run(_serve())
        return 0
    except KeyboardInterrupt:
        return 130
    except BaseException as exc:
        print(f"VibeCAD MCP stdio failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
