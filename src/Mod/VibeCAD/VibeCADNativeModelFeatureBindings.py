# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for the split Model feature family."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFeatureRuntime import NativeModelFeatureRuntime
from VibeCADNativeState import (
    NATIVE_PREVIEW_CONSUMED,
    NATIVE_PREVIEW_MISSING,
    NativeRevisionConflict,
    NativeStateError,
)
from VibeCADNativeTargets import document_uid


_FOCUSED_PROFILE_FEATURE_KINDS = {
    "model.extrude": "extrude",
    "model.revolve": "revolve",
    "model.loft": "loft",
    "model.sweep": "sweep",
    "model.helix": "helix",
}

MODEL_FEATURE_CAPABILITY_NAMES = (
    "model.feature",
    *_FOCUSED_PROFILE_FEATURE_KINDS,
    "model.primitive",
)

_PRIMITIVE_DEFAULTS = {
    "cylinder": {"sweep_degrees": 360.0},
    "sphere": {
        "latitude_start_degrees": -90.0,
        "latitude_end_degrees": 90.0,
        "sweep_degrees": 360.0,
    },
    "cone": {"sweep_degrees": 360.0},
    "ellipsoid": {
        "latitude_start_degrees": -90.0,
        "latitude_end_degrees": 90.0,
        "sweep_degrees": 360.0,
    },
    "torus": {
        "section_start_degrees": -180.0,
        "section_end_degrees": 180.0,
        "sweep_degrees": 360.0,
    },
}

_PRIMITIVE_FIELDS = {
    "box": ("length_mm", "width_mm", "height_mm"),
    "cylinder": ("radius_mm", "height_mm", "sweep_degrees"),
    "sphere": (
        "radius_mm",
        "latitude_start_degrees",
        "latitude_end_degrees",
        "sweep_degrees",
    ),
    "cone": ("radius1_mm", "radius2_mm", "height_mm", "sweep_degrees"),
    "ellipsoid": (
        "radius_x_mm",
        "radius_y_mm",
        "radius_z_mm",
        "latitude_start_degrees",
        "latitude_end_degrees",
        "sweep_degrees",
    ),
    "torus": (
        "major_radius_mm",
        "minor_radius_mm",
        "section_start_degrees",
        "section_end_degrees",
        "sweep_degrees",
    ),
    "prism": ("sides", "circumradius_mm", "height_mm"),
    "wedge": (
        "xmin_mm",
        "ymin_mm",
        "zmin_mm",
        "x2min_mm",
        "z2min_mm",
        "xmax_mm",
        "ymax_mm",
        "zmax_mm",
        "x2max_mm",
        "z2max_mm",
    ),
    "tube": ("outer_radius_mm", "inner_radius_mm", "height_mm"),
}


def _primitive_local_center(kind: str, values: Mapping[str, Any]) -> tuple[float, float, float]:
    if kind == "box":
        return (
            0.5 * float(values["length_mm"]),
            0.5 * float(values["width_mm"]),
            0.5 * float(values["height_mm"]),
        )
    if kind in {"cylinder", "cone", "prism", "tube"}:
        return (0.0, 0.0, 0.5 * float(values["height_mm"]))
    if kind == "wedge":
        return (
            0.5 * (float(values["xmin_mm"]) + float(values["xmax_mm"])),
            0.5 * (float(values["ymin_mm"]) + float(values["ymax_mm"])),
            0.5 * (float(values["zmin_mm"]) + float(values["zmax_mm"])),
        )
    return (0.0, 0.0, 0.0)


def _rotate_vector(
    vector: tuple[float, float, float],
    rotation: Mapping[str, Any],
) -> tuple[float, float, float]:
    axis = rotation["axis"]
    axis_values = tuple(float(axis[name]) for name in ("x", "y", "z"))
    magnitude = math.sqrt(sum(value * value for value in axis_values))
    if magnitude < 1.0e-12:
        raise NativeModelError("A Design rotation axis must be non-zero.")
    unit = tuple(value / magnitude for value in axis_values)
    angle = math.radians(float(rotation["angle_degrees"]))
    cosine = math.cos(angle)
    sine = math.sin(angle)
    dot = sum(left * right for left, right in zip(unit, vector, strict=True))
    cross = (
        unit[1] * vector[2] - unit[2] * vector[1],
        unit[2] * vector[0] - unit[0] * vector[2],
        unit[0] * vector[1] - unit[1] * vector[0],
    )
    return tuple(
        vector[index] * cosine
        + cross[index] * sine
        + unit[index] * dot * (1.0 - cosine)
        for index in range(3)
    )


