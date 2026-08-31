# SPDX-License-Identifier: LGPL-2.1-or-later

"""Managed Codex app-server transport for VibeCAD's OpenAI execution paths.

API-key authentication is supplied through VibeCAD's existing key lookup, while
ChatGPT OAuth remains wholly owned by the pinned Codex app-server. This module
owns the local JSON-RPC connection and its deliberately narrow capabilities.
"""

from __future__ import annotations

import atexit
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping


CODEX_APP_SERVER_VERSION = "0.144.5"
CODEX_APP_SERVER_ENV = "VIBECAD_CODEX_APP_SERVER"
CODEX_HOME_ENV = "VIBECAD_CODEX_HOME"
CODEX_RUNTIME_DIRECTORY = "codex_runtime"
CODEX_RUNTIME_MANIFEST = "runtime.json"
DEFAULT_RPC_TIMEOUT_SECONDS = 30.0
SERVER_REQUEST_SHUTDOWN_GRACE_SECONDS = 0.25
LOGIN_TIMEOUT_SECONDS = 15.0 * 60.0
MAX_SKILL_RESOURCE_BYTES = 256 * 1024
CODEX_OPENAI_PROVIDER_ID = "vibecad_openai"
CODEX_OPENAI_API_KEY_ENV = "VIBECAD_CODEX_OPENAI_API_KEY"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DISABLED_CODEX_FEATURES = (
    "apps",
    "browser_use",
    "code_mode",
    "computer_use",
    "goals",
    "image_generation",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "shell_tool",
    "tool_suggest",
    "unified_exec",
)


class CodexAppServerError(RuntimeError):
    """Raised when the local app-server protocol or process fails."""


@dataclass(frozen=True)
class CodexRuntimeCommand:
    argv: tuple[str, ...]
    executable: Path
    source: str
    version: str | None = None


@dataclass(frozen=True)
class CodexSkill:
    name: str
    description: str
    path: Path


@dataclass
class _PendingRequest:
    event: threading.Event
    result: Any = None
    error: dict[str, Any] | None = None


NotificationHandler = Callable[[str, dict[str, Any]], None]
ServerRequestHandler = Callable[[str, dict[str, Any]], Any]
ServerResponseHandler = Callable[[str], None]


_account_cache_lock = threading.RLock()
_account_cache: dict[str, Any] | None = None


