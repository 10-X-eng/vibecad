# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical modeling measurement tool."""

from copy import deepcopy
from typing import Any

from . import partdesign_measure


TOOL_SPEC = deepcopy(partdesign_measure.TOOL_SPEC)
TOOL_SPEC["name"] = "model.measure"
TOOL_SPEC["description"] = (
    "Measure exact modeled objects or subelements without changing the document."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    return partdesign_measure.run(service, **arguments)
