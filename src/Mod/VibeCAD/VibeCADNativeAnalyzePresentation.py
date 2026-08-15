# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, guarded FEM result and post-pipeline presentation state."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeResultState import (
    prepare_result_target,
    result_reference_state,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext


def set_post_auto_recompute(
    context: NativeRuntimeContext,
    *,
    expected_enabled: Any,
    enabled: Any,
) -> dict[str, Any]:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if type(expected_enabled) is not bool or type(enabled) is not bool:
        raise NativeAnalyzeError(
            "Post auto-recompute values must be booleans.",
            error_code="NATIVE_ANALYZE_PRESENTATION_INVALID",
        )
    context.guard()
    import FreeCAD as App

    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem")
    current = bool(preferences.GetBool("PostAutoRecompute", True))
    if current is not expected_enabled:
        raise NativeAnalyzeError(
            "The FEM post auto-recompute preference changed after turn start.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={"current_enabled": current},
        )
    changed = current is not enabled
    if changed:
        preferences.SetBool("PostAutoRecompute", enabled)
    context.guard()
    verified = bool(preferences.GetBool("PostAutoRecompute", not enabled))
    if verified is not enabled:
        if changed:
            preferences.SetBool("PostAutoRecompute", current)
        raise NativeAnalyzeError(
            "The FEM post auto-recompute preference did not retain the requested value.",
            error_code="NATIVE_ANALYZE_PRESENTATION_FAILED",
        )
    return {"changed": changed, "post_auto_recompute": verified}


def present_legacy_result(
    context: NativeRuntimeContext,
    *,
    result: Any,
    field: Any,
    deformation_scale: Any,
    visible: Any,
) -> dict[str, Any]:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    context.guard()
    target = prepare_result_target(context.document, context.document_uid, result)
    if target.kind != "result":
        raise NativeAnalyzeError(
            "Show Result requires one exact legacy mechanical result.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    try:
        from femresult.resultpresentation import (
            apply_result_presentation,
            prepare_result_presentation,
            restore_result_presentation,
        )

        prepared = prepare_result_presentation(
            target.result,
            field,
            deformation_scale,
            visible,
        )
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        available_fields = []
        try:
            from femresult.resultpresentation import (
                available_result_presentation_fields,
            )

            available_fields = list(
                available_result_presentation_fields(target.result)
            )
        except Exception:
            pass
        raise NativeAnalyzeError(
            str(exc),
            error_code="NATIVE_ANALYZE_PRESENTATION_INVALID",
            repair={
                "result": {
                    "object_name": str(target.result.Name),
                    "current_state_sha256": target.expected_state_sha256,
                },
                "available_fields": available_fields,
                "deformation_scale": {"minimum": 0.0, "maximum": 1_000_000.0},
            },
        ) from exc
    context.guard()
    try:
        presentation = apply_result_presentation(prepared)
        context.guard()
    except Exception as exc:
        try:
            restore_result_presentation(prepared)
        except Exception:
            pass
        if isinstance(exc, NativeAnalyzeError):
            raise
        raise NativeAnalyzeError(
            f"The FEM result presentation could not be applied: {exc}",
            error_code="NATIVE_ANALYZE_PRESENTATION_FAILED",
        ) from exc
    previous = prepared.previous
    changed = any(
        (
            previous.get("field") != presentation["field"],
            previous.get("deformation_scale")
            != presentation["deformation_scale"],
            previous.get("visible") is not presentation["visible"],
        )
    )
    return {
        "changed": changed,
        "previous_presentation": previous,
        "presentation": presentation,
        "result": result_reference_state(target.result),
    }