def codex_home() -> Path:
    override = str(os.environ.get(CODEX_HOME_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return root / "VibeCAD" / "Codex"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VibeCAD" / "Codex"
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return root / "VibeCAD" / "codex"


def codex_workspace() -> Path:
    """Return an instruction-free working directory for ephemeral CAD turns."""
    path = codex_home() / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vibecad_thread_config(
    *,
    web_search_enabled: bool = False,
    skills_enabled: bool = False,
    openai_base_url: str | None = None,
    model_context_window: int | None = None,
    model_auto_compact_token_limit: int | None = None,
) -> dict[str, Any]:
    """Build the narrow Codex capability configuration for one VibeCAD turn."""
    config: dict[str, Any] = {
        "web_search": "live" if web_search_enabled else "disabled",
        "include_apps_instructions": False,
        "include_collaboration_mode_instructions": False,
        "include_environment_context": False,
        "include_permissions_instructions": False,
        "orchestrator.mcp.enabled": False,
        "orchestrator.skills.enabled": False,
        "project_doc_fallback_filenames": [],
        "project_doc_max_bytes": 0,
        "skills.bundled.enabled": bool(skills_enabled),
        "skills.include_instructions": bool(skills_enabled),
        "tools.experimental_request_user_input.enabled": False,
    }
    for feature in DISABLED_CODEX_FEATURES:
        config[f"features.{feature}"] = False
    # Current Codex model catalogs can select code-mode-only tool dispatch even
    # when the local feature toggle is false. Core and interactive conversation
    # tools must remain direct: viewport images cannot be wrapped in exec, and
    # a user question must never be held inside a long-running exec cell.
    config["features.code_mode"] = {
        "enabled": False,
        "direct_only_tool_namespaces": ["core", "conversation", "view"],
    }
    if openai_base_url is not None:
        config.update(codex_openai_provider_config(openai_base_url))
    if model_context_window is not None:
        context_window = int(model_context_window)
        if context_window <= 0:
            raise ValueError("model_context_window must be positive.")
        config["model_context_window"] = context_window
    if model_auto_compact_token_limit is not None:
        compact_limit = int(model_auto_compact_token_limit)
        if compact_limit <= 0:
            raise ValueError("model_auto_compact_token_limit must be positive.")
        if model_context_window is not None and compact_limit >= int(
            model_context_window
        ):
            raise ValueError(
                "model_auto_compact_token_limit must be below the context window."
            )
        config["model_auto_compact_token_limit"] = compact_limit
    return config


def codex_openai_provider_config(base_url: str | None = None) -> dict[str, Any]:
    """Return secret-free Codex configuration for VibeCAD's OpenAI API key."""

    clean_base_url = str(base_url or "").strip().rstrip("/")
    return {
        "model_provider": CODEX_OPENAI_PROVIDER_ID,
        f"model_providers.{CODEX_OPENAI_PROVIDER_ID}.name": "OpenAI via VibeCAD",
        f"model_providers.{CODEX_OPENAI_PROVIDER_ID}.base_url": (
            clean_base_url or DEFAULT_OPENAI_BASE_URL
        ),
        f"model_providers.{CODEX_OPENAI_PROVIDER_ID}.env_key": (
            CODEX_OPENAI_API_KEY_ENV
        ),
        f"model_providers.{CODEX_OPENAI_PROVIDER_ID}.requires_openai_auth": False,
        f"model_providers.{CODEX_OPENAI_PROVIDER_ID}.wire_api": "responses",
    }


def personal_codex_skills_root() -> Path:
    """Return the standard personal Codex skills directory."""
    configured_home = str(os.environ.get("CODEX_HOME") or "").strip()
    root = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    return root / "skills"


def bundled_runtime_root() -> Path:
    return Path(__file__).resolve().parent / CODEX_RUNTIME_DIRECTORY


def _runtime_binary_name() -> str:
    return "codex-app-server.exe" if sys.platform == "win32" else "codex-app-server"


def _manifest_version(root: Path) -> str | None:
    path = root / CODEX_RUNTIME_MANIFEST
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexAppServerError(
            f"Invalid bundled Codex runtime manifest: {exc}"
        ) from exc
    version = str(payload.get("version") or "").strip()
    if not version:
        raise CodexAppServerError("Bundled Codex runtime manifest has no version.")
    return version


def resolve_runtime_command() -> CodexRuntimeCommand:
    """Resolve only an explicit development override or the bundled runtime."""

    override = str(os.environ.get(CODEX_APP_SERVER_ENV) or "").strip()
    if override:
        executable = Path(override).expanduser().resolve()
        if not executable.is_file():
            raise CodexAppServerError(
                f"Configured Codex app-server executable does not exist: {executable}"
            )
        if executable.name.lower().startswith("codex-app-server"):
            argv = (str(executable), "--listen", "stdio://")
        else:
            argv = (str(executable), "app-server", "--listen", "stdio://")
        return CodexRuntimeCommand(argv=argv, executable=executable, source="override")

    root = bundled_runtime_root()
    executable = root / _runtime_binary_name()
    if not executable.is_file():
        raise CodexAppServerError(
            "The bundled Codex app-server runtime is missing. Reinstall VibeCAD "
            "with OpenAI Codex support."
        )
    version = _manifest_version(root)
    if version != CODEX_APP_SERVER_VERSION:
        raise CodexAppServerError(
            "The bundled Codex app-server version does not match VibeCAD: "
            f"expected {CODEX_APP_SERVER_VERSION}, found {version or 'no manifest'}."
        )
    return CodexRuntimeCommand(
        argv=(str(executable), "--listen", "stdio://"),
        executable=executable,
        source="bundled",
        version=version,
    )


def runtime_health() -> dict[str, Any]:
    try:
        command = resolve_runtime_command()
    except Exception as exc:
        return {
            "ready": False,
            "version": CODEX_APP_SERVER_VERSION,
            "source": "",
            "error": str(exc),
        }
    return {
        "ready": True,
        "version": command.version or "development override",
        "source": command.source,
        "executable": str(command.executable),
        "error": "",
    }


def _subprocess_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ)
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    environment["CODEX_HOME"] = str(home)
    environment.setdefault("RUST_LOG", "warn")
    environment.setdefault("LOG_FORMAT", "json")
    # Never let ambient credentials select an unintended Codex identity. The
    # chosen VibeCAD API key is reintroduced only under its dedicated variable.
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "CHATGPT_API_KEY",
    ):
        environment.pop(name, None)
    for name, value in dict(overrides or {}).items():
        clean_name = str(name or "").strip()
        clean_value = str(value or "")
        if clean_name and clean_value:
            environment[clean_name] = clean_value
    return environment


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


