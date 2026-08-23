# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for FEM assignment highlighting and isolation."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import (
    _ANALYSIS_TARGET,
    _OBJECT_NAME,
    _STATE_SHA256,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_ASSIGNMENT_VIEW_CAPABILITY_NAME = "analyze.assignment_view"
_ASSIGNMENT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def _variant(
    operation: str, description: str, action_id: str, properties: dict
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactFemAssignmentAndViewportPresentation",
        transaction_behavior="presentation",
        background_required=False,
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    )


def analyze_assignment_view_capability_definition() -> NativeCapabilityDefinition:
    target = {"analysis": _ANALYSIS_TARGET, "assignment": _ASSIGNMENT_TARGET}
    return NativeCapabilityDefinition(
        name=ANALYZE_ASSIGNMENT_VIEW_CAPABILITY_NAME,
        description="Show exact FEM assignment targets in the current viewport.",
        primary_classification="view",
        variants=(
            _variant(
                "highlight",
                "Select the exact faces or objects targeted by one assignment.",
                "VibeCAD_AnalyzeHighlightAssignment",
                target,
            ),
            _variant(
                "isolate",
                "Show only the study geometry targeted by one assignment.",
                "VibeCAD_AnalyzeIsolateAssignment",
                target,
            ),
            _variant(
                "restore",
                "Restore the exact visibility and selection saved by assignment isolation.",
                "VibeCAD_AnalyzeRestoreAssignmentView",
                {
                    "restore_token": {
                        "type": "string",
                        "minLength": 32,
                        "maxLength": 32,
                        "pattern": "^[0-9a-f]{32}$",
                    }
                },
            ),
        ),
    )


def register_analyze_assignment_view_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_assignment_view_capability_definition())
