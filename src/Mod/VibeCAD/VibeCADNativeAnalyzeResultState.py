# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise exact state for FEM results and post-processing objects."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeSnapshot import concise_object
from VibeCADNativeTargets import NativeObjectRef, resolve_object


MAX_RESULT_FIELDS = 64
MAX_CONTEXT_FIELD_NAMES = 16
MAX_POST_CHILDREN = 32
MAX_POST_GRAPH_OBJECTS = 4096
MAX_FRAME_VALUES = 64

RESULT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        },
        "expected_state_sha256": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": r"^[0-9a-f]{64}$",
        },
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


_LEGACY_STATS = {
    "DisplacementVectors": ("displacement", 6, 7),
    "DisplacementLengths": ("displacement_magnitude", 6, 7),
    "vonMises": ("von_mises_stress", 8, 9),
    "PrincipalMax": ("maximum_principal_stress", 10, 11),
    "PrincipalMed": ("middle_principal_stress", 12, 13),
    "PrincipalMin": ("minimum_principal_stress", 14, 15),
    "MaxShear": ("maximum_shear_stress", 16, 17),
    "Peeq": ("equivalent_plastic_strain", 18, 19),
    "Temperature": ("temperature", 20, 21),
    "MassFlowRate": ("mass_flow_rate", 22, 23),
    "NetworkPressure": ("network_pressure", 24, 25),
}


@dataclass(frozen=True, slots=True)
class PreparedResultTarget:
    result: Any
    kind: str
    expected_state_sha256: str


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_derived(obj: Any, type_name: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_name))
    except Exception:
        return False


def result_kind(obj: Any) -> str:
    if _is_derived(obj, "Fem::FemResultObject"):
        return "result"
    if _is_derived(obj, "Fem::FemPostPipeline"):
        return "pipeline"
    if _is_derived(obj, "Fem::FemPostBranchFilter"):
        return "branch_filter"
    if _is_derived(obj, "Fem::FemPostFilter"):
        return "filter"
    if _is_derived(obj, "Fem::FemPostFunctionProvider"):
        return "function_provider"
    if _is_derived(obj, "Fem::FemPostFunction"):
        return "function"
    proxy = getattr(obj, "Proxy", None)
    if str(getattr(proxy, "Type", "") or "") == "Fem::FemPostVisualization":
        return "visualization"
    raise NativeAnalyzeError(
        "The exact target is not a FEM result or post-processing object.",
        error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
    )


def _identity(obj: Any) -> list[Any] | None:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        return None
    return [str(obj.Name), int(obj.ID)]


def _analysis_owners(document: Any, obj: Any) -> list[list[Any]]:
    owners = []
    for candidate in tuple(getattr(document, "Objects", ()) or ()):
        if not _is_derived(candidate, "Fem::FemAnalysis"):
            continue
        try:
            if obj in tuple(candidate.Group or ()):
                owners.append([str(candidate.Name), int(candidate.ID)])
        except Exception:
            continue
    return owners


def _post_pipeline_owners(document: Any, obj: Any) -> list[list[Any]]:
    pending = [obj]
    visited: set[int] = set()
    owners: dict[int, list[Any]] = {}
    while pending:
        current = pending.pop()
        if not is_live(document, current):
            continue
        identity = int(current.ID)
        if identity in visited:
            continue
        visited.add(identity)
        if len(visited) > MAX_POST_GRAPH_OBJECTS:
            raise NativeAnalyzeError(
                "The FEM post-processing ownership graph exceeds the supported limit."
            )
        if _is_derived(current, "Fem::FemPostPipeline"):
            owners[identity] = [str(current.Name), identity]
            continue
        for parent in tuple(getattr(current, "InList", ()) or ()):
            if not is_live(document, parent) or not (
                _is_derived(parent, "Fem::FemPostPipeline")
                or _is_derived(parent, "Fem::FemPostBranchFilter")
                or _is_derived(parent, "Fem::FemPostFunctionProvider")
            ):
                continue
            try:
                if current in tuple(parent.Group or ()):
                    pending.append(parent)
            except Exception:
                continue
    return [owners[key] for key in sorted(owners)]


def _timeline_chain(document: Any, obj: Any) -> list[list[Any]]:
    chain = []
    visited: set[int] = set()
    current = obj
    while is_live(document, current):
        identity = int(current.ID)
        if identity in visited:
            raise NativeAnalyzeError("The FEM result has cyclic History ownership.")
        visited.add(identity)
        owner = getattr(current, "VibeCADTimelineOwner", None)
        if not is_live(document, owner):
            break
        chain.append([str(owner.Name), int(owner.ID)])
        current = owner
    return chain


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return float(format(number, ".15g")) if math.isfinite(number) else None


