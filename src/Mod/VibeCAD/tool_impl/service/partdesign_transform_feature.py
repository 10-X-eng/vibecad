# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared native PartDesign pattern, mirror, and multitransform support."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_find_subelements


_QUERY_VECTOR = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
    },
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}


AXIS_REFERENCE_SCHEMA = {
    "description": "Reference axis for the pattern.",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "source": {"const": "body_origin", "description": "Use a Body origin axis."},
                "axis": {
                    "type": "string",
                    "enum": ["X_Axis", "Y_Axis", "Z_Axis"],
                    "description": "Body origin axis.",
                },
            },
            "required": ["source", "axis"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "linear_edge_query"},
                "object_name": {"type": "string", "minLength": 1},
                "query": {
                    "type": "object",
                    "properties": {
                        "direction": _QUERY_VECTOR,
                        "direction_tolerance_degrees": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 180,
                        },
                        "near_point": _QUERY_VECTOR,
                        "max_distance": {"type": "number", "minimum": 0},
                        "min_length": {"type": "number", "minimum": 0},
                        "max_length": {"type": "number", "minimum": 0},
                        "expected_count": {"const": 1},
                    },
                    "required": ["expected_count"],
                    "additionalProperties": False,
                },
            },
            "required": ["source", "object_name", "query"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "sketch_axis", "description": "Use an axis of a sketch in the same Body."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the sketch.",
                },
                "axis": {
                    "type": "string",
                    "enum": ["H_Axis", "V_Axis", "N_Axis"],
                    "description": "Sketch horizontal, vertical, or normal axis.",
                },
            },
            "required": ["source", "object_name", "axis"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "datum_axis", "description": "Use a PartDesign datum axis."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the datum axis.",
                },
            },
            "required": ["source", "object_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "linear_edge", "description": "Use a straight model edge."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the object that owns the edge.",
                },
                "subelement": {
                    "type": "string",
                    "pattern": "^Edge[1-9][0-9]*$",
                    "description": "Exact edge name such as Edge4; must be linear.",
                },
            },
            "required": ["source", "object_name", "subelement"],
            "additionalProperties": False,
        },
    ]
}

PLANE_REFERENCE_SCHEMA = {
    "description": "Reference plane for the mirror.",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "source": {"const": "body_origin", "description": "Use a Body origin plane."},
                "plane": {
                    "type": "string",
                    "enum": ["XY_Plane", "XZ_Plane", "YZ_Plane"],
                    "description": "Body origin plane.",
                },
            },
            "required": ["source", "plane"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "planar_face_query"},
                "object_name": {"type": "string", "minLength": 1},
                "query": {
                    "type": "object",
                    "properties": {
                        "normal": _QUERY_VECTOR,
                        "normal_tolerance_degrees": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 180,
                        },
                        "near_point": _QUERY_VECTOR,
                        "max_distance": {"type": "number", "minimum": 0},
                        "min_area": {"type": "number", "minimum": 0},
                        "max_area": {"type": "number", "minimum": 0},
                        "expected_count": {"const": 1},
                    },
                    "required": ["expected_count"],
                    "additionalProperties": False,
                },
            },
            "required": ["source", "object_name", "query"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "datum_plane", "description": "Use a PartDesign datum plane."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the datum plane.",
                },
            },
            "required": ["source", "object_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "sketch_plane", "description": "Use the plane a sketch lies on."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the sketch.",
                },
            },
            "required": ["source", "object_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {
                    "const": "sketch_axis_plane",
                    "description": "Use the plane through a sketch axis, perpendicular to the sketch plane.",
                },
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the sketch.",
                },
                "axis": {
                    "type": "string",
                    "enum": ["H_Axis", "V_Axis"],
                    "description": "Sketch horizontal or vertical axis.",
                },
            },
            "required": ["source", "object_name", "axis"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source": {"const": "planar_face", "description": "Use a planar face on a model object."},
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the object that owns the face.",
                },
                "subelement": {
                    "type": "string",
                    "pattern": "^Face[1-9][0-9]*$",
                    "description": "Exact face name such as Face3; must be planar.",
                },
            },
            "required": ["source", "object_name", "subelement"],
            "additionalProperties": False,
        },
    ]
}


