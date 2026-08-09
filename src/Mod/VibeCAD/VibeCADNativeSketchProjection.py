# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic human-parity external Projection for the exact open Sketch."""

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


LABEL = "Sketch Projection"
OPERATION = "project_external_geometry"
PreparedSketchProjection = PreparedSketchExternalOperation


def prepare_sketch_projection(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchExternalSpec:
    return prepare_sketch_external_target(document_uid, value, label=LABEL)


def preflight_sketch_projection(
    context: NativeRuntimeContext,
    spec: SketchExternalSpec,
) -> PreparedSketchProjection:
    return preflight_sketch_external_operation(
        context,
        spec,
        label=LABEL,
        operation=OPERATION,
        intersection=False,
    )


def create_sketch_projection(
    document: Any,
    prepared: PreparedSketchProjection,
) -> NativeMutationDraft:
    return create_sketch_external_operation(
        document,
        prepared,
        operation=OPERATION,
    )


def verify_sketch_projection(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_sketch_external_operation(
        document,
        draft,
        operation=OPERATION,
    )
