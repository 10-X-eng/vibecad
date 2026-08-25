# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of FEM beam, shell, and 1D fluid definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeElementState import (
    element_definition_kind,
    element_definition_state,
)
from VibeCADNativeAnalyzeElementValues import (
    PreparedElementValues,
    apply_element_values,
    prepare_beam_rotation,
    prepare_beam_section,
    prepare_fluid_section,
    prepare_shell_thickness,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeState import analysis_still_exact, analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_REFERENCE_KINDS = {
    "beam_section": frozenset({"Edge"}),
    "beam_rotation": frozenset({"Edge"}),
    "shell_thickness": frozenset({"Face"}),
    "fluid_section": frozenset({"Edge"}),
}


@dataclass(frozen=True, slots=True)
class PreparedElementCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    kind: str
    label: str
    values: PreparedElementValues


def element_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def prepare_values(kind: str, raw: Any) -> PreparedElementValues:
    if kind == "beam_section":
        return prepare_beam_section(raw)
    if kind == "beam_rotation":
        return prepare_beam_rotation(raw)
    if kind == "shell_thickness":
        return prepare_shell_thickness(raw)
    if kind == "fluid_section":
        return prepare_fluid_section(raw)
    raise NativeAnalyzeError(
        "The requested FEM element definition kind is unavailable."
    )


def prepare_element_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    references: Any,
    value: Any,
) -> PreparedElementCreate:
    if kind not in _REFERENCE_KINDS:
        raise NativeAnalyzeError(
            "The requested FEM element definition kind is unavailable."
        )
    target = prepare_analysis_target(document, document_uid, analysis)
    prepared_references = prepare_geometry_references(
        document,
        document_uid,
        references,
        allowed_kinds=_REFERENCE_KINDS[kind],
    )
    return PreparedElementCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        kind,
        element_label(label),
        prepare_values(kind, value),
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    if kind == "beam_section":
        return ObjectsFem.makeElementGeometry1D(
            document,
            name=document.getUniqueObjectName("ElementGeometry1D"),
        )
    if kind == "beam_rotation":
        return ObjectsFem.makeElementRotation1D(
            document,
            document.getUniqueObjectName("ElementRotation1D"),
        )
    if kind == "shell_thickness":
        return ObjectsFem.makeElementGeometry2D(
            document,
            name=document.getUniqueObjectName("ElementGeometry2D"),
        )
    return ObjectsFem.makeElementFluid1D(
        document,
        document.getUniqueObjectName("ElementFluid1D"),
    )


def create_element_definition(
    document: Any,
    prepared: PreparedElementCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedElementCreate):
        raise TypeError("prepared must be a PreparedElementCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after element-definition preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Element-definition reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    element = _factory(document, prepared.kind)
    if element is None or element_definition_kind(element) != prepared.kind:
        raise NativeAnalyzeError(
            "The FEM element-definition factory returned the wrong object type."
        )
    element.Label = prepared.label
    apply_element_values(element, prepared.values)
    element.References = reference_value(prepared.references)
    prepared.analysis.analysis.addObject(element)
    if element not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The FEM element definition was not added to its analysis."
        )
    publish_operation(document, prepared.boundary, element)
    return NativeMutationDraft(
        value={"element": element, "prepared": prepared},
        recompute_targets=(element, prepared.analysis.analysis),
        created=(object_identity(element),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def references_match(
    obj: Any,
    references: tuple[PreparedGeometryReference, ...],
) -> bool:
    actual = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            return False
        names = (raw[1],) if isinstance(raw[1], str) else tuple(raw[1] or ())
        actual.extend((raw[0], name) for name in names)
    expected = [
        (reference.source, name)
        for reference in references
        for name in reference.subelements
    ]
    if len(actual) != len(expected):
        return False
    for (actual_source, actual_name), (expected_source, expected_name) in zip(
        actual,
        expected,
        strict=True,
    ):
        if actual_source is not expected_source or actual_name != expected_name:
            return False
    return True


def verify_element_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    element = draft.value["element"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, element)
    state = element_definition_state(element)
    if (
        not is_live(document, element)
        or element_definition_kind(element) != prepared.kind
        or str(element.Label) != prepared.label
        or state["definition"] != prepared.values.normalized()
        or not references_match(element, prepared.references)
        or tuple(analysis.Group or ()) != (*prepared.members_before, element)
        or not geometry_references_still_exact(prepared.references)
        or not bool(element.isValid())
    ):
        raise NativeAnalyzeError(
            "The new FEM element definition failed its exact postcondition."
        )
    if analysis_still_exact(analysis, prepared.analysis.expected_state_sha256):
        raise NativeAnalyzeError("The FEM analysis did not record its new member.")
    owner_state = analysis_state(analysis)
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_element_definition": state,
    }