def distribution_schema(quantity_name: str, maximum: float | None = None) -> dict[str, Any]:
    positive: dict[str, Any] = {"type": "number", "exclusiveMinimum": 0}
    if maximum is not None:
        positive["maximum"] = maximum
    occurrences_schema = {
        "type": "integer",
        "minimum": 2,
        "description": "Total occurrence count, original included.",
    }
    return {
        "description": "How occurrences are distributed along the reference.",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "type": {
                        "const": "extent",
                        "description": "Spread occurrences evenly over a total extent.",
                    },
                    quantity_name: {
                        **positive,
                        "description": "Total extent from the first occurrence to the last.",
                    },
                    "occurrences": dict(occurrences_schema),
                },
                "required": ["type", quantity_name, "occurrences"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "type": {
                        "const": "uniform_spacing",
                        "description": "Place occurrences at a fixed spacing.",
                    },
                    "spacing": {
                        **positive,
                        "description": "Gap between consecutive occurrences.",
                    },
                    "occurrences": dict(occurrences_schema),
                },
                "required": ["type", "spacing", "occurrences"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "type": {
                        "const": "exact_spacings",
                        "description": "Place occurrences at explicit per-gap spacings.",
                    },
                    "spacings": {
                        "type": "array",
                        "items": dict(positive),
                        "minItems": 1,
                        "description": "Ordered gaps between consecutive occurrences; count is one more than the number of gaps.",
                    },
                },
                "required": ["type", "spacings"],
                "additionalProperties": False,
            },
        ]
    }


TRANSFORM_MODE_SCHEMA = {
    "type": "string",
    "enum": ["features", "whole_shape"],
    "description": "features transforms only the listed features' geometry; whole_shape transforms the Body's entire resulting shape.",
}


def run_single_transform(
    service: Any,
    *,
    operation: str,
    type_id: str,
    feature_names: list[str],
    label: str,
    transform_mode: str,
    refine: bool,
    reference: dict[str, Any],
    distribution: dict[str, Any] | None = None,
    reversed: bool = False,
) -> dict[str, Any]:
    native_transform_mode = _transform_mode(transform_mode)
    if native_transform_mode is None:
        return _invalid("transform_mode must be features or whole_shape.")
    sources_state = _resolve_sources(
        service,
        feature_names,
        whole_shape=native_transform_mode == "Whole shape",
    )
    if not sources_state.get("ok"):
        return sources_state
    body = sources_state["body"]
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if operation == "linear_pattern":
        reference_state = _resolve_axis(service, body, reference)
        distribution_state = _validate_distribution(distribution, "length", 1e300)
        native_name = "LinearPattern"
    elif operation == "polar_pattern":
        reference_state = _resolve_axis(service, body, reference)
        distribution_state = _validate_distribution(distribution, "angle_degrees", 360.0)
        native_name = "PolarPattern"
    elif operation == "mirror":
        reference_state = _resolve_plane(service, body, reference)
        distribution_state = {"ok": True}
        native_name = "Mirrored"
    else:
        return _invalid(f"Unsupported transform operation: {operation}")
    if not reference_state.get("ok"):
        return reference_state
    if not distribution_state.get("ok"):
        return distribution_state
    tip_block = domain_runtime.invalid_partdesign_tip(body)
    if tip_block is not None:
        return _invalid(
            "The source Body has an invalid or zero-effect Tip.",
            tip_state=tip_block,
        )
    body_shape_before = domain_runtime.shape_summary(body)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_body, target_sources = _reload_sources(service, body.Name, sources_state["names"])
        reference_object = doc.getObject(reference_state["object_name"])
        if reference_object is None:
            raise RuntimeError(f"Transform reference no longer exists: {reference_state['object_name']}")
        feature = target_body.newObject(type_id, native_name)
        feature.Label = clean_label
        feature.Originals = target_sources
        feature.TransformMode = native_transform_mode
        feature.Refine = bool(refine)
        if operation == "linear_pattern":
            feature.Direction = (reference_object, [reference_state["subelement"]])
            _apply_distribution(feature, distribution_state, extent_property="Length")
            feature.Reversed = bool(reversed)
        elif operation == "polar_pattern":
            feature.Axis = (reference_object, [reference_state["subelement"]])
            _apply_distribution(feature, distribution_state, extent_property="Angle")
            feature.Reversed = bool(reversed)
        else:
            feature.MirrorPlane = (reference_object, [reference_state["subelement"]])
        target_body.Tip = feature
        doc.recompute()
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            target_body,
            feature,
            operation,
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": target_body.Name,
            "feature": feature.Name,
            "feature_label": feature.Label,
            "feature_type": feature.TypeId,
            "source_features": [item.Name for item in target_sources],
            "transform_mode": str(feature.TransformMode),
            "reference_object": reference_object.Name,
            "reference_subelement": reference_state["subelement"],
            "resolved_reference": reference_state,
            "distribution": _native_distribution_summary(feature) if distribution is not None else None,
            "requested_occurrence_count": (
                int(distribution_state["occurrences"])
                if distribution is not None
                else 2
            ),
            "generated_occurrence_count": int(
                getattr(feature, "GeneratedOccurrenceCount", 0) or 0
            ),
            "rejected_solid_count": int(
                getattr(feature, "RejectedSolidCount", 0) or 0
            ),
            "reversed": bool(getattr(feature, "Reversed", False)),
            "refine": bool(feature.Refine),
            "body_group": [item.Name for item in list(target_body.Group)],
            "body_tip": getattr(getattr(target_body, "Tip", None), "Name", None),
            "base_feature": getattr(getattr(feature, "BaseFeature", None), "Name", None),
            **effect,
        }

    expected_occurrences = (
        int(distribution_state["occurrences"])
        if distribution is not None
        else 2
    )

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        generated = int(result.get("generated_occurrence_count") or 0)
        rejected = int(result.get("rejected_solid_count") or 0)
        checks = [
            {
                "name": "occurrence_count",
                "ok": generated == expected_occurrences,
                "requested": expected_occurrences,
                "generated": generated,
            },
            {
                "name": "disconnected_occurrences",
                "ok": rejected == 0,
                "rejected_solid_count": rejected,
            },
        ]
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create PartDesign {operation}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.partdesign_feature_response(
        service,
        transaction,
        operation=operation,
    )