def _centered_primitive_placement(
    kind: str,
    values: Mapping[str, Any],
    center: Mapping[str, Any],
    rotation: Mapping[str, Any],
) -> dict[str, Any]:
    local_center = _rotate_vector(_primitive_local_center(kind, values), rotation)
    center_values = tuple(float(center[name]) for name in ("x", "y", "z"))
    return {
        "origin_mm": {
            name: center_values[index] - local_center[index]
            for index, name in enumerate(("x", "y", "z"))
        },
        "rotation": {
            "axis": dict(rotation["axis"]),
            "angle_degrees": float(rotation["angle_degrees"]),
        },
    }


def _aligned_sketch_axis(
    *,
    horizontal: tuple[float, float, float],
    vertical: tuple[float, float, float],
    requested: str,
) -> tuple[str, bool]:
    target = {
        "X": (1.0, 0.0, 0.0),
        "Y": (0.0, 1.0, 0.0),
        "Z": (0.0, 0.0, 1.0),
    }.get(requested)
    if target is None:
        raise NativeModelError("A revolution axis must be global X, Y, or Z.")
    for name, direction in (("H_Axis", horizontal), ("V_Axis", vertical)):
        magnitude = math.sqrt(sum(float(value) ** 2 for value in direction))
        if not math.isfinite(magnitude) or magnitude < 1.0e-12:
            raise NativeModelError("The Sketch has an invalid global placement.")
        dot = sum(
            float(value) * target[index] / magnitude
            for index, value in enumerate(direction)
        )
        if abs(abs(dot) - 1.0) <= 1.0e-7:
            return name, dot < 0.0
    raise NativeModelError(
        f"Global {requested} does not lie in Sketch; choose X, Y, or Z in its plane."
    )


def _mutate_profile_feature(
    runtime: NativeModelFeatureRuntime,
    arguments: Mapping[str, Any],
    ticket: Any,
) -> Mapping[str, Any]:
    feature = dict(arguments["feature"])
    operation = str(feature.pop("kind"))
    profile = dict(arguments["profile"])
    requested_combination = arguments.get("combine")
    if requested_combination is None:
        result = {
            "mode": "new_body",
            "targets": [],
            "destination_component": arguments.get("destination_component"),
        }
    else:
        result = {
            "mode": requested_combination["kind"],
            "targets": requested_combination["bodies"],
            "destination_component": None,
        }
    if result["mode"] == "new_body" and result["destination_component"] is None:
        destination = runtime.profile_destination_component(profile)
        if destination is not None:
            result["destination_component"] = destination
    definition = feature
    if operation == "extrude":
        extent = dict(definition["extent"])
        extent["reversed"] = bool(extent.pop("reversed", False))
        definition["extent"] = extent
    axis_reversed = False
    axis = definition.get("axis")
    if (
        operation in {"revolve", "helix"}
        and isinstance(axis, Mapping)
        and axis.get("kind") == "global_axis"
    ):
        horizontal, vertical = runtime.profile_global_axes(profile)
        axis_name, axis_reversed = _aligned_sketch_axis(
            horizontal=horizontal,
            vertical=vertical,
            requested=str(axis["axis"]),
        )
        definition["axis"] = {
            "object_name": str(profile["object_name"]),
            "subelements": [axis_name],
        }
    elif operation in {"revolve", "helix"} and isinstance(axis, Mapping):
        definition["axis"] = {
            "object_name": str(axis["object_name"]),
            "subelements": [str(axis["subelement"])],
        }
    if operation == "revolve":
        extent = dict(definition["extent"])
        kind = str(extent["kind"])
        direction = str(extent.pop("direction", "forward"))
        if axis_reversed and direction != "symmetric":
            direction = "reverse" if direction == "forward" else "forward"
        if kind == "angle":
            extent["symmetric"] = direction == "symmetric"
        if kind != "up_to_last":
            extent["reversed"] = direction == "reverse"
        definition["extent"] = extent
    if operation == "helix" and axis_reversed:
        definition["reversed"] = not bool(definition["reversed"])
    if operation == "sweep":
        options = dict(definition["options"])
        transformation = dict(options["transformation"])
        options["transformation"] = transformation.pop("kind")
        options["sections"] = transformation.pop("sections", [])
        definition["options"] = options
    return runtime.mutate_feature(
        {
            "operation": "profile",
            "label": arguments["label"],
            "profile": profile,
            "result": result,
            "definition": {"kind": operation, **definition},
        },
        ticket=ticket,
    )


