# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM element-definition operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeElementState import element_definition_state
from VibeCADNativeAnalyzeElementValues import (
    PreparedElementValues,
    apply_element_values,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import (
    _REFERENCE_KINDS,
    element_label,
    prepare_values,
    references_match,
)
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedElementDefinitionTarget,
    PreparedGeometryReference,
    element_definition_target_still_exact,
    geometry_references_still_exact,
    prepare_element_definition_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedElementUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedElementDefinitionTarget
    analysis: Any
    analysis_state_sha256: str
    label: str
    references: tuple[tuple[Any, tuple[str, ...]], ...]
    changed_references: tuple[PreparedGeometryReference, ...] | None
    values: PreparedElementValues | None
    expected_definition: dict[str, Any]


def _owner_analysis(document: Any, element: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and element in tuple(
                obj.Group or ()
            ):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM element definition must belong to exactly one analysis."
        )
    return owners[0]


def _reference_pairs(obj: Any) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    result = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError(
                "The exact FEM element definition has malformed geometry references."
            )
        names = (raw[1],) if isinstance(raw[1], str) else tuple(raw[1] or ())
        result.append((raw[0], tuple(str(name) for name in names)))
    return tuple(result)


def _require_current_history(document: Any, element: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        element not in operations
        or str(getattr(element, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(element, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM element definition is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(element))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM element definition is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def prepare_element_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
    value_field: str,
) -> PreparedElementUpdate:
    prepared_target = prepare_element_definition_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    element = prepared_target.element
    _require_current_history(document, element)
    if not isinstance(changes, Mapping):
        raise NativeAnalyzeError(
            "changes must be one typed element-definition edit object."
        )
    allowed = {"label", "references", value_field}
    if not changes or not set(changes) <= allowed:
        names = ", ".join(sorted(allowed))
        raise NativeAnalyzeError(
            f"changes must contain at least one supported field: {names}."
        )
    current_state = element_definition_state(element)
    label = (
        element_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(element.Label)
    )
    references = _reference_pairs(element)
    changed_references = None
    if "references" in changes:
        changed_references = prepare_geometry_references(
            document,
            document_uid,
            changes["references"],
            allowed_kinds=_REFERENCE_KINDS[kind],
        )
        references = tuple(reference_value(changed_references))
    values = (
        prepare_values(kind, changes[value_field]) if value_field in changes else None
    )
    expected_definition = (
        values.normalized() if values is not None else dict(current_state["definition"])
    )
    current_references = _reference_pairs(element)
    if (
        label == str(element.Label)
        and references == current_references
        and expected_definition == current_state["definition"]
    ):
        raise NativeAnalyzeError(
            "The requested FEM element-definition edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    analysis = _owner_analysis(document, element)
    owner_state = analysis_state(analysis)
    return PreparedElementUpdate(
        creation_boundary(document),
        prepared_target,
        analysis,
        owner_state["state_sha256"],
        label,
        references,
        changed_references,
        values,
        expected_definition,
    )


def update_element_definition(
    document: Any,
    prepared: PreparedElementUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedElementUpdate):
        raise TypeError("prepared must be a PreparedElementUpdate")
    require_boundary(document, prepared.boundary)
    if not element_definition_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM element definition changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if (
        analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
    ):
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.changed_references is not None and not geometry_references_still_exact(
        prepared.changed_references
    ):
        raise NativeAnalyzeError(
            "Element-definition reference geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    element = prepared.target.element
    element.Label = prepared.label
    element.References = list(prepared.references)
    if prepared.values is not None:
        apply_element_values(element, prepared.values)
    return NativeMutationDraft(
        value={"element": element, "prepared": prepared},
        recompute_targets=(element,),
        changed=(object_identity(element),),
    )


def verify_element_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    element = draft.value["element"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = element_definition_state(element)
    members = tuple(prepared.analysis.Group or ())
    if (
        not is_live(document, element)
        or str(element.Label) != prepared.label
        or state["element_definition_kind"] != prepared.target.kind
        or state["definition"] != prepared.expected_definition
        or _reference_pairs(element) != prepared.references
        or element not in members
        or analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
        or not bool(element.isValid())
    ):
        raise NativeAnalyzeError(
            "The FEM element-definition edit failed its exact postcondition."
        )
    if prepared.changed_references is not None and (
        not references_match(element, prepared.changed_references)
        or not geometry_references_still_exact(prepared.changed_references)
    ):
        raise NativeAnalyzeError(
            "Element-definition reference geometry changed before commit."
        )
    return {"updated_element_definition": state}
