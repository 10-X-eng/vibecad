# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native presentation controls for the shared folded/flat sheet state."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


SHEETMETAL_VIEW_CAPABILITY_NAME = "sheet_metal.view"


def sheetmetal_view_capability_definition():
    return NativeCapabilityDefinition(
        name=SHEETMETAL_VIEW_CAPABILITY_NAME,
        description="Show folded/flat sheet geometry.",
        primary_classification="view",
        variants=(NativeCapabilityVariant(
            operation="set_representation",
            description=("Set the cached folded/flat display of a sheet from sheet_metal.inspect in this document. "
                         "Suppressed cuts display their active predecessor. "
                         "Preserves geometry, History and visibility."),
            action_ids=frozenset({"VibeCAD_NativeSheetRepresentation", "SheetMetal_ViewFolded", "SheetMetal_ViewFlat"}),
            surface_ids=frozenset({"model", "sheet_metal"}),
            exact_target_type="ExactSharedSheetState",
            transaction_behavior="presentation", background_required=False,
            parameters=parameters_schema({
                "object_name": OBJECT_NAME_SCHEMA,
                "representation": {"type": "string", "enum": ["folded", "flat"]},
            }, ("object_name", "representation"))),))
