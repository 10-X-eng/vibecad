# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Connect, Embed, and Cutout implementation for Model."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_OPERATIONS = frozenset({"connect", "embed", "cutout"})
_CONNECT_FIELDS = frozenset({"sources", "refine", "tolerance_mm"})
_PAIR_FIELDS = frozenset({"base", "tool", "refine", "tolerance_mm"})
_MAX_SOURCES = 32
_MAX_COMPOUND_LEAVES = 256
_MAX_TOLERANCE_MM = 1_000_000.0
_NATIVE_NAMES = {
    "connect": "Connect",
    "embed": "Embed",
    "cutout": "Cutout",
}
_PROXY_TYPES = {
    "connect": "FeatureConnect",
    "embed": "FeatureEmbed",
    "cutout": "FeatureCutout",
}


@dataclass(frozen=True, slots=True)
class PartJoinSpec:
    operation: str
    operand_refs: tuple[NativeObjectRef, ...]
    refine: bool
    tolerance_mm: float


@dataclass(frozen=True, slots=True)
class PreparedJoinOperand:
    current: CurrentPartElement
    direct_shape: Any
    direct_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class PreparedPartJoin:
    spec: PartJoinSpec
    operands: tuple[PreparedJoinOperand, ...]
    presentations: tuple[tuple[Any, bool], ...]


