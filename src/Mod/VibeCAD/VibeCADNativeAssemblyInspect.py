# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact read-only inspection for active Assembly components."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeTargets import (
    NativeObjectRef,
    document_uid,
    object_reference,
    resolve_object,
)


NATIVE_ASSEMBLY_INSPECT_FAILED = "NATIVE_ASSEMBLY_INSPECT_FAILED"
MAX_LINKED_SELECTION_SUBELEMENTS = 64
MAX_JOINT_CONNECTORS = 4096
MAX_JOINT_CONNECTOR_PAIRS = 100
_JOINT_GEOMETRY = {
    "fixed": None,
    "revolute": frozenset({"cylinder", "circle", "component_origin"}),
    "cylindrical": frozenset({"cylinder", "circle", "component_origin"}),
    "slider": frozenset({"cylinder", "line", "component_origin"}),
    "ball": frozenset({"sphere", "vertex", "component_origin"}),
    "distance": None,
    "parallel": frozenset({"plane", "cylinder", "line", "component_origin"}),
    "perpendicular": frozenset(
        {"plane", "cylinder", "line", "component_origin"}
    ),
    "angle": frozenset({"plane", "cylinder", "line", "component_origin"}),
    "rack_pinion": frozenset({"cylinder", "circle", "component_origin"}),
    "screw": frozenset({"cylinder", "circle", "component_origin"}),
    "belt": frozenset({"cylinder", "circle", "component_origin"}),
    "gears": frozenset({"cylinder", "circle", "component_origin"}),
}