def run_multi_transform(
    service: Any,
    *,
    feature_names: list[str],
    label: str,
    transform_mode: str,
    refine: bool,
    transformations: list[dict[str, Any]],
) -> dict[str, Any]:
    native_transform_mode = _transform_mode(transform_mode)
    if native_transform_mode is None:
        return _invalid("transform_mode must be features or whole_shape.")
    sources_state = _resolve_sources(
        service,
        feature_names,
        whole_shape=native_transform_mode == "Whole shape",
    )
    if not sources_state.get("ok"):
        return sources_state
    body = sources_state["body"]
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(transformations, list) or len(transformations) < 2:
        return _invalid("transformations must contain at least two ordered transformations.")
    validated = []
    for index, definition in enumerate(transformations):
        state = _validate_transformation(service, body, definition)
        if not state.get("ok"):
            state["transformation_index"] = index
            return state
        validated.append(state)
    tip_block = domain_runtime.invalid_partdesign_tip(body)
    if tip_block is not None:
        return _invalid(
            "The source Body has an invalid or zero-effect Tip.",
            tip_state=tip_block,
        )
    body_shape_before = domain_runtime.shape_summary(body)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        target_body, target_sources = _reload_sources(service, body.Name, sources_state["names"])
        multi = target_body.newObject("PartDesign::MultiTransform", "MultiTransform")
        multi.Label = clean_label
        multi.Originals = target_sources
        multi.TransformMode = native_transform_mode
        multi.Refine = bool(refine)
        children = []
        for state in validated:
            child = _create_transform_child(target_body, doc, state)
            children.append(child)
        multi.Transformations = children
        target_body.Tip = multi
        doc.recompute()
        effect = domain_runtime.finalize_partdesign_feature_effect(
            doc,
            target_body,
            multi,
            "multi_transform",
            body_shape_before,
        )
        return {
            "document": doc.Name,
            "body": target_body.Name,
            "feature": multi.Name,
            "feature_label": multi.Label,
            "feature_type": multi.TypeId,
            "source_features": [item.Name for item in target_sources],
            "transform_mode": str(multi.TransformMode),
            "transformations": [_child_summary(child) for child in children],
            "generated_occurrence_count": int(
                getattr(multi, "GeneratedOccurrenceCount", 0) or 0
            ),
            "rejected_solid_count": int(
                getattr(multi, "RejectedSolidCount", 0) or 0
            ),
            "first_failing_child": {
                "index": int(getattr(multi, "LastFailingChildIndex", -1)),
                "name": str(getattr(multi, "LastFailingChild", "")),
            },
            "refine": bool(multi.Refine),
            "body_group": [item.Name for item in list(target_body.Group)],
            "body_tip": getattr(getattr(target_body, "Tip", None), "Name", None),
            "base_feature": getattr(getattr(multi, "BaseFeature", None), "Name", None),
            **effect,
        }

    expected_occurrences = _multi_transform_occurrence_count(validated)

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        generated = int(result.get("generated_occurrence_count") or 0)
        rejected = int(result.get("rejected_solid_count") or 0)
        failing = result.get("first_failing_child") or {}
        checks = [
            {
                "name": "child_transformations",
                "ok": int(failing.get("index", -1)) < 0,
                "first_failing_child": failing,
            },
            {
                "name": "occurrence_count",
                "ok": generated == expected_occurrences,
                "requested": expected_occurrences,
                "generated": generated,
            },
            {
                "name": "disconnected_occurrences",
                "ok": rejected == 0,
                "rejected_solid_count": rejected,
            },
        ]
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create PartDesign multi-transform: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.partdesign_feature_response(
        service,
        transaction,
        operation="multi_transform",
    )


