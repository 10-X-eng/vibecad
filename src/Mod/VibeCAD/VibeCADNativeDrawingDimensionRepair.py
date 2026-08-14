# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional replacement of TechDraw dimension references."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionState import (
    drawing_axonometric_dimension_state,
    drawing_dimension_repair_state,
    drawing_dimension_state,
    drawing_extent_state,
)
from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    exact_drawing_mapping,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingSpecialDimensionState import (
    drawing_arc_length_dimension_state,
    drawing_chamfer_dimension_state,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_KIND_TYPES = {
    "length": "Distance",
    "horizontal": "DistanceX",
    "vertical": "DistanceY",
    "radius": "Radius",
    "diameter": "Diameter",
    "angle": "Angle",
    "three_point_angle": "Angle3Pt",
    "area": "Area",
    "horizontal_extent": "DistanceX",
    "vertical_extent": "DistanceY",
    "horizontal_chamfer": "DistanceX",
    "vertical_chamfer": "DistanceY",
    "arc_length": "Distance",
    "axonometric_length": "Distance",
}
_LINEAR_KINDS = frozenset({"length", "horizontal", "vertical"})
_EXTENT_KINDS = frozenset({"horizontal_extent", "vertical_extent"})
_CHAMFER_KINDS = frozenset({"horizontal_chamfer", "vertical_chamfer"})
_STANDARD_KINDS = frozenset(_KIND_TYPES) - _EXTENT_KINDS - _CHAMFER_KINDS - {
    "arc_length",
    "axonometric_length",
}
_VALUE_MODES = frozenset(
    {
        "projected",
        "x_axis_true_length",
        "y_axis_true_length",
        "z_axis_true_length",
    }
)
_REPLACEMENT_KEYS = {
    "length": frozenset({"kind", "references"}),
    "horizontal": frozenset({"kind", "references"}),
    "vertical": frozenset({"kind", "references"}),
    "radius": frozenset({"kind", "edge", "allow_approximate"}),
    "diameter": frozenset({"kind", "edge", "allow_approximate"}),
    "angle": frozenset({"kind", "first_edge", "second_edge"}),
    "three_point_angle": frozenset(
        {"kind", "first_arm_point", "apex_point", "second_arm_point"}
    ),
    "area": frozenset({"kind", "face"}),
    "horizontal_extent": frozenset({"kind", "extent"}),
    "vertical_extent": frozenset({"kind", "extent"}),
    "horizontal_chamfer": frozenset(
        {"kind", "first_vertex", "second_vertex"}
    ),
    "vertical_chamfer": frozenset(
        {"kind", "first_vertex", "second_vertex"}
    ),
    "arc_length": frozenset({"kind", "arc_edge"}),
    "axonometric_length": frozenset(
        {
            "kind",
            "measurement",
            "extension_direction_edge",
            "expected_value_mode",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class DimensionRepairSpec:
    kind: str
    dimension_type: str
    subelements: tuple[str, ...]
    all_element_targets: tuple[Mapping[str, Any], ...]
    allowed_element_types: frozenset[str]
    allow_approximate: bool = False
    extent_scope: str = "references"
    dimension_direction_subelement: str = ""
    extension_direction_subelement: str = ""
    expected_value_mode: str = ""


@dataclass(frozen=True, slots=True)
class PreparedDrawingDimensionRepair:
    dimension: Any
    dimension_state_before: dict[str, Any]
    preserved_state_before: dict[str, Any]
    target: PreparedDrawingDimensionTarget
    spec: DimensionRepairSpec
    host_validation: dict[str, Any]


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _element(value: Any, noun: str, prefix: str) -> Mapping[str, Any]:
    target = exact_drawing_mapping(
        value,
        frozenset({"subelement", "expected_element_state_sha256"}),
        noun,
        family="dimension repair",
        error_code="NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
    )
    if not str(target["subelement"] or "").startswith(prefix):
        _error(
            f"The {noun} must be one exact projected {prefix}N reference.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_REFERENCE_TYPE_INVALID",
            repair={"accepted_reference_type": prefix.casefold()},
        )
    return target


def _linear_references(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2:
        _error(
            "A repaired linear dimension requires one or two exact references.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    result = tuple(
        exact_drawing_mapping(
            item,
            frozenset({"subelement", "expected_element_state_sha256"}),
            "linear reference",
            family="dimension repair",
            error_code="NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
        for item in value
    )
    if any(
        not str(item["subelement"] or "").startswith(("Edge", "Vertex"))
        for item in result
    ):
        _error(
            "A repaired linear dimension accepts only projected EdgeN or VertexN references.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_REFERENCE_TYPE_INVALID",
        )
    return result


def _extent(value: Any) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    if not isinstance(value, Mapping):
        _error(
            "A repaired extent target must be an object.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    if value.get("scope") == "whole_view" and frozenset(value) == {"scope"}:
        return "whole_view", ()
    if value.get("scope") == "edges" and frozenset(value) == {"scope", "edges"}:
        raw = value["edges"]
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 64:
            _error(
                "An edge-scoped repaired extent requires one to sixty-four exact edges.",
                "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
            )
        return "edges", tuple(
            _element(item, "extent edge", "Edge") for item in raw
        )
    _error(
        "A repaired extent must be exactly whole_view or edges scope.",
        "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
    )


def _axonometric(
    replacement: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any],
    Mapping[str, Any],
    str,
]:
    measurement = replacement["measurement"]
    if not isinstance(measurement, Mapping):
        _error(
            "An axonometric repair measurement must be an object.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    if measurement.get("kind") == "edge" and frozenset(measurement) == {
        "kind",
        "dimension_edge",
    }:
        direction = _element(
            measurement["dimension_edge"], "dimension edge", "Edge"
        )
        references = (direction,)
    elif measurement.get("kind") == "vertex_pair" and frozenset(measurement) == {
        "kind",
        "first_vertex",
        "second_vertex",
        "dimension_direction_edge",
    }:
        references = (
            _element(measurement["first_vertex"], "first vertex", "Vertex"),
            _element(measurement["second_vertex"], "second vertex", "Vertex"),
        )
        direction = _element(
            measurement["dimension_direction_edge"],
            "dimension-direction edge",
            "Edge",
        )
    else:
        _error(
            "An axonometric repair measurement must be exactly edge or vertex_pair.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    extension = _element(
        replacement["extension_direction_edge"],
        "extension-direction edge",
        "Edge",
    )
    expected_mode = str(replacement["expected_value_mode"] or "")
    if expected_mode not in _VALUE_MODES:
        _error(
            "The axonometric repair expected_value_mode is unsupported.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
            repair={"allowed_values": sorted(_VALUE_MODES)},
        )
    return references, direction, extension, expected_mode


def _spec(value: Any) -> DimensionRepairSpec:
    if not isinstance(value, Mapping):
        _error(
            "The Drawing dimension replacement must be one published object branch.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    kind = str(value.get("kind", "") or "")
    if kind not in _KIND_TYPES:
        _error(
            "The Drawing dimension replacement kind is unsupported.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
            repair={"allowed_values": sorted(_KIND_TYPES)},
        )
    if frozenset(value) != _REPLACEMENT_KEYS[kind]:
        _error(
            f"The {kind} replacement contains missing or unrelated fields.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
        )
    targets: tuple[Mapping[str, Any], ...]
    scope = "references"
    allow_approximate = False
    direction = ""
    extension = ""
    expected_mode = ""
    if kind in _LINEAR_KINDS:
        targets = _linear_references(value["references"])
    elif kind in {"radius", "diameter"}:
        targets = (_element(value["edge"], "radial edge", "Edge"),)
        allow_approximate = value["allow_approximate"]
        if type(allow_approximate) is not bool:
            _error(
                "allow_approximate must be true or false.",
                "NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
            )
    elif kind == "angle":
        targets = (
            _element(value["first_edge"], "first angle edge", "Edge"),
            _element(value["second_edge"], "second angle edge", "Edge"),
        )
    elif kind == "three_point_angle":
        targets = (
            _element(value["first_arm_point"], "first arm point", "Vertex"),
            _element(value["apex_point"], "apex point", "Vertex"),
            _element(value["second_arm_point"], "second arm point", "Vertex"),
        )
    elif kind == "area":
        targets = (_element(value["face"], "area face", "Face"),)
    elif kind in _EXTENT_KINDS:
        scope, targets = _extent(value["extent"])
    elif kind in _CHAMFER_KINDS:
        targets = (
            _element(value["first_vertex"], "first chamfer vertex", "Vertex"),
            _element(value["second_vertex"], "second chamfer vertex", "Vertex"),
        )
    elif kind == "arc_length":
        targets = (_element(value["arc_edge"], "arc-length edge", "Edge"),)
    else:
        references, direction_target, extension_target, expected_mode = _axonometric(
            value
        )
        targets = (*references, extension_target)
        if all(
            target["subelement"] != direction_target["subelement"]
            for target in references
        ):
            targets = (*references, direction_target, extension_target)
        direction = str(direction_target["subelement"])
        extension = str(extension_target["subelement"])
    names = tuple(str(target["subelement"]) for target in targets)
    if len(names) != len(set(names)):
        _error(
            "A Drawing dimension repair cannot repeat an exact projected reference.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_REFERENCES_INVALID",
        )
    measurement_names = names
    if kind == "axonometric_length":
        measurement_names = tuple(
            str(target["subelement"]) for target in references
        )
    allowed = frozenset(
        "edge"
        if name.startswith("Edge")
        else "vertex"
        if name.startswith("Vertex")
        else "face"
        for name in names
    )
    return DimensionRepairSpec(
        kind=kind,
        dimension_type=_KIND_TYPES[kind],
        subelements=measurement_names,
        all_element_targets=targets,
        allowed_element_types=allowed,
        allow_approximate=bool(allow_approximate),
        extent_scope=scope,
        dimension_direction_subelement=direction,
        extension_direction_subelement=extension,
        expected_value_mode=expected_mode,
    )


def _resolve_dimension(document: Any, value: Any) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        value,
        frozenset({"object_name", "expected_repair_state_sha256"}),
        "dimension target",
        family="dimension repair",
        error_code="NATIVE_DRAWING_DIMENSION_REPAIR_PARAMETERS_INVALID",
    )
    dimension = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawViewDimension",),
    )
    state = drawing_dimension_repair_state(dimension)
    if str(exact["expected_repair_state_sha256"]) != state["repair_state_sha256"]:
        _error(
            "The exact Drawing dimension changed after it was inspected.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_STALE",
            repair={"current_repair_state_sha256": state["repair_state_sha256"]},
        )
    if not state["repairable"]:
        _error(
            "The exact Drawing dimension cannot be repaired at the current History position.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_TARGET_UNAVAILABLE",
            repair={"issues": state["issues"]},
        )
    return dimension, state


def _preserved_state(dimension: Any) -> dict[str, Any]:
    vector = getattr(dimension, "AreaLeaderPoint", None)
    return {
        "label": str(dimension.Label),
        "x": float(dimension.X),
        "y": float(dimension.Y),
        "type": str(dimension.Type),
        "measure_type": str(dimension.MeasureType),
        "format_spec": str(dimension.FormatSpec),
        "format_over": str(dimension.FormatSpecOverTolerance),
        "format_under": str(dimension.FormatSpecUnderTolerance),
        "arbitrary": bool(dimension.Arbitrary),
        "arbitrary_tolerances": bool(dimension.ArbitraryTolerances),
        "theoretical_exact": bool(dimension.TheoreticalExact),
        "equal_tolerance": bool(dimension.EqualTolerance),
        "over_tolerance": float(dimension.OverTolerance),
        "under_tolerance": float(dimension.UnderTolerance),
        "inverted": bool(dimension.Inverted),
        "show_supplementary": bool(dimension.ShowSupplementary),
        "angle_override": bool(dimension.AngleOverride),
        "line_angle": float(dimension.LineAngle),
        "extension_angle": float(dimension.ExtensionAngle),
        "use_actual_area": bool(dimension.UseActualArea),
        "use_area_leader_point": bool(dimension.UseAreaLeaderPoint),
        "area_leader_point": (
            float(getattr(vector, "x", 0.0)),
            float(getattr(vector, "y", 0.0)),
            float(getattr(vector, "z", 0.0)),
        ),
        "show_units": bool(dimension.ShowUnits),
    }


def _validate_host(view: Any, spec: DimensionRepairSpec) -> dict[str, Any]:
    try:
        if spec.kind == "axonometric_length":
            from TechDrawTools.AxoLengthDimension import analyze_axonometric_length

            analysis = analyze_axonometric_length(
                view,
                spec.subelements,
                spec.dimension_direction_subelement,
                spec.extension_direction_subelement,
            )
            if analysis.value_mode != spec.expected_value_mode:
                _error(
                    "The axonometric value mode changed after it was inspected.",
                    "NATIVE_DRAWING_DIMENSION_REPAIR_INFERENCE_STALE",
                    repair={"current_value_mode": analysis.value_mode},
                )
            return {
                "geometry_configuration": "axonometric_length",
                "value_mode": analysis.value_mode,
                "line_angle_degrees": float(analysis.line_angle_degrees),
                "extension_angle_degrees": float(analysis.extension_angle_degrees),
            }
        import TechDrawGui

        if spec.kind in _EXTENT_KINDS:
            return dict(
                TechDrawGui.validateProjectedExtent(
                    view, spec.dimension_type, list(spec.subelements)
                )
            )
        if spec.kind in _CHAMFER_KINDS:
            return dict(
                TechDrawGui.validateProjectedChamfer(
                    view, spec.dimension_type, list(spec.subelements)
                )
            )
        if spec.kind == "arc_length":
            return dict(TechDrawGui.validateProjectedArcLength(view, spec.subelements[0]))
        return dict(
            TechDrawGui.validateProjectedDimension(
                view,
                spec.dimension_type,
                list(spec.subelements),
                spec.allow_approximate,
            )
        )
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw rejected the {spec.kind} replacement: {str(exc).strip()}",
            "NATIVE_DRAWING_DIMENSION_REPAIR_REFERENCES_INVALID",
            repair={
                "repair_kind": spec.kind,
                "requested_subelements": list(spec.subelements),
                "inspect_operation": "drawing_projected_geometry",
            },
        )


def _current_reference_pairs(state: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(item["object_name"]), str(item["subelement"]))
        for item in state["references_2d"]
    ]


def _assert_change(
    dimension: Any,
    state: Mapping[str, Any],
    view: Any,
    spec: DimensionRepairSpec,
) -> None:
    expected_names = list(spec.subelements)
    if spec.kind in _EXTENT_KINDS and spec.extent_scope == "whole_view":
        expected_names = [""]
    current = _current_reference_pairs(state)
    expected = [(str(view.Name), name) for name in expected_names]
    if (
        current == expected
        and not state["references_3d"]
        and state["valid"]
        and not state["error"]
    ):
        _error(
            "The Drawing dimension already has these valid projected references.",
            "NATIVE_DRAWING_NO_CHANGE",
        )


def prepare_drawing_dimension_repair(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingDimensionRepair:
    if operation != "repair_references":
        raise ValueError("operation is not Drawing dimension repair")
    spec = _spec(values["replacement"])
    dimension, dimension_state = _resolve_dimension(document, values["dimension"])
    if (
        dimension_state["repair_kind"] != spec.kind
        or dimension_state["dimension_type"] != spec.dimension_type
    ):
        _error(
            "The replacement branch does not match the exact dimension's semantic kind.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_KIND_MISMATCH",
            repair={"required_repair_kind": dimension_state["repair_kind"]},
        )
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=spec.all_element_targets,
        allowed_element_types=spec.allowed_element_types,
        family="dimension repair",
        code_prefix="NATIVE_DRAWING_DIMENSION_REPAIR",
    )
    if (
        dimension.findParentPage() is not target.page
        or dimension not in tuple(target.page.Views or ())
        or dimension_state["page_name"] != str(target.page.Name)
    ):
        _error(
            "The exact dimension and replacement view must belong to the exact page.",
            "NATIVE_DRAWING_DIMENSION_REPAIR_PAGE_MISMATCH",
        )
    _assert_change(dimension, dimension_state, target.view, spec)
    return PreparedDrawingDimensionRepair(
        dimension=dimension,
        dimension_state_before=dimension_state,
        preserved_state_before=_preserved_state(dimension),
        target=target,
        spec=spec,
        host_validation=_validate_host(target.view, spec),
    )


def mutate_drawing_dimension_repair(
    _document: Any,
    *,
    prepared: PreparedDrawingDimensionRepair,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingDimensionRepair):
        raise TypeError("prepared must be a PreparedDrawingDimensionRepair")
    import TechDrawGui

    spec = prepared.spec
    dimension = prepared.dimension
    try:
        if spec.kind in _EXTENT_KINDS:
            TechDrawGui.repairProjectedExtent(
                dimension, prepared.target.view, list(spec.subelements)
            )
        elif spec.kind in _CHAMFER_KINDS:
            TechDrawGui.repairProjectedChamfer(
                dimension, prepared.target.view, list(spec.subelements)
            )
        elif spec.kind == "arc_length":
            TechDrawGui.repairProjectedArcLength(
                dimension, prepared.target.view, spec.subelements[0]
            )
        elif spec.kind == "axonometric_length":
            from TechDrawTools.AxoLengthDimension import repair_axonometric_length

            result = repair_axonometric_length(
                dimension,
                prepared.target.view,
                spec.subelements,
                spec.dimension_direction_subelement,
                spec.extension_direction_subelement,
            )
            if result.analysis.value_mode != spec.expected_value_mode:
                raise RuntimeError("The axonometric value mode changed during repair")
        else:
            TechDrawGui.repairProjectedDimension(
                dimension,
                prepared.target.view,
                list(spec.subelements),
                spec.allow_approximate,
            )
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_REPAIR_FAILED",
            f"TechDraw could not repair the {spec.kind} dimension: {str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(dimension, prepared.target.page),
        changed=(object_identity(dimension),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_DIMENSION_REPAIR_POSTCONDITION_FAILED",
        message,
    )


def _assert_boundary(prepared: PreparedDrawingDimensionRepair) -> None:
    target = prepared.target
    document = prepared.dimension.Document
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, target.objects_before))
        or tuple(map(drawing_object_key, tuple(target.page.Views or ())))
        != tuple(map(drawing_object_key, target.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, target.timeline_before))
    ):
        _postcondition_error(
            "Dimension repair changed document objects, page membership, or History."
        )
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
        or drawing_projected_geometry_state(target.view)[
            "projection_state_sha256"
        ]
        != target.projection_state_before["projection_state_sha256"]
    ):
        _postcondition_error("Dimension repair changed the replacement projection.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Dimension repair changed the human selection.")
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility)) for obj, _ in target.visibility_before
    )
    if visibility != target.visibility_before:
        _postcondition_error("Dimension repair changed object visibility.")