class NativeAssemblyInspectError(RuntimeError):
    """The exact selected Assembly source cannot be read safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": NATIVE_ASSEMBLY_INSPECT_FAILED,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class _SelectedObject:
    obj: Any
    document_uid: str
    document_name: str
    object_name: str
    object_id: int
    subelements: tuple[str, ...]

    def canonical(self) -> tuple[Any, ...]:
        return (
            self.obj,
            self.document_uid,
            self.document_name,
            self.object_name,
            self.object_id,
            self.subelements,
        )


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == type_id:
        return True
    reader = getattr(obj, "isDerivedFrom", None)
    if not callable(reader):
        return False
    try:
        return bool(reader(type_id))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _live_object(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    name = str(getattr(obj, "Name", "") or "")
    reader = getattr(document, "getObject", None)
    return bool(
        document is not None and name and callable(reader) and reader(name) is obj
    )


def _selection_api() -> Any:
    import FreeCADGui as Gui

    return Gui.Selection


def _selected_objects(selection_api: Any) -> tuple[_SelectedObject, ...]:
    reader = getattr(selection_api, "getSelectionEx", None)
    if not callable(reader):
        raise NativeAssemblyInspectError(
            "The exact global GUI selection is unavailable."
        )
    try:
        entries = tuple(reader() or ())
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyInspectError(
            "The exact global GUI selection is unreadable."
        ) from exc
    result = []
    for entry in entries:
        obj = getattr(entry, "Object", None)
        if not _live_object(obj):
            raise NativeAssemblyInspectError(
                "The human selection contains a stale document object."
            )
        try:
            subelements = tuple(
                str(value)
                for value in tuple(getattr(entry, "SubElementNames", ()) or ())
            )
            if len(subelements) > MAX_LINKED_SELECTION_SUBELEMENTS:
                raise NativeAssemblyInspectError(
                    "The selected Assembly link exceeds the bounded subelement count."
                )
            result.append(
                _SelectedObject(
                    obj=obj,
                    document_uid=document_uid(obj.Document),
                    document_name=str(obj.Document.Name),
                    object_name=str(obj.Name),
                    object_id=int(obj.ID),
                    subelements=subelements,
                )
            )
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, NativeAssemblyInspectError):
                raise
            raise NativeAssemblyInspectError(
                "The human selection has an invalid object identity."
            ) from exc
    return tuple(result)


def _exact_object_summary(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        **object_reference(obj),
        "document_name": str(obj.Document.Name),
        "object_id": int(obj.ID),
    }
    label = str(getattr(obj, "Label", "") or "").strip()
    if label and label != result["object_name"]:
        result["label"] = label[:160]
    return result


def _linked_assembly(link: Any) -> Any:
    reader = getattr(link, "getLinkedAssembly", None)
    if callable(reader):
        try:
            source = reader()
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyInspectError(
                "The selected Assembly link source is unreadable."
            ) from exc
    else:
        try:
            source = link.LinkedObject
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyInspectError(
                "The selected object has no native linked-Assembly source."
            ) from exc
    if (
        source is link
        or not _live_object(source)
        or not _is_derived(source, "Assembly::AssemblyObject")
        or not _timeline_active(source)
    ):
        raise NativeAssemblyInspectError(
            "The selected Assembly link has no exact active linked Assembly."
        )
    return source


def _finite_vector(value: Any) -> list[float]:
    result = [
        float(getattr(value, "x", 0.0)),
        float(getattr(value, "y", 0.0)),
        float(getattr(value, "z", 0.0)),
    ]
    if not all(math.isfinite(item) for item in result):
        raise NativeAssemblyInspectError(
            "A joint connector contains non-finite coordinates."
        )
    return result


def _connector_axis(placement: Any) -> list[float]:
    try:
        import FreeCAD as App

        return _finite_vector(
            placement.Rotation.multVec(App.Vector(0.0, 0.0, 1.0))
        )
    except NativeAssemblyInspectError:
        raise
    except (AttributeError, ImportError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyInspectError(
            "A joint connector direction is unavailable."
        ) from exc


def _geometry_type(element: Any) -> str:
    geometry = (
        getattr(element, "Surface", None)
        or getattr(element, "Curve", None)
    )
    value = str(getattr(geometry, "TypeId", "") or "")
    for token in ("Part::Geom", "Surface", "Curve"):
        value = value.replace(token, "")
    return value.lower() or str(getattr(element, "ShapeType", "") or "").lower()


def _connector_record(
    component_name: str,
    element_name: str,
    resolved: Any,
) -> dict[str, Any]:
    element = resolved.selected_element
    result: dict[str, Any] = {
        "endpoint": {
            "component": component_name,
            "connector_type": "element",
            "connector": element_name or "Origin",
        },
        "element": element_name,
        "geometry": (
            "component_origin" if element is None else _geometry_type(element)
        ),
        "origin_mm": _finite_vector(resolved.local_frame.Base),
        "axis": _connector_axis(resolved.local_frame),
    }
    if element is None:
        return result
    radius = getattr(
        getattr(element, "Surface", None)
        or getattr(element, "Curve", None),
        "Radius",
        None,
    )
    if radius is not None and math.isfinite(float(radius)):
        result["radius_mm"] = float(radius)
    if hasattr(element, "Area"):
        area = float(element.Area)
        if math.isfinite(area):
            result["area_mm2"] = area
    elif hasattr(element, "Length"):
        length = float(element.Length)
        if math.isfinite(length):
            result["length_mm"] = length
    return result


def _connector_size(value: dict[str, Any]) -> float:
    return float(value.get("area_mm2", value.get("length_mm", 0.0)))


def _axis_key(value: list[float]) -> tuple[float, float, float]:
    axis = tuple(float(item) for item in value)
    for item in axis:
        if abs(item) <= 1.0e-9:
            continue
        if item < 0.0:
            axis = tuple(-entry for entry in axis)
        break
    return tuple(round(item, 6) for item in axis)


def _connector_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["geometry"],
        round(float(value.get("radius_mm", 0.0)), 6),
        tuple(round(float(item), 6) for item in value["origin_mm"]),
        _axis_key(value["axis"]),
    )


def _useful_connectors(
    values: list[dict[str, Any]],
    joint_type: str,
) -> list[dict[str, Any]]:
    allowed = _JOINT_GEOMETRY[joint_type]
    filtered = [
        value
        for value in values
        if allowed is None or value["geometry"] in allowed
    ]
    filtered.sort(
        key=lambda value: (
            value["geometry"] != "component_origin",
            -_connector_size(value),
            value["element"],
        )
    )
    result = []
    seen = set()
    for value in filtered:
        key = _connector_key(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _sample_count(count: int, quota: int) -> tuple[int, ...]:
    if quota >= count:
        return tuple(range(1, count + 1))
    if quota == 1:
        return (1,)
    return tuple(
        1 + (index * (count - 1)) // (quota - 1)
        for index in range(quota)
    )


def _bounded_connector_names(shape: Any, joint_type: str) -> list[str]:
    if joint_type in {"fixed", "distance"}:
        kinds = (
            ("Face", len(shape.Faces)),
            ("Edge", len(shape.Edges)),
            ("Vertex", len(shape.Vertexes)),
        )
    elif joint_type == "ball":
        kinds = (
            ("Face", len(shape.Faces)),
            ("Vertex", len(shape.Vertexes)),
        )
    else:
        kinds = (
            ("Face", len(shape.Faces)),
            ("Edge", len(shape.Edges)),
        )
    total = sum(count for _prefix, count in kinds)
    topology_limit = MAX_JOINT_CONNECTORS - 1
    if total <= topology_limit:
        quotas = [count for _prefix, count in kinds]
    else:
        quotas = [
            max(1, topology_limit * count // total) if count else 0
            for _prefix, count in kinds
        ]
        while sum(quotas) > topology_limit:
            index = max(
                range(len(quotas)),
                key=lambda item: (quotas[item] > 1, quotas[item], -item),
            )
            quotas[index] -= 1
        while sum(quotas) < topology_limit:
            available = [
                index
                for index, ((_prefix, count), quota) in enumerate(
                    zip(kinds, quotas, strict=True)
                )
                if quota < count
            ]
            if not available:
                break
            index = max(
                available,
                key=lambda item: (kinds[item][1] - quotas[item], -item),
            )
            quotas[index] += 1
    return [
        *(f"{prefix}{index}" for (prefix, count), quota in zip(
            kinds, quotas, strict=True
        ) for index in _sample_count(count, quota)),
        "",
    ]


def _element_rank(value: dict[str, Any]) -> int:
    element = str(value.get("element") or "")
    if element.startswith("Face"):
        return 0
    if element.startswith("Edge"):
        return 1
    if element.startswith("Vertex"):
        return 2
    return 3


def _axis_alignment(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:
    dot = sum(
        float(left) * float(right)
        for left, right in zip(first["axis"], second["axis"])
    )
    return min(1.0, abs(dot))


def _relative_radius_difference(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:
    left = first.get("radius_mm")
    right = second.get("radius_mm")
    if left is None or right is None:
        return math.inf
    return abs(float(left) - float(right)) / max(
        abs(float(left)), abs(float(right)), 1.0e-9
    )


def _pair_allowed(
    first: dict[str, Any],
    second: dict[str, Any],
    joint_type: str,
) -> bool:
    left = first["geometry"]
    right = second["geometry"]
    circular = {"cylinder", "circle"}
    axial = {"cylinder", "circle", "line"}
    if joint_type in {"revolute", "cylindrical", "screw", "belt", "gears"}:
        return left in circular and right in circular
    if joint_type == "rack_pinion":
        return (left == "line" and right in circular) or (
            right == "line" and left in circular
        )
    if joint_type == "slider":
        return left in axial and right in axial
    if joint_type == "ball":
        return left in {"sphere", "vertex"} and right in {"sphere", "vertex"}
    return left != "component_origin" and right != "component_origin"


def _pair_score(
    first: dict[str, Any],
    second: dict[str, Any],
    joint_type: str,
) -> tuple[Any, ...]:
    alignment = _axis_alignment(first, second)
    if joint_type in {"revolute", "cylindrical", "screw", "ball"}:
        geometry_score = _relative_radius_difference(first, second)
    elif joint_type == "perpendicular":
        geometry_score = alignment
    else:
        geometry_score = 1.0 - alignment
    return (
        geometry_score,
        _element_rank(first) + _element_rank(second),
        first["geometry"] != second["geometry"],
        -(_connector_size(first) + _connector_size(second)),
        first["element"],
        second["element"],
    )


def _connector_details(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "type": value["geometry"],
        "origin_mm": value["origin_mm"],
        "axis": value["axis"],
    }
    if "radius_mm" in value:
        result["radius_mm"] = value["radius_mm"]
    return result


def _pair_record(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "first": first["endpoint"],
        "second": second["endpoint"],
        "first_geometry": _connector_details(first),
        "second_geometry": _connector_details(second),
    }
    if "radius_mm" in first and "radius_mm" in second:
        result["radius_difference_mm"] = abs(
            float(first["radius_mm"]) - float(second["radius_mm"])
        )
    for side, connector in (("first", first), ("second", second)):
        contract = connector.get("contract")
        if isinstance(contract, Mapping) and contract:
            result[f"{side}_contract"] = dict(contract)
    return result


def source_connector_inventory(
    component: Any,
    joint_type: str,
    *,
    preferred_only: bool = False,
) -> list[dict[str, Any]]:
    """Return bounded native-JCS connectors for one reusable source object."""

    if joint_type not in _JOINT_GEOMETRY:
        raise NativeAssemblyInspectError("joint_type is unavailable.")
    shape = getattr(component, "Shape", None)
    if shape is None or bool(shape.isNull()):
        return []
    import UtilsAssembly

    connectors: list[dict[str, Any]] = []
    names = _bounded_connector_names(shape, joint_type)
    if preferred_only:
        names = [name for name in names if not name or name.startswith("Face")]
    for name in names:
        try:
            element = shape.getElement(name) if name else None
            frame = UtilsAssembly.findPlacement(
                [component, [name, name]],
                False,
            )
        except Exception:
            continue
        resolved = SimpleNamespace(selected_element=element, local_frame=frame)
        connectors.append(
            _connector_record(str(getattr(component, "Name", "") or ""), name, resolved)
        )
    return _useful_connectors(connectors, joint_type)


def _component_is_movable(assembly: Any, component: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isMovableAssemblyComponent(assembly, component))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _published_connector_inventory(
    component: Any,
    joint_type: str,
) -> tuple[bool, list[dict[str, Any]]]:
    import VibeCADReferenceContracts as contracts
    from vibescript_assembly_api import explicit_connector_compatibility

    try:
        semantic_only, descriptors = contracts.component_interface_descriptors(
            component
        )
    except contracts.ReferenceContractError as exc:
        raise NativeAssemblyInspectError(str(exc)) from exc
    records = []
    for descriptor in descriptors:
        record = contracts.connector_interface_record(descriptor)
        if record is None or not explicit_connector_compatibility(
            joint_type, [record.get("contract")]
        )["ok"]:
            continue
        name = str(record["selection"]["interface_name"])
        records.append(
            {
                **record,
                "endpoint": {
                    "component": str(component.Name),
                    "connector_type": "interface",
                    "connector": name,
                },
            }
        )
    return semantic_only, _useful_connectors(records, joint_type)


def rank_connector_pairs(
    first_connectors: list[dict[str, Any]],
    second_connectors: list[dict[str, Any]],
    *,
    joint_type: str,
    limit: int,
    compatible: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return the best compatible pairs from two connector inventories."""

    if joint_type not in _JOINT_GEOMETRY:
        raise NativeAssemblyInspectError("joint_type is unavailable.")
    if type(limit) is not int or not 1 <= limit <= MAX_JOINT_CONNECTOR_PAIRS:
        raise NativeAssemblyInspectError(
            f"limit must be from 1 through {MAX_JOINT_CONNECTOR_PAIRS}."
        )
    origin_pairs = (
        [
            (first, second)
            for first in first_connectors
            for second in second_connectors
            if first["geometry"] == second["geometry"] == "component_origin"
            and (compatible is None or compatible(first, second))
        ][:1]
        if joint_type == "fixed"
        else []
    )
    candidates = (
        (first, second)
        for first in first_connectors
        for second in second_connectors
        if _pair_allowed(first, second, joint_type)
        and (compatible is None or compatible(first, second))
    )
    return [
        *origin_pairs,
        *heapq.nsmallest(
            limit - len(origin_pairs),
            candidates,
            key=lambda pair: _pair_score(pair[0], pair[1], joint_type),
        ),
    ]


