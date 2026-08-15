# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing and native execution for the current Design Hole operation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignHoleCatalog import (
    load_hole_catalog,
    require_hole_catalog_selection,
)
from VibeCADNativeDesignProfileBase import create_profile_design_operation
from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    preflight_design_link,
)
from VibeCADNativeDesignResults import (
    DesignResultSpec,
    resolve_design_result,
    result_spec_from_mapping,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef


_BASE_PROFILE_VALUES = {
    "circles_and_arcs": 6,
    "points_circles_and_arcs": 7,
    "points": 1,
}
_DEPTH_TYPES = {"dimension": "Dimension", "through_all": "ThroughAll"}
_THREAD_DEPTH_TYPES = {
    "hole_depth": "Hole Depth",
    "dimension": "Dimension",
    "tapped_din76": "Tapped (DIN76)",
}
_HEAD_TYPES = {
    "none": "None",
    "counterbore": "Counterbore",
    "countersink": "Countersink",
    "counterdrill": "Counterdrill",
}


@dataclass(frozen=True, slots=True)
class HoleTypeSpec:
    kind: str
    diameter_mm: float | None = None
    standard: str | None = None
    size: str | None = None
    thread_class: str | None = None
    fit: str | None = None
    direction: str | None = None
    thread_depth_kind: str | None = None
    thread_depth_mm: float | None = None
    custom_clearance_mm: float | None = None


@dataclass(frozen=True, slots=True)
class HoleHeadSpec:
    kind: str
    designation: str | None = None
    override_kind: str | None = None
    diameter_mm: float | None = None
    depth_mm: float | None = None
    angle_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class DesignHoleSpec:
    profile: DesignLinkSpec
    result: DesignResultSpec
    base_profile: str
    hole_type: HoleTypeSpec
    head: HoleHeadSpec
    depth_kind: str
    depth_mm: float | None
    drill_kind: str
    drill_angle_degrees: float | None
    depth_reference: str | None
    taper_kind: str
    taper_angle_degrees: float | None
    reversed: bool


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeModelError(f"The Design Hole {label} is invalid.")
    return value


def _exact_fields(value: Mapping[str, Any], fields: set[str], *, label: str) -> None:
    if set(value) != fields:
        raise NativeModelError(f"The Design Hole {label} fields are invalid.")


def _number(value: Any, *, label: str, minimum: float = 0.0) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= minimum or result >= 1_000_000.0:
        raise NativeModelError(f"The Design Hole {label} is out of range.")
    return result


def _signed_number(value: Any, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not -1_000_000.0 <= result <= 1_000_000.0:
        raise NativeModelError(f"The Design Hole {label} is out of range.")
    return result


def _angle(value: Any, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0.0 < result < 180.0:
        raise NativeModelError(f"The Design Hole {label} must be between 0 and 180 degrees.")
    return result


def _catalog_text(value: Any, *, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 64:
        raise NativeModelError(f"The Design Hole {label} is invalid.")
    return clean


def _parse_thread_depth(value: Any) -> tuple[str, float | None]:
    data = _mapping(value, label="thread depth")
    kind = str(data.get("kind") or "")
    fields = {"kind", "depth_mm"} if kind == "dimension" else {"kind"}
    _exact_fields(data, fields, label="thread depth")
    if kind not in _THREAD_DEPTH_TYPES:
        raise NativeModelError("The Design Hole thread depth type is unavailable.")
    return kind, (
        _number(data["depth_mm"], label="thread depth")
        if kind == "dimension"
        else None
    )


def _parse_hole_type(value: Any) -> HoleTypeSpec:
    data = _mapping(value, label="type")
    kind = str(data.get("kind") or "")
    if kind == "plain":
        _exact_fields(data, {"kind", "diameter_mm"}, label="type")
        return HoleTypeSpec(
            kind,
            diameter_mm=_number(data["diameter_mm"], label="diameter"),
        )
    common = {"kind", "standard", "size"}
    if kind == "clearance":
        _exact_fields(data, common | {"fit"}, label="type")
        return HoleTypeSpec(
            kind,
            standard=_catalog_text(data["standard"], label="standard"),
            size=_catalog_text(data["size"], label="size"),
            fit=_catalog_text(data["fit"], label="fit"),
        )
    if kind == "tap_drill":
        _exact_fields(data, common, label="type")
        return HoleTypeSpec(
            kind,
            standard=_catalog_text(data["standard"], label="standard"),
            size=_catalog_text(data["size"], label="size"),
        )
    if kind not in {"threaded_cosmetic", "threaded_modeled"}:
        raise NativeModelError("The Design Hole type is unavailable.")
    fields = common | {"thread_class", "direction", "thread_depth"}
    if kind == "threaded_modeled":
        fields.add("custom_clearance_mm")
    _exact_fields(data, fields, label="type")
    direction = str(data["direction"])
    if direction not in {"right", "left"}:
        raise NativeModelError("The Design Hole thread direction is unavailable.")
    depth_kind, depth_mm = _parse_thread_depth(data["thread_depth"])
    custom = data.get("custom_clearance_mm")
    if custom is not None:
        custom = _signed_number(custom, label="custom clearance")
    return HoleTypeSpec(
        kind,
        standard=_catalog_text(data["standard"], label="standard"),
        size=_catalog_text(data["size"], label="size"),
        thread_class=_catalog_text(data["thread_class"], label="thread class"),
        direction=direction,
        thread_depth_kind=depth_kind,
        thread_depth_mm=depth_mm,
        custom_clearance_mm=custom,
    )


def _parse_head(value: Any) -> HoleHeadSpec:
    data = _mapping(value, label="head")
    kind = str(data.get("kind") or "")
    if kind == "none":
        _exact_fields(data, {"kind"}, label="head")
        return HoleHeadSpec(kind)
    if kind == "counterbore":
        _exact_fields(data, {"kind", "diameter_mm", "depth_mm"}, label="head")
        return HoleHeadSpec(
            kind,
            diameter_mm=_number(data["diameter_mm"], label="head diameter"),
            depth_mm=_number(data["depth_mm"], label="head depth"),
        )
    if kind in {"countersink", "counterdrill"}:
        fields = {"kind", "diameter_mm", "angle_degrees"}
        if kind == "counterdrill":
            fields.add("depth_mm")
        _exact_fields(data, fields, label="head")
        return HoleHeadSpec(
            kind,
            diameter_mm=_number(data["diameter_mm"], label="head diameter"),
            depth_mm=(
                _number(data["depth_mm"], label="head depth")
                if kind == "counterdrill"
                else None
            ),
            angle_degrees=_angle(data["angle_degrees"], label="head angle"),
        )
    if kind != "catalog":
        raise NativeModelError("The Design Hole head type is unavailable.")
    _exact_fields(data, {"kind", "designation", "override"}, label="head")
    override = data["override"]
    if override is None:
        return HoleHeadSpec(
            kind,
            designation=_catalog_text(data["designation"], label="head designation"),
        )
    parsed = _parse_head(override)
    if parsed.kind not in {"counterbore", "countersink"}:
        raise NativeModelError("A catalog Hole head has an invalid override type.")
    return HoleHeadSpec(
        kind,
        designation=_catalog_text(data["designation"], label="head designation"),
        override_kind=parsed.kind,
        diameter_mm=parsed.diameter_mm,
        depth_mm=parsed.depth_mm,
        angle_degrees=parsed.angle_degrees,
    )


def _parse_kind_and_number(
    value: Any,
    *,
    label: str,
    numbered_kind: str,
    number_field: str,
) -> tuple[str, float | None]:
    data = _mapping(value, label=label)
    kind = str(data.get("kind") or "")
    fields = {"kind", number_field} if kind == numbered_kind else {"kind"}
    _exact_fields(data, fields, label=label)
    return kind, (
        _number(data[number_field], label=label)
        if kind == numbered_kind
        else None
    )


def prepare_design_hole(document_uid: str, values: Mapping[str, Any]) -> DesignHoleSpec:
    profile_value = _mapping(values["profile"], label="profile")
    _exact_fields(profile_value, {"object_name"}, label="profile")
    profile = DesignLinkSpec(
        NativeObjectRef(document_uid, str(profile_value["object_name"])),
        (),
    )
    result = result_spec_from_mapping(
        document_uid,
        {
            "mode": "cut",
            "targets": values["targets"],
            "destination_component": None,
        },
    )
    base_profile = str(values["base_profile"])
    if base_profile not in _BASE_PROFILE_VALUES:
        raise NativeModelError("The Design Hole base profile mode is unavailable.")
    depth_kind, depth_mm = _parse_kind_and_number(
        values["depth"],
        label="depth",
        numbered_kind="dimension",
        number_field="depth_mm",
    )
    if depth_kind not in _DEPTH_TYPES:
        raise NativeModelError("The Design Hole depth type is unavailable.")
    drill = _mapping(values["drill_point"], label="drill point")
    drill_kind = str(drill.get("kind") or "")
    if drill_kind == "flat":
        _exact_fields(drill, {"kind"}, label="drill point")
        drill_angle = None
        depth_reference = None
    elif drill_kind == "angled":
        _exact_fields(
            drill,
            {"kind", "angle_degrees", "depth_reference"},
            label="drill point",
        )
        drill_angle = _angle(drill["angle_degrees"], label="drill point angle")
        depth_reference = str(drill["depth_reference"])
        if depth_reference not in {"full_diameter", "tip"}:
            raise NativeModelError("The Design Hole depth reference is unavailable.")
    else:
        raise NativeModelError("The Design Hole drill point is unavailable.")
    taper_kind, taper_angle = _parse_kind_and_number(
        values["taper"],
        label="taper",
        numbered_kind="tapered",
        number_field="angle_degrees",
    )
    if taper_kind not in {"straight", "tapered"}:
        raise NativeModelError("The Design Hole taper type is unavailable.")
    if taper_angle is not None:
        taper_angle = _angle(taper_angle, label="taper angle")
    return DesignHoleSpec(
        profile=profile,
        result=result,
        base_profile=base_profile,
        hole_type=_parse_hole_type(values["hole_type"]),
        head=_parse_head(values["head"]),
        depth_kind=depth_kind,
        depth_mm=depth_mm,
        drill_kind=drill_kind,
        drill_angle_degrees=drill_angle,
        depth_reference=depth_reference,
        taper_kind=taper_kind,
        taper_angle_degrees=taper_angle,
        reversed=bool(values["reversed"]),
    )


def preflight_design_hole(document: Any, spec: DesignHoleSpec) -> None:
    preflight_design_link(
        document,
        spec.profile,
        expected_types=("Part::Part2DObject",),
    )
    resolve_design_result(document, spec.result)
    hole = spec.hole_type
    if hole.kind == "plain":
        if spec.head.kind == "catalog":
            raise NativeModelError("A plain Hole cannot use a catalog head designation.")
        return
    catalog = load_hole_catalog()
    entry = require_hole_catalog_selection(
        catalog,
        standard=str(hole.standard),
        size=str(hole.size),
        thread_class=hole.thread_class,
        fit=hole.fit,
    )
    if spec.head.kind == "catalog":
        expected = next(
            (
                item
                for item in entry["heads"]
                if item["designation"] == spec.head.designation
            ),
            None,
        )
        if expected is None:
            raise NativeModelError("That catalog Hole head is unavailable.")
        if str(hole.size) not in expected["supported_sizes"]:
            raise NativeModelError(
                "That catalog Hole head has no definition for the selected size."
            )
        if (
            spec.head.override_kind is not None
            and expected["kind"] != spec.head.override_kind
        ):
            raise NativeModelError("That catalog Hole head is unavailable or has the wrong type.")


def _set_enum(operation: Any, property_name: str, value: str) -> None:
    choices = list(operation.getEnumerationsOfProperty(property_name) or [])
    if value not in choices:
        raise NativeModelError(f"The requested Hole {property_name} value is unavailable.")
    setattr(operation, property_name, value)


def _configure_hole_type(operation: Any, spec: HoleTypeSpec) -> None:
    if spec.kind == "plain":
        _set_enum(operation, "ThreadType", "None")
        operation.Threaded = False
        operation.ModelThread = False
        operation.CosmeticThread = False
        operation.Diameter = spec.diameter_mm
        return
    _set_enum(operation, "ThreadType", str(spec.standard))
    _set_enum(operation, "ThreadSize", str(spec.size))
    if spec.kind == "clearance":
        operation.Threaded = False
        operation.ModelThread = False
        operation.CosmeticThread = False
        _set_enum(operation, "ThreadFit", str(spec.fit))
        return
    operation.Threaded = True
    operation.ModelThread = spec.kind == "threaded_modeled"
    operation.CosmeticThread = spec.kind == "threaded_cosmetic"
    if spec.kind == "tap_drill":
        return
    _set_enum(operation, "ThreadClass", str(spec.thread_class))
    _set_enum(
        operation,
        "ThreadDirection",
        "Right" if spec.direction == "right" else "Left",
    )
    _set_enum(
        operation,
        "ThreadDepthType",
        _THREAD_DEPTH_TYPES[str(spec.thread_depth_kind)],
    )
    if spec.thread_depth_mm is not None:
        operation.ThreadDepth = spec.thread_depth_mm
    operation.UseCustomThreadClearance = spec.custom_clearance_mm is not None
    if spec.custom_clearance_mm is not None:
        operation.CustomThreadClearance = spec.custom_clearance_mm


def _configure_head(operation: Any, spec: HoleHeadSpec) -> None:
    native_type = spec.designation if spec.kind == "catalog" else _HEAD_TYPES[spec.kind]
    _set_enum(operation, "HoleCutType", str(native_type))
    if spec.kind == "catalog":
        operation.HoleCutCustomValues = spec.override_kind is not None
    if spec.diameter_mm is not None:
        if spec.diameter_mm <= float(operation.Diameter):
            raise NativeModelError("A Hole head diameter must exceed the hole diameter.")
        operation.HoleCutDiameter = spec.diameter_mm
    if spec.depth_mm is not None:
        operation.HoleCutDepth = spec.depth_mm
    if spec.angle_degrees is not None:
        operation.HoleCutCountersinkAngle = spec.angle_degrees


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1.0e-9, abs_tol=1.0e-7)


def _quantity(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _verify_hole(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    spec: DesignHoleSpec = expected["spec"]
    hole = spec.hole_type
    expected_threaded = hole.kind in {
        "tap_drill",
        "threaded_cosmetic",
        "threaded_modeled",
    }
    expected_modeled = hole.kind == "threaded_modeled"
    expected_cosmetic = hole.kind == "threaded_cosmetic"
    if (
        int(operation.BaseProfileType) != _BASE_PROFILE_VALUES[spec.base_profile]
        or str(operation.DepthType) != _DEPTH_TYPES[spec.depth_kind]
        or str(operation.DrillPoint) != ("Flat" if spec.drill_kind == "flat" else "Angled")
        or bool(operation.DrillForDepth) is not (spec.depth_reference == "tip")
        or bool(operation.Reversed) is not spec.reversed
        or bool(operation.Tapered) is not (spec.taper_kind == "tapered")
        or bool(operation.Threaded) is not expected_threaded
        or bool(operation.ModelThread) is not expected_modeled
        or bool(operation.CosmeticThread) is not expected_cosmetic
    ):
        raise NativeModelError("The Design Hole controls changed before commit.")
    for actual, requested, label in (
        (_quantity(operation.Depth), spec.depth_mm, "depth"),
        (
            _quantity(operation.DrillPointAngle),
            spec.drill_angle_degrees,
            "drill angle",
        ),
        (_quantity(operation.TaperedAngle), spec.taper_angle_degrees, "taper angle"),
        (_quantity(operation.Diameter), hole.diameter_mm, "diameter"),
        (_quantity(operation.ThreadDepth), hole.thread_depth_mm, "thread depth"),
        (
            _quantity(operation.CustomThreadClearance),
            hole.custom_clearance_mm,
            "clearance",
        ),
        (_quantity(operation.HoleCutDiameter), spec.head.diameter_mm, "head diameter"),
        (_quantity(operation.HoleCutDepth), spec.head.depth_mm, "head depth"),
        (
            _quantity(operation.HoleCutCountersinkAngle),
            spec.head.angle_degrees,
            "head angle",
        ),
    ):
        if requested is not None and not _close(actual, requested):
            raise NativeModelError(f"The Design Hole {label} changed before commit.")
    expected_head = spec.head.designation or _HEAD_TYPES[spec.head.kind]
    if str(operation.HoleCutType) != expected_head:
        raise NativeModelError("The Design Hole head changed before commit.")
    expected_thread_type = "None" if hole.kind == "plain" else str(hole.standard)
    if str(operation.ThreadType) != expected_thread_type:
        raise NativeModelError("The Design Hole catalog selection changed before commit.")
    if hole.kind != "plain" and str(operation.ThreadSize) != hole.size:
        raise NativeModelError("The Design Hole size changed before commit.")
    if hole.kind == "clearance" and str(operation.ThreadFit) != hole.fit:
        raise NativeModelError("The Design Hole clearance fit changed before commit.")
    if hole.kind in {"threaded_cosmetic", "threaded_modeled"}:
        if (
            str(operation.ThreadClass) != hole.thread_class
            or str(operation.ThreadDirection)
            != ("Right" if hole.direction == "right" else "Left")
            or str(operation.ThreadDepthType)
            != _THREAD_DEPTH_TYPES[str(hole.thread_depth_kind)]
            or bool(operation.UseCustomThreadClearance)
            is not (hole.custom_clearance_mm is not None)
        ):
            raise NativeModelError("The Design Hole thread controls changed before commit.")
    if spec.head.kind == "catalog" and (
        bool(operation.HoleCutCustomValues)
        is not (spec.head.override_kind is not None)
    ):
        raise NativeModelError("The Design Hole catalog-head override changed before commit.")
    shape = operation.AddSubShape
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise NativeModelError("The Design Hole produced no valid cutter solids.")
    return {
        "hole_type": hole.kind,
        "diameter_mm": _quantity(operation.Diameter),
        "depth_type": spec.depth_kind,
        "head": str(operation.HoleCutType),
        "cutter_solid_count": len(shape.Solids),
    }


def create_design_hole(
    document: Any,
    *,
    label: str,
    spec: DesignHoleSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        operation.BaseProfileType = _BASE_PROFILE_VALUES[spec.base_profile]
        operation.DepthType = _DEPTH_TYPES[spec.depth_kind]
        if spec.depth_mm is not None:
            operation.Depth = spec.depth_mm
        operation.DrillPoint = "Flat" if spec.drill_kind == "flat" else "Angled"
        if spec.drill_angle_degrees is not None:
            operation.DrillPointAngle = spec.drill_angle_degrees
        operation.DrillForDepth = spec.depth_reference == "tip"
        operation.Reversed = spec.reversed
        _configure_hole_type(operation, spec.hole_type)
        operation.Tapered = spec.taper_kind == "tapered"
        if spec.taper_angle_degrees is not None:
            operation.TaperedAngle = spec.taper_angle_degrees
        _configure_head(operation, spec.head)
        return {"spec": spec}

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignHole",
        base_name="Hole",
        label=label,
        profile_spec=spec.profile,
        result_spec=spec.result,
        configure_specific=configure,
        verify_specific=_verify_hole,
        configure_after_targets=True,
    )
