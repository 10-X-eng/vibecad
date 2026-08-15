# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider-facing contracts over the exact Native Sketch runtimes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import parameters_schema
from VibeCADNativeSketchBatchSchema import sketch_batch_capability_definition
from VibeCADNativeSketchCleanupSchema import sketch_cleanup_capability_definitions
from VibeCADNativeSketchConstraintSchema import sketch_constraint_capability_definition
from VibeCADNativeSketchControlSchema import sketch_control_capability_definition
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition
from VibeCADNativeSketchInspectSchema import sketch_inspect_capability_definition
from VibeCADNativeSketchLimits import (
    DEFAULT_SKETCH_INSPECT_PAGE_SIZE,
    MAX_SKETCH_INSPECT_PAGE_SIZE,
)
from VibeCADNativeSketchPresentationSchema import (
    sketch_presentation_capability_definition,
)
from VibeCADNativeSketchRevision import SKETCH_REVISION_SCHEMA


DRAW_OPERATIONS = (
    "create_point",
    "create_line",
    "create_polyline",
    "create_arc",
    "create3_point_arc",
    "create_arc_of_ellipse",
    "create_arc_of_hyperbola",
    "create_arc_of_parabola",
    "create_circle",
    "create3_point_circle",
    "create_ellipse",
    "create3_point_ellipse",
    "create_rectangle",
    "create_center_rectangle",
    "create_oblong",
    "create_triangle",
    "create_square",
    "create_pentagon",
    "create_hexagon",
    "create_heptagon",
    "create_octagon",
    "create_regular_polygon",
    "create_slot",
    "create_arc_slot",
    "create_b_spline",
    "create_periodic_b_spline",
    "create_b_spline_by_interpolation",
    "create_periodic_b_spline_by_interpolation",
    "create_text",
)
LINE_DRAW_OPERATIONS = (
    "create_point",
    "create_line",
    "create_polyline",
)
ARC_DRAW_OPERATIONS = (
    "create_arc",
    "create_arc_of_ellipse",
    "create_arc_of_hyperbola",
    "create_arc_of_parabola",
)
THREE_POINT_ARC_DRAW_OPERATIONS = ("create3_point_arc",)
CIRCLE_DRAW_OPERATIONS = (
    "create_circle",
    "create3_point_circle",
)
ELLIPSE_DRAW_OPERATIONS = (
    "create_ellipse",
    "create3_point_ellipse",
)
PROFILE_DRAW_OPERATIONS = (
    "create_rectangle",
    "create_center_rectangle",
    "create_oblong",
    "create_triangle",
    "create_square",
    "create_pentagon",
    "create_hexagon",
    "create_heptagon",
    "create_octagon",
    "create_regular_polygon",
    "create_slot",
    "create_arc_slot",
)
SPLINE_DRAW_OPERATIONS = (
    "create_b_spline",
    "create_periodic_b_spline",
    "create_b_spline_by_interpolation",
    "create_periodic_b_spline_by_interpolation",
)
TEXT_DRAW_OPERATIONS = ("create_text",)
CONSTRAIN_OPERATIONS = (
    "constrain_coincident",
    "constrain_horizontal_vertical",
    "constrain_horizontal",
    "constrain_vertical",
    "constrain_parallel",
    "constrain_perpendicular",
    "constrain_tangent",
    "constrain_equal",
    "constrain_symmetric",
    "constrain_block",
    "constrain_group",
)
DIMENSION_OPERATIONS = (
    "infer_dimension",
    "constrain_distance_x",
    "constrain_distance_y",
    "constrain_distance",
    "constrain_radius_diameter",
    "constrain_radius",
    "constrain_diameter",
    "constrain_angle",
    "constrain_lock",
)
TRANSFORM_OPERATIONS = ("translate", "rotate", "scale", "offset", "symmetry")
EDIT_GEOMETRY_OPERATIONS = (
    "toggle_construction",
    "restore_internal_alignment_geometry",
    "remove_axis_alignment",
    "convert_to_nurbs",
    "increase_bspline_degree",
    "decrease_bspline_degree",
    "increase_bspline_knot_multiplicity",
    "decrease_bspline_knot_multiplicity",
    "insert_bspline_knot",
    "join_curves",
)
EDIT_CONSTRAINT_OPERATIONS = (
    "toggle_driving_reference",
    "toggle_active_inactive",
    "set_virtual_space",
)

