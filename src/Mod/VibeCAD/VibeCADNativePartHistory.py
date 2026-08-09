# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact current-History shape resolution shared by standalone Part actions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeTargets import NativeObjectRef, resolve_object


PART_SOURCE_TYPES = frozenset({"Vertex", "Edge", "Wire", "Face", "Shell"})
ALL_PART_SHAPE_TYPES = PART_SOURCE_TYPES | frozenset(
    {"Solid", "CompSolid", "Compound"}
)


@dataclass(frozen=True, slots=True)
class CurrentPartSource:
    target: Any
    presentation: Any
    shape: Any


@dataclass(frozen=True, slots=True)
class CurrentPartEdge:
    target: Any
    shape: Any
    subelement: str | None
    raw_shape: Any | None = None
    global_placement: Any | None = None
    shape_fingerprint: str | None = None
    presentation: Any | None = None


@dataclass(frozen=True, slots=True)
class CurrentPartElement:
    target: Any
    shape: Any
    subelement: str | None
    raw_shape: Any | None = None
    global_placement: Any | None = None
    shape_fingerprint: str | None = None
    presentation: Any | None = None


def resolve_current_part_target(
    document: Any,
    reference: NativeObjectRef,
    *,
    operation: str,
) -> tuple[Any, Any]:
    import PartGui

    visible = resolve_object(document, reference)
    if not PartGui.isModelingObjectActive(visible):
        raise NativeModelError(f"A {operation} target is not active in current History.")
    target = PartGui.resolveModelingObject(visible)
    if target is None or getattr(target, "Document", None) is not document:
        raise NativeModelError(f"A {operation} target has no current modeling state.")
    return visible, target


def resolve_current_part_source(
    document: Any,
    reference: NativeObjectRef,
    *,
    operation: str,
    allowed_types: frozenset[str] = PART_SOURCE_TYPES,
    reject_solid_compounds: bool = True,
) -> CurrentPartSource:
    import Part
    import PartGui

    visible, target = resolve_current_part_target(
        document,
        reference,
        operation=operation,
    )
    try:
        shape = Part.getShape(target, transform=True)
    except Exception as exc:
        raise NativeModelError(f"A {operation} target has no usable Part shape.") from exc
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeModelError(f"A {operation} target must have a valid current shape.")
    shape_type = str(shape.ShapeType)
    if shape_type == "Compound":
        if reject_solid_compounds and (
            list(getattr(shape, "Solids", ()) or [])
            or list(getattr(shape, "CompSolids", ()) or [])
        ):
            raise NativeModelError(f"A {operation} compound cannot contain solids.")
    elif shape_type not in allowed_types:
        raise NativeModelError(f"That current Part shape type cannot be used by {operation}.")
    presentation = PartGui.resolveModelingPresentationObject(target)
    return CurrentPartSource(target, presentation or visible, shape)


def resolve_current_part_edge(
    document: Any,
    reference: NativeObjectRef,
    *,
    subelement: str | None,
    operation: str,
) -> CurrentPartEdge:
    element = resolve_current_part_element(
        document,
        reference,
        subelement=subelement,
        operation=operation,
    )
    if str(element.shape.ShapeType) != "Edge":
        raise NativeModelError(f"The {operation} reference must resolve to one valid edge.")
    return CurrentPartEdge(
        element.target,
        element.shape,
        element.subelement,
        element.raw_shape,
        element.global_placement,
        element.shape_fingerprint,
        element.presentation,
    )


def resolve_current_part_element(
    document: Any,
    reference: NativeObjectRef,
    *,
    subelement: str | None,
    operation: str,
) -> CurrentPartElement:
    import Part

    visible, target = resolve_current_part_target(
        document,
        reference,
        operation=operation,
    )
    try:
        if subelement:
            shape = Part.getShape(
                target,
                subelement,
                needSubElement=True,
                transform=True,
            )
        else:
            shape = Part.getShape(target, transform=True)
    except Exception as exc:
        raise NativeModelError(f"The exact {operation} geometry no longer exists.") from exc
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeModelError(f"The {operation} reference has no valid shape.")
    try:
        if subelement:
            raw_shape = Part.getShape(
                target,
                subelement,
                needSubElement=True,
                transform=False,
            )
        else:
            raw_shape = Part.getShape(target, transform=False)
    except Exception:
        raw_shape = None
    placement_getter = getattr(target, "getGlobalPlacement", None)
    try:
        global_placement = placement_getter() if callable(placement_getter) else None
    except Exception:
        global_placement = None
    return CurrentPartElement(
        target,
        shape,
        subelement,
        raw_shape,
        global_placement,
        _shape_fingerprint(shape),
        visible,
    )


def is_part_2d(target: Any) -> bool:
    derived = getattr(target, "isDerivedFrom", None)
    try:
        return bool(derived("Part::Part2DObject")) if callable(derived) else False
    except Exception:
        return False


def copy_part_visual(
    source: Any,
    result: Any,
    *,
    include_part_2d: bool = False,
) -> None:
    if not include_part_2d and is_part_2d(source):
        return
    source_view = getattr(source, "ViewObject", None)
    result_view = getattr(result, "ViewObject", None)
    if source_view is None or result_view is None:
        return
    for name in ("ShapeAppearance", "LineColor", "PointColor"):
        try:
            setattr(result_view, name, getattr(source_view, name))
        except Exception:
            continue