def _legacy_fields(obj: Any, *, include_ranges: bool) -> list[dict[str, Any]]:
    stats = tuple(getattr(obj, "Stats", ()) or ())
    fields = []
    for property_name in tuple(getattr(obj, "PropertiesList", ()) or ()):
        try:
            if str(obj.getGroupOfProperty(property_name)) != "NodeData":
                continue
            property_type = str(obj.getTypeIdOfProperty(property_name))
            values = getattr(obj, property_name)
            count = len(values)
        except Exception:
            continue
        if count <= 0:
            continue
        semantic, lower_index, upper_index = _LEGACY_STATS.get(
            str(property_name), (str(property_name), -1, -1)
        )
        item = {
            "name": str(property_name),
            "semantic": semantic,
            "components": 3 if "VectorList" in property_type else 1,
            "value_count": int(count),
        }
        if include_ranges and 0 <= lower_index < upper_index < len(stats):
            lower = _finite(stats[lower_index])
            upper = _finite(stats[upper_index])
            if lower is not None and upper is not None:
                item["range"] = [lower, upper]
        fields.append(item)
        if len(fields) >= MAX_RESULT_FIELDS:
            break
    return fields


def _vtk_field_unit(name: str) -> str | None:
    normalized = name.strip().lower()
    if normalized == "arc_length":
        return "mm"
    if (
        "stress" in normalized
        or normalized.startswith("vonmises")
        or normalized.startswith("tresca")
    ):
        return "Pa"
    if normalized.startswith("displacement"):
        return "m"
    if normalized.startswith("temperature") and "flux" not in normalized:
        return "K"
    if normalized == "temperature flux":
        return "W/m^2"
    if normalized.startswith("current density"):
        return "A/m^2"
    if normalized.startswith("electric field"):
        return "V/m"
    if normalized == "electric energy density":
        return "J/m^3"
    if normalized.startswith("magnetic field strength"):
        return "A/m"
    if normalized.startswith("magnetic flux density"):
        return "T"
    if normalized == "nodal force":
        return "N"
    return None


