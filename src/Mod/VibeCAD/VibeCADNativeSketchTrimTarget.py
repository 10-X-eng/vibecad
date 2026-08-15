# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Trim command."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeSketchCurvePointTarget import (
    SketchCurvePointSelection,
    SketchCurvePointSpec,
    prepare_sketch_curve_point_target,
)


LABEL = "Sketch Trim"
SketchTrimSelection = SketchCurvePointSelection
SketchTrimSpec = SketchCurvePointSpec


def prepare_sketch_trim_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCurvePointSpec:
    return prepare_sketch_curve_point_target(
        document_uid,
        value,
        label=LABEL,
    )
