# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Split command."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeSketchCurvePointTarget import (
    SketchCurvePointSelection,
    SketchCurvePointSpec,
    prepare_sketch_curve_point_target,
)


LABEL = "Sketch Split"
SketchSplitSelection = SketchCurvePointSelection
SketchSplitSpec = SketchCurvePointSpec


def prepare_sketch_split_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchCurvePointSpec:
    return prepare_sketch_curve_point_target(
        document_uid,
        value,
        label=LABEL,
    )