def grouped_result_labels(label: str, count: int) -> tuple[str, ...]:
    values = []
    for index in range(1, count + 1):
        if index == count:
            values.append(label)
            continue
        suffix = f" — output {index}"
        values.append(f"{label[: max(1, 160 - len(suffix))]}{suffix}")
    return tuple(values)


def _shape_is_exact(current: Any, expected: Any) -> bool:
    return (
        current is not None
        and not current.isNull()
        and current.isPartner(expected)
        and current.Placement == expected.Placement
        and str(current.Orientation) == str(expected.Orientation)
    )


def _shape_fingerprint(shape: Any) -> str | None:
    """Hash exact BREP state when OCC recreates an equivalent wrapper shape."""
    try:
        encoded = shape.exportBrepToString().encode("utf-8")
    except Exception:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _presentation_still_resolves(
    document: Any,
    presentation: Any | None,
    target: Any,
) -> bool:
    if presentation is None:
        return True
    name = str(getattr(presentation, "Name", "") or "")
    if not name or document.getObject(name) is not presentation:
        return False
    try:
        import PartGui

        return bool(PartGui.isModelingObjectActive(presentation)) and (
            PartGui.resolveModelingObject(presentation) is target
        )
    except Exception:
        return False


def current_part_source_is_exact(document: Any, source: CurrentPartSource) -> bool:
    import Part

    name = str(getattr(source.target, "Name", "") or "")
    if (
        not name
        or document.getObject(name) is not source.target
        or not _presentation_still_resolves(
            document,
            source.presentation,
            source.target,
        )
    ):
        return False
    try:
        return _shape_is_exact(Part.getShape(source.target, transform=True), source.shape)
    except Exception:
        return False


def current_part_edge_is_exact(document: Any, edge: CurrentPartEdge) -> bool:
    return current_part_element_is_exact(
        document,
        CurrentPartElement(
            edge.target,
            edge.shape,
            edge.subelement,
            edge.raw_shape,
            edge.global_placement,
            edge.shape_fingerprint,
            edge.presentation,
        ),
    )


def current_part_element_is_exact(document: Any, element: CurrentPartElement) -> bool:
    import Part

    name = str(getattr(element.target, "Name", "") or "")
    if (
        not name
        or document.getObject(name) is not element.target
        or not _presentation_still_resolves(
            document,
            element.presentation,
            element.target,
        )
    ):
        return False
    try:
        current = (
            Part.getShape(
                element.target,
                element.subelement,
                needSubElement=True,
                transform=True,
            )
            if element.subelement
            else Part.getShape(element.target, transform=True)
        )
        if _shape_is_exact(current, element.shape):
            return True
        if (
            element.shape_fingerprint is not None
            and _shape_fingerprint(current) == element.shape_fingerprint
            and str(current.ShapeType) == str(element.shape.ShapeType)
            and str(current.Orientation) == str(element.shape.Orientation)
        ):
            return True
        if element.raw_shape is None or element.global_placement is None:
            return False
        current_raw = (
            Part.getShape(
                element.target,
                element.subelement,
                needSubElement=True,
                transform=False,
            )
            if element.subelement
            else Part.getShape(element.target, transform=False)
        )
        placement_getter = getattr(element.target, "getGlobalPlacement", None)
        current_placement = placement_getter() if callable(placement_getter) else None
        return (
            _shape_is_exact(current_raw, element.raw_shape)
            and current_placement == element.global_placement
        )
    except Exception:
        return False


def link_sub(value: Any) -> tuple[Any | None, tuple[str, ...]]:
    if not value:
        return None, ()
    if isinstance(value, tuple):
        target, subelements = value
        if isinstance(subelements, str):
            return target, (subelements,) if subelements else ()
        return target, tuple(str(item) for item in (subelements or ()))
    return value, ()


def flatten_link_sub_list(
    values: Any,
) -> tuple[tuple[Any | None, tuple[str, ...]], ...]:
    """Flatten Python's grouped LinkSubList readback without losing order."""
    flattened = []
    for value in tuple(values):
        target, subelements = link_sub(value)
        for subelement in subelements or ("",):
            flattened.append((target, (subelement,)))
    return tuple(flattened)


def part_profile_type(shape: Any) -> str | None:
    """Mirror the live Part Loft/Sweep compound profile normalization."""
    candidate = shape
    if str(shape.ShapeType) == "Compound":
        children = tuple(shape.childShapes(False, False))
        if len(children) == 1:
            candidate = children[0]
        elif children and all(str(child.ShapeType) == "Edge" for child in children):
            import Part

            groups = tuple(Part.sortEdges(list(children)))
            if len(groups) == 1:
                try:
                    candidate = Part.Wire(groups[0])
                except Exception:
                    return None
    shape_type = str(candidate.ShapeType)
    return shape_type if shape_type in {"Vertex", "Edge", "Wire", "Face"} else None


def close_number(left: float, right: float, *, tolerance: float = 1.0e-8) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))
