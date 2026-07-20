# SPDX-License-Identifier: LGPL-2.1-or-later

"""Rebind persisted build123d input aliases to exact FreeCAD objects."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "build123d.set_inputs",
    "description": (
        "Replace only the complete named input-object mapping of one inspected "
        "build123d model and rerun its unchanged source and parameters. Use this "
        "when a component must consume a different exact FreeCAD output; alias "
        "names used by the source must remain present."
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
            "input_objects": {
                "type": "object",
                "description": "Complete replacement map of source aliases to exact shaped FreeCAD object names.",
                "propertyNames": {"pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"},
                "additionalProperties": {"type": "string", "minLength": 1},
            },
        },
        "required": ["model_id", "expected_revision", "input_objects"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
