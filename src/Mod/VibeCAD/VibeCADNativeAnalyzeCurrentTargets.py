# SPDX-License-Identifier: LGPL-2.1-or-later

"""Resolve concise provider names to exact current Analyze targets."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import analysis_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef, resolve_object


def _runtime_context(runtime: Any) -> NativeRuntimeContext:
    context = getattr(runtime, "_context", None)
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("An Analyze provider binding requires its document runtime.")
    context.guard()
    return context


def current_state(
    runtime: Any,
    object_name: Any,
    reader: Callable[[Any], Mapping[str, Any]],
) -> tuple[Any, dict[str, Any]]:
    context = _runtime_context(runtime)
    obj = resolve_object(
        context.document,
        NativeObjectRef(context.document_uid, str(object_name or "")),
    )
    state = dict(reader(obj))
    if (
        str(state.get("object_name") or "") != str(obj.Name)
        or len(str(state.get("state_sha256") or "")) != 64
    ):
        raise NativeAnalyzeError("The current Analyze object has no exact state.")
    return obj, state


def current_target(
    runtime: Any,
    object_name: Any,
    reader: Callable[[Any], Mapping[str, Any]],
) -> dict[str, Any]:
    _obj, state = current_state(runtime, object_name, reader)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }


def current_analysis_target(runtime: Any, object_name: Any) -> dict[str, Any]:
    _analysis, state = current_state(runtime, object_name, analysis_state)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
        "expected_member_count": int(state["member_count"]),
    }
