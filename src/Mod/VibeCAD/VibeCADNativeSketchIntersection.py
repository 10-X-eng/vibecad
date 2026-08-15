# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity external Intersection for the exact open Sketch."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchExternalOperation import (
    PreparedSketchExternalOperation,
    create_sketch_external_operation,
    preflight_sketch_external_operation,
    verify_sketch_external_operation,
)
from VibeCADNativeSketchExternalTarget import (
    SketchExternalSpec,
    prepare_sketch_external_target,
)


LABEL = "Sketch Intersection"
OPERATION = "intersect_external_geometry"
PreparedSketchIntersection = PreparedSketchExternalOperation


def prepare_sketch_intersection(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchExternalSpec:
    return prepare_sketch_external_target(document_uid, value, label=LABEL)


def preflight_sketch_intersection(
    context: NativeRuntimeContext,
    spec: SketchExternalSpec,
) -> PreparedSketchIntersection:
    return preflight_sketch_external_operation(
        context,
        spec,
        label=LABEL,
        operation=OPERATION,
        intersection=True,
    )


def create_sketch_intersection(
    document: Any,
    prepared: PreparedSketchIntersection,
) -> NativeMutationDraft:
    return create_sketch_external_operation(
        document,
        prepared,
        operation=OPERATION,
    )


def verify_sketch_intersection(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_external_operation(
        document,
        draft,
        operation=OPERATION,
    )
