# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Drawing rich annotations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeDrawingRichAnnotationRuntime import (
    NativeDrawingRichAnnotationRuntime,
)
from VibeCADNativeDrawingRichAnnotationSchema import (
    DRAWING_NOTE_CAPABILITY_NAMES,
)


def _execute(call: Any, *, content_kind: str) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeDrawingRichAnnotationRuntime):
        raise TypeError("A Drawing rich annotation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Drawing rich annotation call requires argument data.")
    return runtime.execute(
        arguments,
        ticket=getattr(call, "ticket", None),
        content_kind=content_kind,
    )


def _execute_plain(call: Any) -> Mapping[str, Any]:
    return _execute(call, content_kind="plain_text")


def _execute_rich(call: Any) -> Mapping[str, Any]:
    return _execute(call, content_kind="safe_html")


def register_drawing_rich_annotation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name, execute in zip(
        DRAWING_NOTE_CAPABILITY_NAMES,
        (_execute_plain, _execute_rich),
        strict=True,
    ):
        registry.register_implementation(
            NativeCapabilityImplementation(name, execute)
        )


def drawing_rich_annotation_runtime_bindings(
    runtime: NativeDrawingRichAnnotationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeDrawingRichAnnotationRuntime):
        raise TypeError("runtime must be a NativeDrawingRichAnnotationRuntime")
    return {name: runtime for name in DRAWING_NOTE_CAPABILITY_NAMES}
