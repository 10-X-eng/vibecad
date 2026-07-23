# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical modeling subelement lookup."""

from copy import deepcopy
from typing import Any

from . import partdesign_find_subelements


TOOL_SPEC = deepcopy(partdesign_find_subelements.TOOL_SPEC)
TOOL_SPEC["name"] = "model.find_subelements"
TOOL_SPEC["description"] = (
    "Resolve exact face, edge, and vertex names on a modeled shape before a feature operation."
)


def run(service: Any, **arguments: Any) -> dict[str, Any]:
    return partdesign_find_subelements.run(service, **arguments)
