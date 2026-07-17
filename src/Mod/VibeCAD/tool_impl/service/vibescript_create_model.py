# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one persisted VibeScript model from complete initial source."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "vibescript.create_model",
    "description": (
        "Create one persisted, source-parametric VibeScript component model. "
        "Call vibescript.describe_api once before writing the first "
        "source to learn every available helper, the execution namespace, the "
        "import policy, and the budget. Source executes in an isolated FreeCADCmd "
        "worker against a temporary document, uses millimetres, receives doc and "
        "params, and must "
        "assign result to an ordered dict whose keys exactly match "
        "expected_outputs and whose values are document objects each owning "
        "exactly one valid solid. Accepted solids are transferred as exact BREP "
        "and exposed in the user's document through stable published objects; "
        "the worker's PartDesign feature history is not copied into the live "
        "document. Source and parameters remain the editable model authority, so "
        "iterate with edit_source or set_parameters and regenerate. Use one model "
        "for one independently editable "
        "component or coherent subassembly; do not put an entire complex product "
        "in one program. A failed candidate is persisted under its returned "
        "model id so it can be inspected and repaired without recreating the "
        "program."
        " Declare stable functional interfaces in an optional top-level "
        "interfaces dict whenever assemblies, drawings, FEM, or CAM will "
        "reference the output. Each interface names a result output and uses "
        "an origin or expected-count geometric query; exact FaceN/EdgeN names "
        "are forbidden because implementation history is replaceable."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "model_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 96,
                "description": "Unique human-readable label for this component model.",
            },
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512000,
                "description": "Complete initial VibeScript Python source assigning the final output dictionary to result. Build every sketch with SketchBuilder so it is fully constrained through named dimensions instead of raw constraint index tuples. Select dress-up edges with EdgeQuery geometric predicates immediately from the feature that creates them and keep them in named variables instead of rediscovering final-shape edge indices. Drive dimensions from params so the same source regenerates deterministically when parameters change. For every functional mating, datum, load, drawing, or machining reference, also assign interfaces = {name: {output, selection, description}} using selection type origin or a unique expected-count geometric query. Imports are limited to FreeCAD, Part, PartDesign, Sketcher, vibescript_api, and safe stdlib modules.",
            },
            "parameters": {
                "type": "object",
                "description": "Flat object of driving dimensions exposed to source as params. Every value must be a single finite number (millimetres or degrees); nested objects, arrays, strings, and booleans are rejected. Compute derived tables or interpolated values inside source from these scalars. Every key must be a valid Python identifier not starting with an underscore.",
                "propertyNames": {"pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
                "additionalProperties": {"type": "number"},
            },
            "expected_outputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
                "description": "Ordered names of every physical single-solid output returned in result.",
                "items": {"type": "string", "minLength": 1, "maxLength": 96},
            },
        },
        "required": ["model_name", "source", "parameters", "expected_outputs"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
