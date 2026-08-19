# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact targets for the human Sketch Fillet command."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeSketchCurveEditTarget import (
    SketchCurveEditCorner as SketchFilletCorner,
    SketchCurveEditCurve as SketchFilletCurve,
    SketchCurveEditPair as SketchFilletCurvePair,
    SketchCurveEditSpec as SketchFilletSpec,
    prepare_sketch_curve_edit_target,
)


__all__ = (
    "SketchFilletCorner",
    "SketchFilletCurve",
    "SketchFilletCurvePair",
    "SketchFilletSpec",
    "prepare_sketch_fillet_target",
)


LABEL = "Sketch Fillet"


def prepare_sketch_fillet_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchFilletSpec:
    return prepare_sketch_curve_edit_target(
        document_uid,
        value,
        label=LABEL,
        size_field="radius_mm",
    )
