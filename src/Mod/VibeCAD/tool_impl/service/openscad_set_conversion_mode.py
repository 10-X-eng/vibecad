# SPDX-License-Identifier: LGPL-2.1-or-later

"""Select the persisted geometry fidelity for one OpenSCAD model."""

TOOL_SPEC = {
    "name": "openscad.set_conversion_mode",
    "description": (
        "Change one OpenSCAD model between exact analytic BREP conversion and "
        "explicit faceted BREP conversion, then rebuild and validate that revision. "
        "Use faceted_brep only when tessellated geometry is acceptable; VibeCAD never "
        "changes fidelity automatically after an exact conversion failure."
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
            "conversion_mode": {
                "type": "string",
                "enum": ["exact_brep", "faceted_brep"],
                "description": "Required geometry fidelity for this model revision.",
            },
        },
        "required": ["model_id", "expected_revision", "conversion_mode"],
        "additionalProperties": False,
    },
}

RUNNER_HANDLED = True
