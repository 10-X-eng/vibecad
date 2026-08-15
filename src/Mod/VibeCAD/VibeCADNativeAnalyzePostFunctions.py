# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reusable implicit functions for FEM post-processing pipelines."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    finalize_new_operation_resource,
    publish_operation,
    require_boundary,
    stage_operation_resource_reconciliation,
    verify_operation_block,
)
from VibeCADNativeAnalyzePost import _label
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_COORDINATE_MM = 1_000_000_000.0

_FUNCTION_TYPES = {
    "plane": "Fem::FemPostPlaneFunction",
    "sphere": "Fem::FemPostSphereFunction",
    "cylinder": "Fem::FemPostCylinderFunction",
    "box": "Fem::FemPostBoxFunction",
}


@dataclass(frozen=True, slots=True)
class PreparedPostFunction:
    boundary: AnalyzeCreationBoundary
    pipeline: PreparedResultTarget
    provider: Any | None
    provider_children: tuple[Any, ...]
    kind: str
    label: str
    parameters: tuple[tuple[str, Any], ...]


def _derived(obj: Any, type_name: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_name))
    except Exception:
        return False


def _function_providers(pipeline: Any) -> tuple[Any, ...]:
    return tuple(
        child
        for child in tuple(getattr(pipeline, "Group", ()) or ())
        if _derived(child, "Fem::FemPostFunctionProvider")
    )


def _vector(value: Any, field: str, *, unit: bool = False) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(f"{field} must contain only x, y, and z.")
    coordinates = []
    for axis in ("x", "y", "z"):
        raw = value[axis]
        if type(raw) not in {int, float} or not math.isfinite(float(raw)):
            raise NativeAnalyzeError(f"{field}.{axis} must be one finite number.")
        coordinate = float(raw)
        if not -MAX_COORDINATE_MM <= coordinate <= MAX_COORDINATE_MM:
            raise NativeAnalyzeError(
                f"{field}.{axis} must be between -1000000000 and 1000000000."
            )
        coordinates.append(coordinate)
    result = tuple(coordinates)
    if unit:
        length = math.sqrt(sum(coordinate * coordinate for coordinate in result))
        if length <= 1e-12:
            raise NativeAnalyzeError(f"{field} must be a nonzero direction.")
        result = tuple(coordinate / length for coordinate in result)
    return result