class CodexAppServerClient:
    """Thread-safe JSON-RPC client over the app-server stdio transport."""

    def __init__(
        self,
        *,
        notification_handler: NotificationHandler | None = None,
        server_request_handler: ServerRequestHandler | None = None,
        command: CodexRuntimeCommand | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._notification_handler = notification_handler
        self._server_request_handler = server_request_handler
        self._server_response_handler: ServerResponseHandler | None = None
        self._command = command
        self._environment = dict(environment or {})
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._write_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._pending: dict[int, _PendingRequest] = {}
        self._next_request_id = 1
        self._closed = threading.Event()
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._server_request_threads: set[threading.Thread] = set()
        self._shutdown_reason = ""
        self._process_exit_code: int | None = None
        self._late_server_response_count = 0
        self._discarded_server_request_count = 0

    @property
    def stderr_tail(self) -> list[str]:
        with self._state_lock:
            return list(self._stderr_lines)

    @property
    def shutdown_details(self) -> dict[str, Any]:
        """Return bounded transport lifecycle diagnostics."""

        with self._state_lock:
            exit_code = self._process_exit_code
            if exit_code is None and self._process is not None:
                exit_code = self._process.poll()
            reason = self._shutdown_reason
            if not reason and exit_code is not None:
                reason = "app-server process exited"
            return {
                "closed": self._closed.is_set(),
                "reason": reason,
                "process_exit_code": exit_code,
                "active_server_request_count": len(self._server_request_threads),
                "late_server_response_count": self._late_server_response_count,
                "discarded_server_request_count": (
                    self._discarded_server_request_count
                ),
                "stderr_tail": list(self._stderr_lines)[-3:],
            }

    @property
    def alive(self) -> bool:
        process = self._process
        return (
            process is not None and process.poll() is None and not self._closed.is_set()
        )

    def __enter__(self) -> "CodexAppServerClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def start(self) -> None:
        with self._state_lock:
            if self.alive:
                return
            if self._process is not None:
                raise CodexAppServerError(
                    "Codex app-server client cannot be restarted."
                )
            command = self._command or resolve_runtime_command()
            startupinfo = None
            if sys.platform == "win32" and hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            try:
                self._process = subprocess.Popen(
                    list(command.argv),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=_subprocess_environment(self._environment),
                    creationflags=_creation_flags(),
                    startupinfo=startupinfo,
                )
            except Exception as exc:
                raise CodexAppServerError(
                    f"Could not start Codex app-server: {exc}"
                ) from exc
            self._reader_thread = threading.Thread(
                target=self._read_stdout,
                name="VibeCAD-Codex-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name="VibeCAD-Codex-stderr",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "vibecad",
                        "title": "VibeCAD",
                        "version": str(
                            os.environ.get("VIBECAD_VERSION") or "development"
                        ),
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout=DEFAULT_RPC_TIMEOUT_SECONDS,
            )
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> Any:
        if not self.alive:
            raise CodexAppServerError("Codex app-server is not running.")
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _PendingRequest(event=threading.Event())
            self._pending[request_id] = pending
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = dict(params)
        self._write_message(message)
        if not pending.event.wait(timeout=max(0.01, float(timeout))):
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"Codex app-server request {method} timed out.")
        if pending.error is not None:
            message = str(pending.error.get("message") or pending.error)
            code = pending.error.get("code")
            raise CodexAppServerError(
                f"Codex app-server {method} failed"
                + (f" ({code})" if code is not None else "")
                + f": {message}"
            )
        return pending.result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.alive:
            raise CodexAppServerError("Codex app-server is not running.")
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        self._write_message(message)

    def set_handlers(
        self,
        *,
        notification_handler: NotificationHandler | None,
        server_request_handler: ServerRequestHandler | None,
    ) -> None:
        """Bind callbacks for the one VibeCAD turn currently using this client."""

        with self._state_lock:
            self._notification_handler = notification_handler
            self._server_request_handler = server_request_handler

    def set_server_response_handler(
        self,
        handler: ServerResponseHandler | None,
    ) -> None:
        """Observe a server-request reply only after it reaches app-server."""

        with self._state_lock:
            self._server_response_handler = handler

    def close(self, *, reason: str = "client shutdown") -> None:
        process = self._process
        if process is None:
            return
        with self._state_lock:
            if not self._shutdown_reason:
                self._shutdown_reason = str(reason or "client shutdown")
            self._closed.set()
        with self._write_lock:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        with self._state_lock:
            self._process_exit_code = process.poll()
        self._fail_pending("Codex app-server connection closed.")
        self._wait_for_server_requests(SERVER_REQUEST_SHUTDOWN_GRACE_SECONDS)

    def _write_message(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        with self._write_lock:
            process = self._process
            if (
                self._closed.is_set()
                or process is None
                or process.stdin is None
                or process.poll() is not None
            ):
                raise CodexAppServerError("Codex app-server input is unavailable.")
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except Exception as exc:
                raise CodexAppServerError(
                    f"Could not write to Codex app-server: {exc}"
                ) from exc

    def _read_stdout(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        try:
            if stream is None:
                return
            for line in stream:
                clean = line.strip()
                if not clean:
                    continue
                try:
                    message = json.loads(clean)
                except Exception as exc:
                    self._record_stderr(
                        f"Invalid app-server JSON: {exc}: {clean[:400]}"
                    )
                    continue
                if not isinstance(message, dict):
                    self._record_stderr(
                        "Codex app-server emitted a non-object message."
                    )
                    continue
                self._dispatch_message(message)
        finally:
            code = process.poll() if process is not None else None
            tail = " | ".join(self.stderr_tail[-3:])
            detail = f"Codex app-server exited with code {code}."
            if tail:
                detail += f" {tail}"
            with self._state_lock:
                if not self._shutdown_reason:
                    self._shutdown_reason = "app-server output stream closed"
                self._process_exit_code = code
                self._closed.set()
            self._fail_pending(detail)

    def _read_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        for line in stream:
            clean = line.strip()
            if clean:
                self._record_stderr(clean)

    def _record_stderr(self, line: str) -> None:
        with self._state_lock:
            self._stderr_lines.append(str(line))

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is not None and not method:
            try:
                numeric_id = int(request_id)
            except (TypeError, ValueError):
                return
            with self._state_lock:
                pending = self._pending.pop(numeric_id, None)
            if pending is None:
                return
            error = message.get("error")
            if isinstance(error, dict):
                pending.error = error
            else:
                pending.result = message.get("result")
            pending.event.set()
            return

        params = message.get("params")
        clean_params = params if isinstance(params, dict) else {}
        if request_id is not None and method:
            thread = threading.Thread(
                target=self._handle_server_request,
                args=(request_id, method, clean_params),
                name=f"VibeCAD-Codex-request-{method}",
                daemon=True,
            )
            with self._state_lock:
                if self._closed.is_set():
                    self._discarded_server_request_count += 1
                    self._stderr_lines.append(
                        f"Discarded {method} request after transport shutdown."
                    )
                    return
                self._server_request_threads.add(thread)
                try:
                    thread.start()
                except Exception:
                    self._server_request_threads.discard(thread)
                    raise
            return
        with self._state_lock:
            notification_handler = self._notification_handler
        if method and notification_handler is not None:
            try:
                notification_handler(method, clean_params)
            except Exception as exc:
                self._record_stderr(f"Notification handler failed for {method}: {exc}")

    def _handle_server_request(
        self,
        request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> None:
        try:
            with self._state_lock:
                if self._closed.is_set():
                    self._discarded_server_request_count += 1
                    self._stderr_lines.append(
                        f"Discarded {method} request after transport shutdown."
                    )
                    return
                server_request_handler = self._server_request_handler
            if server_request_handler is None:
                self._send_server_response(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"VibeCAD does not handle {method}.",
                        },
                    },
                    method=method,
                )
                return
            try:
                result = server_request_handler(method, params)
            except Exception as exc:
                self._send_server_response(
                    {
                        "id": request_id,
                        "error": {"code": -32000, "message": str(exc)},
                    },
                    method=method,
                )
                return
            sent = self._send_server_response(
                {"id": request_id, "result": result},
                method=method,
            )
            if sent:
                with self._state_lock:
                    response_handler = self._server_response_handler
                if response_handler is not None:
                    try:
                        response_handler(method)
                    except Exception as exc:
                        self._record_stderr(
                            f"Server response handler failed for {method}: {exc}"
                        )
        finally:
            current = threading.current_thread()
            with self._state_lock:
                self._server_request_threads.discard(current)

    def _send_server_response(
        self,
        message: dict[str, Any],
        *,
        method: str,
    ) -> bool:
        """Send one server-request reply or quietly retire it after shutdown."""

        with self._state_lock:
            if self._closed.is_set():
                self._late_server_response_count += 1
                self._stderr_lines.append(
                    f"Discarded late {method} response after transport shutdown."
                )
                return False
        try:
            self._write_message(message)
        except CodexAppServerError as exc:
            with self._state_lock:
                self._late_server_response_count += 1
                self._stderr_lines.append(
                    f"Discarded unsendable {method} response: {exc}"
                )
            return False
        return True

    def _wait_for_server_requests(self, timeout: float) -> None:
        """Give detached request handlers a bounded chance to retire."""

        deadline = time.monotonic() + max(0.0, float(timeout))
        current = threading.current_thread()
        while True:
            with self._state_lock:
                threads = [
                    thread
                    for thread in self._server_request_threads
                    if thread is not current and thread.is_alive()
                ]
            if not threads:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return
            threads[0].join(timeout=remaining)

    def _fail_pending(self, message: str) -> None:
        with self._state_lock:
            values = list(self._pending.values())
            self._pending.clear()
        for pending in values:
            pending.error = {"code": -32099, "message": message}
            pending.event.set()


@dataclass(slots=True)
class _ManagedCodexRuntime:
    lease_lock: threading.RLock
    state_lock: threading.RLock
    client: Any = None
    thread_ids: dict[str, str] = field(default_factory=dict)
    prompt_section_digests: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class ManagedCodexSession:
    """Exclusive access to one app-server and one remembered Codex thread."""

    client: Any
    thread_id: str
    _runtime: _ManagedCodexRuntime
    _thread_key: str

    @property
    def previous_prompt_section_digests(self) -> dict[str, str]:
        with self._runtime.state_lock:
            return dict(
                self._runtime.prompt_section_digests.get(self._thread_key) or {}
            )

    def remember_thread(self, thread_id: str) -> None:
        clean = str(thread_id or "").strip()
        if not clean:
            raise ValueError("Codex thread id cannot be empty.")
        with self._runtime.state_lock:
            previous = str(self._runtime.thread_ids.get(self._thread_key) or "")
            if previous and previous != clean:
                self._runtime.prompt_section_digests.pop(self._thread_key, None)
            self._runtime.thread_ids[self._thread_key] = clean
            self.thread_id = clean

    def remember_prompt_section_digests(
        self,
        section_digests: Mapping[str, str],
    ) -> None:
        clean = {
            str(name): str(digest)
            for name, digest in section_digests.items()
            if str(name).strip() and str(digest).strip()
        }
        with self._runtime.state_lock:
            self._runtime.prompt_section_digests[self._thread_key] = clean


_managed_codex_lock = threading.RLock()
_managed_codex_runtimes: dict[tuple[int, str], _ManagedCodexRuntime] = {}


def _set_client_handlers(
    client: Any,
    notification_handler: NotificationHandler | None,
    server_request_handler: ServerRequestHandler | None,
) -> None:
    setter = getattr(client, "set_handlers", None)
    if not callable(setter):
        raise CodexAppServerError(
            "Managed Codex transport cannot rebind turn callbacks."
        )
    setter(
        notification_handler=notification_handler,
        server_request_handler=server_request_handler,
    )


@contextmanager
def managed_codex_session(
    *,
    runtime_key: str,
    thread_key: str,
    client_factory: Callable[..., Any],
    notification_handler: NotificationHandler,
    server_request_handler: ServerRequestHandler,
    environment: Mapping[str, str] | None = None,
):
    """Reuse an app-server thread across turns in one VibeCAD conversation.

    The lease is exclusive because app-server callbacks are bound to the
    currently executing turn. Different human-selected ribbon surfaces retain
    different thread ids while sharing the same managed runtime process.
    """

    clean_runtime = str(runtime_key or "").strip()
    clean_thread = str(thread_key or "").strip()
    if not clean_runtime or not clean_thread or not callable(client_factory):
        raise ValueError("Managed Codex session keys and client factory are required.")
    key = (id(client_factory), clean_runtime)
    with _managed_codex_lock:
        runtime = _managed_codex_runtimes.setdefault(
            key,
            _ManagedCodexRuntime(
                lease_lock=threading.RLock(),
                state_lock=threading.RLock(),
            ),
        )
    with runtime.lease_lock:
        with runtime.state_lock:
            client = runtime.client
        if client is None or not bool(getattr(client, "alive", False)):
            client = client_factory(
                notification_handler=notification_handler,
                server_request_handler=server_request_handler,
                environment=environment,
            )
            client.start()
            with runtime.state_lock:
                runtime.client = client
        else:
            _set_client_handlers(client, notification_handler, server_request_handler)
        with runtime.state_lock:
            remembered_thread_id = str(runtime.thread_ids.get(clean_thread) or "")
        lease = ManagedCodexSession(
            client=client,
            thread_id=remembered_thread_id,
            _runtime=runtime,
            _thread_key=clean_thread,
        )
        try:
            yield lease
        finally:
            _set_client_handlers(client, None, None)


def _take_managed_codex_runtimes() -> list[tuple[Any, tuple[str, ...]]]:
    """Detach managed clients without waiting for an in-flight turn lease."""

    with _managed_codex_lock:
        runtimes = list(_managed_codex_runtimes.values())
        _managed_codex_runtimes.clear()
    detached: list[tuple[Any, tuple[str, ...]]] = []
    for runtime in runtimes:
        with runtime.state_lock:
            client = runtime.client
            thread_ids = tuple(dict.fromkeys(runtime.thread_ids.values()))
            runtime.client = None
            runtime.thread_ids.clear()
            runtime.prompt_section_digests.clear()
        if client is not None:
            detached.append((client, thread_ids))
    return detached


def reset_managed_codex_sessions() -> None:
    """Close managed transports and forget their in-process thread mapping."""

    with _managed_codex_lock:
        runtimes = list(_managed_codex_runtimes.values())
        _managed_codex_runtimes.clear()
    for runtime in runtimes:
        # Explicit reset preserves its graceful behavior and waits for the
        # exclusive turn lease. Application shutdown uses the force-close path
        # below and must never wait here.
        with runtime.lease_lock:
            with runtime.state_lock:
                client = runtime.client
                thread_ids = tuple(dict.fromkeys(runtime.thread_ids.values()))
                runtime.client = None
                runtime.thread_ids.clear()
                runtime.prompt_section_digests.clear()
            if client is None:
                continue
            try:
                if bool(getattr(client, "alive", False)):
                    for thread_id in thread_ids:
                        try:
                            client.request(
                                "thread/delete",
                                {"threadId": thread_id},
                                timeout=5.0,
                            )
                        except Exception:
                            pass
                client.close()
            except Exception:
                pass


def shutdown_managed_codex_sessions() -> None:
    """Force-close managed transports without waiting on active turn ownership."""

    for client, _thread_ids in _take_managed_codex_runtimes():
        try:
            client.close()
        except Exception:
            pass
    try:
        from VibeCADCodexResponses import shutdown_codex_responses_gateways

        shutdown_codex_responses_gateways()
    except Exception:
        pass


atexit.register(shutdown_managed_codex_sessions)


def load_codex_skill_catalog(
    client: CodexAppServerClient,
    *,
    cwd: Path,
) -> dict[str, CodexSkill]:
    """Load enabled skills from the isolated runtime and personal Codex home."""
    extra_roots: list[str] = []
    personal_root = personal_codex_skills_root()
    vibecad_root = codex_home() / "skills"
    if personal_root.is_dir():
        personal_resolved = personal_root.resolve()
        vibecad_resolved = vibecad_root.resolve()
        if personal_resolved != vibecad_resolved:
            extra_roots.append(str(personal_resolved))
    client.request(
        "skills/extraRoots/set",
        {"extraRoots": extra_roots},
        timeout=DEFAULT_RPC_TIMEOUT_SECONDS,
    )
    result = client.request(
        "skills/list",
        {"cwds": [str(cwd)], "forceReload": True},
        timeout=DEFAULT_RPC_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        raise CodexAppServerError("Codex skills/list returned no object.")

    catalog: dict[str, CodexSkill] = {}
    for entry in result.get("data") or []:
        if not isinstance(entry, dict):
            continue
        for item in entry.get("skills") or []:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            raw_path = str(item.get("path") or "").strip()
            if not name or not raw_path:
                continue
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                continue
            existing = catalog.get(name)
            if existing is not None and existing.path != path:
                raise CodexAppServerError(
                    f"Codex returned ambiguous enabled skill name {name!r}."
                )
            catalog[name] = CodexSkill(
                name=name,
                description=description,
                path=path,
            )
    return catalog


def read_codex_skill_resource(
    catalog: dict[str, CodexSkill],
    *,
    name: str,
    resource: str = "SKILL.md",
) -> dict[str, Any]:
    """Read one UTF-8 resource contained by an enabled skill directory."""
    clean_name = str(name or "").strip()
    skill = catalog.get(clean_name)
    if skill is None:
        return {
            "ok": False,
            "error": f"No enabled Codex skill named {clean_name!r}.",
            "available_skills": sorted(catalog),
        }

    clean_resource = str(resource or "SKILL.md").strip().replace("\\", "/")
    relative = Path(clean_resource)
    if (
        not clean_resource
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        return {
            "ok": False,
            "error": "Skill resource must be a relative path inside the skill directory.",
        }

    root = skill.path.parent.resolve()
    try:
        target = (root / relative).resolve(strict=True)
    except OSError as exc:
        return {"ok": False, "error": f"Skill resource is unavailable: {exc}"}
    if target != root and root not in target.parents:
        return {
            "ok": False,
            "error": "Skill resource resolves outside the enabled skill directory.",
        }
    if not target.is_file():
        return {"ok": False, "error": "Skill resource is not a regular file."}
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {"ok": False, "error": f"Skill resource is unreadable: {exc}"}
    if size > MAX_SKILL_RESOURCE_BYTES:
        return {
            "ok": False,
            "error": (
                "Skill resource exceeds the 256 KiB per-resource context limit "
                f"({size} bytes)."
            ),
        }
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "error": f"Skill resource is not readable UTF-8 text: {exc}"}
    return {
        "ok": True,
        "skill": skill.name,
        "resource": target.relative_to(root).as_posix(),
        "content": content,
    }


def update_cached_account(account: dict[str, Any] | None) -> None:
    global _account_cache
    with _account_cache_lock:
        _account_cache = dict(account) if isinstance(account, dict) else None


def cached_account() -> dict[str, Any] | None:
    with _account_cache_lock:
        return dict(_account_cache) if isinstance(_account_cache, dict) else None


def read_account(*, refresh_token: bool = False) -> dict[str, Any]:
    with CodexAppServerClient() as client:
        result = client.request(
            "account/read",
            {"refreshToken": bool(refresh_token)},
            timeout=DEFAULT_RPC_TIMEOUT_SECONDS,
        )
    if not isinstance(result, dict):
        raise CodexAppServerError("Codex account/read returned no object.")
    account = result.get("account")
    update_cached_account(account if isinstance(account, dict) else None)
    return dict(result)


class ChatGPTLoginSession:
    """One cancellable, managed ChatGPT login against the bundled runtime."""

    def __init__(self) -> None:
        self._completed = threading.Event()
        self._cancel_requested = threading.Event()
        self._state_lock = threading.RLock()
        self._login_id = ""
        self._success = False
        self._error = ""
        self._client = CodexAppServerClient(notification_handler=self._notification)

    @property
    def login_id(self) -> str:
        with self._state_lock:
            return self._login_id

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "account/updated":
            auth_mode = params.get("authMode")
            if auth_mode == "chatgpt":
                update_cached_account(
                    {"type": "chatgpt", "planType": params.get("planType")}
                )
            elif auth_mode is None:
                update_cached_account(None)
            return
        if method != "account/login/completed":
            return
        event_login_id = str(params.get("loginId") or "")
        with self._state_lock:
            if self._login_id and event_login_id and event_login_id != self._login_id:
                return
            self._success = bool(params.get("success"))
            self._error = str(params.get("error") or "")
        self._completed.set()

    def start(self, mode: str) -> dict[str, Any]:
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in {"browser", "device"}:
            raise ValueError("ChatGPT login mode must be browser or device.")
        self._client.start()
        params = (
            {
                "type": "chatgpt",
                "useHostedLoginSuccessPage": True,
                "appBrand": "chatgpt",
            }
            if clean_mode == "browser"
            else {"type": "chatgptDeviceCode"}
        )
        result = self._client.request(
            "account/login/start", params, timeout=DEFAULT_RPC_TIMEOUT_SECONDS
        )
        if not isinstance(result, dict):
            raise CodexAppServerError("Codex login/start returned no object.")
        expected_type = "chatgpt" if clean_mode == "browser" else "chatgptDeviceCode"
        if result.get("type") != expected_type or not result.get("loginId"):
            raise CodexAppServerError(
                "Codex login/start returned an unexpected login response."
            )
        with self._state_lock:
            self._login_id = str(result["loginId"])
        return dict(result)

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def wait(self, *, timeout: float = LOGIN_TIMEOUT_SECONDS) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout))
        while not self._completed.wait(0.1):
            if self._cancel_requested.is_set():
                login_id = self.login_id
                if login_id and self._client.alive:
                    self._client.request(
                        "account/login/cancel",
                        {"loginId": login_id},
                        timeout=5.0,
                    )
                raise CodexAppServerError("ChatGPT sign-in was cancelled.")
            if time.monotonic() >= deadline:
                login_id = self.login_id
                if login_id and self._client.alive:
                    self._client.request(
                        "account/login/cancel",
                        {"loginId": login_id},
                        timeout=5.0,
                    )
                raise TimeoutError("ChatGPT sign-in timed out.")
            if not self._client.alive:
                tail = " | ".join(self._client.stderr_tail[-3:])
                raise CodexAppServerError(
                    "Codex app-server stopped during ChatGPT sign-in"
                    + (f": {tail}" if tail else ".")
                )
        with self._state_lock:
            success = self._success
            error = self._error
        if not success:
            raise CodexAppServerError(error or "ChatGPT sign-in failed.")
        result = self._client.request(
            "account/read", {"refreshToken": True}, timeout=DEFAULT_RPC_TIMEOUT_SECONDS
        )
        if not isinstance(result, dict):
            raise CodexAppServerError(
                "Codex account/read returned no object after login."
            )
        account = result.get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise CodexAppServerError(
                "ChatGPT sign-in completed without a ChatGPT subscription account."
            )
        update_cached_account(account)
        return dict(result)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ChatGPTLoginSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def logout_account() -> dict[str, Any]:
    with CodexAppServerClient() as client:
        result = client.request(
            "account/logout", None, timeout=DEFAULT_RPC_TIMEOUT_SECONDS
        )
    update_cached_account(None)
    return dict(result) if isinstance(result, dict) else {"ok": True}