def _joint_connector_inventory(
    document: Any,
    assembly: Any,
    component_ref: NativeObjectRef,
    joint_type: str,
) -> tuple[Any, list[dict[str, Any]]]:
    component = resolve_object(document, component_ref)
    if component not in assembly_components(assembly):
        raise NativeAssemblyInspectError(
            "The requested object is not a component of the active Assembly."
        )
    if not _timeline_active(component) or not _component_is_movable(
        assembly, component
    ):
        raise NativeAssemblyInspectError(
            "The requested object is not an active movable Assembly component."
        )
    semantic_only, semantic = _published_connector_inventory(
        component, joint_type
    )
    if semantic_only:
        return component, semantic
    return component, source_connector_inventory(component, joint_type)


def read_joint_connector_pairs(
    document: Any,
    first_ref: NativeObjectRef,
    second_ref: NativeObjectRef,
    *,
    joint_type: str,
    limit: int,
    guard: Callable[[], None],
) -> dict[str, Any]:
    """Return component origins and the best geometry endpoint pairs."""

    if not isinstance(first_ref, NativeObjectRef) or not isinstance(
        second_ref, NativeObjectRef
    ):
        raise TypeError("component references must be NativeObjectRef values")
    if first_ref == second_ref:
        raise NativeAssemblyInspectError("Choose two different Assembly components.")
    if joint_type not in _JOINT_GEOMETRY:
        raise NativeAssemblyInspectError("joint_type is unavailable.")
    if type(limit) is not int or not 1 <= limit <= MAX_JOINT_CONNECTOR_PAIRS:
        raise NativeAssemblyInspectError(
            f"limit must be from 1 through {MAX_JOINT_CONNECTOR_PAIRS}."
        )
    if not callable(guard):
        raise TypeError("guard must be callable")

    guard()
    try:
        assembly = read_active_assembly(document)
        if assembly is None:
            raise NativeAssemblyInspectError("No Assembly is active.")
        first_component, first_connectors = _joint_connector_inventory(
            document, assembly, first_ref, joint_type
        )
        second_component, second_connectors = _joint_connector_inventory(
            document, assembly, second_ref, joint_type
        )
        pairs = rank_connector_pairs(
            first_connectors,
            second_connectors,
            joint_type=joint_type,
            limit=limit,
        )
        result = {
            "operation": "joint_connector_pairs",
            "joint_type": joint_type,
            "first_component": _exact_object_summary(first_component),
            "second_component": _exact_object_summary(second_component),
            "pairs": [_pair_record(first, second) for first, second in pairs],
        }
        guard()
        if not _live_object(first_component) or not _live_object(second_component):
            raise NativeAssemblyInspectError(
                "An Assembly component changed while reading connector pairs."
            )
        return result
    except NativeAssemblyInspectError:
        raise
    except Exception as exc:
        raise NativeAssemblyInspectError(str(exc)) from exc


