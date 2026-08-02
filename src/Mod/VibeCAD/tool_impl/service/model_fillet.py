# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical Body-native edge fillet tool."""

from copy import deepcopy
from typing import Any

from . import partdesign_fillet


TOOL_SPEC = deepcopy(partdesign_fillet.TOOL_SPEC)
TOOL_SPEC["name"] = "model.fillet"
TOOL_SPEC["description"] = (
    "Round exact edges of the active Body with one native parametric fillet feature."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    return partdesign_fillet.run(service, **arguments)