def _object_ref(document_uid: str, value: Any, *, role: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Part Join {role} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _controls(value: Mapping[str, Any]) -> tuple[bool, float]:
    if type(value["refine"]) is not bool:
        raise NativeModelError("Part Join refine must be true or false.")
    raw_tolerance = value["tolerance_mm"]
    if type(raw_tolerance) not in (int, float):
        raise NativeModelError("Part Join tolerance_mm must be a finite number.")
    tolerance = float(raw_tolerance)
    if not math.isfinite(tolerance) or not 0.0 <= tolerance <= _MAX_TOLERANCE_MM:
        raise NativeModelError("Part Join tolerance_mm is outside its allowed range.")
    return value["refine"], tolerance


def prepare_part_join(
    document_uid: str,
    operation: str,
    value: Mapping[str, Any],
) -> PartJoinSpec:
    if operation not in _OPERATIONS or not isinstance(value, Mapping):
        raise NativeModelError("That Part Join operation is unavailable.")
    expected = _CONNECT_FIELDS if operation == "connect" else _PAIR_FIELDS
    if set(value) != expected:
        raise NativeModelError("Part Join fields do not match the selected operation.")
    refine, tolerance = _controls(value)
    if operation == "connect":
        raw_sources = value["sources"]
        if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= _MAX_SOURCES:
            raise NativeModelError("Part Join Connect requires 1 to 32 exact sources.")
        refs = tuple(
            _object_ref(document_uid, item, role="source") for item in raw_sources
        )
    else:
        refs = (
            _object_ref(document_uid, value["base"], role="base"),
            _object_ref(document_uid, value["tool"], role="tool"),
        )
    names = tuple(reference.object_name for reference in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Join operands must be distinct.")
    return PartJoinSpec(operation, refs, refine, tolerance)


def _shape_fingerprint(shape: Any) -> str | None:
    try:
        return hashlib.sha256(shape.exportBrepToString().encode("utf-8")).hexdigest()
    except Exception:
        return None


def _shape_is_exact(current: Any, expected: Any, fingerprint: str | None) -> bool:
    try:
        if (
            current is not None
            and not current.isNull()
            and current.isPartner(expected)
            and current.Placement == expected.Placement
            and str(current.Orientation) == str(expected.Orientation)
        ):
            return True
        return fingerprint is not None and _shape_fingerprint(current) == fingerprint
    except Exception:
        return False


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def _resolve_operand(document: Any, reference: NativeObjectRef, *, role: str):
    current = resolve_current_part_element(
        document,
        reference,
        subelement=None,
        operation=f"Part Join {role}",
    )
    try:
        direct_shape = current.target.Shape
    except Exception as exc:
        raise NativeModelError(f"The Part Join {role} has no directly usable shape.") from exc
    if direct_shape is None or direct_shape.isNull() or not direct_shape.isValid():
        raise NativeModelError(f"The Part Join {role} requires a valid current shape.")
    return PreparedJoinOperand(current, direct_shape, _shape_fingerprint(direct_shape))


def _validate_connect_operands(operands: tuple[PreparedJoinOperand, ...]) -> None:
    from BOPTools import ShapeMerge
    from BOPTools.Utils import compoundLeaves

    if len(operands) == 1:
        shape = operands[0].direct_shape
        if str(shape.ShapeType) != "Compound" or len(shape.childShapes()) < 2:
            raise NativeModelError(
                "Part Join Connect needs at least two shapes or one multi-child Compound."
            )
    leaves = []
    try:
        for operand in operands:
            leaves.extend(compoundLeaves(operand.direct_shape))
    except Exception as exc:
        raise NativeModelError("Part Join Connect could not inspect its source shapes.") from exc
    if not 2 <= len(leaves) <= _MAX_COMPOUND_LEAVES:
        raise NativeModelError("Part Join Connect requires 2 to 256 non-Compound leaves.")
    try:
        dimension = ShapeMerge.dimensionOfShapes(leaves)
    except Exception as exc:
        raise NativeModelError(
            "Part Join Connect requires source shapes of one geometric dimension."
        ) from exc
    if dimension <= 0:
        raise NativeModelError("Part Join Connect cannot connect vertices or empty shapes.")


def preflight_part_join(document: Any, spec: PartJoinSpec) -> PreparedPartJoin:
    import PartGui

    if not isinstance(spec, PartJoinSpec):
        raise TypeError("spec must be a PartJoinSpec")
    operands = tuple(
        _resolve_operand(document, reference, role=f"operand {index}")
        for index, reference in enumerate(spec.operand_refs, start=1)
    )
    targets = tuple(operand.current.target for operand in operands)
    if len(targets) != len({id(target) for target in targets}):
        raise NativeModelError("Part Join operands resolve to duplicate current shapes.")
    if spec.operation == "connect":
        _validate_connect_operands(operands)

    presentations = []
    for operand in operands:
        presentation = PartGui.resolveModelingPresentationObject(operand.current.target)
        if presentation is not None and all(
            existing[0] is not presentation for existing in presentations
        ):
            presentations.append((presentation, _visible(presentation)))
    return PreparedPartJoin(spec, operands, tuple(presentations))


def _prepared_is_exact(
    document: Any,
    prepared: PreparedPartJoin,
    *,
    original_visibility: bool,
) -> bool:
    for operand in prepared.operands:
        if not current_part_element_is_exact(document, operand.current):
            return False
        try:
            direct_shape = operand.current.target.Shape
        except Exception:
            return False
        if not _shape_is_exact(
            direct_shape,
            operand.direct_shape,
            operand.direct_fingerprint,
        ):
            return False
    return not original_visibility or all(
        _visible(presentation) is was_visible
        for presentation, was_visible in prepared.presentations
    )


def _set_operands(result: Any, prepared: PreparedPartJoin) -> None:
    targets = tuple(operand.current.target for operand in prepared.operands)
    if prepared.spec.operation == "connect":
        result.Objects = targets
    else:
        result.Base, result.Tool = targets


def create_part_join(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartJoin,
) -> NativeMutationDraft:
    import PartGui
    from BOPTools import JoinFeatures

    if not isinstance(prepared, PreparedPartJoin):
        raise TypeError("prepared must be a PreparedPartJoin")
    if not _prepared_is_exact(document, prepared, original_visibility=True):
        raise NativeModelError("A Part Join operand changed after preflight.")
    spec = prepared.spec
    native_name = _NATIVE_NAMES[spec.operation]
    factory = getattr(JoinFeatures, f"make{native_name}")
    result = factory(native_name)
    if result is None or result.TypeId != "Part::FeaturePython":
        raise NativeModelError("The Part Join factory returned the wrong object type.")
    result.Label = label
    _set_operands(result, prepared)
    result.Refine = spec.refine
    result.Tolerance = spec.tolerance_mm
    result.Proxy.execute(result)
    result.purgeTouched()
    shape = result.Shape
    if not result.isValid() or shape.isNull() or not shape.isValid():
        raise NativeModelError(
            str(result.getStatusString() or "Part Join did not produce valid geometry.")
        )

    replaced = tuple(
        presentation
        for presentation, was_visible in prepared.presentations
        if was_visible
    )
    if replaced:
        if not PartGui.setModelingReplacedInputs(result, replaced):
            raise NativeModelError("Part Join could not retain its replaced inputs.")
        for presentation in replaced:
            presentation.Visibility = False
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "result": result,
            "replaced": replaced,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
        replaced=tuple(object_identity(item) for item in replaced),
    )


def _property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _exact_controls(result: Any, prepared: PreparedPartJoin) -> bool:
    targets = tuple(operand.current.target for operand in prepared.operands)
    spec = prepared.spec
    if spec.operation == "connect":
        operands_match = tuple(result.Objects) == targets
    else:
        operands_match = result.Base is targets[0] and result.Tool is targets[1]
    return (
        operands_match
        and bool(result.Refine) is spec.refine
        and math.isclose(
            _property_number(result.Tolerance),
            spec.tolerance_mm,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
    )


def verify_part_join(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedPartJoin = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Part::FeaturePython"
        or str(result.Label) != draft.value["label"]
        or not _exact_controls(result, prepared)
        or str(getattr(getattr(result, "Proxy", None), "Type", "") or "")
        != _PROXY_TYPES[spec.operation]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
    ):
        raise NativeModelError("The Part Join result changed its exact controls.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != draft.value["replaced"]
        or not _prepared_is_exact(document, prepared, original_visibility=False)
    ):
        raise NativeModelError("The Part Join retained definition is inconsistent.")
    if any(_visible(presentation) for presentation, _ in prepared.presentations):
        raise NativeModelError("Part Join did not preserve its input visibility state.")

    return {
        "root": object_reference(result),
        "operation": spec.operation,
        "operand_count": len(prepared.operands),
        "refined": spec.refine,
        "tolerance_mm": spec.tolerance_mm,
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
