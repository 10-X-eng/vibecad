# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native creation contracts for editable folded/flat sheets."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA, POSITIVE_MM_SCHEMA, NONNEGATIVE_MM_SCHEMA, parameters_schema,
)


SHEETMETAL_CREATE_CAPABILITY_NAME = "sheet_metal.create"
CREATE_FIELDS = {
    "from_source": frozenset({"object_name", "reference_face"}),
    "base_shape": frozenset({"shape_type", "thickness", "bend_radius", "width", "length", "height",
                             "flange_width", "origin", "fill_gaps", "container_name"}),
    "base_from_sketch": frozenset({"object_name", "thickness", "bend_radius", "length",
                                   "bend_side", "midplane", "reverse"}),
    "from_solid": frozenset({"object_name", "subelements", "thickness", "bend_radius", "invert"}),
    "add_flange": frozenset({"object_name", "subelements", "length", "bend_radius", "bend_angle",
                             "invert", "bend_type", "length_spec"}),
    "fold_from_sketch": frozenset({"object_name", "subelements", "sketch_name", "bend_radius", "bend_angle",
                                   "invert", "invert_bend", "k_factor", "position"}),
}
CREATE_DEFAULTS = {"base_shape": {"container_name": None, "flange_width": 8}}


def sheetmetal_create_capability_definition():
    fields = {
        "object_name": {**OBJECT_NAME_SCHEMA,
                        "description": "Exact existing input object: sheet source or cut state for a flange/fold, sketch for a sketch base, or solid for conversion. The result supplies the newly created object's name."},
        "sketch_name": {**OBJECT_NAME_SCHEMA, "description": "Exact editable sketch with one non-construction straight bend line, on the sheet skin, in the same Part/Body as the source."},
        "invert_bend": {"type": "boolean", "description": "Swap which side of the bend line moves; invert controls bend direction separately."},
        "k_factor": {"type": "number", "minimum": 0, "maximum": 1,
                     "description": "Neutral-axis allowance, shared by folding and development; use the sheet material's K-factor."},
        "position": {"type": "string", "enum": ["middle", "backward", "forward"],
                     "description": "Place the bend zone centered on, behind or ahead of the sketch line."},
        "shape_type": {"type": "string", "enum": ["Flat", "L-Shape", "U-Shape", "Tub", "Hat", "Box"]},
        "thickness": POSITIVE_MM_SCHEMA,
        "bend_radius": NONNEGATIVE_MM_SCHEMA,
        "width": POSITIVE_MM_SCHEMA,
        "length": {**POSITIVE_MM_SCHEMA, "description": "Base length, sketch wall extrusion, or added flange length (mm)."},
        "bend_angle": {"type": "number", "exclusiveMinimum": 0, "maximum": 180,
                       "description": "Flange bend angle in degrees; invert reverses its direction."},
        "bend_type": {"type": "string", "enum": ["Material Outside", "Material Inside", "Thickness Outside"]},
        "length_spec": {"type": "string", "enum": ["Leg", "Outer Sharp", "Inner Sharp", "Tangential"]},
        "height": {**POSITIVE_MM_SCHEMA, "description": "Wall height for formed base shapes (mm); retained but unused for Flat."},
        "flange_width": {**POSITIVE_MM_SCHEMA, "default": 8,
                         "description": "Optional return-flange width for Hat/Box (mm); defaults to the ribbon's 8 mm. Unused for other shapes."},
        "origin": {"type": "string", "enum": ["-X,-Y", "-X,0", "-X,+Y", "0,-Y", "0,0", "0,+Y", "+X,-Y", "+X,0", "+X,+Y"],
                   "description": "Base origin alignment in its container's XY frame."},
        "fill_gaps": {"type": "boolean", "description": "Extend adjoining flanges toward their corners."},
        "container_name": {"anyOf": [OBJECT_NAME_SCHEMA, {"type": "null"}], "default": None,
                           "description": "Exact existing Part or empty Body; null creates at document root."},
        "bend_side": {"type": "string", "enum": ["Outside", "Inside", "Middle"]},
        "midplane": {"type": "boolean", "description": "Extrude symmetrically about the sketch plane."},
        "reverse": {"type": "boolean", "description": "Reverse the sketch extrusion direction."},
        "invert": {"type": "boolean", "description": "Reverse the solid conversion or flange bend direction."},
        "subelements": {"type": "array", "minItems": 1, "maxItems": 256, "uniqueItems": True,
                        "items": {"type": "string", "maxLength": 13, "pattern": "^(Face|Edge)[1-9][0-9]{0,8}$"},
                        "description": "Exact folded source topology: from_solid removes Faces/rips Edges; add_flange uses thickness-side Faces or straight boundary Edges."},
    }
    variants = []
    for operation, action, target, description in (
        ("base_shape", "SheetMetal_BaseShape", "NewSheetBaseShape",
         "Create a parametric upstream sheet base in mm. The result reports actual bounds; bend allowances can limit effective dimensions. Use from_source on a planar face to attach shared folded/flat editing."),
        ("base_from_sketch", "SheetMetal_AddBase", "ExactSheetSourceSketch",
         "Create a sheet from an exact editable sketch in this document. Open sketches form bent walls; closed sketches form a thickness extrusion. Retains the sketch and its container. Use from_source to attach shared folded/flat editing."),
        ("from_solid", "SheetMetal_FromSolid", "ExactSheetSourceSolid",
         "Convert a solid using removed faces and rip edges. Retains editable source links and History; validates the result asynchronously. Use from_source for folded/flat state; validity alone does not prove unfoldability."),
        ("add_flange", "SheetMetal_CreateFlange", "ExactSheetFlangeSource",
         "Add a bent flange to exact boundary edges or thickness-side faces on a sheet source or cut state. Retains its input history. Use from_source on a returned reference face for the new folded/flat state."),
        ("fold_from_sketch", "SheetMetal_CreateFold", "ExactSheetFoldSource",
         "Bend a skin along an editable straight-line sketch. Internal tabs need relief cuts and a line across the remaining bridge. Retains sheet/cut and sketch history. Use from_source on a returned face for folded/flat editing."),
    ):
        names = CREATE_FIELDS[operation]
        ribbon_action = {"base_shape": "SheetMetal_CreateBaseShape",
                         "base_from_sketch": "SheetMetal_CreateFromSketch",
                         "from_solid": "SheetMetal_CreateFromSolid",
                         "add_flange": "SheetMetal_CreateFlange",
                         "fold_from_sketch": "SheetMetal_CreateFold"}[operation]
        variant_fields = {name: fields[name] for name in sorted(names)}
        if operation == "fold_from_sketch":
            variant_fields["subelements"] = {"type": "array", "minItems": 1, "maxItems": 1,
                "items": {"type": "string", "maxLength": 13, "pattern": "^Face[1-9][0-9]{0,8}$",
                          "examples": ["Face1"]},
                "description": "One exact planar sheet skin on the folded source, containing the bend line."}
        variants.append(NativeCapabilityVariant(operation=operation, description=description,
            action_ids=frozenset({action, ribbon_action}), surface_ids=frozenset({"sheet_metal"}), exact_target_type=target,
            transaction_behavior="background", background_required=True,
            parameters=parameters_schema(variant_fields,
                tuple(sorted(names - CREATE_DEFAULTS.get(operation, {}).keys())))))
    return NativeCapabilityDefinition(name=SHEETMETAL_CREATE_CAPABILITY_NAME,
        description="Create parametric sheet sources and attach editable folded/flat state, preserving source features and native History.",
        primary_classification="mutation", variants=(NativeCapabilityVariant(
            operation="from_source",
            description="Attach editable folded/flat state to one prepared sheet source and a planar reference face. Keeps the upstream feature, editable parameters and native History.",
            action_ids=frozenset({"SheetMetal_CreateEditable"}),
            surface_ids=frozenset({"sheet_metal"}), exact_target_type="ExactSheetSourceFace",
            transaction_behavior="background", background_required=True,
            parameters=parameters_schema({"object_name": {**OBJECT_NAME_SCHEMA,
                "description": "Exact existing sheet source returned by source creation; use its next_step arguments to attach folded/flat editing."},
                "reference_face": {"type": "string", "pattern": "^Face[1-9][0-9]*$", "maxLength": 32,
                                   "description": "Exact planar sheet skin on the prepared source. Use an observed reference_faces candidate from source_geometry; a thickness end wall cannot be unfolded at the stock gauge."}},
                ("object_name", "reference_face"))), *variants))