GEOMETRY_CAPABILITY_NAMES = frozenset(
    {
        "sketch.draw_line",
        "sketch.draw_arc",
        "sketch.draw_three_point_arc",
        "sketch.draw_circle",
        "sketch.draw_ellipse",
        "sketch.draw_profile",
        "sketch.draw_spline",
        "sketch.draw_text",
        "sketch.transform",
        "sketch.edit",
        "sketch.fillet",
        "sketch.chamfer",
        "sketch.external",
        "sketch.trim",
        "sketch.split",
        "sketch.extend",
        "sketch.delete",
    }
)
CONSTRAINT_CAPABILITY_NAMES = frozenset(
    {"sketch.constrain", "sketch.dimension", "sketch.edit"}
)
SKETCH_PROVIDER_CAPABILITY_NAMES = frozenset(
    {
        *GEOMETRY_CAPABILITY_NAMES,
        *CONSTRAINT_CAPABILITY_NAMES,
        "sketch.batch",
        "sketch.inspect",
        "sketch.presentation",
        "sketch.control",
    }
)

_HOST_TARGET_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "expected_external_reference_count",
        "expected_source_geometry_count",
        "expected_source_constraint_count",
        "expected_source_external_geometry_count",
        "expected_source_external_reference_count",
    }
)


def _schema_number_bound(
    options: list[dict[str, Any]],
    inclusive: str,
    exclusive: str,
    chooser: Any,
) -> tuple[str, Any] | None:
    values = []
    for option in options:
        if inclusive in option:
            values.append((option[inclusive], inclusive))
        elif exclusive in option:
            values.append((option[exclusive], exclusive))
    if not values:
        return None
    value = chooser(item[0] for item in values)
    return inclusive, value