def _expected_state(prepared: PreparedDrawingDimensionRepair) -> dict[str, Any]:
    dimension = prepared.dimension
    kind = prepared.spec.kind
    if kind in _EXTENT_KINDS:
        return drawing_extent_state(dimension)
    if kind in _CHAMFER_KINDS:
        return drawing_chamfer_dimension_state(dimension)
    if kind == "arc_length":
        return drawing_arc_length_dimension_state(dimension)
    if kind == "axonometric_length":
        return drawing_axonometric_dimension_state(dimension)
    return drawing_dimension_state(dimension)


def _assert_preserved(prepared: PreparedDrawingDimensionRepair) -> None:
    before = prepared.preserved_state_before
    after = _preserved_state(prepared.dimension)
    allowed: set[str] = set()
    if prepared.spec.kind in _CHAMFER_KINDS | {"arc_length"}:
        allowed.add("format_spec")
    if prepared.spec.kind == "arc_length":
        allowed.add("arbitrary")
    if prepared.spec.kind == "axonometric_length":
        allowed.update({"format_spec", "arbitrary", "line_angle", "extension_angle"})
    changed = sorted(
        name for name in before if name not in allowed and after[name] != before[name]
    )
    if changed:
        _postcondition_error(
            "Dimension repair changed unrelated persisted fields: "
            + ", ".join(changed)
            + "."
        )