def _vtk_fields(
    dataset: Any,
    *,
    include_ranges: bool,
    unit_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    overrides = dict(unit_overrides or {})
    fields = []
    for association, getter_name in (
        ("point", "GetPointData"),
        ("cell", "GetCellData"),
    ):
        try:
            attributes = getattr(dataset, getter_name)()
            count = int(attributes.GetNumberOfArrays())
        except Exception:
            continue
        for index in range(count):
            try:
                array = attributes.GetArray(index)
                if array is None:
                    continue
                name = str(array.GetName() or f"unnamed_{index}")
                components = int(array.GetNumberOfComponents())
                tuples = int(array.GetNumberOfTuples())
            except Exception:
                continue
            item = {
                "name": name[:160],
                "association": association,
                "components": components,
                "value_count": tuples,
            }
            unit = overrides.get(name) or _vtk_field_unit(name)
            if unit is not None:
                item["unit"] = unit
            if include_ranges and tuples > 0 and components > 0:
                try:
                    raw_range = array.GetRange(-1 if components > 1 else 0)
                    lower = _finite(raw_range[0])
                    upper = _finite(raw_range[1])
                    if lower is not None and upper is not None:
                        item["range"] = [lower, upper]
                        item["range_component"] = (
                            "magnitude" if components > 1 else "scalar"
                        )
                except Exception:
                    pass
            fields.append(item)
            if len(fields) >= MAX_RESULT_FIELDS:
                return fields
    return fields


def _dataset_state(obj: Any, *, include_ranges: bool) -> dict[str, Any]:
    try:
        dataset = obj.getDataSet()
    except Exception:
        dataset = None
    if dataset is None:
        return {
            "data_available": False,
            "point_count": 0,
            "cell_count": 0,
            "fields": [],
            "field_count": 0,
            "fields_truncated": False,
        }
    try:
        point_count = int(dataset.GetNumberOfPoints())
    except Exception:
        point_count = 0
    try:
        cell_count = int(dataset.GetNumberOfCells())
    except Exception:
        cell_count = 0
    unit_overrides = {}
    if _is_derived(obj, "Fem::FemPostCalculatorFilter"):
        try:
            field_name = str(obj.FieldName or "")
            result_unit = str(obj.ResultUnit or "")
            if field_name and result_unit:
                unit_overrides[field_name] = result_unit
        except Exception:
            pass
    elif _is_derived(obj, "Fem::FemPostDataAlongLineFilter"):
        try:
            field_name = str(obj.PlotData or "")
            result_unit = str(obj.Unit or "")
            if field_name and result_unit:
                unit_overrides[field_name] = result_unit
        except Exception:
            pass
    elif _is_derived(obj, "Fem::FemPostDataAtPointFilter"):
        try:
            field_name = str(obj.FieldName or "")
            result_unit = str(obj.Unit or "")
            if field_name and result_unit:
                unit_overrides[field_name] = result_unit
        except Exception:
            pass
    fields = _vtk_fields(
        dataset,
        include_ranges=include_ranges,
        unit_overrides=unit_overrides,
    )
    try:
        total_fields = int(dataset.GetPointData().GetNumberOfArrays()) + int(
            dataset.GetCellData().GetNumberOfArrays()
        )
    except Exception:
        total_fields = len(fields)
    return {
        "data_available": True,
        "point_count": point_count,
        "cell_count": cell_count,
        "fields": fields,
        "field_count": total_fields,
        "fields_truncated": total_fields > len(fields),
    }


def _safe_setting(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, float):
        return _finite(value)
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        result = [_finite(value.x), _finite(value.y), _finite(value.z)]
        return result if all(item is not None for item in result) else None
    if hasattr(value, "Value"):
        return _finite(value.Value)
    return None


def _post_settings(obj: Any) -> dict[str, Any]:
    skipped = frozenset(
        {
            "Data",
            "Table",
            "XAxisData",
            "YAxisData",
            "PointData",
            "Group",
            "Label",
            "Label2",
            "Placement",
            "ExpressionEngine",
            "VibeCADTimelineOwner",
            "VibeCADTimelineReplacedInputs",
            "VibeCADTimelineRole",
        }
    )
    settings = {}
    for raw_name in tuple(getattr(obj, "PropertiesList", ()) or ()):
        name = str(raw_name)
        if name in skipped:
            continue
        try:
            property_type = str(obj.getTypeIdOfProperty(name))
            if "Link" in property_type or property_type.endswith("List"):
                continue
            value = _safe_setting(obj.getPropertyByName(name))
        except Exception:
            continue
        if value is not None:
            settings[name] = value
    return settings


def _linked_functions(obj: Any) -> list[dict[str, Any]]:
    links = []
    for raw_name in tuple(getattr(obj, "PropertiesList", ()) or ()):
        name = str(raw_name)
        try:
            if str(obj.getTypeIdOfProperty(name)) != "App::PropertyLink":
                continue
            target = obj.getPropertyByName(name)
        except Exception:
            continue
        identity = _identity(target)
        if identity is not None:
            links.append({"property": name, "object_name": identity[0]})
    return links


def _presentation_state(obj: Any) -> dict[str, Any]:
    if _is_derived(obj, "Fem::FemResultObject"):
        try:
            from femresult.resultpresentation import result_presentation_state

            return result_presentation_state(obj)
        except Exception:
            pass
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return {}
    result: dict[str, Any] = {"visible": bool(getattr(view, "Visibility", False))}
    for native_name, output_name in (
        ("Field", "field"),
        ("Component", "component"),
        ("DisplayMode", "display_mode"),
        ("Transparency", "transparency_percent"),
    ):
        try:
            result[output_name] = getattr(view, native_name)
        except Exception:
            continue
    return result


def _children(obj: Any) -> tuple[list[dict[str, Any]], list[list[Any]]]:
    visible = []
    exact = []
    for child in tuple(getattr(obj, "Group", ()) or ()):
        identity = _identity(child)
        if identity is None:
            continue
        exact.append(identity)
        if len(visible) < MAX_POST_CHILDREN:
            visible.append(concise_object(child))
    return visible, exact


def _post_descendants(obj: Any) -> list[list[Any]]:
    document = getattr(obj, "Document", None)
    pending = list(tuple(getattr(obj, "Group", ()) or ()))
    visited: set[int] = set()
    descendants = []
    while pending:
        current = pending.pop(0)
        if not is_live(document, current):
            continue
        identity = int(current.ID)
        if identity in visited:
            continue
        visited.add(identity)
        if len(visited) > MAX_POST_GRAPH_OBJECTS:
            raise NativeAnalyzeError(
                "The FEM post-processing child graph exceeds the supported limit."
            )
        descendants.append([str(current.Name), identity])
        pending.extend(tuple(getattr(current, "Group", ()) or ()))
    return descendants


def _legacy_result_state(obj: Any, *, include_ranges: bool) -> dict[str, Any]:
    fields = _legacy_fields(obj, include_ranges=include_ranges)
    mesh = getattr(obj, "Mesh", None)
    mesh_identity = _identity(mesh)
    return {
        "result_type": str(getattr(obj, "ResultType", "") or ""),
        "time": _finite(getattr(obj, "Time", 0.0)),
        "eigenmode": int(getattr(obj, "Eigenmode", 0) or 0),
        "eigenmode_frequency": _finite(
            getattr(obj, "EigenmodeFrequency", 0.0)
        ),
        "node_count": len(tuple(getattr(obj, "NodeNumbers", ()) or ())),
        "mesh": mesh_identity[0] if mesh_identity is not None else None,
        "fields": fields,
        "field_count": len(fields),
        "fields_truncated": len(fields) >= MAX_RESULT_FIELDS,
    }


def _post_state(obj: Any, *, include_ranges: bool) -> dict[str, Any]:
    kind = result_kind(obj)
    children, exact_children = _children(obj)
    data_state = (
        _dataset_state(obj, include_ranges=include_ranges)
        if kind in {"pipeline", "branch_filter", "filter"}
        else {
            "data_available": False,
            "point_count": 0,
            "cell_count": 0,
            "fields": [],
            "field_count": 0,
            "fields_truncated": False,
        }
    )
    state = {
        **data_state,
        "settings": _post_settings(obj),
        "links": _linked_functions(obj),
        "child_count": len(exact_children),
        "children": children,
        "children_truncated": len(exact_children) > len(children),
        "descendant_count": len(_post_descendants(obj)),
    }
    if kind == "pipeline":
        frames = []
        try:
            frames = [
                value
                for value in (_finite(item) for item in obj.getFrameValues())
                if value is not None
            ]
        except Exception:
            pass
        state["frame"] = str(getattr(obj, "Frame", "") or "")
        state["frame_count"] = len(frames)
        state["frame_values"] = frames[:MAX_FRAME_VALUES]
        state["frame_values_truncated"] = len(frames) > MAX_FRAME_VALUES
    return state


def result_state(obj: Any, *, include_ranges: bool = True) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM result is no longer live.")
    kind = result_kind(obj)
    analysis_owners = _analysis_owners(document, obj)
    post_pipeline_owners = _post_pipeline_owners(document, obj)
    timeline_chain = _timeline_chain(document, obj)
    details = (
        _legacy_result_state(obj, include_ranges=include_ranges)
        if kind == "result"
        else _post_state(obj, include_ranges=include_ranges)
    )
    presentation = _presentation_state(obj)
    digest_fields = [
        {
            "name": field.get("name"),
            "association": field.get("association"),
            "components": field.get("components"),
            "value_count": field.get("value_count"),
        }
        for field in details.get("fields", [])
    ]
    digest_payload = {
        "object": [str(obj.Name), int(obj.ID), str(obj.Label), str(obj.TypeId)],
        "kind": kind,
        "analysis_owners": analysis_owners,
        "post_pipeline_owners": post_pipeline_owners,
        "timeline_chain": timeline_chain,
        "data": {
            key: value
            for key, value in details.items()
            if key not in {"fields", "children", "frame_values"}
        },
        "fields": digest_fields,
        "children": [
            [str(child.Name), int(child.ID)]
            for child in tuple(getattr(obj, "Group", ()) or ())
            if is_live(document, child)
        ],
        "descendants": _post_descendants(obj),
        "presentation": presentation,
    }
    return {
        **concise_object(obj),
        "result_kind": kind,
        "analysis_owners": [identity[0] for identity in analysis_owners],
        "post_pipeline_owners": [identity[0] for identity in post_pipeline_owners],
        "timeline_owner_chain": [identity[0] for identity in timeline_chain],
        **details,
        "presentation": presentation,
        "state_sha256": _digest(digest_payload),
    }


def result_reference_state(obj: Any) -> dict[str, Any]:
    state = result_state(obj, include_ranges=False)
    result = {
        key: state[key]
        for key in (
            "object_name",
            "object_id",
            "label",
            "type_id",
            "result_kind",
            "analysis_owners",
            "post_pipeline_owners",
            "timeline_owner_chain",
            "data_available",
            "point_count",
            "cell_count",
            "fields_truncated",
            "state_sha256",
        )
        if key in state
    }
    fields = list(state.get("fields", []) or ())
    field_count = int(state.get("field_count", len(fields)) or 0)
    names = [str(field.get("name", ""))[:80] for field in fields]
    result["field_count"] = field_count
    result["field_names"] = names[:MAX_CONTEXT_FIELD_NAMES]
    result["field_names_truncated"] = field_count > len(result["field_names"])
    return result


def prepare_result_target(
    document: Any,
    document_uid: str,
    value: Any,
    *,
    expected_kinds: frozenset[str] | None = None,
) -> PreparedResultTarget:
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "result target must contain only object_name and expected_state_sha256."
        )
    result = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"])),
    )
    state = result_state(result, include_ranges=False)
    kind = state["result_kind"]
    if expected_kinds is not None and kind not in expected_kinds:
        allowed = ", ".join(sorted(expected_kinds))
        raise NativeAnalyzeError(
            f"The exact target is {kind}; this operation accepts only {allowed}.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    expected = str(value["expected_state_sha256"] or "")
    if state["state_sha256"] != expected:
        raise NativeAnalyzeError(
            "The exact FEM result changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "result": {"object_name": str(result.Name)},
                "result_kind": kind,
                "current_state_sha256": state["state_sha256"],
            },
        )
    return PreparedResultTarget(result, kind, expected)
