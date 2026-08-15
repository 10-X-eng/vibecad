# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for deterministic CAM operation state changes."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureDressupArraySchema import (
    ARRAY_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupAxisMapSchema import (
    AXIS_MAP_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupDogboneSchema import (
    DOGBONE_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupDragKnifeSchema import (
    DRAG_KNIFE_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupLeadInOutSchema import (
    LEAD_IN_OUT_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupMirrorSchema import (
    MIRROR_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupPathBoundarySchema import (
    PATH_BOUNDARY_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupRampEntrySchema import (
    RAMP_ENTRY_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupTagSchema import (
    TAG_DRESSUP_PARAMETERS_SCHEMA,
)
from VibeCADNativeManufactureDressupZCorrectSchema import (
    Z_CORRECT_DRESSUP_PARAMETERS_SCHEMA,
)


MANUFACTURE_MODIFY_CAPABILITY_NAME = "manufacture.modify"
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_JOB = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_ACTIVE_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_active": {
            "type": "boolean",
            "description": "Active state observed in the exact Job snapshot.",
        },
        "active": {
            "type": "boolean",
            "description": "Explicit desired state; this operation never performs a blind toggle.",
        },
    },
    ("object_name", "expected_active", "active"),
)
_COPY_JOB = _closed(
    {
        "job": _EXACT_JOB,
        "operation_names": {
            "type": "array",
            "items": _OBJECT_NAME,
            "minItems": 1,
            "maxItems": 64,
            "uniqueItems": True,
            "description": (
                "Exact Job operation-group object names to copy. Names are outputs, "
                "not underlying dress-up bases."
            ),
        },
    },
    ("job", "operation_names"),
)


def manufacture_modify_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_MODIFY_CAPABILITY_NAME,
        description=(
            "Modify exact Job-owned CAM operations: set explicit Active states or copy "
            "complete source-preserving History closures without changing human selection."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_active",
                description=(
                    "Set one through 64 named operations to explicit Active states in "
                    "one transaction. The exact Job hash protects the complete ordered "
                    "operation graph, and each target includes its expected prior state."
                ),
                action_ids=frozenset({"CAM_OpActiveToggle"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobAndOperationActiveStates",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "job": _EXACT_JOB,
                        "targets": {
                            "type": "array",
                            "items": _ACTIVE_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "description": (
                                "Distinct operation-group entries from the exact Job; "
                                "all entries sharing one dress-up base must be included."
                            ),
                        },
                    },
                    ("job", "targets"),
                ),
            ),
            NativeCapabilityVariant(
                operation="copy_operations",
                description=(
                    "Copy one through 64 exact Job-owned CAM operations atomically. "
                    "Semantic History closure preserves dress-ups and owned geometry; "
                    "multiple outputs become one owned History step."
                ),
                action_ids=frozenset({"CAM_OperationCopy"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamOperationCopySet",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "jobs": {
                            "type": "array",
                            "items": _COPY_JOB,
                            "minItems": 1,
                            "maxItems": 8,
                            "description": (
                                "Distinct exact Jobs and their selected operation names; "
                                "the total across all Jobs may not exceed 64."
                            ),
                        },
                    },
                    ("jobs",),
                ),
            ),
            NativeCapabilityVariant(
                operation="array_dressup",
                description=(
                    "Replace one exact current Job operation with a parametric Linear-1D, "
                    "Linear-2D, or Polar Array dress-up. Jitter is explicit and seeded."
                ),
                action_ids=frozenset({"CAM_DressupArray"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobOperationAndArrayDressupPattern",
                transaction_behavior="document",
                background_required=False,
                parameters=ARRAY_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="axis_map_dressup",
                description=(
                    "Replace one exact current Job operation with an Axis Map dress-up. "
                    "The linear input, rotary output, wrap radius, and direction are explicit."
                ),
                action_ids=frozenset({"CAM_DressupAxisMap"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobOperationAndAxisMapParameters",
                transaction_behavior="document",
                background_required=False,
                parameters=AXIS_MAP_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="dogbone_dressup",
                description=(
                    "Replace one exact current Job operation with a Dogbone or shipped "
                    "T-bone corner-relief dress-up. Side, incision rule, closed-profile "
                    "filtering, and disabled placed-path corner groups are explicit."
                ),
                action_ids=frozenset({"CAM_DressupDogbone"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndDogboneReliefDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=DOGBONE_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="drag_knife_dressup",
                description=(
                    "Replace one exact current Job operation with Drag Knife blade-tip "
                    "compensation. The corner filter, physical blade offset, and safe "
                    "absolute pivot height are explicit."
                ),
                action_ids=frozenset({"CAM_DressupDragKnife"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndDragKnifeCompensation"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=DRAG_KNIFE_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="lead_in_out_dressup",
                description=(
                    "Replace one exact current Job operation with entry and exit motion. "
                    "Each side is one closed shipped-style request with only its meaningful "
                    "parameters."
                ),
                action_ids=frozenset({"CAM_DressupLeadInOut"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndLeadInOutMotionDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=LEAD_IN_OUT_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="path_boundary_dressup",
                description=(
                    "Replace one exact current Job operation with a Path Boundary "
                    "dress-up and one explicit owned clipping solid."
                ),
                action_ids=frozenset({"CAM_DressupPathBoundary"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndPathBoundaryDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=PATH_BOUNDARY_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="mirror_dressup",
                description=(
                    "Replace one exact current Job operation with a Mirror dress-up. "
                    "Reflect about the global origin, one exact Job model's global "
                    "bounds center, or one exact axis-aligned reference subelement."
                ),
                action_ids=frozenset({"CAM_DressupMirror"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndMirrorPlacementDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=MIRROR_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="ramp_entry_dressup",
                description=(
                    "Replace one exact current Job operation with bounded Ramp Entry "
                    "motion. The physical entry strategy, angle from vertical, and "
                    "optional absolute-Z activation threshold are explicit."
                ),
                action_ids=frozenset({"CAM_DressupRampEntry"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndRampEntryDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=RAMP_ENTRY_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="tag_dressup",
                description=(
                    "Replace one exact current Job operation with editable holding tags. "
                    "Use exact locations, deterministic automatic distribution, or map "
                    "the enabled positions and shape from one exact Tag dress-up."
                ),
                action_ids=frozenset({"CAM_DressupTag"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndHoldingTagDefinition"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=TAG_DRESSUP_PARAMETERS_SCHEMA,
            ),
            NativeCapabilityVariant(
                operation="z_correct_dressup",
                description=(
                    "Replace one exact current Job operation with Z Correction from "
                    "one probe map selected explicitly by the human. The map is "
                    "validated and generated off-thread, then embedded and hash-pinned."
                ),
                action_ids=frozenset({"CAM_DressupZCorrect"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOperationAndHumanAuthorizedProbeMap"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=Z_CORRECT_DRESSUP_PARAMETERS_SCHEMA,
            ),
        ),
    )


def register_manufacture_modify_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_modify_capability_definition())
