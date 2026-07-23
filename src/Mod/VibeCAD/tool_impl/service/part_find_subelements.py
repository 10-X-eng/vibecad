# SPDX-License-Identifier: LGPL-2.1-or-later

"""Generic subelement resolution for any shaped document object."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import partdesign_find_subelements

TOOL_SPEC = deepcopy(partdesign_find_subelements.TOOL_SPEC)
TOOL_SPEC["name"] = "part.find_subelements"
TOOL_SPEC["workbench"] = "PartDesignWorkbench"
TOOL_SPEC["description"] = (
    "Return every face or edge on one explicitly named object that satisfies the "
    "supplied geometric predicates. Works on any object with shape geometry: Part "
    "features, PartDesign Bodies, Draft objects, and imported solids. Results include "
    "native subelement names and measurable geometry; this operation selects nothing "
    "and never chooses one match for the caller."
)


def run(service: Any, **kwargs: Any) -> dict[str, Any]:
    return partdesign_find_subelements.run(service, **kwargs)