def _verify_drawing_dimension_repair(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrawingDimensionRepair = draft.value["prepared"]
    if prepared.dimension.Document is not document:
        _postcondition_error("The repaired dimension left its exact document.")
    _assert_boundary(prepared)
    _assert_preserved(prepared)
    state = _expected_state(prepared)
    spec = prepared.spec
    expected_references = [
        {"view_name": str(prepared.target.view.Name), "subelement": name}
        for name in spec.subelements
    ]
    if spec.kind in _EXTENT_KINDS:
        references_match = state["target"] == {
            "scope": spec.extent_scope,
            "subelements": list(spec.subelements),
        }
    else:
        references_match = state["references"] == expected_references
    if (
        not references_match
        or state["page_name"] != str(prepared.target.page.Name)
        or state["view_name"] != str(prepared.target.view.Name)
        or state["dimension_type"] != spec.dimension_type
        or state["measure_type"] != "Projected"
        or not state["timeline_usable"]
        or not state["valid"]
    ):
        _postcondition_error(
            "The repaired dimension did not retain its exact replacement references."
        )
    if spec.kind == "axonometric_length":
        axonometric = state["axonometric"]
        if (
            not math.isclose(
                float(axonometric["line_angle_degrees"]),
                float(prepared.host_validation["line_angle_degrees"]),
                abs_tol=1.0e-9,
            )
            or not math.isclose(
                float(axonometric["extension_angle_degrees"]),
                float(prepared.host_validation["extension_angle_degrees"]),
                abs_tol=1.0e-9,
            )
            or axonometric["arbitrary_display"]
            is not (spec.expected_value_mode != "projected")
        ):
            _postcondition_error(
                "The repaired axonometric dimension did not retain its exact value mode."
            )
    repair_state = drawing_dimension_repair_state(prepared.dimension)
    if (
        repair_state["repair_kind"] != spec.kind
        or repair_state["repair_state_sha256"]
        == prepared.dimension_state_before["repair_state_sha256"]
        or not repair_state["repairable"]
    ):
        _postcondition_error("The dimension repair state did not advance exactly.")
    page_state = drawing_page_state(prepared.target.page)
    if page_state["view_count"] != prepared.target.page_state_before["view_count"]:
        _postcondition_error("Dimension repair changed Drawing page membership.")
    return {
        "operation": "repair_references",
        "repair_kind": spec.kind,
        "geometry_configuration": prepared.host_validation[
            "geometry_configuration"
        ],
        "dimension": state,
        "repair_target": {
            "object_name": repair_state["object_name"],
            "expected_repair_state_sha256": repair_state[
                "repair_state_sha256"
            ],
            "repair_kind": repair_state["repair_kind"],
        },
    }


def verify_drawing_dimension_repair(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    try:
        return _verify_drawing_dimension_repair(document, draft)
    except NativeMutationError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_DIMENSION_REPAIR_POSTCONDITION_FAILED",
            "The Drawing dimension repair could not be verified exactly: "
            f"{str(exc).strip()}",
        ) from exc
