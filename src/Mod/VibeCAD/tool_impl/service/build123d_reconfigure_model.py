# SPDX-License-Identifier: LGPL-2.1-or-later

"""Intentionally replace a build123d model program and public contract."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "build123d.reconfigure_model",
    "description": (
        "Intentionally replace the complete source, parameters, named inputs, and "
        "named output contract of one inspected build123d model. Use only when the "
        "component architecture or interface must change; use edit_source, "
        "set_parameters, or set_inputs for ordinary iteration. The replacement is "
        "persisted as the working revision and geometry-validated before it replaces "
        "the accepted FreeCAD output."
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
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512000,
                "description": "Complete replacement build123d source assigning the new Shape dictionary to result. Make alignment and placement explicit; assert critical datums, dimensions, and selector counts. For a curved or twisted loft, use enough intermediate sections to control its path because a two-section loft connects stations directly. Select dress-up edges immediately from the feature that creates them and keep them in named variables instead of rediscovering final-shape edge indices. Model required root flare or edge transitions into the primary geometry when dress-up clearance is marginal.",
            },
            "parameters": {
                "type": "object",
                "description": "Complete replacement JSON-safe params object.",
                "additionalProperties": True,
            },
            "input_objects": {
                "type": "object",
                "description": "Complete replacement alias-to-FreeCAD-object input map.",
                "propertyNames": {"pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"},
                "additionalProperties": {"type": "string", "minLength": 1},
            },
            "expected_outputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
                "description": "Complete replacement ordered output-name list.",
                "items": {"type": "string", "minLength": 1, "maxLength": 96},
            },
        },
        "required": ["model_id", "expected_revision", "source", "parameters", "input_objects", "expected_outputs"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