def list_models() -> dict[str, Any]:
    models: list[str] = []
    details: list[dict[str, Any]] = []
    default_model = ""
    cursor: str | None = None
    with CodexAppServerClient() as client:
        while True:
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            result = client.request(
                "model/list", params, timeout=DEFAULT_RPC_TIMEOUT_SECONDS
            )
            if not isinstance(result, dict):
                raise CodexAppServerError("Codex model/list returned no object.")
            for item in result.get("data") or []:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                if model_id and model_id not in models:
                    models.append(model_id)
                    efforts = []
                    for effort in item.get("supportedReasoningEfforts") or []:
                        if not isinstance(effort, dict):
                            continue
                        name = str(effort.get("reasoningEffort") or "").strip()
                        if name and name not in efforts:
                            efforts.append(name)
                    details.append(
                        {
                            "id": model_id,
                            "display_name": str(item.get("displayName") or model_id),
                            "description": str(item.get("description") or ""),
                            "default_reasoning_effort": str(
                                item.get("defaultReasoningEffort") or ""
                            ),
                            "supported_reasoning_efforts": efforts,
                            "input_modalities": list(item.get("inputModalities") or []),
                            "is_default": bool(item.get("isDefault")),
                        }
                    )
                    if item.get("isDefault"):
                        default_model = model_id
            cursor = str(result.get("nextCursor") or "").strip() or None
            if cursor is None:
                break
    return {
        "ok": True,
        "models": models,
        "model_details": details,
        "default_model": default_model,
        "error": None,
    }


def runtime_execution_smoke() -> dict[str, Any]:
    started = time.monotonic()
    with CodexAppServerClient() as client:
        result = client.request(
            "account/read",
            {"refreshToken": False},
            timeout=DEFAULT_RPC_TIMEOUT_SECONDS,
        )
    if not isinstance(result, dict) or "requiresOpenaiAuth" not in result:
        raise CodexAppServerError(
            "Codex app-server account smoke returned invalid data."
        )
    return {
        "version": CODEX_APP_SERVER_VERSION,
        "elapsed_seconds": time.monotonic() - started,
        "account_present": isinstance(result.get("account"), dict),
    }


def platform_runtime_key() -> str:
    machine = platform.machine().lower()
    return f"{sys.platform}:{machine}"