def _resolve_sources(
    service: Any,
    feature_names: Any,
    *,
    whole_shape: bool,
) -> dict[str, Any]:
    if not isinstance(feature_names, list) or not feature_names:
        return _invalid("feature_names must contain at least one exact internal feature name.")
    names = [str(name or "").strip() for name in feature_names]
    if any(not name for name in names):
        return _invalid("feature_names cannot contain empty names.")
    if len(set(names)) != len(names):
        return _invalid("feature_names cannot contain duplicates.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    features = []
    body = None
    for name in names:
        feature = doc.getObject(name)
        if feature is None:
            return _invalid(f"PartDesign feature not found by exact internal name: {name}")
        owner = service._partdesign_body_for_feature(feature)
        if owner is None:
            return _invalid(f"Feature {name} is not owned by exactly one PartDesign Body.")
        if body is None:
            body = owner
        elif owner.Name != body.Name:
            return _invalid(
                "Every source feature must belong to the same PartDesign Body.",
                feature=name,
                feature_body=owner.Name,
                expected_body=body.Name,
            )
        is_part_shape = bool(
            callable(getattr(feature, "isDerivedFrom", None))
            and feature.isDerivedFrom("Part::Feature")
        )
        is_add_sub_feature = bool(
            callable(getattr(feature, "isDerivedFrom", None))
            and feature.isDerivedFrom("PartDesign::FeatureAddSub")
        )
        if not is_part_shape:
            return _invalid(f"Object {name} is not a transformable shape result.")
        if not whole_shape and not is_add_sub_feature:
            return _invalid(
                f"Object {name} does not expose additive/subtractive feature intent. "
                "Use transform_mode='whole_shape' for an ordinary Part or full-result node."
            )
        state = domain_runtime.feature_state_summary(feature)
        if (
            state.get("inspection_complete") is not True
            or state.get("marked_invalid")
            or state.get("shape_null")
            or state.get("shape_valid") is not True
        ):
            return _invalid(f"Source feature {name} is invalid or has no shape.", feature_state=state)
        features.append(feature)
    return {"ok": True, "body": body, "features": features, "names": names}


def _reload_sources(service: Any, body_name: str, names: list[str]) -> tuple[Any, list[Any]]:
    doc = service._active_document()
    body = service._get_partdesign_body(body_name)
    if doc is None or body is None:
        raise RuntimeError(f"PartDesign Body no longer exists: {body_name}")
    features = []
    for name in names:
        feature = doc.getObject(name)
        if feature is None or service._partdesign_body_for_feature(feature) != body:
            raise RuntimeError(f"Transform source ownership changed: {name}")
        features.append(feature)
    return body, features


def _resolve_axis(service: Any, body: Any, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return _invalid("axis reference must be an object.")
    source = str(reference.get("source") or "")
    doc = service._active_document()
    if source == "body_origin":
        role = str(reference.get("axis") or "")
        obj = service._partdesign_origin_feature(body, role)
        if obj is None:
            return _invalid(f"Body origin axis not found: {role}")
        return {"ok": True, "object_name": obj.Name, "subelement": ""}
    object_name = str(reference.get("object_name") or "").strip()
    obj = doc.getObject(object_name) if doc is not None else None
    if obj is None:
        return _invalid(f"Axis reference object not found: {object_name}")
    if source == "sketch_axis":
        if getattr(obj, "TypeId", "") != "Sketcher::SketchObject":
            return _invalid(f"Axis reference {object_name} is not a sketch.")
        if service._partdesign_body_for_feature(obj) != body:
            return _invalid(f"Axis sketch {object_name} does not belong to Body {body.Name}.")
        return {"ok": True, "object_name": obj.Name, "subelement": str(reference["axis"])}
    if source == "datum_axis":
        if getattr(obj, "TypeId", "") != "PartDesign::Line":
            return _invalid(f"Axis reference {object_name} is not a PartDesign datum axis.")
        if service._partdesign_body_for_feature(obj) != body:
            return _invalid(f"Datum axis {object_name} does not belong to Body {body.Name}.")
        return {"ok": True, "object_name": obj.Name, "subelement": ""}
    if source == "linear_edge":
        subelement = str(reference.get("subelement") or "")
        try:
            edge = obj.Shape.getElement(subelement)
            curve_name = type(edge.Curve).__name__.lower()
        except Exception as exc:
            return _invalid(f"Linear edge reference is invalid: {object_name}.{subelement}: {exc}")
        if "line" not in curve_name:
            return _invalid(
                f"Axis edge {object_name}.{subelement} is not linear.",
                curve_type=type(edge.Curve).__name__,
            )
        return {"ok": True, "object_name": obj.Name, "subelement": subelement}
    if source == "linear_edge_query":
        query = dict(reference.get("query") or {})
        query.pop("expected_count", None)
        result = partdesign_find_subelements.run(
            service,
            object_name=obj.Name,
            element_type="edge",
            geometry_type="line",
            **query,
        )
        if not result.get("ok"):
            return _invalid("Linear-edge query failed.", query_result=result)
        matches = list(result.get("matches") or [])
        if len(matches) != 1:
            return _invalid(
                "Linear-edge query must resolve to exactly one edge.",
                expected_count=1,
                actual_count=len(matches),
                candidates=matches,
            )
        return {
            "ok": True,
            "object_name": obj.Name,
            "subelement": str(matches[0]["name"]),
            "resolved_geometry": matches[0],
            "resolution": "count_guarded_query",
        }
    return _invalid("axis reference source is invalid.")


def _resolve_plane(service: Any, body: Any, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return _invalid("plane reference must be an object.")
    source = str(reference.get("source") or "")
    doc = service._active_document()
    if source == "body_origin":
        role = str(reference.get("plane") or "")
        obj = service._partdesign_origin_feature(body, role)
        if obj is None:
            return _invalid(f"Body origin plane not found: {role}")
        return {"ok": True, "object_name": obj.Name, "subelement": ""}
    object_name = str(reference.get("object_name") or "").strip()
    obj = doc.getObject(object_name) if doc is not None else None
    if obj is None:
        return _invalid(f"Plane reference object not found: {object_name}")
    if source == "datum_plane":
        if getattr(obj, "TypeId", "") != "PartDesign::Plane":
            return _invalid(f"Plane reference {object_name} is not a PartDesign datum plane.")
        if service._partdesign_body_for_feature(obj) != body:
            return _invalid(f"Datum plane {object_name} does not belong to Body {body.Name}.")
        return {"ok": True, "object_name": obj.Name, "subelement": ""}
    if source in {"sketch_plane", "sketch_axis_plane"}:
        if getattr(obj, "TypeId", "") != "Sketcher::SketchObject":
            return _invalid(f"Plane reference {object_name} is not a sketch.")
        if service._partdesign_body_for_feature(obj) != body:
            return _invalid(f"Plane sketch {object_name} does not belong to Body {body.Name}.")
        subelement = "" if source == "sketch_plane" else str(reference["axis"])
        return {"ok": True, "object_name": obj.Name, "subelement": subelement}
    if source == "planar_face":
        subelement = str(reference.get("subelement") or "")
        try:
            face = obj.Shape.getElement(subelement)
            surface_name = type(face.Surface).__name__.lower()
        except Exception as exc:
            return _invalid(f"Planar face reference is invalid: {object_name}.{subelement}: {exc}")
        if "plane" not in surface_name:
            return _invalid(
                f"Mirror reference {object_name}.{subelement} is not planar.",
                surface_type=type(face.Surface).__name__,
            )
        return {"ok": True, "object_name": obj.Name, "subelement": subelement}
    if source == "planar_face_query":
        query = dict(reference.get("query") or {})
        query.pop("expected_count", None)
        result = partdesign_find_subelements.run(
            service,
            object_name=obj.Name,
            element_type="face",
            geometry_type="plane",
            **query,
        )
        if not result.get("ok"):
            return _invalid("Planar-face query failed.", query_result=result)
        matches = list(result.get("matches") or [])
        if len(matches) != 1:
            return _invalid(
                "Planar-face query must resolve to exactly one face.",
                expected_count=1,
                actual_count=len(matches),
                candidates=matches,
            )
        return {
            "ok": True,
            "object_name": obj.Name,
            "subelement": str(matches[0]["name"]),
            "resolved_geometry": matches[0],
            "resolution": "count_guarded_query",
        }
    return _invalid("plane reference source is invalid.")


def _validate_distribution(
    distribution: Any,
    extent_key: str,
    maximum: float,
) -> dict[str, Any]:
    if not isinstance(distribution, dict):
        return _invalid("distribution must be an object.")
    kind = str(distribution.get("type") or "")
    if kind == "extent":
        value = _positive(distribution.get(extent_key), f"distribution.{extent_key}", maximum)
        if not value.get("ok"):
            return value
        occurrences = _occurrences(distribution.get("occurrences"))
        if not occurrences.get("ok"):
            return occurrences
        return {
            "ok": True,
            "mode": "Extent",
            "extent": value["value"],
            "occurrences": occurrences["value"],
            "offset": None,
            "spacings": [],
        }
    if kind == "uniform_spacing":
        spacing = _positive(distribution.get("spacing"), "distribution.spacing", maximum)
        if not spacing.get("ok"):
            return spacing
        occurrences = _occurrences(distribution.get("occurrences"))
        if not occurrences.get("ok"):
            return occurrences
        return {
            "ok": True,
            "mode": "Spacing",
            "extent": None,
            "occurrences": occurrences["value"],
            "offset": spacing["value"],
            "spacings": [],
        }
    if kind == "exact_spacings":
        raw_spacings = distribution.get("spacings")
        if not isinstance(raw_spacings, list) or not raw_spacings:
            return _invalid("distribution.spacings must contain at least one gap.")
        spacings = []
        for index, raw in enumerate(raw_spacings):
            spacing = _positive(raw, f"distribution.spacings[{index}]", maximum)
            if not spacing.get("ok"):
                return spacing
            spacings.append(spacing["value"])
        return {
            "ok": True,
            "mode": "Spacing",
            "extent": None,
            "occurrences": len(spacings) + 1,
            "offset": spacings[0],
            "spacings": spacings,
        }
    return _invalid("distribution.type must be extent, uniform_spacing, or exact_spacings.")


def _apply_distribution(feature: Any, state: dict[str, Any], *, extent_property: str) -> None:
    feature.Occurrences = int(state["occurrences"])
    feature.Mode = state["mode"]
    if state["mode"] == "Extent":
        setattr(feature, extent_property, float(state["extent"]))
        feature.SpacingPattern = []
        feature.Spacings = []
    else:
        feature.Offset = float(state["offset"])
        if state["spacings"]:
            feature.SpacingPattern = []
            feature.Spacings = list(state["spacings"])
        else:
            feature.SpacingPattern = [float(state["offset"])]
            feature.Spacings = [-1.0] * (int(state["occurrences"]) - 1)


def _validate_transformation(service: Any, body: Any, definition: Any) -> dict[str, Any]:
    if not isinstance(definition, dict):
        return _invalid("Each transformation must be an object.")
    kind = str(definition.get("type") or "")
    if kind in {"linear", "polar"}:
        reference = _resolve_axis(service, body, definition.get("reference"))
        if not reference.get("ok"):
            return reference
        quantity = "length" if kind == "linear" else "angle_degrees"
        maximum = 1e300 if kind == "linear" else 360.0
        distribution = _validate_distribution(definition.get("distribution"), quantity, maximum)
        if not distribution.get("ok"):
            return distribution
        return {
            "ok": True,
            "type": kind,
            "reference": reference,
            "distribution": distribution,
            "reversed": bool(definition.get("reversed")),
        }
    if kind == "mirror":
        reference = _resolve_plane(service, body, definition.get("reference"))
        if not reference.get("ok"):
            return reference
        return {"ok": True, "type": kind, "reference": reference}
    if kind == "scale":
        factor = _positive(definition.get("factor"), "scale factor", 1e300)
        occurrences = _occurrences(definition.get("occurrences"))
        if not factor.get("ok"):
            return factor
        if not occurrences.get("ok"):
            return occurrences
        return {
            "ok": True,
            "type": kind,
            "factor": factor["value"],
            "occurrences": occurrences["value"],
        }
    return _invalid("Transformation type must be linear, polar, mirror, or scale.")


def _create_transform_child(body: Any, doc: Any, state: dict[str, Any]) -> Any:
    kind = state["type"]
    if kind == "linear":
        child = body.newObject("PartDesign::LinearPattern", "LinearPatternTransform")
        reference = doc.getObject(state["reference"]["object_name"])
        if reference is None:
            raise RuntimeError("MultiTransform linear reference no longer exists.")
        child.Direction = (reference, [state["reference"]["subelement"]])
        _apply_distribution(child, state["distribution"], extent_property="Length")
        child.Reversed = state["reversed"]
    elif kind == "polar":
        child = body.newObject("PartDesign::PolarPattern", "PolarPatternTransform")
        reference = doc.getObject(state["reference"]["object_name"])
        if reference is None:
            raise RuntimeError("MultiTransform polar reference no longer exists.")
        child.Axis = (reference, [state["reference"]["subelement"]])
        _apply_distribution(child, state["distribution"], extent_property="Angle")
        child.Reversed = state["reversed"]
    elif kind == "mirror":
        child = body.newObject("PartDesign::Mirrored", "MirrorTransform")
        reference = doc.getObject(state["reference"]["object_name"])
        if reference is None:
            raise RuntimeError("MultiTransform mirror reference no longer exists.")
        child.MirrorPlane = (reference, [state["reference"]["subelement"]])
    else:
        child = body.newObject("PartDesign::Scaled", "ScaleTransform")
        child.Factor = state["factor"]
        child.Occurrences = state["occurrences"]
    return child


def _multi_transform_occurrence_count(states: list[dict[str, Any]]) -> int:
    count = 0
    for state in states:
        kind = state["type"]
        child_count = (
            int(state["distribution"]["occurrences"])
            if kind in {"linear", "polar"}
            else 2
            if kind == "mirror"
            else int(state["occurrences"])
        )
        if count == 0:
            count = child_count
        elif kind != "scale":
            count *= child_count
    return count


def _child_summary(child: Any) -> dict[str, Any]:
    summary = {
        "name": child.Name,
        "type": child.TypeId,
        "state": [str(value) for value in list(child.State)],
    }
    for name in (
        "Mode",
        "Length",
        "Angle",
        "Offset",
        "Occurrences",
        "Reversed",
        "Factor",
        "GeneratedOccurrenceCount",
        "RejectedSolidCount",
    ):
        if hasattr(child, name):
            value = getattr(child, name)
            if isinstance(value, bool):
                value = bool(value)
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = str(value)
            summary[name.lower()] = value
    return summary


def _native_distribution_summary(feature: Any) -> dict[str, Any]:
    result = {
        "mode": str(feature.Mode),
        "occurrences": int(feature.Occurrences),
        "offset": float(feature.Offset),
        "spacings": [float(value) for value in list(feature.Spacings)],
        "spacing_pattern": [float(value) for value in list(feature.SpacingPattern)],
    }
    if hasattr(feature, "Length"):
        result["length"] = float(feature.Length)
    if hasattr(feature, "Angle"):
        result["angle_degrees"] = float(feature.Angle)
    return result


def _transform_mode(value: Any) -> str | None:
    return {"features": "Features", "whole_shape": "Whole shape"}.get(str(value or ""))


def _occurrences(value: Any) -> dict[str, Any]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _invalid("occurrences must be an integer of at least 2.")
    if parsed < 2 or isinstance(value, float) and not value.is_integer():
        return _invalid("occurrences must be an integer of at least 2.")
    return {"ok": True, "value": parsed}


def _positive(value: Any, name: str, maximum: float) -> dict[str, Any]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return _invalid(f"{name} must be a positive number.")
    if not 0.0 < parsed <= maximum:
        return _invalid(f"{name} must be greater than 0 and no more than {maximum}.")
    return {"ok": True, "value": parsed}


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
