# SPDX-License-Identifier: LGPL-2.1-or-later

"""Mutually exclusive Internal Agent and MCP control modes for VibeCAD.

The MCP process owns only protocol and network I/O. Every CAD read or write is
sent through a narrow pipe to the host, where the existing VibeCAD tool-surface
resolver and provider tool runner retain authority over validation, revisions,
transactions, cancellation, and document-thread execution.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from enum import Enum
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from typing import Any, Callable


MCP_HOST = "127.0.0.1"
MCP_PORT = 8765
MCP_PATH = "/mcp"
MCP_ENDPOINT = f"http://{MCP_HOST}:{MCP_PORT}{MCP_PATH}"
MCP_START_TIMEOUT_SECONDS = 15.0
MCP_STOP_TIMEOUT_SECONDS = 15.0
MCP_MAX_IMAGE_BYTES = 8 * 1024 * 1024
MCP_KEYRING_ACCOUNT = "mcp-bearer-token-v1"
MCP_TOKEN_BYTES = 32

READ_WORKBENCH_TOOL = "vibecad.read_workbench"
SWITCH_WORKBENCH_TOOL = "vibecad.switch_workbench"


def _mcp_keyring_module() -> Any | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _valid_mcp_token(value: Any) -> str:
    token = str(value or "").strip()
    if len(token) < 40:
        return ""
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in token):
        return ""
    return token


def _store_mcp_token(token: str) -> str:
    from VibeCADAuth import KEYRING_SERVICE

    keyring = _mcp_keyring_module()
    if keyring is None:
        raise RuntimeError(
            "VibeCAD cannot persist its MCP bearer token because no OS "
            "credential-store backend is available."
        )
    try:
        keyring.set_password(KEYRING_SERVICE, MCP_KEYRING_ACCOUNT, token)
        persisted = _valid_mcp_token(
            keyring.get_password(KEYRING_SERVICE, MCP_KEYRING_ACCOUNT)
        )
    except Exception as exc:
        raise RuntimeError(
            f"VibeCAD could not store its MCP bearer token securely: {exc}"
        ) from exc
    if not persisted:
        raise RuntimeError(
            "The OS credential store did not return the persisted MCP bearer token."
        )
    return persisted


def _persistent_mcp_token(*, regenerate: bool = False) -> str:
    """Return one stable bearer token from the OS credential store."""

    from VibeCADAuth import KEYRING_SERVICE

    keyring = _mcp_keyring_module()
    if keyring is None:
        raise RuntimeError(
            "VibeCAD cannot persist its MCP bearer token because no OS "
            "credential-store backend is available."
        )
    if not regenerate:
        try:
            existing = _valid_mcp_token(
                keyring.get_password(KEYRING_SERVICE, MCP_KEYRING_ACCOUNT)
            )
        except Exception as exc:
            raise RuntimeError(
                f"VibeCAD could not read its MCP bearer token securely: {exc}"
            ) from exc
        if existing:
            return existing
    return _store_mcp_token(secrets.token_urlsafe(MCP_TOKEN_BYTES))


def _runtime_product_version() -> str:
    try:
        import FreeCAD as App

        values = list(App.Version())
        version = ".".join(str(value) for value in values[:3])
        return version or "unknown"
    except Exception:
        return "unknown"


class ControlMode(str, Enum):
    INTERNAL = "internal"
    MCP = "mcp"


class ControllerState(str, Enum):
    INTERNAL = "internal"
    STARTING_MCP = "starting_mcp"
    MCP = "mcp"
    STOPPING_MCP = "stopping_mcp"


def controller_tool_schemas() -> list[dict[str, Any]]:
    """Return the only tools added to the resolved surface in MCP mode."""

    return [
        {
            "name": READ_WORKBENCH_TOOL,
            "description": (
                "Read the active VibeCAD workbench and the exact workbenches "
                "available for switching."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": SWITCH_WORKBENCH_TOOL,
            "description": (
                "Switch VibeCAD to an exact workbench name returned by "
                "vibecad.read_workbench. This changes the human ribbon; Model "
                "and Assembly keep one combined MCP authoring surface."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workbench": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Exact workbench name returned by "
                            "vibecad.read_workbench."
                        ),
                    }
                },
                "required": ["workbench"],
                "additionalProperties": False,
            },
        },
    ]


def _surface_signature(
    snapshot: dict[str, Any],
) -> tuple[str, str, str, str, str, str]:
    surface = snapshot.get("modeling_surface")
    details = surface if isinstance(surface, dict) else {}
    schemas = snapshot.get("schemas") or []
    from VibeCADProvider import provider_tool_schema_digest

    return (
        str(snapshot.get("workbench") or ""),
        str(details.get("engine") or ""),
        str(details.get("surface_id") or ""),
        provider_tool_schema_digest(schemas),
        str(snapshot.get("document_identity") or ""),
        str(snapshot.get("source_authority_digest") or ""),
    )


class _HostToolSession:
    """One serialized MCP controller session inside the FreeCAD host."""

    def __init__(
        self,
        document_thread_dispatch: Callable[[Callable[[], Any]], Any],
        cancellation: threading.Event,
        question_callback: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
        | None = None,
    ) -> None:
        self._dispatch = document_thread_dispatch
        self._cancellation = cancellation
        self._question_callback = question_callback
        self._service: Any | None = None
        self._runner: Any | None = None
        self._runner_signature: tuple[str, str, str, str, str, str] | None = None
        self._tool_trace: list[dict[str, Any]] = []

    def _get_service(self) -> Any:
        if self._service is None:
            from VibeCADCore import get_service

            self._service = self._dispatch(get_service)
        return self._service

    def _live_surface(self) -> dict[str, Any]:
        from VibeCADModelingSurface import resolve_service_surface
        from VibeCADSession import _minimal_runtime_state, provider_tool_schemas
        import VibeCADVibeScriptDomains as vibescript_domains

        service = self._get_service()

        def capture() -> dict[str, Any]:
            import FreeCAD as App

            workbench = service.active_workbench_name()
            resolution = resolve_service_surface(service, workbench)
            runtime_state = _minimal_runtime_state(service)
            schemas = provider_tool_schemas(
                service,
                workbench,
                runtime_state=runtime_state,
                interaction_mode="build",
            )
            document = getattr(App, "ActiveDocument", None)
            document_identity = ""
            if document is not None:
                document_identity = ":".join(
                    (
                        str(getattr(document, "Uid", "") or ""),
                        str(getattr(document, "Name", "") or ""),
                        str(getattr(document, "UndoCount", 0) or 0),
                        str(getattr(document, "RedoCount", 0) or 0),
                        str(len(list(getattr(document, "Objects", []) or []))),
                    )
                )
            source_authority: list[tuple[str, str, str]] = []
            pack = vibescript_domains.get_vibescript_pack(workbench)
            if pack is not None:
                captured = vibescript_domains.capture_editable_sources_snapshot(
                    service, pack.domain
                )
                source_authority = sorted(
                    (
                        str(item.get("source_id") or ""),
                        str(item.get("current_revision") or ""),
                        str(item.get("status") or ""),
                    )
                    for item in list(captured.get("sources") or [])
                    if isinstance(item, dict)
                )
            source_digest = hashlib.sha256(
                json.dumps(
                    source_authority,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            return {
                "workbench": workbench,
                "modeling_surface": {
                    "workbench": str(resolution.workbench or ""),
                    "engine": resolution.engine,
                    "domain": resolution.domain,
                    "surface_id": resolution.surface_id,
                    "available": resolution.available,
                    **(
                        {"unavailable_reason": resolution.unavailable_reason}
                        if not resolution.available
                        else {}
                    ),
                },
                "runtime_state": runtime_state,
                "schemas": schemas,
                "document_identity": document_identity,
                "source_authority_digest": source_digest,
            }

        return self._dispatch(capture)

    def list_tools(self) -> dict[str, Any]:
        snapshot = self._live_surface()
        return {
            "ok": True,
            "workbench": snapshot.get("workbench"),
            "modeling_surface": snapshot.get("modeling_surface"),
            "tools": [
                *list(snapshot.get("schemas") or []),
                *controller_tool_schemas(),
            ],
        }

    def _runner_for(self, snapshot: dict[str, Any]) -> Any:
        signature = _surface_signature(snapshot)
        if self._runner is not None and signature == self._runner_signature:
            return self._runner

        from VibeCADSession import _build_context_for_provider, make_provider_tool_runner

        service = self._get_service()
        context = _build_context_for_provider(
            service,
            None,
            "build",
            self._dispatch,
        )
        self._runner = make_provider_tool_runner(
            service,
            tool_trace=self._tool_trace,
            progress_callback=None,
            cancellation_check=self._cancellation.is_set,
            steering_check=None,
            question_callback=self._question_callback,
            document_thread_dispatch=self._dispatch,
            turn_surface=None,
            turn_schemas=[
                dict(schema)
                for schema in list(context.get("provider_tool_schemas") or [])
                if isinstance(schema, dict)
            ],
            turn_modeling_surface=(
                dict(context["modeling_surface"])
                if isinstance(context.get("modeling_surface"), dict)
                else None
            ),
            turn_component_catalog=(
                dict(context["_vibecad_component_catalog"])
                if isinstance(context.get("_vibecad_component_catalog"), dict)
                else None
            ),
            turn_editable_sources=(
                dict(context["editable_sources"])
                if isinstance(context.get("editable_sources"), dict)
                else None
            ),
            interaction_mode="build",
            provider_calls_allowed=False,
        )
        self._runner_signature = signature
        return self._runner

    def _read_workbench(self) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            import FreeCADGui as Gui

            active = Gui.activeWorkbench()
            workbenches = Gui.listWorkbenches()
            available = []
            for name, workbench in sorted(workbenches.items()):
                available.append(
                    {
                        "name": str(name),
                        "label": str(
                            (workbench if isinstance(workbench, str) else "")
                            or getattr(workbench, "MenuText", "")
                            or getattr(workbench, "Label", "")
                            or name
                        ),
                    }
                )
            return {
                "ok": True,
                "active_workbench": (
                    str(active.name()) if active is not None else None
                ),
                "available_workbenches": available,
            }

        return self._dispatch(read)

    def _switch_workbench(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = str(arguments.get("workbench") or "").strip()
        if not target:
            return {
                "ok": False,
                "failure_code": "WORKBENCH_REQUIRED",
                "failure_stage": "schema",
                "error": "workbench must be one exact non-empty name.",
            }

        def switch() -> dict[str, Any]:
            import FreeCADGui as Gui
            from VibeCADEditState import active_edit_state

            edit_state = active_edit_state(getattr(Gui, "ActiveDocument", None))
            if edit_state.active:
                return {
                    "ok": False,
                    "failure_code": "NATIVE_TASK_ACTIVE",
                    "failure_stage": "precondition",
                    "error": (
                        "Close or cancel the active native task before switching "
                        "workbenches."
                    ),
                    "observed": {
                        "edit_object": str(
                            getattr(edit_state.document_object, "Name", "") or ""
                        ),
                        "edit_mode": edit_state.mode,
                    },
                }
            available = Gui.listWorkbenches()
            if target not in available:
                return {
                    "ok": False,
                    "failure_code": "UNKNOWN_WORKBENCH",
                    "failure_stage": "precondition",
                    "error": f"Unknown VibeCAD workbench: {target}",
                    "candidates": sorted(str(name) for name in available),
                }
            previous = Gui.activeWorkbench()
            previous_name = str(previous.name()) if previous is not None else None
            Gui.activateWorkbench(target)
            active = Gui.activeWorkbench()
            active_name = str(active.name()) if active is not None else None
            if active_name != target:
                return {
                    "ok": False,
                    "failure_code": "WORKBENCH_SWITCH_FAILED",
                    "failure_stage": "native_call",
                    "error": f"VibeCAD did not activate workbench {target}.",
                    "observed": {"active_workbench": active_name},
                }
            from VibeCADModelingSurface import share_authoring_surface

            return {
                "ok": True,
                "previous_workbench": previous_name,
                "active_workbench": active_name,
                "tool_list_changed": not share_authoring_surface(
                    previous_name,
                    active_name,
                ),
            }

        result = self._dispatch(switch)
        if result.get("ok") and result.get("tool_list_changed"):
            self._runner = None
            self._runner_signature = None
            # Gui.activateWorkbench emits MainWindow.workbenchActivated. The
            # single GUI observer owns generation changes for both human and
            # MCP switches, avoiding duplicate list-change notifications.
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == READ_WORKBENCH_TOOL:
            return {"result": self._read_workbench(), "image_attachment": None}
        if name == SWITCH_WORKBENCH_TOOL:
            return {
                "result": self._switch_workbench(arguments),
                "image_attachment": None,
            }

        snapshot = self._live_surface()
        runner = self._runner_for(snapshot)
        raw = runner(name, json.dumps(arguments, ensure_ascii=True))
        if not isinstance(raw, dict):
            raw = {"ok": False, "error": "VibeCAD tool returned no object result."}
        attachment = raw.get("_vibecad_image_attachment")
        from VibeCADProvider import _provider_visible_tool_result

        visible = _provider_visible_tool_result(raw)
        if len(self._tool_trace) > 256:
            del self._tool_trace[:-128]
        return {
            "result": visible,
            "image_attachment": (
                dict(attachment) if isinstance(attachment, dict) else None
            ),
        }


class _ServerHostProxy:
    """Serialized request/reply client used by the MCP process."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._lock = threading.Lock()
        self._request_id = 0

    def request(self, method: str, **parameters: Any) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._connection.send(
                {
                    "request_id": request_id,
                    "method": method,
                    "parameters": parameters,
                }
            )
            response = self._connection.recv()
            if not isinstance(response, dict) or response.get("request_id") != request_id:
                raise RuntimeError("VibeCAD MCP host bridge returned an invalid response.")
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "MCP host bridge failed."))
            payload = response.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("VibeCAD MCP host bridge returned no payload.")
            return payload