def read_joint_connectors(
    document: Any,
    component_ref: NativeObjectRef,
    *,
    joint_type: str,
    offset: int,
    page_size: int,
    guard: Callable[[], None],
) -> dict[str, Any]:
    """List only connector paths that the Assembly joint runtime can resolve."""

    if not isinstance(component_ref, NativeObjectRef):
        raise TypeError("component_ref must be a NativeObjectRef")
    if joint_type not in _JOINT_GEOMETRY:
        raise NativeAssemblyInspectError("joint_type is unavailable.")
    if type(offset) is not int or not 0 <= offset <= MAX_JOINT_CONNECTORS:
        raise NativeAssemblyInspectError("offset must be from 0 through 4096.")
    if type(page_size) is not int or not 1 <= page_size <= 48:
        raise NativeAssemblyInspectError("page_size must be from 1 through 48.")
    if not callable(guard):
        raise TypeError("guard must be callable")

    guard()
    try:
        assembly = read_active_assembly(document)
        if assembly is None:
            raise NativeAssemblyInspectError("No Assembly is active.")
        component, connectors = _joint_connector_inventory(
            document, assembly, component_ref, joint_type
        )
        page = connectors[offset : offset + page_size]
        next_offset = offset + len(page)
        result = {
            "operation": "joint_connectors",
            "component": _exact_object_summary(component),
            "joint_type": joint_type,
            "connector_count": len(connectors),
            "offset": offset,
            "connectors": page,
            "next_offset": next_offset if next_offset < len(connectors) else None,
        }
        guard()
        if not _live_object(component):
            raise NativeAssemblyInspectError(
                "The Assembly component changed while reading its connectors."
            )
        return result
    except NativeAssemblyInspectError:
        raise
    except Exception as exc:
        raise NativeAssemblyInspectError(str(exc)) from exc