def _merge_schemas(raw_options: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Create one explicit typed envelope; semantic forms remain runtime-checked."""

    options = [_flatten_schema(option) for option in raw_options]
    if not options:
        raise ValueError("A schema envelope needs at least one option.")
    if len(options) == 1 or all(option == options[0] for option in options[1:]):
        return options[0]
    kinds = {option.get("type") for option in options}
    if kinds <= {"integer", "number"}:
        kind = "number" if "number" in kinds else "integer"
        result: dict[str, Any] = {"type": kind}
        lower = _schema_number_bound(options, "minimum", "exclusiveMinimum", min)
        upper = _schema_number_bound(options, "maximum", "exclusiveMaximum", max)
        if lower:
            result[lower[0]] = lower[1]
        if upper:
            result[upper[0]] = upper[1]
        enum_values = [
            value for option in options for value in list(option.get("enum") or [])
        ]
        if enum_values and all(set(option) <= {"type", "enum"} for option in options):
            result = {"type": kind, "enum": list(dict.fromkeys(enum_values))}
        return result
    if kinds == {"string"}:
        values = []
        for option in options:
            values.extend(option.get("enum", [option.get("const")]))
        values = [value for value in values if isinstance(value, str)]
        result = {"type": "string"}
        if values:
            result["enum"] = list(dict.fromkeys(values))
        lengths = [int(option["maxLength"]) for option in options if "maxLength" in option]
        if lengths:
            result["maxLength"] = max(lengths)
        return result
    if kinds == {"array"}:
        result = {
            "type": "array",
            "items": _merge_schemas(
                option.get("items", {}) for option in options
            ),
            "minItems": min(int(option.get("minItems", 0)) for option in options),
            "maxItems": max(int(option.get("maxItems", 1_000_000)) for option in options),
        }
        if all(option.get("uniqueItems") is True for option in options):
            result["uniqueItems"] = True
        return result
    if kinds == {"object"}:
        property_options: dict[str, list[Mapping[str, Any]]] = {}
        for option in options:
            for name, schema in dict(option.get("properties") or {}).items():
                property_options.setdefault(name, []).append(schema)
        properties = {
            name: _merge_schemas(choices)
            for name, choices in property_options.items()
        }
        required = [
            name
            for name in options[0].get("required", [])
            if all(name in option.get("required", []) for option in options[1:])
        ]
        result = parameters_schema(properties, tuple(required))
        form_schema = properties.get("form")
        if isinstance(form_schema, Mapping):
            forms = list(form_schema.get("enum") or [])
            if forms:
                entries = []
                for index, option in enumerate(options):
                    option_properties = dict(option.get("properties") or {})
                    option_form = option_properties.get("form", {})
                    labels = list(option_form.get("enum") or [])
                    if not labels and isinstance(option_form.get("const"), str):
                        labels = [option_form["const"]]
                    label = "|".join(labels) if labels else f"form_{index + 1}"
                    fields = [
                        name for name in option.get("required", []) if name != "form"
                    ]
                    entries.append(f"{label}={','.join(fields) or 'no additional fields'}")
                result["description"] = "Fields by form: " + "; ".join(entries) + "."
        return result
    if len(kinds) == 1 and None not in kinds:
        return _flatten_schema(options[0])
    raise ValueError(f"Cannot publish a precise Sketch schema across types {kinds!r}.")


def _flatten_schema(raw: Mapping[str, Any]) -> dict[str, Any]:
    schema = deepcopy(dict(raw))
    compositions = []
    for keyword in ("oneOf", "anyOf"):
        values = schema.pop(keyword, None)
        if isinstance(values, list):
            compositions.extend(value for value in values if isinstance(value, Mapping))
    if compositions:
        result = _merge_schemas(compositions)
        description = schema.get("description")
        if isinstance(description, str) and description.strip():
            result["description"] = description.strip()
        return result
    if isinstance(schema.get("properties"), Mapping):
        schema["properties"] = {
            name: _flatten_schema(value)
            for name, value in schema["properties"].items()
        }
    if isinstance(schema.get("items"), Mapping):
        schema["items"] = _flatten_schema(schema["items"])
    return schema


def _provider_parameters(
    raw: Mapping[str, Any],
    *,
    require_revision: bool,
) -> dict[str, Any]:
    source = _flatten_schema(raw)
    properties = {
        name: value
        for name, value in source["properties"].items()
        if name not in _HOST_TARGET_FIELDS
    }
    if require_revision:
        properties = {"revision": deepcopy(SKETCH_REVISION_SCHEMA), **properties}
    required = [
        name
        for name in source.get("required", [])
        if name not in _HOST_TARGET_FIELDS
    ]
    return parameters_schema(
        properties,
        (("revision", *required) if require_revision else tuple(required)),
    )


def _harmonize_variants(
    variants: Iterable[NativeCapabilityVariant],
) -> tuple[NativeCapabilityVariant, ...]:
    prepared = []
    for variant in variants:
        prepared.append(
            (
                variant,
                _provider_parameters(
                    variant.parameters,
                    require_revision=variant.operation != "read_state",
                ),
            )
        )
    property_options: dict[str, list[Mapping[str, Any]]] = {}
    for _variant, parameters in prepared:
        for name, schema in parameters["properties"].items():
            property_options.setdefault(name, []).append(schema)
    shared = {
        name: _merge_schemas(options)
        for name, options in property_options.items()
    }
    result = []
    for variant, parameters in prepared:
        parameters["properties"] = {
            name: deepcopy(shared[name])
            for name in parameters["properties"]
        }
        result.append(
            NativeCapabilityVariant(
                operation=variant.operation,
                description=variant.description,
                action_ids=variant.action_ids,
                surface_ids=variant.surface_ids,
                exact_target_type=variant.exact_target_type,
                transaction_behavior=variant.transaction_behavior,
                background_required=variant.background_required,
                parameters=parameters,
            )
        )
    return tuple(result)


def _definition(
    name: str,
    description: str,
    variants: Iterable[NativeCapabilityVariant],
    *,
    classification: str = "mutation",
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification=classification,
        variants=_harmonize_variants(variants),
    )


def sketch_provider_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    geometry = {v.operation: v for v in sketch_geometry_capability_definition().variants}
    constraint = {
        v.operation: v for v in sketch_constraint_capability_definition().variants
    }
    cleanup = {
        v.operation: v
        for definition in sketch_cleanup_capability_definitions()
        for v in definition.variants
    }
    inspect_source = sketch_inspect_capability_definition()
    inspect_read = NativeCapabilityVariant(
        operation="read_state",
        description="Read exact geometry, constraints, solver state, and the current revision.",
        action_ids=frozenset({"VibeCAD_NativeSketchState"}),
        surface_ids=frozenset({"sketch.edit"}),
        exact_target_type="HumanOpenedSketch",
        transaction_behavior="none",
        background_required=False,
        parameters=parameters_schema(
            {
                "geometry_offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1_000_000,
                },
                "constraint_offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1_000_000,
                },
                "external_geometry_offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1_000_000,
                },
                "page_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SKETCH_INSPECT_PAGE_SIZE,
                    "default": DEFAULT_SKETCH_INSPECT_PAGE_SIZE,
                    "description": (
                        "Items per geometry, constraint, and external-geometry page; "
                        f"integer 1 through {MAX_SKETCH_INSPECT_PAGE_SIZE} "
                        f"(default {DEFAULT_SKETCH_INSPECT_PAGE_SIZE})."
                    ),
                },
            },
            (),
        ),
    )
    inspect_variants = (inspect_read, *inspect_source.variants)
    definitions = (
        _definition(
            "sketch.draw_line",
            "Draw an exact point, line segment, or connected polyline.",
            (geometry[name] for name in LINE_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_arc",
            "Draw an exact center-defined circular or conic arc.",
            (geometry[name] for name in ARC_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_three_point_arc",
            (
                "Draw one circular arc from first_endpoint_mm through rim_point_mm "
                "to second_endpoint_mm."
            ),
            (geometry[name] for name in THREE_POINT_ARC_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_circle",
            "Draw an exact center-radius or three-point circle.",
            (geometry[name] for name in CIRCLE_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_ellipse",
            "Draw an exact center-defined or three-point ellipse.",
            (geometry[name] for name in ELLIPSE_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_profile",
            "Draw an exact standard closed profile, polygon, or slot.",
            (geometry[name] for name in PROFILE_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_spline",
            "Draw an exact open or periodic B-spline from poles or interpolation points.",
            (geometry[name] for name in SPLINE_DRAW_OPERATIONS),
        ),
        _definition(
            "sketch.draw_text",
            "Draw one exact Sketch text geometry.",
            (geometry[name] for name in TEXT_DRAW_OPERATIONS),
        ),
        _definition("sketch.constrain", "Apply geometric Sketch constraints.", (constraint[name] for name in CONSTRAIN_OPERATIONS)),
        _definition("sketch.dimension", "Apply dimensional Sketch constraints.", (constraint[name] for name in DIMENSION_OPERATIONS)),
        _definition("sketch.transform", "Transform exact Sketch geometry.", (geometry[name] for name in TRANSFORM_OPERATIONS)),
        _definition(
            "sketch.edit",
            "Edit exact Sketch geometry and constraint state.",
            tuple(geometry[name] for name in EDIT_GEOMETRY_OPERATIONS)
            + tuple(constraint[name] for name in EDIT_CONSTRAINT_OPERATIONS),
        ),
        _definition("sketch.fillet", "Fillet one exact Sketch corner.", (geometry["create_fillet"],)),
        _definition("sketch.chamfer", "Chamfer one exact Sketch corner.", (geometry["create_chamfer"],)),
        _definition("sketch.external", "Reference or copy external Sketch geometry.", (geometry[name] for name in ("project_external_geometry", "intersect_external_geometry", "carbon_copy"))),
        _definition("sketch.trim", "Trim one exact Sketch curve.", (cleanup["trim"],)),
        _definition("sketch.split", "Split one exact Sketch curve.", (cleanup["split"],)),
        _definition("sketch.extend", "Extend one exact Sketch curve.", (cleanup["extend"],)),
        _definition("sketch.delete", "Delete exact Sketch geometry.", (cleanup["delete_geometry"],)),
        _definition("sketch.batch", "Create primitive geometry and constraints atomically.", sketch_batch_capability_definition().variants),
        _definition("sketch.inspect", "Read the human-opened Sketch and exact relationships.", inspect_variants, classification="read"),
        _definition("sketch.presentation", "Control Sketch presentation.", sketch_presentation_capability_definition().variants, classification="view"),
        _definition("sketch.control", "Finish the human-opened Sketch session.", sketch_control_capability_definition().variants),
    )
    if {definition.name for definition in definitions} != SKETCH_PROVIDER_CAPABILITY_NAMES:
        raise RuntimeError("The Native Sketch provider definition set is incomplete.")
    return definitions


def register_sketch_provider_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in sketch_provider_capability_definitions():
        if definition.name == "sketch.batch":
            registry.register_shared_definition(definition)
        else:
            registry.register_definition(definition)


def sketch_runtime_variant_parameters(
    operation: str,
) -> Mapping[str, Any]:
    """Return the exact document-runtime contract for one provider operation."""

    definitions = (
        sketch_geometry_capability_definition(),
        sketch_constraint_capability_definition(),
        sketch_batch_capability_definition(),
        sketch_inspect_capability_definition(),
        sketch_presentation_capability_definition(),
        sketch_control_capability_definition(),
        *sketch_cleanup_capability_definitions(),
    )
    matches = [
        variant.parameters
        for definition in definitions
        for variant in definition.variants
        if variant.operation == operation
    ]
    if len(matches) != 1:
        raise KeyError(f"Sketch operation {operation!r} has no unique internal contract.")
    return matches[0]
