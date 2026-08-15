# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Section preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"operands"})


@dataclass(frozen=True, slots=True)
class PartSectionSpec:
    operands: tuple[NativeObjectRef, NativeObjectRef]


@dataclass(frozen=True, slots=True)
class PreparedPartSection:
    spec: PartSectionSpec
    operands: tuple[CurrentPartElement, CurrentPartElement]
    presentations: tuple[Any, ...]


def prepare_part_section(document_uid: str, value: Mapping[str, Any]) -> PartSectionSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Section definition must contain its exact operands.")
    operands = value["operands"]
    if not isinstance(operands, list) or len(operands) != 2:
        raise NativeModelError("Part Section requires exactly two ordered shape operands.")
    names = []
    for operand in operands:
        if not isinstance(operand, Mapping) or set(operand) != {"object_name"}:
            raise NativeModelError("A Part Section operand target is invalid.")
        names.append(str(operand["object_name"] or ""))
    if names[0] == names[1]:
        raise NativeModelError("Part Section operand targets must be distinct.")
    return PartSectionSpec(
        tuple(NativeObjectRef(document_uid, name) for name in names)
    )


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_section(document: Any, spec: PartSectionSpec) -> PreparedPartSection:
    import PartGui

    if not isinstance(spec, PartSectionSpec):
        raise TypeError("spec must be a PartSectionSpec")
    operands = tuple(
        resolve_current_part_element(
            document,
            reference,
            subelement=None,
            operation="Part Section operand",
        )
        for reference in spec.operands
    )
    if operands[0].target is operands[1].target:
        raise NativeModelError("Part Section operands resolve to the same current shape.")
    presentations = []
    for operand in operands:
        presentation = PartGui.resolveModelingPresentationObject(operand.target)
        if presentation is not None and _visible(presentation):
            if presentation not in presentations:
                presentations.append(presentation)
    return PreparedPartSection(spec, operands, tuple(presentations))


def _copy_line_material(source: Any, result: Any) -> None:
    source_view = getattr(source, "ViewObject", None)
    result_view = getattr(result, "ViewObject", None)
    if source_view is None or result_view is None:
        raise NativeModelError("Part Section could not access its display style.")
    try:
        appearances = tuple(source_view.ShapeAppearance)
        if not appearances:
            raise NativeModelError("The first Part Section operand has no display style.")
        result_view.LineMaterial = appearances[0]
    except NativeModelError:
        raise
    except Exception as exc:
        raise NativeModelError("Part Section could not copy its line material.") from exc


def create_part_section(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartSection,
) -> NativeMutationDraft:
    import PartGui

    for index, operand in enumerate(prepared.operands, start=1):
        if not current_part_element_is_exact(document, operand):
            raise NativeModelError(
                f"Part Section operand {index} changed after preflight."
            )

    result = document.addObject("Part::Section", "Section")
    if result is None or str(getattr(result, "TypeId", "")) != "Part::Section":
        raise NativeModelError("The Part Section factory returned the wrong object type.")
    result.Label = label
    result.Base = prepared.operands[0].target
    result.Tool = prepared.operands[1].target
    result.Approximation = False
    refine = bool(result.Refine)

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
    ):
        raise NativeModelError(
            str(result.getStatusString() or "Part Section did not produce valid geometry.")
        )
    _copy_line_material(prepared.operands[0].target, result)

    PartGui.publishDesignDefinitionBlock((result,))
    if prepared.presentations:
        if not PartGui.setModelingReplacedInputs(result, prepared.presentations):
            raise NativeModelError("Part Section could not retain its replaced inputs.")
        for presentation in prepared.presentations:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "result": result,
            "refine": refine,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_section(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    if document.getObject(result.Name) is not result or result.TypeId != "Part::Section":
        raise NativeModelError("The Part Section result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Part Section result changed its label.")
    if (
        result.Base is not prepared.operands[0].target
        or result.Tool is not prepared.operands[1].target
        or bool(result.Approximation)
        or bool(result.Refine) is not draft.value["refine"]
    ):
        raise NativeModelError("The Part Section result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part Section result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != prepared.presentations
    ):
        raise NativeModelError("The Part Section Design identity is invalid.")
    for index, operand in enumerate(prepared.operands, start=1):
        if not current_part_element_is_exact(document, operand):
            raise NativeModelError(
                f"Part Section operand {index} changed before commit."
            )
    if any(_visible(presentation) for presentation in prepared.presentations):
        raise NativeModelError("A replaced Part Section input became visible before commit.")

    return {
        "root": object_reference(result),
        "shape_type": str(shape.ShapeType),
        "vertex_count": len(shape.Vertexes),
        "edge_count": len(shape.Edges),
        "length_mm": float(shape.Length),
        "refined": bool(result.Refine),
    }
