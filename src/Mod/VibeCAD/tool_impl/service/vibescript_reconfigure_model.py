# SPDX-License-Identifier: LGPL-2.1-or-later

"""Intentionally replace a VibeScript model program and public contract."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "vibescript.reconfigure_model",
    "description": (
        "Intentionally replace the complete source, parameters, and named "
        "output contract of one inspected VibeScript model. Use only when the "
        "component architecture or interface must change; use edit_source or "
        "set_parameters for ordinary iteration, and consult "
        "vibescript.describe_api for the authoring helpers available in "
        "source. The replacement is persisted as the working revision, executed "
        "and validated in an isolated FreeCADCmd worker, then published through "
        "one bounded live-document transaction before it replaces the accepted "
        "FreeCAD output."
        " Existing published outputs and interfaces that have downstream "
        "consumers cannot be removed; the transaction fails with the exact "
        "consumers instead of breaking them."
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
                "description": "Exact model id returned by vibescript.inspect_model.",
            },
            "expected_revision": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "Exact current revision returned by vibescript.inspect_model.",
            },
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512000,
                "description": "Complete replacement VibeScript source assigning the new output dictionary to result and preserving or deliberately revising its top-level interfaces dict. Build every sketch with SketchBuilder so it is fully constrained through named dimensions instead of raw constraint index tuples. Select dress-up edges with EdgeQuery geometric predicates immediately from the feature that creates them and keep them in named variables instead of rediscovering final-shape edge indices. Drive dimensions from params so the same source regenerates deterministically when parameters change.",
            },
            "parameters": {
                "type": "object",
                "description": "Complete replacement flat params object. Every value must be a single finite number (millimetres or degrees); nested objects, arrays, strings, and booleans are rejected. Compute derived values inside source. Every key must be a valid Python identifier not starting with an underscore.",
                "propertyNames": {"pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
                "additionalProperties": {"type": "number"},
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
        "required": [
            "model_id",
            "expected_revision",
            "source",
            "parameters",
            "expected_outputs",
        ],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
