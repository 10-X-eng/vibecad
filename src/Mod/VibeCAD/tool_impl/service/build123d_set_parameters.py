# SPDX-License-Identifier: LGPL-2.1-or-later

"""Change build123d driving parameters without rewriting source."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "build123d.set_parameters",
    "description": (
        "Update only the driving parameters of one inspected build123d model and "
        "rerun its unchanged source. patch uses RFC 7396 JSON merge semantics: "
        "objects merge recursively and null removes a key. Geometry is validated "
        "before accepted FreeCAD geometry changes; failed working parameters remain "
        "inspectable and repairable."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "model_id": {
                "type": "string",
                "pattern": "^[0-9a-f]{32}$",
                "description": "Exact model id returned by core.inspect scope='program'.",
            },
            "expected_revision": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "Exact current revision returned by core.inspect scope='program'.",
            },
            "patch": {
                "type": "object",
                "minProperties": 1,
                "description": "JSON merge patch applied to the current params object.",
                "additionalProperties": True,
            },
        },
        "required": ["model_id", "expected_revision", "patch"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
