# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical Body-native shell/thickness tool."""

from copy import deepcopy
from typing import Any

from . import partdesign_thickness


TOOL_SPEC = deepcopy(partdesign_thickness.TOOL_SPEC)
TOOL_SPEC["name"] = "model.thickness"
TOOL_SPEC["description"] = (
    "Hollow or thicken the active Body from exact removable faces with one parametric feature."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    return partdesign_thickness.run(service, **arguments)
