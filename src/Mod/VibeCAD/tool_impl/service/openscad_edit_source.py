# SPDX-License-Identifier: LGPL-2.1-or-later

"""Apply guarded exact edits to persisted OpenSCAD source."""

TOOL_SPEC = {
    "name": "openscad.edit_source",
    "description": (
        "Surgically edit one tracked file in an existing OpenSCAD project. Every "
        "old_text block must match exactly once in the inspected working revision. The candidate is "
        "compiled and geometry-validated before accepted FreeCAD Bodies change; a "
        "failed revision remains inspectable and never replaces accepted geometry."
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
            "source_file": {
                "type": "string",
                "minLength": 1,
                "description": "Exact tracked project-relative .scad path returned by core.inspect scope='program'.",
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
                            "description": "Replacement source block; empty deletes the matched text.",
                        },
                    },
                    "required": ["old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["model_id", "expected_revision", "source_file", "edits"],
        "additionalProperties": False,
    },
}

RUNNER_HANDLED = True
