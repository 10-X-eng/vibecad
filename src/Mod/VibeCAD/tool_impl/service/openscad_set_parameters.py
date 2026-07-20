# SPDX-License-Identifier: LGPL-2.1-or-later

"""Patch one OpenSCAD model's named parameter overrides."""

TOOL_SPEC = {
    "name": "openscad.set_parameters",
    "description": (
        "Apply a JSON merge patch to one OpenSCAD model's named parameter overrides "
        "without rewriting its source. The candidate is compiled and validated before "
        "accepted geometry changes. Use null to remove an override."
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
                "description": "Exact working revision returned by core.inspect scope='program'.",
            },
            "patch": {
                "type": "object",
                "minProperties": 1,
                "description": "JSON merge patch for named scalar or array OpenSCAD parameters.",
                "additionalProperties": True,
            },
        },
        "required": ["model_id", "expected_revision", "patch"],
        "additionalProperties": False,
    },
}

RUNNER_HANDLED = True