def _positive(value: Any, field: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise NativeAnalyzeError(f"{field} must be one finite number.")
    result = float(value)
    if not 0.0 < result <= MAX_COORDINATE_MM:
        raise NativeAnalyzeError(
            f"{field} must be greater than 0 and no greater than 1000000000 mm."
        )
    return result


def _normalize_parameters(kind: str, values: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    if kind == "plane":
        parameters = {
            "PlaneOrigin": _vector(values["origin_mm"], "origin_mm"),
            "PlaneNormal": _vector(values["normal"], "normal", unit=True),
        }
    elif kind == "sphere":
        parameters = {
            "SphereCenter": _vector(values["center_mm"], "center_mm"),
            "SphereRadius": _positive(values["radius_mm"], "radius_mm"),
        }
    elif kind == "cylinder":
        parameters = {
            "CylinderCenter": _vector(values["center_mm"], "center_mm"),
            "CylinderAxis": _vector(values["axis"], "axis", unit=True),
            "CylinderRadius": _positive(values["radius_mm"], "radius_mm"),
        }
    elif kind == "box":
        parameters = {
            "BoxCenter": _vector(values["center_mm"], "center_mm"),
            "BoxLength": _positive(values["length_mm"], "length_mm"),
            "BoxWidth": _positive(values["width_mm"], "width_mm"),
            "BoxHeight": _positive(values["height_mm"], "height_mm"),
        }
    else:
        raise AssertionError(f"Unhandled post-function kind: {kind}")
    return tuple(parameters.items())


def _pipeline_diagonal(pipeline: Any) -> float:
    try:
        bounds = tuple(float(value) for value in pipeline.getDataSet().GetBounds())
    except Exception:
        return 1.0
    if len(bounds) != 6 or not all(math.isfinite(value) for value in bounds):
        return 1.0
    diagonal = math.sqrt(
        (bounds[1] - bounds[0]) ** 2
        + (bounds[3] - bounds[2]) ** 2
        + (bounds[5] - bounds[4]) ** 2
    )
    return diagonal if diagonal > 0.0 and math.isfinite(diagonal) else 1.0


def prepare_post_function(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    pipeline: Any,
    label: Any,
    **values: Any,
) -> PreparedPostFunction:
    pipeline_target = prepare_result_target(
        document,
        document_uid,
        pipeline,
        expected_kinds=frozenset({"pipeline"}),
    )
    providers = _function_providers(pipeline_target.result)
    if len(providers) > 1:
        raise NativeAnalyzeError(
            "The exact pipeline contains more than one function provider.",
            error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
            repair={
                "pipeline": {"object_name": str(pipeline_target.result.Name)},
                "function_providers": [str(provider.Name) for provider in providers[:8]],
            },
        )
    provider = providers[0] if providers else None
    provider_children = tuple(getattr(provider, "Group", ()) or ()) if provider else ()
    return PreparedPostFunction(
        creation_boundary(document),
        pipeline_target,
        provider,
        provider_children,
        kind,
        _label(label),
        _normalize_parameters(kind, values),
    )


def create_post_function(
    document: Any,
    prepared: PreparedPostFunction,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostFunction):
        raise TypeError("prepared must be a PreparedPostFunction")
    require_boundary(document, prepared.boundary)
    pipeline = prepared.pipeline.result
    if not is_live(document, pipeline):
        raise NativeAnalyzeError(
            "The exact post-processing pipeline is no longer live.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    providers = _function_providers(pipeline)
    expected_providers = (prepared.provider,) if prepared.provider is not None else ()
    if providers != expected_providers or (
        prepared.provider is not None
        and tuple(prepared.provider.Group or ()) != prepared.provider_children
    ):
        raise NativeAnalyzeError(
            "The exact pipeline function graph changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    provider = prepared.provider
    provider_created = provider is None
    try:
        if provider_created:
            old_resources = stage_operation_resource_reconciliation(
                document,
                prepared.boundary,
                pipeline,
            )
            provider = document.addObject(
                "Fem::FemPostFunctionProvider",
                document.getUniqueObjectName("Functions"),
            )
            provider.Label = "Post Functions"
            pipeline.addObject(provider)
            finalize_new_operation_resource(
                document,
                prepared.boundary,
                pipeline,
                old_resources,
                provider,
            )
            function_boundary = creation_boundary(document)
        else:
            function_boundary = prepared.boundary

        function = document.addObject(
            _FUNCTION_TYPES[prepared.kind],
            document.getUniqueObjectName(prepared.kind.title()),
        )
        function.Label = prepared.label
        provider.addObject(function)
        for property_name, value in prepared.parameters:
            setattr(function, property_name, value)
        if prepared.kind == "plane":
            function.ViewObject.Scale = _pipeline_diagonal(pipeline)
        publish_operation(document, function_boundary, function)
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM {prepared.kind} post function could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    created = [object_identity(function)]
    if provider_created:
        created.insert(0, object_identity(provider))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "provider": provider,
            "provider_created": provider_created,
            "function_boundary": function_boundary,
            "function": function,
        },
        recompute_targets=(function, provider, pipeline),
        created=tuple(created),
        changed=(object_identity(pipeline),),
    )


def _parameter_matches(function: Any, name: str, expected: Any) -> bool:
    actual = getattr(function, name)
    if isinstance(expected, tuple):
        return all(
            math.isclose(float(value), target, rel_tol=0.0, abs_tol=1e-12)
            for value, target in zip((actual.x, actual.y, actual.z), expected, strict=True)
        )
    return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12)


def verify_post_function(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    provider = draft.value["provider"]
    function = draft.value["function"]
    pipeline = prepared.pipeline.result
    verify_operation_block(document, draft.value["function_boundary"], function)
    providers = _function_providers(pipeline)
    checks = {
        "live function": is_live(document, function),
        "live provider": is_live(document, provider),
        "one pipeline provider": providers == (provider,),
        "provider membership": function in tuple(provider.Group or ()),
        "function type": str(function.TypeId) == _FUNCTION_TYPES[prepared.kind],
        "label": str(function.Label) == prepared.label,
        "parameters": all(
            _parameter_matches(function, name, value)
            for name, value in prepared.parameters
        ),
    }
    if draft.value["provider_created"]:
        checks["provider resource ownership"] = (
            str(provider.VibeCADTimelineRole) == "resource"
            and provider.VibeCADTimelineOwner is pipeline
        )
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            f"The FEM {prepared.kind} post function failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_function": result_state(function),
        "function_provider": result_state(provider, include_ranges=False),
        "pipeline": result_state(pipeline, include_ranges=False),
        "provider_created": bool(draft.value["provider_created"]),
    }
