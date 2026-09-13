# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-neutral, structured adversarial design review.

The reviewer runs outside the CAD agent loop. It receives no FreeCAD tools and
must return one forced structured tool call, so review results never depend on
scraping prose.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from jsonschema import Draft202012Validator

from VibeCADProvider import (
    CancellationCheck,
    ProgressCallback,
    ProviderUnavailable,
    _capture_outbound_request,
    _clear_inherited_sdk_modules,
    _gemini_forced_tool_completion,
    _json_safe,
    _provider_reasoning_effort,
    _run_provider_subprocess,
    _send_child_error,
)


REVIEW_TOOL_NAME = "submit_design_review"
REVIEW_INSTRUCTIONS = """You are VibeCAD's independent principal mechanical design reviewer.

Review the supplied customer intent and design draft.
Call submit_design_review exactly once. You have no CAD mutation tools and must
not author geometry. Challenge whether the proposal satisfies the requested
product rather than merely producing easy geometry. Examine architecture,
component boundaries, interfaces, assembly, kinematics, swept clearances, load
and contact paths, stress concentrations, materials, manufacturing process,
tolerances and fits, serviceability, safety, visual/form requirements, and the
verification plan. Use the supplied live facts only as facts; do not invent
dimensions or claim analysis that was not performed.

Mark a finding blocking when construction should not start until it is resolved.
Use revise when any blocking or major finding remains. Ask the user only about a
choice that materially changes geometry or function and cannot be resolved by a
defensible engineering default. Return concise, actionable findings, not a second
design essay and not an approval gate.
"""


REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["ready", "revise"],
        },
        "summary": {"type": "string", "minLength": 1},
        "strengths": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1},
        },
        "findings": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocking", "major", "minor"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "requirements",
                            "architecture",
                            "geometry",
                            "interfaces",
                            "mechanisms",
                            "loads",
                            "materials",
                            "manufacturing",
                            "tolerances",
                            "assembly",
                            "safety",
                            "verification",
                        ],
                    },
                    "issue": {"type": "string", "minLength": 1},
                    "consequence": {"type": "string", "minLength": 1},
                    "required_change": {"type": "string", "minLength": 1},
                },
                "required": [
                    "severity",
                    "category",
                    "issue",
                    "consequence",
                    "required_change",
                ],
                "additionalProperties": False,
            },
        },
        "required_revisions": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "minLength": 1},
        },
        "questions_for_user": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "minLength": 1},
                    "why_it_matters": {"type": "string", "minLength": 1},
                    "recommended_answer": {"type": "string", "minLength": 1},
                },
                "required": [
                    "question",
                    "why_it_matters",
                    "recommended_answer",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "verdict",
        "summary",
        "strengths",
        "findings",
        "required_revisions",
        "questions_for_user",
    ],
    "additionalProperties": False,
}


def _review_tool_schema() -> dict[str, Any]:
    return {
        "name": REVIEW_TOOL_NAME,
        "description": (
            "Submit the independent mechanical-design review as structured "
            "findings and required revisions."
        ),
        "parameters": REVIEW_RESULT_SCHEMA,
    }


def _anthropic_strict_schema(value: Any) -> Any:
    """Compile the review schema to Anthropic's strict-output subset."""

    unsupported = {
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
    }
    if isinstance(value, list):
        return [_anthropic_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _anthropic_strict_schema(item)
        for key, item in value.items()
        if key not in unsupported
    }


