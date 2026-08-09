# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact targets for the human Sketch Chamfer command."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeSketchCurveEditTarget import (
    SketchCurveEditCorner as SketchChamferCorner,
    SketchCurveEditCurve as SketchChamferCurve,
    SketchCurveEditPair as SketchChamferCurvePair,
    SketchCurveEditSpec as SketchChamferSpec,
    prepare_sketch_curve_edit_target,
)


__all__ = (
    "SketchChamferCorner",
    "SketchChamferCurve",
    "SketchChamferCurvePair",
    "SketchChamferSpec",
    "prepare_sketch_chamfer_target",
)


LABEL = "Sketch Chamfer"


def prepare_sketch_chamfer_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchChamferSpec:
    return prepare_sketch_curve_edit_target(
        document_uid,
        value,
        label=LABEL,
    )