def _feature(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelFeatureRuntime):
        raise TypeError("A Model feature call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model feature call requires argument data.")
    return _mutate_profile_feature(
        runtime,
        arguments,
        getattr(call, "ticket", None),
    )


def _focused_extrude_side(values: Mapping[str, Any]) -> dict[str, Any]:
    side = dict(values)
    if side["kind"] == "length":
        side["taper_degrees"] = float(side.get("taper_degrees", 0.0))
    else:
        side["offset_mm"] = float(side.get("offset_mm", 0.0))
    return side


def _focused_extrude_definition(arguments: Mapping[str, Any]) -> dict[str, Any]:
    direction_axis = arguments.get("direction_axis")
    direction_vector = arguments.get("direction_vector")
    if direction_axis is not None and direction_vector is not None:
        raise NativeModelError(
            "An Extrude direction uses either an axis or a vector, not both."
        )
    align = bool(arguments.get("align_with_sketch_normal", True))
    if direction_axis is not None:
        direction = {
            "kind": "reference_axis",
            "target": direction_axis,
            "along_sketch_normal": align,
        }
    elif direction_vector is not None:
        direction = {
            "kind": "custom_vector",
            "vector": direction_vector,
            "along_sketch_normal": align,
        }
    else:
        direction = {"kind": "sketch_normal"}

    requested_extent = dict(arguments["extent"])
    kind = str(requested_extent.pop("kind"))
    if kind == "two_sides":
        extent = {
            "kind": "two_sides",
            "sides": [
                _focused_extrude_side(requested_extent.pop("side1")),
                _focused_extrude_side(requested_extent.pop("side2")),
            ],
            "reversed": bool(requested_extent.pop("reversed", False)),
        }
    else:
        direction_name = str(requested_extent.pop("direction", "forward"))
        extent = {
            "kind": "symmetric" if direction_name == "symmetric" else "one_side",
            "sides": [
                _focused_extrude_side({"kind": kind, **requested_extent})
            ],
            "reversed": direction_name == "reverse",
        }
    return {"kind": "extrude", "direction": direction, "extent": extent}


