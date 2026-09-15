# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounded native reads for the shared folded/flat SheetMetal workflow."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


SHEETMETAL_INSPECT_CAPABILITY_NAME = "sheet_metal.inspect"
MAX_PAGE_SIZE = 32
MAX_OFFSET = 1_000_000
MAX_REVISION = 9_007_199_254_740_991


def sheetmetal_inspect_capability_definition():
    target = parameters_schema({
        "document_uid": {"type": "string", "minLength": 1, "maxLength": 128},
        "object_name": OBJECT_NAME_SCHEMA,
    }, ("document_uid", "object_name"))
    paging = {
        "offset": {"type": "integer", "minimum": 0, "maximum": MAX_OFFSET, "default": 0},
        "page_size": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE,
                      "default": MAX_PAGE_SIZE},
        "expected_revision": {"anyOf": [{"type": "integer", "minimum": 0, "maximum": MAX_REVISION},
                                        {"type": "null"}], "default": None,
                              "description": "offset>0 needs first-page structural_revision."},
    }
    descriptions = {
        "list_sheets": "Find shared sheet states with their visibility, including suppressed and future History entries. Page through exact targets; labels need not be unique.",
        "read_sheet": "Read a sheet's current revision, material, dimensions, representation and readiness. Counts identify available History, cuts and mapping regions for further inspection.",
        "list_history": "Page through the sheet's native History chain, exact predecessors, suppression, editor commands and linked sketches.",
        "list_cuts": "Page through native and legacy cuts, including operation IDs, hole dimensions and profile links. Native cuts retain editable History states.",
        "list_regions": "Page through prepared planar/bend mapping regions and their source faces. Inspect read_sheet for the developed coordinate frame and readiness.",
    }
    variants = []
    for operation, description in descriptions.items():
        properties = {} if operation == "read_sheet" else dict(paging)
        required = () if operation == "list_sheets" else ("target",)
        if required:
            properties["target"] = target
        variants.append(NativeCapabilityVariant(
            operation=operation, description=description,
            action_ids=frozenset({"VibeCAD_NativeInspectSheet"}),
            surface_ids=frozenset({"model", "sheet_metal"}),
            exact_target_type=None if operation == "list_sheets" else "ExactSharedSheetState",
            transaction_behavior="none", background_required=False,
            parameters=parameters_schema(properties, required)))
    variants.append(NativeCapabilityVariant(
        operation="prepare",
        description="Prepare a reopened sheet for cuts and export without changing the document. Then inspect regions or edit the sheet.",
        action_ids=frozenset({"VibeCAD_NativeInspectSheet"}),
        surface_ids=frozenset({"model", "sheet_metal"}), exact_target_type="ExactSharedSheetState",
        transaction_behavior="none", background_required=True, provider_supplemental=True,
        parameters=parameters_schema({"target": target}, ("target",))))
    return NativeCapabilityDefinition(
        name=SHEETMETAL_INSPECT_CAPABILITY_NAME,
        description="Inspect sheet geometry and History.",
        primary_classification="read", variants=tuple(variants))