def _review_prompt(
    customer_intent: str,
    design_draft: str,
    context: dict[str, Any],
) -> str:
    live_facts = {
        key: context[key]
        for key in (
            "intent_memory",
            "cad_state",
            "working_set",
            "partdesign",
            "sketcher",
            "reference_images",
        )
        if context.get(key) not in (None, "", [], {})
    }
    return json.dumps(
        {
            "customer_intent": customer_intent,
            "design_draft": design_draft,
            "verified_live_context": live_facts,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    )


def _validate_review(review: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(REVIEW_RESULT_SCHEMA).iter_errors(review),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path)
        location = f" at {path}" if path else ""
        raise RuntimeError(f"Design review is invalid{location}: {error.message}")
    severe = any(
        finding.get("severity") in {"blocking", "major"}
        for finding in review["findings"]
    )
    if severe and review["verdict"] != "revise":
        raise RuntimeError(
            "Design review marked a blocking or major finding but returned ready."
        )
    return _json_safe(review)


def _anthropic_review_child_main(
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
        schema = _review_tool_schema()
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "system": REVIEW_INSTRUCTIONS,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": schema["name"],
                    "description": schema["description"],
                    "input_schema": _anthropic_strict_schema(
                        schema["parameters"]
                    ),
                    "strict": True,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema["name"]},
        }
        client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 2}
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout_seconds is not None and timeout_seconds > 0:
            client_kwargs["timeout"] = timeout_seconds
        _capture_outbound_request(
            context,
            provider="anthropic",
            sdk_call="Anthropic.messages.create.design_review",
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
        if len(calls) != 1 or str(getattr(calls[0], "name", "")) != REVIEW_TOOL_NAME:
            raise RuntimeError(
                "Anthropic design reviewer did not submit exactly one review."
            )
        raw_review = getattr(calls[0], "input", None)
        if not isinstance(raw_review, dict):
            raise RuntimeError("Anthropic design review was not an object.")
        review = _validate_review(raw_review)
        conn.send({"type": "done", "final_output": "", "raw": review})
    except Exception as exc:
        conn.send({"type": "error", "error": str(exc)})
    finally:
        conn.close()


def _gemini_review_child_main(
    conn,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    _max_turns: int | None,
    clear_inherited_modules: bool,
    base_url: str | None = None,
) -> None:
    try:
        if clear_inherited_modules:
            _clear_inherited_sdk_modules()
        review = _gemini_forced_tool_completion(
            prompt=prompt,
            context=context,
            model=model,
            api_key=api_key,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            base_url=base_url,
            instructions=REVIEW_INSTRUCTIONS,
            tool_schema=_review_tool_schema(),
            operation_label="design review",
        )
        conn.send(
            {
                "type": "done",
                "final_output": "",
                "raw": _validate_review(review),
            }
        )
    except BaseException as exc:
        _send_child_error(conn, "Google Gemini design reviewer", exc)
    finally:
        conn.close()


def _codex_review(
    *,
    provider: str,
    prompt: str,
    context: dict[str, Any],
    model: str,
    api_key: str | None,
    base_url: str | None,
    reasoning_effort: str | None,
    cancellation_check: CancellationCheck | None,
    timeout_seconds: float | None,
) -> dict[str, Any]:
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

    schema = _review_tool_schema()
    state_lock = threading.RLock()
    completed = threading.Event()
    review: dict[str, Any] | None = None
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
                turn_error = (
                    str(error.get("message") or error)
                    if isinstance(error, dict)
                    else str(error or "")
                )
        completed.set()

    def server_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count, review
        if method != "item/tool/call":
            raise CodexAppServerError(
                f"Design review does not permit Codex server request {method}."
            )
        if params.get("namespace") not in (None, "") or params.get("tool") != REVIEW_TOOL_NAME:
            raise CodexAppServerError("Design reviewer called the wrong tool.")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            raise CodexAppServerError("Design review arguments were not an object.")
        with state_lock:
            call_count += 1
            if call_count != 1:
                raise CodexAppServerError(
                    "Design reviewer submitted more than one review."
                )
            review = _validate_review(arguments)
        return {
            "success": True,
            "contentItems": [
                {"type": "inputText", "text": "Design review accepted."}
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
                    "No ChatGPT subscription is signed in for design review."
                )
        thread_request: dict[str, Any] = {
            "cwd": str(codex_workspace()),
            "approvalPolicy": "never",
            "allowProviderModelFallback": False,
            "sandbox": "read-only",
            "baseInstructions": REVIEW_INSTRUCTIONS,
            "developerInstructions": (
                "Call only submit_design_review. Do not call CAD, shell, file, "
                "web, skill, plugin, app, or computer-control tools."
            ),
            "ephemeral": True,
            "environments": [],
            "dynamicTools": [
                {
                    "type": "function",
                    "name": REVIEW_TOOL_NAME,
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
            "serviceName": "vibecad-design-review",
        }
        if clean_provider in {"openai", "grok"}:
            thread_request["modelProvider"] = CODEX_OPENAI_PROVIDER_ID
        if str(model or "").strip():
            thread_request["model"] = str(model).strip()
        _capture_outbound_request(
            context,
            provider=clean_provider,
            sdk_call="codex-app-server.thread/start.design_review",
            turn=1,
            request=thread_request,
            base_url=base_url if clean_provider in {"openai", "grok"} else None,
        )
        thread_result = client.request("thread/start", thread_request, timeout=30.0)
        thread = (
            thread_result.get("thread")
            if isinstance(thread_result, dict)
            else None
        )
        if not isinstance(thread, dict) or not thread.get("id"):
            raise RuntimeError("Design-review Codex thread was not created.")
        thread_id = str(thread["id"])
        effort = _provider_reasoning_effort(reasoning_effort) or "medium"
        turn_request = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "environments": [],
            "effort": effort,
            "summary": "none",
        }
        _capture_outbound_request(
            context,
            provider=clean_provider,
            sdk_call="codex-app-server.turn/start.design_review",
            turn=1,
            request=turn_request,
            base_url=base_url if clean_provider in {"openai", "grok"} else None,
        )
        turn_result = client.request("turn/start", turn_request, timeout=30.0)
        turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
        if not isinstance(turn, dict) or not turn.get("id"):
            raise RuntimeError("Design-review Codex turn was not created.")
        turn_id = str(turn["id"])
        while not completed.wait(0.05):
            if cancellation_check is not None and cancellation_check():
                client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=5.0,
                )
                raise ProviderUnavailable("Design review stopped by user.")
            if time.monotonic() >= deadline:
                client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=5.0,
                )
                raise TimeoutError("Codex design review timed out.")
            if not client.alive:
                raise ProviderUnavailable(
                    "Codex app-server stopped during design review."
                )
        with state_lock:
            status = turn_status
            error = turn_error
            structured_review = dict(review) if isinstance(review, dict) else None
            structured_call_count = call_count
        if status != "completed":
            raise ProviderUnavailable(
                error or f"Design review ended with {status or 'unknown status'}."
            )
        if structured_call_count != 1 or structured_review is None:
            raise RuntimeError(
                "Codex design reviewer did not submit exactly one review."
            )
        return structured_review
    except CodexAppServerError as exc:
        raise ProviderUnavailable(str(exc)) from exc
    finally:
        if client.alive and thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=5.0)
            except Exception:
                pass
        client.close()


