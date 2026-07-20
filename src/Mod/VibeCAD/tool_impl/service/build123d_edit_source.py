# SPDX-License-Identifier: LGPL-2.1-or-later

"""Apply exact source replacements to one persisted build123d model."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "build123d.edit_source",
    "description": (
        "Surgically edit one existing build123d program without changing its "
        "parameters, input aliases, or output contract. First inspect the model. "
        "Each old_text block must match the inspected source exactly once at its "
        "edit step; otherwise nothing runs. The candidate program is parsed and "
        "geometry-validated before accepted FreeCAD geometry changes. Failed working "
        "revisions remain editable and do not replace the last accepted output."
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
            "edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": 24,
                "description": "Ordered exact source replacements applied to the inspected revision.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Exact current source block that must occur once.",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Replacement block; empty deletes the exact match.",
                        },
                    },
                    "required": ["old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["model_id", "expected_revision", "edits"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