def read_selected_linked_assembly(
    document: Any,
    link_ref: NativeObjectRef,
    *,
    guard: Callable[[], None],
    selection_api: Any | None = None,
) -> dict[str, Any]:
    """Read one explicit AssemblyLink without changing GUI state."""

    if not isinstance(link_ref, NativeObjectRef):
        raise TypeError("link_ref must be a NativeObjectRef")
    if not callable(guard):
        raise TypeError("guard must be callable")
    selected_api = selection_api if selection_api is not None else _selection_api()

    guard()
    try:
        link = resolve_object(
            document,
            link_ref,
            expected_types=("Assembly::AssemblyLink",),
        )
    except Exception as exc:
        raise NativeAssemblyInspectError(str(exc)) from exc
    if not _timeline_active(link):
        raise NativeAssemblyInspectError(
            "The Assembly link is outside the current History position."
        )

    selection_before = _selected_objects(selected_api)
    selected_subelements = (
        list(selection_before[0].subelements)
        if len(selection_before) == 1 and selection_before[0].obj is link
        else []
    )

    document_objects = tuple(getattr(document, "Objects", ()) or ())
    source = _linked_assembly(link)
    source_document = source.Document
    source_objects = tuple(getattr(source_document, "Objects", ()) or ())
    result = {
        "operation": "linked_source",
        "assembly_link": _exact_object_summary(link),
        "linked_assembly": _exact_object_summary(source),
        "source_is_external": source_document is not document,
        "rigid": bool(getattr(link, "Rigid", True)),
        "selected_subelements": selected_subelements,
    }

    guard()
    selection_after = _selected_objects(selected_api)
    if tuple(item.canonical() for item in selection_after) != tuple(
        item.canonical() for item in selection_before
    ):
        raise NativeAssemblyInspectError(
            "The human selection changed while reading the linked Assembly source."
        )
    if (
        tuple(getattr(document, "Objects", ()) or ()) != document_objects
        or tuple(getattr(source_document, "Objects", ()) or ()) != source_objects
        or not _live_object(link)
        or not _live_object(source)
        or _linked_assembly(link) is not source
    ):
        raise NativeAssemblyInspectError(
            "The linked Assembly graph changed while it was being read."
        )
    result["selection_unchanged"] = True
    result["active_document_unchanged"] = True
    result["document_graph_unchanged"] = True
    return result
