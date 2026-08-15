# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Compound Filter implementation for the Model ribbon."""

from __future__ import annotations

from dataclasses import dataclass
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


_MODE_TO_NATIVE = {
    "bypass": "bypass",
    "specific_items": "specific items",
    "collision": "collision-pass",
    "volume": "window-volume",
    "area": "window-area",
    "length": "window-length",
    "distance": "window-distance",
}
_WINDOW_MODES = frozenset({"volume", "area", "length", "distance"})
_MAX_CHILDREN = 4096
_MAX_SELECTORS = 256
_MAX_INDEX = 1_000_000


@dataclass(frozen=True, slots=True)
class CompoundFilterSelector:
    index: int | None
    start: int | None
    stop: int | None
    step: int | None

    @property
    def native_text(self) -> str:
        if self.index is not None:
            return str(self.index)
        values = (self.start, self.stop, self.step)
        count = 2 if self.step is None else 3
        return ":".join("" if value is None else str(value) for value in values[:count])


@dataclass(frozen=True, slots=True)
class PartCompoundFilterSpec:
    source_ref: NativeObjectRef
    mode: str
    stencil_ref: NativeObjectRef | None
    selectors: tuple[CompoundFilterSelector, ...]
    window_percent: tuple[float, float] | None
    maximum: float | None
    invert: bool

    @property
    def native_mode(self) -> str:
        return _MODE_TO_NATIVE[self.mode]

    @property
    def native_items(self) -> str:
        return ";".join(selector.native_text for selector in self.selectors)


@dataclass(frozen=True, slots=True)
class PreparedPartCompoundFilter:
    spec: PartCompoundFilterSpec
    source: CurrentPartElement
    stencil: CurrentPartElement | None
    presentations: tuple[tuple[Any, bool], ...]
    input_child_count: int
    output_signatures: tuple[tuple[Any, ...], ...]


