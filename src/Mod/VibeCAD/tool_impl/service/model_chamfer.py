# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical Body-native edge chamfer tool."""

from copy import deepcopy
from typing import Any

from . import partdesign_chamfer


TOOL_SPEC = deepcopy(partdesign_chamfer.TOOL_SPEC)
TOOL_SPEC["name"] = "model.chamfer"
TOOL_SPEC["description"] = (
    "Bevel exact edges of the active Body with one native parametric chamfer feature."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    return partdesign_chamfer.run(service, **arguments)