def _maybe_preview_or_apply_extrude(
    runtime: NativeModelFeatureRuntime,
    arguments: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    stage = str(arguments.get("stage") or "propose").strip()
    if stage == "apply":
        return None
    if stage != "propose":
        raise NativeModelError("model.extrude stage must be propose or apply.")
    uid = document_uid(runtime._context.document)
    return runtime._context.state.propose_mutation_preview(
        uid,
        capability_name="model.extrude",
        arguments=arguments,
    )


def _extrude_apply_arguments(
    runtime: NativeModelFeatureRuntime,
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    stage = str(arguments.get("stage") or "propose").strip()
    if stage != "apply":
        return arguments
    preview_id = str(arguments.get("preview_id") or "").strip()
    if not preview_id:
        raise NativeModelError("model.extrude apply needs preview_id.")
    uid = document_uid(runtime._context.document)
    try:
        return runtime._context.state.consume_mutation_preview(
            uid,
            preview_id,
            capability_name="model.extrude",
        )
    except NativeRevisionConflict:
        raise
    except NativeStateError as exc:
        raise NativeModelError(str(exc)) from exc


def _focused_feature(kind: str):
    def execute(call: Any) -> Mapping[str, Any]:
        runtime = getattr(call, "runtime", None)
        arguments = getattr(call, "arguments", None)
        if not isinstance(runtime, NativeModelFeatureRuntime):
            raise TypeError("A Model feature call requires its exact runtime.")
        if not isinstance(arguments, Mapping):
            raise TypeError("A Model feature call requires argument data.")
        if kind == "extrude":
            previewed = _maybe_preview_or_apply_extrude(runtime, arguments)
            if previewed is not None:
                return previewed
            arguments = _extrude_apply_arguments(runtime, arguments)
        profile = dict(arguments["profile"])
        profile_scope = str(arguments["profile_scope"])
        internal_faces = arguments.get("internal_faces")
        if profile_scope == "entire_sketch":
            if internal_faces is not None:
                raise NativeModelError(
                    "An entire-sketch profile does not take internal faces."
                )
        elif profile_scope == "selected_internal_faces":
            if not isinstance(internal_faces, list) or not internal_faces:
                raise NativeModelError(
                    "A selected-internal-faces profile needs internal_faces."
                )
            profile["regions"] = internal_faces
        else:
            raise NativeModelError("That profile scope is unavailable.")
        common = {
            name: arguments[name]
            for name in (
                "operation",
                "label",
                "combine",
                "destination_component",
            )
            if name in arguments
        }
        common["profile"] = profile
        focused_fields = {
            "operation",
            "label",
            "profile",
            "profile_scope",
            "internal_faces",
            "combine",
            "destination_component",
            "stage",
            "preview_id",
        }
        feature = (
            _focused_extrude_definition(arguments)
            if kind == "extrude"
            else {
                "kind": kind,
                **{
                    name: value
                    for name, value in arguments.items()
                    if name not in focused_fields
                },
            }
        )
        return _mutate_profile_feature(
            runtime,
            {**common, "feature": feature},
            getattr(call, "ticket", None),
        )

    return execute


def _primitive(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelFeatureRuntime):
        raise TypeError("A Model primitive call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model primitive call requires argument data.")
    kind = str(arguments["operation"])
    supplied = {
        name: arguments[name]
        for name in _PRIMITIVE_FIELDS[kind]
        if name in arguments
    }
    values = {**_PRIMITIVE_DEFAULTS.get(kind, {}), **supplied}
    rotation = arguments.get("rotation") or {
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "angle_degrees": 0.0,
    }
    placement = _centered_primitive_placement(
        kind,
        values,
        arguments["center_mm"],
        rotation,
    )
    result = runtime.mutate_feature(
        {
            "operation": "primitive",
            "label": arguments["label"],
            "placement": placement,
            "result": {
                "mode": "new_body",
                "targets": [],
                "destination_component": None,
            },
            "definition": {
                "kind": kind,
                **{name: values[name] for name in _PRIMITIVE_FIELDS[kind]},
            },
        },
        ticket=getattr(call, "ticket", None),
    )
    bodies = result.pop("bodies", None)
    if not isinstance(bodies, list) or len(bodies) != 1:
        raise NativeModelError("A primitive must return one exact Body.")
    body = dict(bodies[0])
    body_reference = body.pop("body", None)
    feature_reference = result.pop("operation", None)
    result.pop("result_mode", None)
    if not isinstance(body_reference, Mapping) or not isinstance(
        feature_reference,
        Mapping,
    ):
        raise NativeModelError("A primitive result has invalid object identities.")
    return {
        "body": dict(body_reference),
        "feature": dict(feature_reference),
        **body,
        **result,
    }


def register_model_feature_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation("model.feature", _feature)
    )
    for capability, kind in _FOCUSED_PROFILE_FEATURE_KINDS.items():
        registry.register_implementation(
            NativeCapabilityImplementation(capability, _focused_feature(kind))
        )
    registry.register_implementation(
        NativeCapabilityImplementation("model.primitive", _primitive)
    )


def model_feature_runtime_bindings(
    runtime: NativeModelFeatureRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelFeatureRuntime):
        raise TypeError("runtime must be a NativeModelFeatureRuntime")
    return {
        "model.feature": runtime,
        **{name: runtime for name in _FOCUSED_PROFILE_FEATURE_KINDS},
        "model.primitive": runtime,
    }
