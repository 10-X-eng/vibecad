# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded edits to one shared folded/flat sheet and its native History."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA, POSITIVE_MM_SCHEMA, NONNEGATIVE_MM_SCHEMA, SIGNED_MM_SCHEMA,
    parameters_schema,
)


SHEETMETAL_EDIT_CAPABILITY_NAME = "sheet_metal.edit"
EDIT_FIELDS = {
    "add_circle": frozenset({"center", "radius", "representation", "region"}),
    "update_circle": frozenset({"operation_id", "center", "radius", "representation", "region"}),
    "add_profile": frozenset({"profile"}),
    "replace_profile": frozenset({"operation_id", "profile"}),
    "set_suppressed": frozenset({"operation_id", "suppressed"}),
    "remove_operation": frozenset({"operation_id"}),
    "set_material": frozenset({"material", "k_factor"}),
    "set_parameters": frozenset({"changes"}),
}
EDIT_REQUIRED = {
    "add_circle": ("center", "radius"), "update_circle": ("operation_id",),
    "add_profile": ("profile",), "replace_profile": ("operation_id", "profile"),
    "set_suppressed": ("operation_id", "suppressed"), "remove_operation": ("operation_id",),
    "set_material": (), "set_parameters": ("changes",),
}


def sheetmetal_edit_capability_definition():
    center = {"type": "array", "minItems": 3, "maxItems": 3, "items": SIGNED_MM_SCHEMA,
              "description": "Center [x,y,z] in document coordinates of the chosen folded/flat representation, in mm."}
    changes = parameters_schema({
        "thickness": POSITIVE_MM_SCHEMA,
        "flange_length": {**POSITIVE_MM_SCHEMA, "description":
            "Source Length/length: base length for base shapes, extrusion length for sketch sheets, or flange length for bend features. Read the inspected property and label."},
        "width": {**POSITIVE_MM_SCHEMA, "description": "Base-shape width (mm), when exposed by sheet inspection."},
        "height": {**POSITIVE_MM_SCHEMA, "description": "Formed base-shape wall height (mm); separate from its base length. Not used for Flat sources."},
        "flange_width": {**POSITIVE_MM_SCHEMA, "description": "Return-flange width (mm) for Hat/Box base shapes."},
        "bend_radius": NONNEGATIVE_MM_SCHEMA,
        "bend_angle": {"anyOf": [
            {"type": "number", "minimum": -180, "exclusiveMaximum": 0},
            {"type": "number", "exclusiveMinimum": 0, "maximum": 180}]},
    }, ())
    changes["minProperties"] = 1
    fields = {
        "center": center, "radius": POSITIVE_MM_SCHEMA,
        "representation": {"type": "string", "enum": ["folded", "flat"], "default": "flat"},
        "region": {"anyOf": [{"type": "integer", "minimum": 0, "maximum": 1_000_000},
                              {"type": "null"}], "default": None,
                   "description": "Required for a folded center: use sheet_metal.inspect list_regions. Omit for flat centers or radius-only edits."},
        "operation_id": {"type": "string", "minLength": 1, "maxLength": 128,
                         "description": "Exact cut operation_id from sheet_metal.inspect list_cuts."},
        "profile": parameters_schema({
            "document_uid": {"type": "string", "minLength": 1, "maxLength": 128},
            "object_name": OBJECT_NAME_SCHEMA}, ("document_uid", "object_name")),
        "suppressed": {"type": "boolean"},
        "material": {"type": "string", "maxLength": 256},
        "k_factor": {"type": "number", "minimum": 0, "maximum": 1},
        "changes": changes,
    }
    descriptions = {
        "add_circle": "Add a through-hole to this sheet in folded or flat coordinates; both representations and native History update together.",
        "update_circle": "Change an existing circular cut's radius and/or center, retaining its History identity and downstream cuts.",
        "add_profile": "Cut with a closed sketch on the developed plane or a planar folded skin. Input-sheet attachments are supported. Creates a downstream cut retaining the editable sketch and bend mapping.",
        "replace_profile": "Replace a native sketch cut's profile with an exact sketch in this document; preserve the cut identity and downstream History.",
        "set_suppressed": "Suppress or restore a native cut without deleting it or its sketch. Future History states must be made current first.",
        "remove_operation": "Remove only a legacy definition cut. Use set_suppressed for native cuts to preserve editable dependencies.",
        "set_material": "Set the sheet's material name and/or K factor. Rebuilds both representations and downstream cuts; this does not select an RMFG catalog material.",
        "set_parameters": "Change supported upstream sheet dimensions (mm) or bend angle (degrees). Base shapes expose separate base width, wall height and applicable return-flange width. Inspect read_sheet for editable dimensions and their exact upstream properties.",
    }
    variants = []
    for operation, names in EDIT_FIELDS.items():
        properties = {name: fields[name] for name in sorted(names)}
        if operation == "update_circle":
            for name in ("center", "radius"):
                properties[name] = {"anyOf": [fields[name], {"type": "null"}], "default": None}
        if operation == "set_material":
            for name in names:
                properties[name] = {"anyOf": [fields[name], {"type": "null"}], "default": None}
        properties["object_name"] = OBJECT_NAME_SCHEMA
        action = {"set_parameters": "SheetMetal_EditParameters", "set_material": "SheetMetal_EditMaterial"}.get(
            operation, "SheetMetal_EditCuts")
        variants.append(NativeCapabilityVariant(
            operation=operation, description=descriptions[operation],
            action_ids=frozenset({action}), surface_ids=frozenset({"sheet_metal"}),
            exact_target_type="ExactSharedSheetState", transaction_behavior="background",
            background_required=True, provider_supplemental=True,
            parameters=parameters_schema(properties, ("object_name", *EDIT_REQUIRED[operation]))))
    return NativeCapabilityDefinition(name=SHEETMETAL_EDIT_CAPABILITY_NAME,
        description="Edit sheet holes, sketch cuts, dimensions, material and suppression. Folded and flat representations update together.",
        primary_classification="mutation", variants=tuple(variants))