def run_design_review(
    *,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    reasoning_effort: str | None,
    customer_intent: str,
    design_draft: str,
    context: dict[str, Any],
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    clean_provider = str(provider or "").strip().lower()
    if clean_provider not in {
        "openai",
        "anthropic",
        "chatgpt",
        "grok",
        "gemini",
    }:
        raise ValueError(f"Unsupported design-review provider: {provider!r}.")
    prompt = _review_prompt(customer_intent, design_draft, context)
    if clean_provider in {"openai", "chatgpt", "grok"}:
        return _codex_review(
            provider=clean_provider,
            prompt=prompt,
            context=context,
            model=model,
            api_key=api_key,
            base_url=base_url,
            reasoning_effort=reasoning_effort,
            cancellation_check=cancellation_check,
            timeout_seconds=timeout_seconds,
        )
    result = _run_provider_subprocess(
        prompt=prompt,
        context=context,
        tool_runner=None,
        model=model,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
        max_turns=1,
        base_url=base_url,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
        child_main=(
            _gemini_review_child_main
            if clean_provider == "gemini"
            else _anthropic_review_child_main
        ),
        provider_label="VibeCAD design reviewer",
    )
    if not isinstance(result.raw, dict):
        raise RuntimeError("Design reviewer returned no structured result.")
    return _validate_review(dict(result.raw))
