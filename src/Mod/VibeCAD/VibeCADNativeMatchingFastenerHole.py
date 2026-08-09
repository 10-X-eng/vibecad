# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standard-derived Hole creation for the Model ribbon."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADFastenerModel import (
    ModelFastenerGraph,
    model_fastener_graph_from_body,
    validate_model_fastener_graph,
)
from VibeCADFasteners import (
    FastenerCatalogError,
    HOLE_SCHEMA,
    PROP_HOLE_FASTENER_KEY,
    PROP_HOLE_FIT,
    PROP_HOLE_PURPOSE,
    PROP_HOLE_RESOLUTION,
    PROP_HOLE_SCHEMA,
    configure_fastener_hole_feature,
    resolve_fastener_hole,
)
from VibeCADNativeDesignProfileBase import create_profile_design_operation
from VibeCADNativeDesignReferences import DesignLinkSpec, preflight_design_link
from VibeCADNativeDesignResults import (
    DesignResultSpec,
    resolve_design_result,
    result_spec_from_mapping,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    document_uid,
    object_reference,
    resolve_object,
)


_PURPOSES = frozenset({"clearance", "tapped", "counterbore", "countersink"})
_FITS = frozenset({"normal", "close", "loose"})
_FIELDS = frozenset({"fastener", "profile", "purpose", "fit", "targets"})


@dataclass(frozen=True, slots=True)
class MatchingFastenerHoleSpec:
    fastener_ref: NativeObjectRef
    profile: DesignLinkSpec
    result: DesignResultSpec
    purpose: str
    fit: str
    canonical_key: str
    resolution: Mapping[str, Any]


def _object_ref(document_uid_value: str, value: Any, *, label: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"The matching-hole {label} target is invalid.")
    return NativeObjectRef(document_uid_value, str(value["object_name"] or ""))