def _object_ref(document_uid: str, value: Any, *, role: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Compound Filter {role} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _bounded_integer(value: Any, *, role: str) -> int:
    if type(value) is not int or not -_MAX_INDEX <= value <= _MAX_INDEX:
        raise NativeModelError(f"A Compound Filter {role} must be a bounded integer.")
    return value


def _selectors(value: Any) -> tuple[CompoundFilterSelector, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SELECTORS:
        raise NativeModelError("Specific-item filtering requires 1 to 256 selectors.")
    result = []
    for selector in value:
        if type(selector) is int:
            result.append(
                CompoundFilterSelector(
                    _bounded_integer(selector, role="index"),
                    None,
                    None,
                    None,
                )
            )
            continue
        if not isinstance(selector, list) or len(selector) not in (2, 3):
            raise NativeModelError(
                "A Compound Filter selector must be an index or [start, stop, step] slice."
            )
        values = [
            None
            if item is None
            else _bounded_integer(item, role="slice value")
            for item in selector
        ]
        if len(values) == 2:
            values.append(None)
        if values[2] == 0:
            raise NativeModelError("A Compound Filter slice step cannot be zero.")
        result.append(CompoundFilterSelector(None, *values))
    return tuple(result)


def _finite_number(value: Any, *, role: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise NativeModelError(f"A Compound Filter {role} must be finite.")
    return float(value)


def prepare_part_compound_filter(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartCompoundFilterSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Compound Filter definition is invalid.")
    mode = str(value.get("mode") or "")
    expected = {
        "bypass": {"source", "mode"},
        "specific_items": {"source", "mode", "selectors", "invert"},
        "collision": {"source", "mode", "stencil", "invert"},
        "volume": {
            "source",
            "mode",
            "stencil",
            "window_percent",
            "maximum",
            "invert",
        },
        "area": {
            "source",
            "mode",
            "stencil",
            "window_percent",
            "maximum",
            "invert",
        },
        "length": {
            "source",
            "mode",
            "stencil",
            "window_percent",
            "maximum",
            "invert",
        },
        "distance": {
            "source",
            "mode",
            "stencil",
            "window_percent",
            "maximum",
            "invert",
        },
    }.get(mode)
    if expected is None or set(value) != expected:
        raise NativeModelError(
            "Compound Filter fields do not match the selected filter mode."
        )
    source = _object_ref(document_uid, value["source"], role="source")
    stencil = None
    if "stencil" in value and value["stencil"] is not None:
        stencil = _object_ref(document_uid, value["stencil"], role="stencil")
    if mode in {"collision", "distance"} and stencil is None:
        raise NativeModelError(f"Compound Filter {mode} mode requires a stencil.")
    if stencil is not None and stencil.object_name == source.object_name:
        raise NativeModelError("Compound Filter source and stencil must be distinct.")

    selectors = _selectors(value["selectors"]) if mode == "specific_items" else ()
    window = None
    maximum = None
    if mode in _WINDOW_MODES:
        raw_window = value["window_percent"]
        if not isinstance(raw_window, list) or len(raw_window) != 2:
            raise NativeModelError("A Compound Filter window requires [from, to] percentages.")
        window = tuple(
            _finite_number(item, role="window percentage") for item in raw_window
        )
        if any(abs(item) > 1_000_000.0 for item in window):
            raise NativeModelError("Compound Filter window percentages exceed their bound.")
        raw_maximum = value["maximum"]
        if raw_maximum is not None:
            maximum = _finite_number(raw_maximum, role="maximum override")
            if maximum <= 0.0 or maximum > 1.0e18:
                raise NativeModelError(
                    "A Compound Filter maximum override must be positive, bounded, or null."
                )
    invert = bool(value.get("invert", False))
    if "invert" in value and type(value["invert"]) is not bool:
        raise NativeModelError("Compound Filter invert must be true or false.")
    return PartCompoundFilterSpec(
        source,
        mode,
        stencil,
        selectors,
        window,
        maximum,
        invert,
    )


def _shape_signature(shape: Any) -> tuple[Any, ...]:
    bounds = shape.BoundBox
    return (
        str(shape.ShapeType),
        len(shape.Vertexes),
        len(shape.Edges),
        len(shape.Faces),
        len(shape.Solids),
        float(shape.Volume),
        float(shape.Area),
        float(shape.Length),
        float(bounds.XMin),
        float(bounds.XMax),
        float(bounds.YMin),
        float(bounds.YMax),
        float(bounds.ZMin),
        float(bounds.ZMax),
    )


def _metric(shape: Any, mode: str) -> float:
    if mode == "volume":
        return float(shape.Volume)
    if mode == "area":
        return float(shape.Area)
    if mode == "length":
        return float(shape.Length)
    raise NativeModelError("That Compound Filter metric is unavailable.")


def _selected_indices(
    spec: PartCompoundFilterSpec,
    children: tuple[Any, ...],
    stencil_shape: Any | None,
) -> tuple[int, ...]:
    if spec.mode == "bypass":
        return tuple(range(len(children)))
    if spec.mode == "specific_items":
        selected = []
        flags = [False] * len(children)
        for selector in spec.selectors:
            if selector.index is not None:
                index = selector.index
                normalized = index if index >= 0 else len(children) + index
                if not 0 <= normalized < len(children):
                    raise NativeModelError(
                        f"Compound Filter item index {index} is outside the source Compound."
                    )
                selected.append(normalized)
                flags[normalized] = True
                continue
            indices = range(len(children))[
                slice(selector.start, selector.stop, selector.step)
            ]
            selected.extend(indices)
            for index in indices:
                flags[index] = True
        return (
            tuple(index for index, included in enumerate(flags) if not included)
            if spec.invert
            else tuple(selected)
        )
    if spec.mode == "collision":
        if stencil_shape is None:
            raise NativeModelError("Compound Filter collision mode lost its stencil.")
        import Part

        return tuple(
            index
            for index, child in enumerate(children)
            if (child.distToShape(stencil_shape)[0] < Part.Precision.confusion())
            != spec.invert
        )

    values = []
    for child in children:
        values.append(
            float(child.distToShape(stencil_shape)[0])
            if spec.mode == "distance" and stencil_shape is not None
            else _metric(child, spec.mode)
        )
    maximum = max(values)
    if stencil_shape is not None and spec.mode in {"volume", "area", "length"}:
        maximum = _metric(stencil_shape, spec.mode)
    if spec.maximum is not None:
        maximum = spec.maximum
    start, stop = spec.window_percent or (0.0, 0.0)
    lower = start / 100.0 * maximum
    upper = stop / 100.0 * maximum
    return tuple(
        index
        for index, metric in enumerate(values)
        if (lower <= metric <= upper) != spec.invert
    )


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_compound_filter(
    document: Any,
    spec: PartCompoundFilterSpec,
) -> PreparedPartCompoundFilter:
    import PartGui

    if not isinstance(spec, PartCompoundFilterSpec):
        raise TypeError("spec must be a PartCompoundFilterSpec")
    source = resolve_current_part_element(
        document,
        spec.source_ref,
        subelement=None,
        operation="Compound Filter source",
    )
    try:
        base_shape = source.target.Shape
    except Exception as exc:
        raise NativeModelError(
            "Compound Filter source has no directly filterable shape."
        ) from exc
    if (
        base_shape is None
        or base_shape.isNull()
        or not base_shape.isValid()
        or str(base_shape.ShapeType) not in {"Compound", "CompSolid"}
    ):
        raise NativeModelError("Compound Filter requires one valid Compound or CompSolid.")
    children = tuple(base_shape.childShapes())
    if not children or len(children) > _MAX_CHILDREN:
        raise NativeModelError("Compound Filter requires 1 to 4096 direct child shapes.")
    stencil = None
    if spec.stencil_ref is not None:
        stencil = resolve_current_part_element(
            document,
            spec.stencil_ref,
            subelement=None,
            operation="Compound Filter stencil",
        )
        if stencil.target is source.target:
            raise NativeModelError("Compound Filter source and stencil resolve identically.")
        try:
            stencil_shape = stencil.target.Shape
        except Exception as exc:
            raise NativeModelError(
                "Compound Filter stencil has no directly usable shape."
            ) from exc
        if stencil_shape is None or stencil_shape.isNull() or not stencil_shape.isValid():
            raise NativeModelError("Compound Filter stencil has no valid current shape.")
    else:
        stencil_shape = None
    try:
        indices = _selected_indices(
            spec,
            children,
            stencil_shape,
        )
    except NativeModelError:
        raise
    except Exception as exc:
        raise NativeModelError("Compound Filter could not evaluate its exact controls.") from exc
    if not indices:
        raise NativeModelError("Nothing passes through the Compound Filter.")

    presentations = []
    for element in tuple(item for item in (source, stencil) if item is not None):
        presentation = PartGui.resolveModelingPresentationObject(element.target)
        if presentation is None:
            presentation = element.target
        if all(existing[0] is not presentation for existing in presentations):
            presentations.append((presentation, _visible(presentation)))
    return PreparedPartCompoundFilter(
        spec=spec,
        source=source,
        stencil=stencil,
        presentations=tuple(presentations),
        input_child_count=len(children),
        output_signatures=tuple(_shape_signature(children[index]) for index in indices),
    )


def _prepared_is_exact(
    document: Any,
    prepared: PreparedPartCompoundFilter,
    *,
    require_original_visibility: bool,
) -> bool:
    elements = tuple(
        item for item in (prepared.source, prepared.stencil) if item is not None
    )
    return all(current_part_element_is_exact(document, item) for item in elements) and (
        not require_original_visibility
        or all(
            _visible(presentation) is was_visible
            for presentation, was_visible in prepared.presentations
        )
    )


def create_part_compound_filter(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartCompoundFilter,
) -> NativeMutationDraft:
    import PartGui
    from CompoundTools.CompoundFilter import makeCompoundFilter

    if not _prepared_is_exact(
        document,
        prepared,
        require_original_visibility=True,
    ):
        raise NativeModelError("A Compound Filter source changed after preflight.")
    spec = prepared.spec
    result = makeCompoundFilter("CompoundFilter", into_group=document)
    if result is None or result.TypeId != "Part::FeaturePython":
        raise NativeModelError("The Compound Filter factory returned the wrong object type.")
    result.Label = label
    result.Base = prepared.source.target
    result.FilterType = spec.native_mode
    result.items = spec.native_items
    result.Stencil = prepared.stencil.target if prepared.stencil is not None else None
    if spec.window_percent is not None:
        result.WindowFrom, result.WindowTo = spec.window_percent
    result.OverrideMaxVal = spec.maximum or 0.0
    result.Invert = spec.invert
    recomputed = document.recompute([result], True, True)
    if (
        recomputed is False
        or not result.isValid()
        or result.Shape.isNull()
        or not result.Shape.isValid()
    ):
        raise NativeModelError(
            str(result.getStatusString() or "Compound Filter produced invalid geometry.")
        )

    replaced = tuple(
        presentation
        for presentation, was_visible in prepared.presentations
        if was_visible
    )
    if replaced:
        if not PartGui.setModelingReplacedInputs(result, replaced):
            raise NativeModelError("Compound Filter could not retain its replaced inputs.")
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


def _signatures_equal(
    expected: tuple[tuple[Any, ...], ...],
    actual: tuple[tuple[Any, ...], ...],
) -> bool:
    if len(expected) != len(actual):
        return False
    for wanted, observed in zip(expected, actual):
        if wanted[:5] != observed[:5]:
            return False
        if any(
            not math.isclose(left, right, rel_tol=1.0e-9, abs_tol=1.0e-7)
            for left, right in zip(wanted[5:], observed[5:])
        ):
            return False
    return True


def verify_part_compound_filter(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedPartCompoundFilter = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Part::FeaturePython"
        or str(result.Label) != draft.value["label"]
        or result.Base is not prepared.source.target
        or result.Stencil
        is not (prepared.stencil.target if prepared.stencil is not None else None)
        or str(result.FilterType) != spec.native_mode
        or str(result.items) != spec.native_items
        or not math.isclose(float(result.OverrideMaxVal), spec.maximum or 0.0, abs_tol=1.0e-9)
        or bool(result.Invert) is not spec.invert
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
    ):
        raise NativeModelError("The Compound Filter result changed its exact controls.")
    if spec.window_percent is not None and (
        not math.isclose(float(result.WindowFrom), spec.window_percent[0], abs_tol=1.0e-9)
        or not math.isclose(float(result.WindowTo), spec.window_percent[1], abs_tol=1.0e-9)
    ):
        raise NativeModelError("The Compound Filter result changed its window.")
    if (
        str(getattr(getattr(result, "Proxy", None), "Type", "") or "")
        != "CompoundFilter"
        or str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != draft.value["replaced"]
        or not _prepared_is_exact(
            document,
            prepared,
            require_original_visibility=False,
        )
    ):
        raise NativeModelError("The Compound Filter retained definition is inconsistent.")
    for presentation, _was_visible in prepared.presentations:
        if _visible(presentation):
            raise NativeModelError("Compound Filter did not hide its retained inputs.")

    expected = prepared.output_signatures
    actual = (
        (_shape_signature(shape),)
        if len(expected) == 1
        else tuple(_shape_signature(child) for child in shape.childShapes())
    )
    if not _signatures_equal(expected, actual):
        raise NativeModelError("The Compound Filter output differs from its selected children.")
    return {
        "root": object_reference(result),
        "mode": spec.mode,
        "input_child_count": prepared.input_child_count,
        "output_child_count": len(expected),
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
