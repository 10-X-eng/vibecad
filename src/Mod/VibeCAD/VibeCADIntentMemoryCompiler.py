# SPDX-License-Identifier: LGPL-2.1-or-later

"""Private provider call that compiles durable project intent.

This is deliberately separate from the CAD agent loop.  The provider receives
one forced structured tool and cannot call FreeCAD operations or return prose
that VibeCAD then scrapes.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from VibeCADIntentMemory import (
    active_memory_context,
    compiler_tool_schema,
)
from VibeCADProvider import (
    CancellationCheck,
    ProgressCallback,
    ProviderUnavailable,
    _capture_outbound_request,
    _clear_inherited_sdk_modules,
    _gemini_forced_tool_completion,
    _json_safe,
    _run_provider_subprocess,
    _send_child_error,
)


COMPILER_INSTRUCTIONS = """You maintain VibeCAD Intent Memory.

Call the supplied commit_intent_memory_update function exactly once. Classify
every uncovered conversation turn in the supplied order. Preserve durable
customer intent: intended outcomes, requirements, constraints, accepted
decisions, components, interfaces, mechanisms, manufacturing requirements,
verification obligations, assumptions, open questions, and explicitly rejected
directions. Use concise standalone statements that remain meaningful without the
conversation transcript.

Do not store mutable CAD state, feature progress, object names created during a
run, tool narration, provider errors, apologies, or conversational filler. Do
not silently turn model proposals into user requirements. Use user_explicit only
for a direct user statement, user_confirmed for a proposal the user accepted,
and model_assumption only for an unresolved engineering assumption that must
remain visible. Supersede contradicted entries explicitly. Cite every durable
statement with the exact source turn IDs supplied. Never invent a turn ID.
"""


def _compiler_prompt(
    memory: dict[str, Any],
    uncovered_turns: list[dict[str, Any]],
    legacy_design_markdown: str,
) -> str:
    payload: dict[str, Any] = {
        "intent_memory": active_memory_context(memory),
        "uncovered_turns": uncovered_turns,
    }
    legacy = str(legacy_design_markdown or "").strip()
    if legacy and not memory.get("exists"):
        payload["legacy_design_markdown"] = legacy
        payload["legacy_migration_rule"] = (
            "Use this only as a candidate summary. Retain a statement only when "
            "the supplied conversation provenance supports it."
        )
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _anthropic_compiler_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    _reasoning_effort: str | None,
    timeout_seconds: float | None,
    _max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        import anthropic

        if not api_key:
            raise ProviderUnavailable("No Anthropic API key is configured.")
        schema = compiler_tool_schema()
        tool_name = schema["name"]
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "system": COMPILER_INSTRUCTIONS,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": tool_name,
                    "description": schema["description"],
                    "input_schema": schema["parameters"],
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 2}
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout_seconds is not None and timeout_seconds > 0:
            client_kwargs["timeout"] = timeout_seconds
        _capture_outbound_request(
            context,
            provider="anthropic",
            sdk_call="Anthropic.messages.create.intent_memory",
            turn=1,
            request=request,
            base_url=base_url,
        )
        response = anthropic.Anthropic(**client_kwargs).messages.create(**request)
        calls = [
            block
            for block in list(getattr(response, "content", []) or [])
            if str(getattr(block, "type", "") or "") == "tool_use"
        ]
        if len(calls) != 1:
            raise RuntimeError(
                "Anthropic Intent Memory compiler did not return exactly one "
                "structured tool call."
            )
        call = calls[0]
        if str(getattr(call, "name", "") or "") != tool_name:
            raise RuntimeError("Anthropic compiler called the wrong function.")
        update = getattr(call, "input", None)
        if not isinstance(update, dict):
            raise RuntimeError("Anthropic Intent Memory tool input was not an object.")
        conn.send({"type": "done", "final_output": "", "raw": _json_safe(update)})
    except BaseException as exc:
        _send_child_error(conn, "Anthropic Intent Memory compiler", exc)
    finally:
        conn.close()


def _gemini_compiler_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    _reasoning_effort: str | None,
    timeout_seconds: float | None,
    _max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        update = _gemini_forced_tool_completion(
            prompt=prompt,
            context=context,
            model=model,
            api_key=api_key,
            reasoning_effort=None,
            timeout_seconds=timeout_seconds,
            base_url=base_url,
            instructions=COMPILER_INSTRUCTIONS,
            tool_schema=compiler_tool_schema(),
            operation_label="Intent Memory compiler",
        )
        conn.send({"type": "done", "final_output": "", "raw": update})
    except BaseException as exc:
        _send_child_error(conn, "Google Gemini Intent Memory compiler", exc)
    finally:
        conn.close()


def _codex_compiler(
    *,
    provider: str,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    base_url: str | None,
    cancellation_check: CancellationCheck | None,
    progress_callback: ProgressCallback | None,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    """Compile Intent Memory through one Codex dynamic tool call."""

    from VibeCADCodex import (
        CODEX_OPENAI_API_KEY_ENV,
        CODEX_OPENAI_PROVIDER_ID,
        CodexAppServerClient,
        CodexAppServerError,
        codex_workspace,
        vibecad_thread_config,
    )

    clean_provider = str(provider or "").strip().lower()
    if clean_provider not in {"openai", "chatgpt", "grok"}:
        raise ValueError(f"Unsupported Codex auth provider: {provider!r}.")
    if clean_provider in {"openai", "grok"} and not str(api_key or "").strip():
        raise ProviderUnavailable(
            "No OpenAI API key is configured."
            if clean_provider == "openai"
            else "No Grok / X account is signed in."
        )

    schema = compiler_tool_schema()
    tool_name = str(schema["name"])
    state_lock = threading.RLock()
    completed = threading.Event()
    update: dict[str, Any] | None = None
    call_count = 0
    thread_id = ""
    turn_id = ""
    turn_status = ""
    turn_error = ""

    def notification(method: str, params: dict[str, Any]) -> None:
        nonlocal turn_status, turn_error
        if method != "turn/completed":
            return
        event_thread_id = str(params.get("threadId") or "")
        if thread_id and event_thread_id and event_thread_id != thread_id:
            return
        turn = params.get("turn")
        if isinstance(turn, dict):
            with state_lock:
                turn_status = str(turn.get("status") or "")
                error = turn.get("error")
                if isinstance(error, dict):
                    turn_error = str(error.get("message") or error)
                elif error:
                    turn_error = str(error)
        completed.set()

    def server_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count, update
        if method != "item/tool/call":
            raise CodexAppServerError(
                f"Intent Memory does not permit Codex server request {method}."
            )
        if params.get("namespace") not in (None, "") or params.get("tool") != tool_name:
            raise CodexAppServerError(
                "Intent Memory received the wrong dynamic tool call."
            )
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            raise CodexAppServerError(
                "Intent Memory tool arguments were not an object."
            )
        with state_lock:
            call_count += 1
            if call_count != 1:
                raise CodexAppServerError(
                    "Intent Memory received more than one structured tool call."
                )
            update = _json_safe(arguments)
        return {
            "success": True,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": "Intent Memory update accepted.",
                }
            ],
        }

    client = CodexAppServerClient(
        notification_handler=notification,
        server_request_handler=server_request,
        environment=(
            {CODEX_OPENAI_API_KEY_ENV: str(api_key)}
            if clean_provider in {"openai", "grok"}
            else None
        ),
    )
    timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 300.0
    deadline = time.monotonic() + timeout
    try:
        client.start()
        if clean_provider == "chatgpt":
            account_result = client.request(
                "account/read", {"refreshToken": False}, timeout=30.0
            )
            account = (
                account_result.get("account")
                if isinstance(account_result, dict)
                else None
            )
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                raise ProviderUnavailable(
                    "No ChatGPT subscription is signed in for Intent Memory."
                )
        thread_request: dict[str, Any] = {
            "cwd": str(codex_workspace()),
            "approvalPolicy": "never",
            "allowProviderModelFallback": False,
            "sandbox": "read-only",
            "baseInstructions": COMPILER_INSTRUCTIONS,
            "developerInstructions": (
                "Use only commit_intent_memory_update. Do not call shell, file, "
                "web, plugin, app, or CAD tools."
            ),
            "ephemeral": True,
            "environments": [],
            "dynamicTools": [
                {
                    "type": "function",
                    "name": tool_name,
                    "description": schema["description"],
                    "deferLoading": False,
                    "inputSchema": schema["parameters"],
                }
            ],
            "config": vibecad_thread_config(
                openai_base_url=(
                    str(base_url or "")
                    if clean_provider in {"openai", "grok"}
                    else None
                )
            ),
            "serviceName": "vibecad-intent-memory",
        }
        if clean_provider in {"openai", "grok"}:
            thread_request["modelProvider"] = CODEX_OPENAI_PROVIDER_ID
        if str(model or "").strip():
            thread_request["model"] = str(model).strip()
        _capture_outbound_request(
            context,
            provider=clean_provider,
            sdk_call="codex-app-server.thread/start.intent_memory",
            turn=1,
            request=thread_request,
            base_url=base_url if clean_provider in {"openai", "grok"} else None,
        )
        thread_result = client.request("thread/start", thread_request, timeout=30.0)
        thread = (
            thread_result.get("thread") if isinstance(thread_result, dict) else None
        )
        if not isinstance(thread, dict) or not thread.get("id"):
            raise RuntimeError("Intent Memory Codex thread was not created.")
        thread_id = str(thread["id"])
        turn_request = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "environments": [],
            "effort": "medium",
            "summary": "none",
        }
        _capture_outbound_request(
            context,
            provider=clean_provider,
            sdk_call="codex-app-server.turn/start.intent_memory",
            turn=1,
            request=turn_request,
            base_url=base_url if clean_provider in {"openai", "grok"} else None,
        )
        turn_result = client.request("turn/start", turn_request, timeout=30.0)
        turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
        if not isinstance(turn, dict) or not turn.get("id"):
            raise RuntimeError("Intent Memory Codex turn was not created.")
        turn_id = str(turn["id"])
        while not completed.wait(0.05):
            if cancellation_check is not None and cancellation_check():
                client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=5.0,
                )
                raise ProviderUnavailable("Intent Memory update stopped by user.")
            if time.monotonic() >= deadline:
                client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=5.0,
                )
                raise TimeoutError("Codex Intent Memory update timed out.")
            if not client.alive:
                raise ProviderUnavailable(
                    "Codex app-server stopped during the Intent Memory update."
                )
        with state_lock:
            status = turn_status
            error = turn_error
            structured_update = dict(update) if isinstance(update, dict) else None
            structured_call_count = call_count
        if status != "completed":
            raise ProviderUnavailable(
                error or f"Intent Memory turn ended with {status or 'unknown status'}."
            )
        if structured_call_count != 1 or structured_update is None:
            raise RuntimeError(
                "Codex Intent Memory compiler did not submit exactly one "
                "structured update."
            )
        return structured_update
    except CodexAppServerError as exc:
        raise ProviderUnavailable(str(exc)) from exc
    finally:
        if client.alive and thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=5.0)
            except Exception:
                pass
        client.close()


def compile_intent_memory_update(
    *,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    memory: dict[str, Any],
    uncovered_turns: list[dict[str, Any]],
    legacy_design_markdown: str = "",
    debug_context: dict[str, Any] | None = None,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one isolated, forced-tool compiler request and return its arguments."""
    clean_provider = str(provider or "").strip().lower()
    if clean_provider not in {
        "openai",
        "anthropic",
        "chatgpt",
        "grok",
        "gemini",
    }:
        raise ValueError(f"Unsupported Intent Memory provider: {provider!r}.")
    if not uncovered_turns:
        raise ValueError("Intent Memory compiler requires at least one uncovered turn.")
    context = dict(debug_context or {})
    context["intent_memory_request"] = {
        "provider": clean_provider,
        "model": model,
        "base_revision": memory.get("revision"),
        "uncovered_turn_count": len(uncovered_turns),
    }
    prompt = _compiler_prompt(memory, uncovered_turns, legacy_design_markdown)
    if clean_provider in {"openai", "chatgpt", "grok"}:
        return _codex_compiler(
            provider=clean_provider,
            prompt=prompt,
            context=context,
            model=model,
            api_key=api_key,
            base_url=base_url,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
            timeout_seconds=timeout_seconds,
        )
    result = _run_provider_subprocess(
        prompt=prompt,
        context=context,
        tool_runner=None,
        model=model,
        api_key=api_key,
        reasoning_effort=None,
        timeout_seconds=timeout_seconds,
        max_turns=1,
        base_url=base_url,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
        child_main=(
            _gemini_compiler_child_main
            if clean_provider == "gemini"
            else _anthropic_compiler_child_main
        ),
        provider_label="VibeCAD Intent Memory compiler",
    )
    if not isinstance(result.raw, dict):
        raise RuntimeError("Intent Memory compiler returned no structured update.")
    return dict(result.raw)