def _attachment_content(attachment: dict[str, Any] | None) -> Any | None:
    if not isinstance(attachment, dict):
        return None
    path_text = str(attachment.get("path") or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    try:
        size = path.stat().st_size
        if size <= 0 or size > MCP_MAX_IMAGE_BYTES:
            return None
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    mime_type = str(attachment.get("mime_type") or "").strip()
    if not mime_type:
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    from mcp_types import ImageContent

    return ImageContent(data=data, mimeType=mime_type)


class _BearerTokenMiddleware:
    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        authorization = headers.get("authorization", "")
        supplied = (
            authorization[7:]
            if authorization.lower().startswith("bearer ")
            else ""
        )
        if not supplied or not hmac.compare_digest(supplied, self._token):
            body = b'{"error":"invalid_token","error_description":"Authentication required"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


class _ActivityMiddleware:
    def __init__(self, app: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        self._app = app
        self._emit = emit
        self._active = 0
        self._lock = threading.Lock()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        with self._lock:
            self._active += 1
            active = self._active
        self._emit({"event": "client_activity", "active_requests": active})
        try:
            await self._app(scope, receive, send)
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
                active = self._active
            self._emit({"event": "client_activity", "active_requests": active})


def _mcp_server_process_main(
    host_connection: Any,
    status_connection: Any,
    shutdown_event: Any,
    surface_generation: Any,
    token: str,
    host: str,
    port: int,
    path: str,
) -> None:
    """Run only MCP/HTTP concerns in the isolated child process."""

    status_lock = threading.Lock()

    def emit(event: dict[str, Any]) -> None:
        try:
            with status_lock:
                status_connection.send(dict(event))
        except (BrokenPipeError, EOFError, OSError):
            pass

    try:
        from mcp.server import NotificationOptions, Server
        from mcp.server.subscriptions import (
            InMemorySubscriptionBus,
            ListenHandler,
        )
        from mcp.server.transport_security import TransportSecuritySettings
        from mcp.shared.subscriptions import ToolsListChanged
        from mcp_types import CallToolResult, ListToolsResult, TextContent, Tool
        import uvicorn

        proxy = _ServerHostProxy(host_connection)

        subscriptions = InMemorySubscriptionBus()

        class VibeCADProtocolServer(Server):
            def __init__(self) -> None:
                self._client_sessions: dict[int, Any] = {}
                super().__init__(
                    "VibeCAD",
                    version=_runtime_product_version(),
                    instructions=(
                        "Control the live VibeCAD document through the active "
                        "workbench's VibeScript tools. Read or switch workbenches "
                        "explicitly when needed."
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
                # The SDK derives modern change capability from subscriptions,
                # while handshake-era clients require this explicit flag.
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

            async def _list_tools(self, ctx: Any, params: Any) -> Any:
                del params
                self._remember_session(ctx.session)
                payload = await asyncio.to_thread(proxy.request, "list_tools")
                tools = []
                for schema in payload.get("tools") or []:
                    if not isinstance(schema, dict):
                        continue
                    tools.append(
                        Tool(
                            name=str(schema.get("name") or ""),
                            description=str(schema.get("description") or ""),
                            inputSchema=dict(schema.get("parameters") or {}),
                        )
                    )
                return ListToolsResult(tools=tools)

            async def _call_tool(self, ctx: Any, params: Any) -> Any:
                self._remember_session(ctx.session)
                payload = await asyncio.to_thread(
                    proxy.request,
                    "call_tool",
                    name=str(params.name),
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
                # Modern MCP clients receive this through subscriptions/listen;
                # handshake-era clients receive the equivalent notification on
                # their persistent Streamable HTTP session.
                await subscriptions.publish(ToolsListChanged())
                stale_sessions = []
                for identity, session in list(self._client_sessions.items()):
                    try:
                        await session.send_tool_list_changed()
                    except Exception:
                        stale_sessions.append(identity)
                for identity in stale_sessions:
                    self._client_sessions.pop(identity, None)

        server = VibeCADProtocolServer()
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        )
        app: Any = server.streamable_http_app(
            streamable_http_path=path,
            json_response=False,
            stateless_http=False,
            transport_security=security,
            host=host,
        )
        app = _ActivityMiddleware(app, emit)
        app = _BearerTokenMiddleware(app, token)
        uvicorn_server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )

        async def run() -> None:
            serve_task = asyncio.create_task(uvicorn_server.serve())
            observed_generation = int(surface_generation.value)
            while not serve_task.done():
                if uvicorn_server.started:
                    emit(
                        {
                            "event": "listening",
                            "endpoint": f"http://{host}:{port}{path}",
                            "pid": os.getpid(),
                        }
                    )
                    break
                if shutdown_event.is_set():
                    uvicorn_server.should_exit = True
                    break
                await asyncio.sleep(0.05)

            while not serve_task.done():
                if shutdown_event.is_set():
                    uvicorn_server.should_exit = True
                current_generation = int(surface_generation.value)
                if current_generation != observed_generation:
                    observed_generation = current_generation
                    await server.notify_tool_list_changed()
                    emit(
                        {
                            "event": "tool_list_changed",
                            "generation": observed_generation,
                        }
                    )
                await asyncio.sleep(0.1)
            await serve_task

        asyncio.run(run())
        emit({"event": "stopped"})
    except BaseException as exc:
        emit(
            {
                "event": "error",
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
    finally:
        try:
            host_connection.close()
        except Exception:
            pass
        try:
            status_connection.close()
        except Exception:
            pass


class VibeCADControlModeController:
    """Single authority that makes Internal Agent and MCP mutually exclusive."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._spawn_lock = threading.Lock()
        self._state = ControllerState.INTERNAL
        self._desired_mode = ControlMode.INTERNAL
        self._connection_state = "disabled"
        self._last_error = ""
        self._active_requests = 0
        self._last_activity_at = 0.0
        self._transition_id = 0
        self._document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None
        self._internal_active: Callable[[], bool] = lambda: False
        self._cancel_internal: Callable[[], None] = lambda: None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self._question_callback: (
            Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None
        ) = None
        self._process: Any | None = None
        self._host_connection: Any | None = None
        self._status_connection: Any | None = None
        self._shutdown_event: Any | None = None
        self._surface_generation: Any | None = None
        self._surface_workbench = ""
        self._tool_cancellation: threading.Event | None = None
        self._token = ""
        self._bridge_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._start_thread: threading.Thread | None = None

    def configure_host(
        self,
        *,
        document_thread_dispatch: Callable[[Callable[[], Any]], Any],
        internal_active: Callable[[], bool],
        cancel_internal: Callable[[], None],
        question_callback: Callable[
            [list[dict[str, Any]]], list[dict[str, Any]]
        ]
        | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        with self._lock:
            self._document_thread_dispatch = document_thread_dispatch
            self._internal_active = internal_active
            self._cancel_internal = cancel_internal
            self._question_callback = question_callback
            self._event_callback = event_callback

    def internal_agent_allowed(self) -> bool:
        with self._lock:
            return (
                self._state == ControllerState.INTERNAL
                and self._desired_mode == ControlMode.INTERNAL
            )

    def require_internal_agent(self) -> None:
        if not self.internal_agent_allowed():
            raise RuntimeError(
                "The Internal Agent is disabled while VibeCAD MCP control is enabled."
            )

    def snapshot(self, *, include_token: bool = False) -> dict[str, Any]:
        with self._lock:
            token = self._token if include_token else ""
            return {
                "state": self._state.value,
                "desired_mode": self._desired_mode.value,
                "internal_agent_enabled": self.internal_agent_allowed(),
                "mcp_enabled": self._state == ControllerState.MCP,
                "endpoint": MCP_ENDPOINT,
                "connection_state": self._connection_state,
                "active_requests": self._active_requests,
                "last_activity_at": self._last_activity_at or None,
                "last_error": self._last_error,
                "token": token,
                "token_available": bool(self._token),
                "pid": getattr(self._process, "pid", None),
            }

    def connection_configuration(self) -> dict[str, Any]:
        snapshot = self.snapshot(include_token=True)
        token = str(snapshot.pop("token") or "")
        return {
            "url": MCP_ENDPOINT,
            "transport": "streamable-http",
            "headers": {"Authorization": f"Bearer {token}"} if token else {},
            "status": snapshot,
        }

    def regenerate_mcp_token(self) -> dict[str, Any]:
        """Explicitly rotate the stored token and restart an active server."""

        failure = ""
        with self._spawn_lock:
            with self._lock:
                if self._state not in {
                    ControllerState.INTERNAL,
                    ControllerState.MCP,
                }:
                    return {
                        "ok": False,
                        "error": (
                            "Wait for the current MCP start or stop transition "
                            "to finish before regenerating its token."
                        ),
                        "snapshot": self.snapshot(),
                    }
                restart = self._state == ControllerState.MCP
                try:
                    token = _persistent_mcp_token(regenerate=True)
                except Exception as exc:
                    failure = str(exc)
                    self._last_error = str(exc)
                else:
                    self._token = token
                    self._last_error = ""

        if failure:
            self._emit("mcp_token_regeneration_failed")
            return {
                "ok": False,
                "error": failure,
                "snapshot": self.snapshot(),
            }

        if restart:
            self.request_mcp_enabled(False)
            self.request_mcp_enabled(True)
        else:
            self._emit("mcp_token_regenerated")
        return {
            "ok": True,
            "restarting": restart,
            "snapshot": self.snapshot(),
        }

    def _emit(self, event: str, *, rollback_preference: bool = False) -> None:
        callback = None
        with self._lock:
            callback = self._event_callback
            payload = {
                "event": event,
                "rollback_preference": rollback_preference,
                "snapshot": self.snapshot(),
            }
        if callback is not None:
            try:
                callback(payload)
            except Exception:
                pass

    def request_mcp_enabled(self, enabled: bool) -> dict[str, Any]:
        emit_event = ""
        configuration_error = ""
        with self._lock:
            desired = ControlMode.MCP if enabled else ControlMode.INTERNAL
            if enabled:
                if self._state in {ControllerState.STARTING_MCP, ControllerState.MCP}:
                    return self.snapshot()
                if self._state == ControllerState.STOPPING_MCP:
                    # The existing server must finish before a replacement can
                    # start.  The process monitor observes desired_mode and
                    # starts it after releasing every old bridge resource.
                    self._desired_mode = desired
                    if self._process is not None:
                        self._connection_state = "stopping"
                        return self.snapshot()
                if self._document_thread_dispatch is None:
                    configuration_error = (
                        "VibeCAD MCP host dispatch is not initialized."
                    )
                    self._last_error = configuration_error
                    self._desired_mode = ControlMode.INTERNAL
                    self._state = ControllerState.INTERNAL
                    self._connection_state = "error"
                else:
                    self._transition_id += 1
                    transition_id = self._transition_id
                    self._desired_mode = desired
                    self._state = ControllerState.STARTING_MCP
                    self._connection_state = "starting"
                    self._last_error = ""
            else:
                if self._state == ControllerState.INTERNAL:
                    self._desired_mode = desired
                    self._connection_state = "disabled"
                    return self.snapshot()
                self._transition_id += 1
                transition_id = self._transition_id
                self._desired_mode = desired
                self._state = ControllerState.STOPPING_MCP
                self._connection_state = "stopping"
                if self._tool_cancellation is not None:
                    self._tool_cancellation.set()
                if self._shutdown_event is not None:
                    self._shutdown_event.set()
                emit_event = "mcp_stopping"

        if configuration_error:
            self._emit("mcp_error", rollback_preference=True)
        elif enabled:
            try:
                self._cancel_internal()
            except Exception as exc:
                self._fail_start(transition_id, f"Could not stop Internal Agent: {exc}")
                return self.snapshot()
            start_thread = threading.Thread(
                target=self._start_when_internal_stops,
                args=(transition_id,),
                name="VibeCAD-MCP-Start",
                daemon=True,
            )
            with self._lock:
                self._start_thread = start_thread
            start_thread.start()
            self._emit("mcp_starting")
        else:
            self._emit(emit_event or "mcp_stopping")
        return self.snapshot()

    def _start_when_internal_stops(self, transition_id: int) -> None:
        try:
            deadline = time.monotonic() + MCP_START_TIMEOUT_SECONDS
            while self._internal_active():
                with self._lock:
                    if (
                        transition_id != self._transition_id
                        or self._desired_mode != ControlMode.MCP
                    ):
                        return
                if time.monotonic() >= deadline:
                    self._fail_start(
                        transition_id,
                        "Internal Agent did not stop before the MCP start timeout.",
                    )
                    return
                time.sleep(0.05)
            with self._lock:
                if (
                    transition_id != self._transition_id
                    or self._desired_mode != ControlMode.MCP
                ):
                    return
            self._start_process(transition_id)
        except BaseException as exc:
            self._fail_start(transition_id, f"MCP server start failed: {exc}")
        finally:
            self._settle_cancelled_start()
            with self._lock:
                if self._start_thread is threading.current_thread():
                    self._start_thread = None

    def _settle_cancelled_start(self) -> None:
        """Complete a cancelled pre-spawn transition without stranding the UI."""

        should_emit = False
        with self._lock:
            if (
                self._desired_mode == ControlMode.INTERNAL
                and self._state == ControllerState.STOPPING_MCP
                and self._process is None
            ):
                self._state = ControllerState.INTERNAL
                self._connection_state = "disabled"
                self._token = ""
                should_emit = True
        if should_emit:
            self._emit("internal_agent_enabled")

    def _start_process(self, transition_id: int) -> None:
        with self._spawn_lock:
            with self._lock:
                if (
                    transition_id != self._transition_id
                    or self._desired_mode != ControlMode.MCP
                    or self._process is not None
                ):
                    return
            self._spawn_process_locked(transition_id)

    def _spawn_process_locked(self, transition_id: int) -> None:
        """Create one child while ``_spawn_lock`` excludes a second spawn."""

        from VibeCADProvider import (
            _provider_multiprocessing_context,
            _provider_spawn_bootstrap_environment,
        )

        dispatch = self._document_thread_dispatch
        if dispatch is None:
            raise RuntimeError("VibeCAD MCP host dispatch is not initialized.")

        context = _provider_multiprocessing_context()
        host_connection, server_connection = context.Pipe()
        status_receive, status_send = context.Pipe(duplex=False)
        shutdown_event = context.Event()
        surface_generation = context.Value("Q", 0)
        cancellation = threading.Event()
        token = _persistent_mcp_token()
        process = context.Process(
            target=_mcp_server_process_main,
            args=(
                server_connection,
                status_send,
                shutdown_event,
                surface_generation,
                token,
                MCP_HOST,
                MCP_PORT,
                MCP_PATH,
            ),
        )
        process.daemon = True
        original_stdin = sys.stdin
        replacement_stdin = None
        started = False
        try:
            if not hasattr(sys.stdin, "close"):
                replacement_stdin = open(os.devnull, "r", encoding="utf-8")
                sys.stdin = replacement_stdin
            with _provider_spawn_bootstrap_environment():
                process.start()
            started = True
        except BaseException:
            shutdown_event.set()
            for connection in (
                host_connection,
                server_connection,
                status_receive,
                status_send,
            ):
                try:
                    connection.close()
                except Exception:
                    pass
            if started and process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            raise
        finally:
            sys.stdin = original_stdin
            if replacement_stdin is not None:
                replacement_stdin.close()
        server_connection.close()
        status_send.close()

        with self._lock:
            superseded = (
                transition_id != self._transition_id
                or self._desired_mode != ControlMode.MCP
            )
            if superseded:
                shutdown_event.set()
            self._process = process
            self._host_connection = host_connection
            self._status_connection = status_receive
            self._shutdown_event = shutdown_event
            self._surface_generation = surface_generation
            self._tool_cancellation = cancellation
            self._token = token
            if superseded:
                self._state = ControllerState.STOPPING_MCP
                self._connection_state = "stopping"

        session = _HostToolSession(
            dispatch,
            cancellation,
            self._question_callback,
        )
        self._bridge_thread = threading.Thread(
            target=self._host_bridge_loop,
            args=(host_connection, process, session),
            name="VibeCAD-MCP-Host-Bridge",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            args=(transition_id, process, status_receive, shutdown_event),
            name="VibeCAD-MCP-Monitor",
            daemon=True,
        )
        self._bridge_thread.start()
        self._monitor_thread.start()

    @staticmethod
    def _host_bridge_loop(connection: Any, process: Any, session: _HostToolSession) -> None:
        try:
            while process.is_alive() or connection.poll(0.1):
                if not connection.poll(0.1):
                    continue
                request = connection.recv()
                request_id = request.get("request_id") if isinstance(request, dict) else None
                try:
                    if not isinstance(request, dict):
                        raise RuntimeError("Invalid MCP host request.")
                    method = str(request.get("method") or "")
                    parameters = request.get("parameters")
                    parameters = parameters if isinstance(parameters, dict) else {}
                    if method == "list_tools":
                        payload = session.list_tools()
                    elif method == "call_tool":
                        arguments = parameters.get("arguments")
                        payload = session.call_tool(
                            str(parameters.get("name") or ""),
                            arguments if isinstance(arguments, dict) else {},
                        )
                    else:
                        raise RuntimeError(f"Unknown MCP host method: {method}")
                    response = {
                        "request_id": request_id,
                        "ok": True,
                        "payload": payload,
                    }
                except BaseException as exc:
                    response = {
                        "request_id": request_id,
                        "ok": False,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                connection.send(response)
        except (BrokenPipeError, EOFError, OSError):
            pass
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _monitor_process(
        self,
        transition_id: int,
        process: Any,
        status_connection: Any,
        shutdown_event: Any,
    ) -> None:
        start_deadline = time.monotonic() + MCP_START_TIMEOUT_SECONDS
        received_terminal = False
        while process.is_alive() or status_connection.poll(0.1):
            if status_connection.poll(0.1):
                try:
                    event = status_connection.recv()
                except EOFError:
                    break
                if isinstance(event, dict):
                    name = str(event.get("event") or "")
                    if name in {"error", "stopped"}:
                        received_terminal = True
                    self._handle_server_event(transition_id, event)
            with self._lock:
                starting = self._state == ControllerState.STARTING_MCP
            if starting and time.monotonic() >= start_deadline:
                shutdown_event.set()
                self._fail_start(
                    transition_id,
                    "MCP server did not begin listening before the start timeout.",
                )
                break
        process.join(timeout=MCP_STOP_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
        bridge = self._bridge_thread
        if bridge is not None and bridge is not threading.current_thread():
            # The internal agent remains disabled until the host-side execution
            # path has returned from its current call. Cancellation-aware tools
            # return promptly; a non-interruptible native call is allowed to
            # finish rather than overlap a newly enabled controller.
            bridge.join()
        try:
            status_connection.close()
        except Exception:
            pass
        with self._lock:
            desired = self._desired_mode
            state = self._state
        if desired == ControlMode.MCP and state in {
            ControllerState.STARTING_MCP,
            ControllerState.MCP,
        }:
            message = (
                "MCP server stopped unexpectedly."
                if received_terminal
                else f"MCP server process exited with code {process.exitcode}."
            )
            self._fail_start(transition_id, message)
        self._finish_process(process)

    def _handle_server_event(
        self, transition_id: int, event: dict[str, Any]
    ) -> None:
        name = str(event.get("event") or "")
        if name == "listening":
            with self._lock:
                if (
                    transition_id != self._transition_id
                    or self._desired_mode != ControlMode.MCP
                ):
                    if self._shutdown_event is not None:
                        self._shutdown_event.set()
                    return
                self._state = ControllerState.MCP
                self._connection_state = "listening"
            self._emit("mcp_enabled")
            return
        if name == "client_activity":
            with self._lock:
                self._active_requests = max(0, int(event.get("active_requests") or 0))
                self._last_activity_at = time.time()
                if self._state == ControllerState.MCP:
                    self._connection_state = (
                        "active" if self._active_requests else "listening"
                    )
            self._emit("mcp_connection_changed")
            return
        if name == "tool_list_changed":
            self._emit("mcp_tool_list_changed")
            return
        if name == "error":
            self._fail_start(
                transition_id,
                str(event.get("error") or "MCP server failed."),
            )

    def _fail_start(self, transition_id: int, message: str) -> None:
        with self._lock:
            if transition_id != self._transition_id:
                return
            self._last_error = str(message)
            self._desired_mode = ControlMode.INTERNAL
            self._state = (
                ControllerState.STOPPING_MCP
                if self._process is not None
                else ControllerState.INTERNAL
            )
            self._connection_state = "error"
            self._active_requests = 0
            if self._tool_cancellation is not None:
                self._tool_cancellation.set()
            if self._shutdown_event is not None:
                self._shutdown_event.set()
        self._emit("mcp_error", rollback_preference=True)

    def _finish_process(self, process: Any) -> None:
        restart = False
        restart_transition = 0
        event = "internal_agent_enabled"
        with self._lock:
            if self._process is not process:
                return
            self._active_requests = 0
            self._process = None
            self._host_connection = None
            self._status_connection = None
            self._shutdown_event = None
            self._surface_generation = None
            self._tool_cancellation = None
            self._bridge_thread = None
            if self._monitor_thread is threading.current_thread():
                self._monitor_thread = None
            if self._desired_mode == ControlMode.MCP:
                self._transition_id += 1
                restart_transition = self._transition_id
                self._state = ControllerState.STARTING_MCP
                self._connection_state = "starting"
                self._last_error = ""
                restart = True
                event = "mcp_starting"
            else:
                self._state = ControllerState.INTERNAL
                if self._connection_state != "error":
                    self._connection_state = "disabled"
        self._emit(event)
        if restart:
            start_thread = threading.Thread(
                target=self._start_when_internal_stops,
                args=(restart_transition,),
                name="VibeCAD-MCP-Restart",
                daemon=True,
            )
            with self._lock:
                self._start_thread = start_thread
            start_thread.start()

    def notify_tool_surface_changed(self, workbench_name: str | None = None) -> None:
        with self._lock:
            current = str(workbench_name or "").strip()
            previous = self._surface_workbench
            if current:
                self._surface_workbench = current
            if previous and current:
                from VibeCADModelingSurface import share_authoring_surface

                if share_authoring_surface(previous, current):
                    return
            generation = self._surface_generation
            if generation is None:
                return
            with generation.get_lock():
                generation.value += 1

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            # Qt may already have stopped dispatching queued callbacks. The
            # process and bridge still shut down, but no worker is allowed to
            # synchronously wait on a UI refresh during application teardown.
            self._event_callback = None
        self.request_mcp_enabled(False)
        start = self._start_thread
        if wait and start is not None and start is not threading.current_thread():
            start.join(timeout=MCP_START_TIMEOUT_SECONDS + 1.0)
        monitor = self._monitor_thread
        if wait and monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=MCP_STOP_TIMEOUT_SECONDS + 2.0)


_controller = VibeCADControlModeController()


def get_control_mode_controller() -> VibeCADControlModeController:
    return _controller


def internal_agent_allowed() -> bool:
    return _controller.internal_agent_allowed()


def require_internal_agent() -> None:
    _controller.require_internal_agent()