def _validated_fastener_graph(
    document: Any,
    reference: NativeObjectRef,
) -> ModelFastenerGraph:
    body = resolve_object(
        document,
        reference,
        expected_types=("PartDesign::Body",),
    )
    try:
        graph = model_fastener_graph_from_body(document, body)
        validate_model_fastener_graph(
            document,
            graph,
            label=str(graph.body.Label),
            canonical_key=str(graph.identity["canonical_key"]),
        )
        return graph
    except (FastenerCatalogError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc


def prepare_matching_fastener_hole(
    document: Any,
    value: Any,
) -> MatchingFastenerHoleSpec:
    """Parse and prove every source before a matching-hole transaction opens."""

    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeModelError("A matching-fastener-hole definition is invalid.")
    uid = document_uid(document)
    purpose = str(value["purpose"])
    fit = str(value["fit"])
    if purpose not in _PURPOSES:
        raise NativeModelError("The matching-hole purpose is unavailable.")
    if fit not in _FITS:
        raise NativeModelError("The matching-hole fit is unavailable.")
    if purpose == "tapped" and fit != "normal":
        raise NativeModelError("A tapped matching hole requires fit='normal'.")

    fastener_ref = _object_ref(uid, value["fastener"], label="fastener")
    profile_ref = _object_ref(uid, value["profile"], label="profile")
    profile_spec = DesignLinkSpec(profile_ref, ())
    result_spec = result_spec_from_mapping(
        uid,
        {
            "mode": "cut",
            "targets": value["targets"],
            "destination_component": None,
        },
    )

    graph = _validated_fastener_graph(document, fastener_ref)
    sketch = preflight_design_link(
        document,
        profile_spec,
        expected_types=("Sketcher::SketchObject",),
    )
    if sketch.getParentGeoFeatureGroup() is not None:
        raise NativeModelError(
            "A matching hole requires a reusable Design-scope Sketch."
        )
    if int(getattr(sketch, "GeometryCount", 0) or 0) < 1:
        raise NativeModelError("A matching-hole Sketch contains no hole locations.")
    try:
        import PartDesign

        PartDesign.validateDesign(sketch)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise NativeModelError(
            "The matching-hole Sketch is not a valid reusable definition."
        ) from exc

    targets, component = resolve_design_result(document, result_spec)
    if component is not None:
        raise NativeModelError("A matching hole cannot publish into a Component.")
    if graph.body in targets:
        raise NativeModelError(
            "The source standard-fastener Body cannot also be a matching-hole target."
        )
    try:
        resolution = resolve_fastener_hole(
            graph.generator,
            purpose=purpose,
            fit=fit,
        )
    except FastenerCatalogError as exc:
        raise NativeModelError(str(exc)) from exc
    canonical_key = str(graph.identity["canonical_key"])
    if str(resolution.get("fastener_canonical_key") or "") != canonical_key:
        raise NativeModelError(
            "The matching-hole fastener identity did not resolve exactly."
        )
    return MatchingFastenerHoleSpec(
        fastener_ref,
        profile_spec,
        result_spec,
        purpose,
        fit,
        canonical_key,
        dict(resolution),
    )


def _close(actual: Any, expected: Any) -> bool:
    return math.isclose(
        float(getattr(actual, "Value", actual)),
        float(expected),
        rel_tol=1.0e-9,
        abs_tol=1.0e-7,
    )


def _verify_matching_hole(
    operation: Any,
    expected: Mapping[str, Any],
) -> Mapping[str, Any]:
    spec = expected["spec"]
    graph = _validated_fastener_graph(operation.Document, spec.fastener_ref)
    if str(graph.identity["canonical_key"]) != spec.canonical_key:
        raise NativeModelError("The matching-hole fastener changed before commit.")

    resolution = dict(expected["resolution"])
    try:
        stored_resolution = json.loads(str(getattr(operation, PROP_HOLE_RESOLUTION)))
    except (TypeError, ValueError) as exc:
        raise NativeModelError("The matching-hole resolution record is invalid.") from exc
    tapped = spec.purpose == "tapped"
    if (
        int(operation.BaseProfileType) != int(expected["base_profile_type"])
        or str(operation.DepthType) != "ThroughAll"
        or not bool(operation.Refine)
        or str(getattr(operation, PROP_HOLE_SCHEMA, "")) != HOLE_SCHEMA
        or str(getattr(operation, PROP_HOLE_FASTENER_KEY, ""))
        != spec.canonical_key
        or str(getattr(operation, PROP_HOLE_PURPOSE, "")) != spec.purpose
        or str(getattr(operation, PROP_HOLE_FIT, "")) != spec.fit
        or stored_resolution != resolution
        or str(operation.ThreadType) != str(resolution["native_thread_type"])
        or str(operation.ThreadSize) != str(resolution["native_thread_size"])
        or bool(operation.Threaded) is not tapped
        or bool(operation.ModelThread)
        or bool(operation.CosmeticThread) is not tapped
        or str(operation.ThreadDirection)
        != ("Left" if bool(resolution["left_handed"]) else "Right")
    ):
        raise NativeModelError("The matching-hole controls changed before commit.")
    if not _close(operation.Diameter, resolution["resolved_diameter_mm"]):
        raise NativeModelError("The matching-hole diameter changed before commit.")
    if not tapped and str(operation.ThreadFit) != str(resolution["native_fit"]):
        raise NativeModelError("The matching-hole fit changed before commit.")
    expected_cut = (
        str(resolution["native_cut_type"])
        if spec.purpose in {"counterbore", "countersink"}
        else "None"
    )
    if str(operation.HoleCutType) != expected_cut:
        raise NativeModelError("The matching-hole head cut changed before commit.")
    if spec.purpose in {"counterbore", "countersink"}:
        if bool(operation.HoleCutCustomValues) or not _close(
            operation.HoleCutDiameter,
            resolution["resolved_cut_diameter_mm"],
        ):
            raise NativeModelError(
                "The matching-hole head dimensions changed before commit."
            )
    cutter = operation.AddSubShape
    if cutter.isNull() or not cutter.isValid() or not cutter.Solids:
        raise NativeModelError("The matching hole produced no valid cutter solids.")

    result: dict[str, Any] = {
        "fastener": object_reference(graph.body),
        "canonical_key": spec.canonical_key,
        "purpose": spec.purpose,
        "fit": spec.fit,
        "diameter_mm": float(operation.Diameter.Value),
        "cutter_solid_count": len(cutter.Solids),
    }
    if spec.purpose in {"counterbore", "countersink"}:
        result["head"] = {
            "type": expected_cut,
            "diameter_mm": float(operation.HoleCutDiameter.Value),
        }
    return result


def create_matching_fastener_hole(
    document: Any,
    *,
    label: str,
    spec: MatchingFastenerHoleSpec,
) -> NativeMutationDraft:
    if not isinstance(spec, MatchingFastenerHoleSpec):
        raise TypeError("spec must be a MatchingFastenerHoleSpec")
    graph = _validated_fastener_graph(document, spec.fastener_ref)
    if str(graph.identity["canonical_key"]) != spec.canonical_key:
        raise NativeModelError("The matching-hole fastener changed after preflight.")

    def configure(operation: Any) -> Mapping[str, Any]:
        operation.DepthType = "ThroughAll"
        try:
            resolution = configure_fastener_hole_feature(
                operation,
                graph.generator,
                purpose=spec.purpose,
                fit=spec.fit,
            )
        except FastenerCatalogError as exc:
            raise NativeModelError(str(exc)) from exc
        for name, expected in spec.resolution.items():
            if resolution.get(name) != expected:
                raise NativeModelError(
                    "The matching-hole catalog changed after preflight."
                )
        operation.Refine = True
        return {
            "spec": spec,
            "resolution": dict(resolution),
            "base_profile_type": int(operation.BaseProfileType),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignHole",
        base_name="MatchingFastenerHole",
        label=label,
        profile_spec=spec.profile,
        result_spec=spec.result,
        configure_specific=configure,
        verify_specific=_verify_matching_hole,
        configure_after_targets=True,
    )
