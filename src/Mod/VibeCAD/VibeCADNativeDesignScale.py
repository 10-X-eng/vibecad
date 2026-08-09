# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Body-aware Design Scale preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignResults import DesignResultSpec, create_design_operation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_DEFINITION_FIELDS = {
    "uniform": frozenset({"kind", "factor", "center_mm"}),
    "non_uniform": frozenset(
        {"kind", "x_factor", "y_factor", "z_factor", "center_mm"}
    ),
}
_MAX_TARGETS = 16
_MIN_FACTOR = 1.0e-6
_MAX_FACTOR = 1.0e6
_MAX_CENTER = 1.0e9


@dataclass(frozen=True, slots=True)
class DesignScaleSpec:
    target_refs: tuple[NativeObjectRef, ...]
    uniform: bool
    uniform_factor: float
    axis_factors: tuple[float, float, float]
    center: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ResolvedDesignScaleTarget:
    body: Any
    state: Any
    body_id: str
    shape: Any
    frame: Any


@dataclass(frozen=True, slots=True)
class PreparedDesignScale:
    spec: DesignScaleSpec
    targets: tuple[ResolvedDesignScaleTarget, ...]


def _number(value: Any, name: str, *, maximum: float) -> float:
    if isinstance(value, bool):
        raise NativeModelError(f"A Design Scale {name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"A Design Scale {name} must be a number.") from exc
    if not math.isfinite(number) or abs(number) > maximum:
        raise NativeModelError(f"A Design Scale {name} is outside its finite range.")
    return number


def _factor(value: Any, name: str) -> float:
    factor = _number(value, name, maximum=_MAX_FACTOR)
    if factor < _MIN_FACTOR:
        raise NativeModelError(
            f"A Design Scale {name} must be from {_MIN_FACTOR:g} to {_MAX_FACTOR:g}."
        )
    return factor


def _center(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError("A Design Scale center is invalid.")
    return tuple(
        _number(value[axis], f"center {axis}", maximum=_MAX_CENTER)
        for axis in "xyz"
    )


def _targets(document_uid: str, value: Any) -> tuple[NativeObjectRef, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_TARGETS:
        raise NativeModelError("Design Scale requires 1 to 16 exact target Bodies.")
    refs = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"object_name"}:
            raise NativeModelError("A Design Scale Body target is invalid.")
        refs.append(NativeObjectRef(document_uid, str(item["object_name"] or "")))
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Design Scale cannot repeat a target Body.")
    return tuple(refs)


def prepare_design_scale(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignScaleSpec:
    if not isinstance(values, Mapping) or set(values) != {
        "label",
        "targets",
        "definition",
    }:
        raise NativeModelError("A Design Scale call must contain its exact controls.")
    definition = values["definition"]
    if not isinstance(definition, Mapping):
        raise NativeModelError("A Design Scale definition is invalid.")
    kind = str(definition.get("kind") or "")
    expected = _DEFINITION_FIELDS.get(kind)
    if expected is None:
        raise NativeModelError("That Design Scale mode is unavailable.")
    if set(definition) != expected:
        raise NativeModelError("The Design Scale fields do not match its mode.")
    center = _center(definition["center_mm"])
    if kind == "uniform":
        uniform_factor = _factor(definition["factor"], "factor")
        axis_factors = (1.0, 1.0, 1.0)
    else:
        uniform_factor = 1.0
        axis_factors = tuple(
            _factor(definition[f"{axis}_factor"], f"{axis} factor")
            for axis in "xyz"
        )
    return DesignScaleSpec(
        _targets(document_uid, values["targets"]),
        kind == "uniform",
        uniform_factor,
        axis_factors,
        center,
    )


def _current_state(body: Any) -> Any:
    import PartGui

    return PartGui.resolveModelingObject(body)


def _shape_is_exact(current: Any, expected: Any) -> bool:
    return (
        current is not None
        and not current.isNull()
        and current.isPartner(expected)
        and current.Placement == expected.Placement
        and str(current.Orientation) == str(expected.Orientation)
    )


def _target_is_exact(document: Any, target: ResolvedDesignScaleTarget) -> bool:
    try:
        import PartGui

        body = target.body
        return (
            document.getObject(body.Name) is body
            and PartGui.isModelingObjectActive(body)
            and str(body.VibeCADBodyId) == target.body_id
            and _current_state(body) is target.state
            and _shape_is_exact(body.Shape, target.shape)
            and body.getGlobalPlacement() == target.frame
        )
    except Exception:
        return False


def preflight_design_scale(
    document: Any,
    spec: DesignScaleSpec,
) -> PreparedDesignScale:
    import PartGui

    if not isinstance(spec, DesignScaleSpec):
        raise TypeError("spec must be a DesignScaleSpec")
    targets = []
    state_identities: set[int] = set()
    for reference in spec.target_refs:
        body = resolve_object(
            document,
            reference,
            expected_types=("PartDesign::Body",),
        )
        shape = getattr(body, "Shape", None)
        state = _current_state(body)
        body_id = str(getattr(body, "VibeCADBodyId", "") or "")
        frame = body.getGlobalPlacement()
        if not PartGui.isModelingObjectActive(body):
            raise NativeModelError("A Design Scale Body is not active in current History.")
        if (
            shape is None
            or shape.isNull()
            or not shape.isValid()
            or len(shape.Solids) != 1
            or state is None
            or getattr(state, "Document", None) is not document
            or not body_id
        ):
            raise NativeModelError(
                "Every Design Scale target Body must contain one exact current solid state."
            )
        state_identity = id(state)
        if state_identity in state_identities:
            raise NativeModelError("Design Scale targets resolve to a duplicate Body state.")
        state_identities.add(state_identity)
        # Keep the exact current TopoShape value.  A deep copy intentionally has
        # a different kernel identity and cannot prove that the Body remained
        # on the same state between preflight and transaction entry.
        targets.append(ResolvedDesignScaleTarget(body, state, body_id, shape, frame))
    return PreparedDesignScale(spec, tuple(targets))


def _property_number(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _verify_scale(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    prepared: PreparedDesignScale = expected["prepared"]
    spec = prepared.spec
    output_shapes = tuple(operation.OutputShapes)
    expected_frames = tuple(target.frame for target in prepared.targets)
    factors = tuple(
        _property_number(getattr(operation, f"{axis.upper()}Scale"))
        for axis in "xyz"
    )
    center = tuple(float(getattr(operation.Center, axis)) for axis in "xyz")
    if (
        bool(operation.Uniform) is not spec.uniform
        or not math.isclose(
            _property_number(operation.UniformScale),
            spec.uniform_factor,
            rel_tol=1.0e-10,
            abs_tol=1.0e-8,
        )
        or any(
            not math.isclose(actual, requested, rel_tol=1.0e-10, abs_tol=1.0e-8)
            for actual, requested in zip(factors, spec.axis_factors, strict=True)
        )
        or any(
            not math.isclose(actual, requested, rel_tol=1.0e-10, abs_tol=1.0e-8)
            for actual, requested in zip(center, spec.center, strict=True)
        )
        or operation.BaseFeature is not None
        or not operation.Shape.isNull()
        or tuple(operation.InputStates)
        != tuple(target.state for target in prepared.targets)
        or tuple(operation.InputBodyIds)
        != tuple(target.body_id for target in prepared.targets)
        or tuple(operation.OutputBodyIds)
        != tuple(target.body_id for target in prepared.targets)
        or tuple(operation.OutputPreviousInputIndices)
        != tuple(range(len(prepared.targets)))
        or any(str(value) for value in operation.OutputComponentIds)
        or tuple(operation.InputFrames) != expected_frames
        or tuple(operation.OutputFrames) != expected_frames
        or tuple(operation.OutputPresence)
        != tuple(True for _target in prepared.targets)
        or len(output_shapes) != len(prepared.targets)
        or any(
            shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
            for shape in output_shapes
        )
    ):
        raise NativeModelError("The Design Scale controls or exact Body ports changed.")
    return {
        "mode": "uniform" if spec.uniform else "non_uniform",
        "uniform_factor": spec.uniform_factor if spec.uniform else None,
        "axis_factors": list(spec.axis_factors) if not spec.uniform else None,
        "center_mm": dict(zip(("x", "y", "z"), spec.center, strict=True)),
        "target_count": len(prepared.targets),
    }


def create_design_scale(
    document: Any,
    *,
    label: str,
    prepared: PreparedDesignScale,
) -> NativeMutationDraft:
    import FreeCAD as App

    if not isinstance(prepared, PreparedDesignScale):
        raise TypeError("prepared must be a PreparedDesignScale")
    if any(not _target_is_exact(document, target) for target in prepared.targets):
        raise NativeModelError("A Design Scale target changed after preflight.")
    spec = prepared.spec
    result_spec = DesignResultSpec("modify", spec.target_refs, None)

    def configure(operation: Any) -> Mapping[str, Any]:
        operation.Uniform = spec.uniform
        operation.UniformScale = spec.uniform_factor
        operation.XScale, operation.YScale, operation.ZScale = spec.axis_factors
        operation.Center = App.Vector(*spec.center)
        return {"prepared": prepared}

    return create_design_operation(
        document,
        type_id="PartDesign::DesignScale",
        base_name="Scale",
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=_verify_scale,
        configure_after_targets=True,
    )
