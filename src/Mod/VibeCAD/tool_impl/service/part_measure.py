# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact native geometric measurements for any shaped document object."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import partdesign_measure

TOOL_SPEC = deepcopy(partdesign_measure.TOOL_SPEC)
TOOL_SPEC["name"] = "part.measure"
TOOL_SPEC["workbench"] = "PartDesignWorkbench"
TOOL_SPEC["description"] = (
    "Measure exact native object/subelement geometry, minimum distance, or direction "
    "angle for any shaped object. Datum points, axes, and planes are measured "
    "analytically in global coordinates; bounded solids and subelements use "
    "OpenCascade. Returns CAD facts only; it does not infer requirement satisfaction "
    "or choose geometry."
)


def run(service: Any, measurement: dict[str, Any]) -> dict[str, Any]:
    return partdesign_measure.run(service, measurement)
