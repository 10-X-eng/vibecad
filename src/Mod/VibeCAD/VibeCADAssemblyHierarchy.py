# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, model-facing snapshots of native Assembly source hierarchies.

FreeCAD assigns new internal names when an ``Assembly::AssemblyLink`` mirrors a
source assembly.  Those names are implementation details and are unsuitable as
LLM-authored program inputs.  This module captures source occurrence names as a
stable path contract, together with the exact native state needed to rebuild the
hierarchy in an isolated worker.

The capture entry point is document-thread safe: it copies kernel shapes and
reads bounded properties, but performs no artifact I/O, recompute, solve, or
topology analysis.  Serialization and topology validation happen later on the
background lifecycle thread.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from pathlib import Path
import re
from typing import Any


ASSEMBLY_HIERARCHY_SCHEMA = "vibecad-assembly-source-hierarchy-v1"
MAX_HIERARCHY_DEPTH = 16
MAX_HIERARCHY_NODES = 512
MAX_HIERARCHY_OCCURRENCES = 2048
MAX_HIERARCHY_JOINTS = 1024
MAX_HIERARCHY_SHAPES = 256
MAX_BOM_PROPERTIES_PER_NODE = 64
MAX_BOM_PROPERTY_TEXT = 4096
MAX_CONTEXT_OCCURRENCES = 256
MAX_CONTEXT_JOINTS = 128

_ELEMENT = re.compile(r"^(?:Face|Edge|Vertex)[1-9][0-9]*$")
_SUPPORTED_NATIVE_JOINTS = frozenset(
    {
        "Fixed",
        "Revolute",
        "Cylindrical",
        "Slider",
        "Ball",
        "Distance",
        "Parallel",
        "Perpendicular",
        "Angle",
        "RackPinion",
        "Screw",
        "Gears",
        "Belt",
    }
)
_IGNORED_PROPERTY_NAMES = frozenset(
    {
        "ExpressionEngine",
        "Group",
        "Label",
        "Label2",
        "LinkPlacement",
        "LinkedObject",
        "Placement",
        "Proxy",
        "Shape",
        "Support",
        "Visibility",
    }
)
_IGNORED_CONTAINER_TYPES = frozenset(
    {
        "Assembly::BomGroup",
        "Assembly::JointGroup",
        "Assembly::SimulationGroup",
        "Assembly::ViewGroup",
    }
)


class AssemblyHierarchyError(RuntimeError):
    """A precise source-hierarchy failure suitable for provider feedback."""


def _identity(obj: Any) -> tuple[str, str]:
    document = getattr(obj, "Document", None)
    return (
        str(getattr(document, "Uid", "") or ""),
        str(getattr(obj, "Name", "") or ""),
    )


def _is_derived(obj: Any, type_id: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_id))
    except Exception:
        return str(getattr(obj, "TypeId", "") or "") == type_id


def _matrix(placement: Any, *, context: str) -> list[float]:
    try:
        values = [float(value) for value in placement.toMatrix().A]
    except Exception as exc:
        raise AssemblyHierarchyError(f"{context} has no readable Placement: {exc}") from exc
    if len(values) != 16 or any(not math.isfinite(value) for value in values):
        raise AssemblyHierarchyError(f"{context} has a non-finite Placement matrix.")
    return values


def _placement_matrix(obj: Any, *, context: str) -> list[float]:
    import FreeCAD as App

    return _matrix(getattr(obj, "Placement", App.Placement()), context=context)


def _bounded_text(value: Any, *, context: str) -> str:
    text = str(value or "")
    if len(text) > MAX_BOM_PROPERTY_TEXT:
        raise AssemblyHierarchyError(
            f"{context} contains {len(text)} characters; the limit is "
            f"{MAX_BOM_PROPERTY_TEXT}."
        )
    return text


def _bom_property(obj: Any, name: str, property_type: str) -> dict[str, Any] | None:
    """Capture exactly the scalar property families supported by native BOM."""

    try:
        value = getattr(obj, name)
    except Exception as exc:
        raise AssemblyHierarchyError(
            f"Could not read BOM property {name!r} on {getattr(obj, 'Name', '')!r}: {exc}"
        ) from exc
    context = f"BOM property {getattr(obj, 'Name', '')}.{name}"
    if property_type == "App::PropertyString":
        return {
            "name": name,
            "property_type": property_type,
            "kind": "string",
            "value": _bounded_text(value, context=context),
        }
    if property_type == "App::PropertyEnumeration":
        try:
            choices = [str(item) for item in obj.getEnumerationsOfProperty(name)]
        except Exception as exc:
            raise AssemblyHierarchyError(
                f"Could not read enumeration choices for {context}: {exc}"
            ) from exc
        if not choices or len(choices) > 256:
            raise AssemblyHierarchyError(
                f"{context} must expose between 1 and 256 enumeration choices."
            )
        return {
            "name": name,
            "property_type": property_type,
            "kind": "enumeration",
            "value": _bounded_text(value, context=context),
            "choices": choices,
        }
    if property_type.startswith("App::PropertyBool"):
        return {
            "name": name,
            "property_type": property_type,
            "kind": "boolean",
            "value": bool(value),
        }
    if property_type.startswith("App::PropertyInteger"):
        if isinstance(value, bool):
            return None
        return {
            "name": name,
            "property_type": property_type,
            "kind": "integer",
            "value": int(value),
        }
    if property_type.startswith("App::PropertyFloat"):
        number = float(value)
        if not math.isfinite(number):
            raise AssemblyHierarchyError(f"{context} is not finite.")
        return {
            "name": name,
            "property_type": property_type,
            "kind": "float",
            "value": number,
        }
    # PropertyQuantity and its dimensioned subclasses expose numeric Value and
    # Unit in Python.  Placement/vector/matrix types were excluded above.
    numeric = getattr(value, "Value", None)
    unit = getattr(value, "Unit", None)
    if (
        property_type.startswith("App::Property")
        and isinstance(numeric, (int, float))
        and not isinstance(numeric, bool)
        and unit is not None
    ):
        number = float(numeric)
        if not math.isfinite(number):
            raise AssemblyHierarchyError(f"{context} is not finite.")
        return {
            "name": name,
            "property_type": property_type,
            "kind": "quantity",
            "value": number,
            "assignment": _bounded_text(value, context=context),
        }
    return None


def _bom_properties(obj: Any) -> list[dict[str, Any]]:
    properties = []
    for name in sorted(str(item) for item in (getattr(obj, "PropertiesList", []) or [])):
        if (
            name in _IGNORED_PROPERTY_NAMES
            or name.startswith("VibeCAD")
            or name.startswith("_")
        ):
            continue
        try:
            property_type = str(obj.getTypeIdOfProperty(name) or "")
        except Exception:
            continue
        item = _bom_property(obj, name, property_type)
        if item is None:
            continue
        properties.append(item)
        if len(properties) > MAX_BOM_PROPERTIES_PER_NODE:
            raise AssemblyHierarchyError(
                f"Assembly source {getattr(obj, 'Name', '')!r} exposes more than "
                f"{MAX_BOM_PROPERTIES_PER_NODE} BOM-compatible scalar properties. "
                "Remove unrelated dynamic properties or use a smaller source container."
            )
    return properties


def _document_basename(obj: Any) -> str:
    document = getattr(obj, "Document", None)
    file_name = str(getattr(document, "FileName", "") or "")
    return Path(file_name).name if file_name else ""


def capture_bom_identity(obj: Any) -> dict[str, Any]:
    """Capture the bounded native BOM identity of one referenced object."""

    return {
        "document_file_name": _document_basename(obj),
        "bom_properties": _bom_properties(obj),
    }


def _container_children(container: Any) -> list[Any]:
    group = [
        child
        for child in list(getattr(container, "Group", []) or [])
        if child is not None
        and str(getattr(child, "TypeId", "") or "") not in _IGNORED_CONTAINER_TYPES
    ]
    if not _is_derived(container, "Assembly::AssemblyObject"):
        return group

    # Match AssemblyLink::synchronizeComponents: boolean/intermediate Part
    # features are not independent assembly occurrences.
    dependency_children: set[Any] = set()
    for child in group:
        if not _is_derived(child, "Part::Feature"):
            continue
        for property_name in ("Base", "Tool"):
            linked = getattr(child, property_name, None)
            if linked is not None:
                dependency_children.add(linked)
        for linked in list(getattr(child, "Shapes", []) or []):
            if linked is not None:
                dependency_children.add(linked)
    return [child for child in group if child not in dependency_children]


def _component_eligible(obj: Any) -> bool:
    return any(
        _is_derived(obj, type_id)
        for type_id in ("App::Link", "App::Part", "Part::Feature")
    )


def _linked_target(obj: Any) -> tuple[Any, str, bool]:
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id == "Assembly::AssemblyLink":
        target = getattr(obj, "LinkedObject", None)
        visited: set[int] = set()
        while target is not None and str(getattr(target, "TypeId", "") or "") == (
            "Assembly::AssemblyLink"
        ):
            if id(target) in visited:
                raise AssemblyHierarchyError(
                    f"Assembly link {getattr(obj, 'Name', '')!r} has a cyclic LinkedObject chain."
                )
            visited.add(id(target))
            target = getattr(target, "LinkedObject", None)
        if target is None or not _is_derived(target, "Assembly::AssemblyObject"):
            raise AssemblyHierarchyError(
                f"Assembly link {getattr(obj, 'Name', '')!r} does not resolve to a native "
                "Assembly::AssemblyObject. Relink it before using it as a VibeScript source."
            )
        return target, "assembly_link", bool(getattr(obj, "Rigid", True))
    if _is_derived(obj, "App::Link"):
        if bool(getattr(obj, "isLinkGroup", lambda: False)()):
            raise AssemblyHierarchyError(
                f"Assembly occurrence {getattr(obj, 'Name', '')!r} is an App::LinkGroup. "
                "Expand it into explicit stable Assembly occurrences before using flexible "
                "VibeScript hierarchy or a detailed BOM."
            )
        target = getattr(obj, "LinkedObject", None)
        visited: set[int] = set()
        while target is not None and _is_derived(target, "App::Link") and str(
            getattr(target, "TypeId", "") or ""
        ) != "Assembly::AssemblyLink":
            if id(target) in visited:
                raise AssemblyHierarchyError(
                    f"Link {getattr(obj, 'Name', '')!r} has a cyclic LinkedObject chain."
                )
            visited.add(id(target))
            target = getattr(target, "LinkedObject", None)
        if target is None:
            raise AssemblyHierarchyError(
                f"Assembly occurrence {getattr(obj, 'Name', '')!r} has no LinkedObject."
            )
        return target, "link", True
    return obj, "direct", True


def _node_kind(obj: Any) -> str:
    if _is_derived(obj, "Assembly::AssemblyObject"):
        return "assembly"
    if _is_derived(obj, "App::Part") and not _is_derived(obj, "Part::Feature"):
        return "part"
    return "shape"


def _shape_copy(obj: Any, *, node_name: str) -> Any | None:
    import FreeCAD as App

    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    try:
        if bool(shape.isNull()):
            return None
        detached = shape.copy()
        # Artifact geometry is local to its source definition.  Occurrence
        # placement is represented exactly once by the occurrence record.
        detached.Placement = App.Placement()
        return detached
    except Exception as exc:
        raise AssemblyHierarchyError(
            f"Could not detach Shape for Assembly hierarchy node {node_name!r}: {exc}"
        ) from exc


def _joint_quantity(obj: Any, name: str) -> float:
    raw = getattr(obj, name)
    value = float(getattr(raw, "Value", raw))
    if not math.isfinite(value):
        raise AssemblyHierarchyError(
            f"Joint {getattr(obj, 'Name', '')!r} property {name!r} is not finite."
        )
    return value


def _joint_record(joint: Any) -> dict[str, Any]:
    native_type = str(getattr(joint, "JointType", "") or "")
    if native_type not in _SUPPORTED_NATIVE_JOINTS:
        raise AssemblyHierarchyError(
            f"Joint {getattr(joint, 'Name', '')!r} has unsupported native type "
            f"{native_type!r}."
        )
    record: dict[str, Any] = {
        "name": str(getattr(joint, "Name", "") or ""),
        "label": str(getattr(joint, "Label", "") or ""),
        "native_type": native_type,
        "suppressed": bool(getattr(joint, "Suppressed", False)),
        "distance": _joint_quantity(joint, "Distance"),
        "distance2": _joint_quantity(joint, "Distance2"),
        "angle": _joint_quantity(joint, "Angle"),
        "offset1_matrix": _matrix(joint.Offset1, context=f"joint {joint.Name} Offset1"),
        "offset2_matrix": _matrix(joint.Offset2, context=f"joint {joint.Name} Offset2"),
        "placement1_matrix": _matrix(
            joint.Placement1, context=f"joint {joint.Name} Placement1"
        ),
        "placement2_matrix": _matrix(
            joint.Placement2, context=f"joint {joint.Name} Placement2"
        ),
        "length_limits": [
            _joint_quantity(joint, "LengthMin")
            if bool(getattr(joint, "EnableLengthMin", False))
            else None,
            _joint_quantity(joint, "LengthMax")
            if bool(getattr(joint, "EnableLengthMax", False))
            else None,
        ],
        "angle_limits": [
            _joint_quantity(joint, "AngleMin")
            if bool(getattr(joint, "EnableAngleMin", False))
            else None,
            _joint_quantity(joint, "AngleMax")
            if bool(getattr(joint, "EnableAngleMax", False))
            else None,
        ],
    }
    return record


def _joint_objects(assembly: Any) -> list[Any]:
    result = []
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "") or "") != "Assembly::JointGroup":
            continue
        for joint in list(getattr(child, "Group", []) or []):
            if hasattr(joint, "JointType") and hasattr(joint, "Reference1"):
                result.append(joint)
    return result


def _grounded_objects(assembly: Any) -> list[Any]:
    result = []
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "") or "") != "Assembly::JointGroup":
            continue
        for joint in list(getattr(child, "Group", []) or []):
            target = getattr(joint, "ObjectToGround", None)
            if target is not None:
                result.append(target)
    return result


def _reference_value(joint: Any, name: str) -> tuple[Any, list[str]]:
    try:
        raw = getattr(joint, name)
        target = raw[0]
        subelements = [str(item or "") for item in list(raw[1])]
    except Exception as exc:
        raise AssemblyHierarchyError(
            f"Joint {getattr(joint, 'Name', '')!r} has an unreadable {name}: {exc}"
        ) from exc
    if target is None or len(subelements) != 2:
        raise AssemblyHierarchyError(
            f"Joint {getattr(joint, 'Name', '')!r} {name} must contain one object and "
            "exactly two native subelement strings."
        )
    return target, subelements


def capture_assembly_hierarchy(
    root: Any,
    *,
    detach_shapes: bool,
    leaf_contract: Callable[[Any], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Capture one native AssemblyObject or App::Part as a stable-path graph.

    A Part root carries no joint state, but uses the same authenticated node and
    occurrence contract needed by detailed native BOMs and connector paths.
    """

    root_kind = _node_kind(root)
    if root_kind not in {"assembly", "part"}:
        raise AssemblyHierarchyError(
            f"Object {getattr(root, 'Name', '')!r} is not an "
            "Assembly::AssemblyObject or native App::Part container."
        )

    nodes: list[dict[str, Any]] = []
    node_by_identity: dict[tuple[str, str], str] = {}
    node_objects: dict[str, Any] = {}
    occurrence_objects: dict[str, Any] = {}
    occurrence_by_id: dict[str, dict[str, Any]] = {}
    detached_shapes: dict[str, Any] = {}
    active: list[tuple[str, str]] = []
    occurrence_count = 0
    shape_count = 0

    def ensure_node(obj: Any, depth: int) -> str:
        nonlocal occurrence_count, shape_count
        identity = _identity(obj)
        if not all(identity):
            raise AssemblyHierarchyError(
                f"Assembly source object {getattr(obj, 'Name', '')!r} has no stable "
                "document identity."
            )
        existing = node_by_identity.get(identity)
        if existing is not None:
            if identity in active:
                cycle = " -> ".join(name for _uid, name in [*active, identity])
                raise AssemblyHierarchyError(
                    f"Assembly source hierarchy contains a cycle: {cycle}. Break the "
                    "cyclic component link before using it in VibeScript."
                )
            return existing
        if depth > MAX_HIERARCHY_DEPTH:
            raise AssemblyHierarchyError(
                f"Assembly hierarchy exceeds depth {MAX_HIERARCHY_DEPTH} at "
                f"{identity[1]!r}. Split the source into shallower modules."
            )
        if len(nodes) >= MAX_HIERARCHY_NODES:
            raise AssemblyHierarchyError(
                f"Assembly hierarchy exceeds {MAX_HIERARCHY_NODES} unique source nodes. "
                "Split the source into reusable module programs."
            )
        node_id = f"n{len(nodes):04d}"
        kind = _node_kind(obj)
        node: dict[str, Any] = {
            "node_id": node_id,
            "kind": kind,
            "identity": {
                "document_uid": identity[0],
                "object_name": identity[1],
            },
            "document_name": str(getattr(getattr(obj, "Document", None), "Name", "") or ""),
            "document_file_name": _document_basename(obj),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": str(getattr(obj, "TypeId", "") or ""),
            "bom_properties": _bom_properties(obj),
            "occurrences": [],
        }
        if leaf_contract is not None:
            contract = dict(leaf_contract(obj) or {})
            node["reference_contract"] = contract
        nodes.append(node)
        node_by_identity[identity] = node_id
        node_objects[node_id] = obj
        active.append(identity)
        try:
            if detach_shapes:
                detached = _shape_copy(obj, node_name=identity[1])
                if detached is not None:
                    shape_count += 1
                    if shape_count > MAX_HIERARCHY_SHAPES:
                        raise AssemblyHierarchyError(
                            f"Assembly hierarchy exceeds {MAX_HIERARCHY_SHAPES} unique "
                            "shape artifacts. Reuse linked source objects or split the module."
                        )
                    detached_shapes[node_id] = detached
                    node["has_shape_artifact"] = True
            if kind in {"assembly", "part"}:
                seen_names: set[str] = set()
                for child in _container_children(obj):
                    if not _component_eligible(child):
                        continue
                    occurrence_index = occurrence_count
                    occurrence_count += 1
                    if occurrence_count > MAX_HIERARCHY_OCCURRENCES:
                        raise AssemblyHierarchyError(
                            f"Assembly hierarchy exceeds {MAX_HIERARCHY_OCCURRENCES} "
                            "occurrences. Split the source into reusable module programs."
                        )
                    name = str(getattr(child, "Name", "") or "")
                    if not name or "/" in name or name in seen_names:
                        raise AssemblyHierarchyError(
                            f"Container {identity[1]!r} has an empty, repeated, or path-unsafe "
                            f"occurrence name {name!r}."
                        )
                    seen_names.add(name)
                    target, link_mode, rigid = _linked_target(child)
                    source_node_id = ensure_node(target, depth + 1)
                    # Reserve the identity before descending. Nested containers
                    # allocate their own occurrences recursively; deriving this
                    # ID from the post-recursion count aliases the parent with
                    # the deepest child and corrupts stable path resolution.
                    occurrence_id = f"o{occurrence_index:05d}"
                    occurrence = {
                        "occurrence_id": occurrence_id,
                        "name": name,
                        "label": str(getattr(child, "Label", "") or ""),
                        "type_id": str(getattr(child, "TypeId", "") or ""),
                        "link_mode": link_mode,
                        "rigid": rigid,
                        "placement_matrix": _placement_matrix(
                            child, context=f"Assembly occurrence {name!r}"
                        ),
                        "scale": float(getattr(child, "Scale", 1.0) or 1.0),
                        "source_node_id": source_node_id,
                    }
                    if occurrence_id in occurrence_by_id:
                        raise AssemblyHierarchyError(
                            f"Assembly hierarchy allocated duplicate occurrence identity "
                            f"{occurrence_id!r}. Regenerate the source hierarchy before "
                            "submitting the model turn."
                        )
                    if not math.isfinite(occurrence["scale"]) or abs(
                        occurrence["scale"]
                    ) <= 1.0e-12:
                        raise AssemblyHierarchyError(
                            f"Assembly occurrence {name!r} has invalid scale "
                            f"{occurrence['scale']!r}."
                        )
                    node["occurrences"].append(occurrence)
                    occurrence_objects[occurrence_id] = child
                    occurrence_by_id[occurrence_id] = occurrence
        finally:
            active.pop()
        return node_id

    root_node_id = ensure_node(root, 0)

    node_by_id = {str(node["node_id"]): node for node in nodes}

    def local_object_paths(
        node_id: str,
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        """Map native and recursively synchronized children to stable paths.

        AssemblyLink-generated object names are deliberately excluded from the
        persisted contract.  Matching by ``LinkedObject`` lets the same source
        occurrence path survive every synchronization and every rigid/flexible
        transition, including nested subassemblies.  FreeCAD may return a new
        Python wrapper for the same native object after synchronization, so all
        transient lookups use the object's document UID and name rather than
        Python wrapper identity.
        """

        result: dict[tuple[str, str], tuple[str, ...]] = {}

        def map_link_children(
            link: Any,
            source_node_id: str,
            prefix: tuple[str, ...],
            depth: int,
        ) -> None:
            if depth > MAX_HIERARCHY_DEPTH:
                raise AssemblyHierarchyError(
                    f"AssemblyLink synchronization beneath {'/'.join(prefix)!r} "
                    f"exceeds depth {MAX_HIERARCHY_DEPTH}."
                )
            source_node = node_by_id[source_node_id]
            source_occurrences = {
                _identity(occurrence_objects[str(item["occurrence_id"])]): item
                for item in list(source_node["occurrences"])
            }
            mapped_occurrences: set[str] = set()
            for mirrored in list(getattr(link, "Group", []) or []):
                linked = getattr(mirrored, "LinkedObject", None)
                source = (
                    source_occurrences.get(_identity(linked))
                    if linked is not None
                    else None
                )
                if source is None:
                    continue
                occurrence_id = str(source["occurrence_id"])
                if occurrence_id in mapped_occurrences:
                    raise AssemblyHierarchyError(
                        f"AssemblyLink {getattr(link, 'Name', '')!r} mirrors source "
                        f"occurrence {source['name']!r} more than once. Recompute the "
                        "native subassembly before using it in VibeScript."
                    )
                mapped_occurrences.add(occurrence_id)
                child_path = (*prefix, str(source["name"]))
                result[_identity(mirrored)] = child_path
                child_node = node_by_id[str(source["source_node_id"])]
                if (
                    str(child_node["kind"]) == "assembly"
                    and str(getattr(mirrored, "TypeId", "") or "")
                    == "Assembly::AssemblyLink"
                ):
                    map_link_children(
                        mirrored,
                        str(child_node["node_id"]),
                        child_path,
                        depth + 1,
                    )

        node = node_by_id[node_id]
        for occurrence in list(node["occurrences"]):
            path = (str(occurrence["name"]),)
            obj = occurrence_objects[str(occurrence["occurrence_id"])]
            result[_identity(obj)] = path
            source_node = node_by_id[str(occurrence["source_node_id"])]
            if (
                str(occurrence["link_mode"]) == "assembly_link"
                and str(source_node["kind"]) == "assembly"
            ):
                map_link_children(obj, str(source_node["node_id"]), path, 1)
        return result

    total_joints = 0
    for node in nodes:
        if str(node["kind"]) != "assembly":
            continue
        node_id = str(node["node_id"])
        assembly = node_objects[node_id]
        path_by_object = local_object_paths(node_id)
        occurrence_by_name = {
            str(item["name"]): item for item in list(node["occurrences"])
        }

        def normalize_target(target: Any, subelements: list[str]) -> tuple[list[str], list[str]]:
            path = path_by_object.get(_identity(target))
            if path is None and target is assembly:
                path = ()
            if path is None:
                target_identity = _identity(target)
                available = [
                    {
                        "document_uid": identity[0],
                        "object_name": identity[1],
                        "path": "/".join(stable_path),
                    }
                    for identity, stable_path in sorted(path_by_object.items())[:32]
                ]
                raise AssemblyHierarchyError(
                    f"Assembly {node['identity']['object_name']!r} joint reference targets "
                    f"unmapped object {getattr(target, 'Name', '')!r} with identity "
                    f"{target_identity!r}; available stable occurrences are {available!r}. "
                    "Recreate the joint against one of those top-level or synchronized "
                    "subassembly occurrences."
                )
            remaining = list(subelements)
            current = target
            # Rigid subassemblies encode internal occurrences as native-name
            # prefixes. Translate those prefixes back to stable source names.
            while remaining and remaining[0]:
                token = remaining[0].split(".", 1)[0]
                child = next(
                    (
                        item
                        for item in list(getattr(current, "Group", []) or [])
                        if str(getattr(item, "Name", "") or "") == token
                    ),
                    None,
                )
                child_path = (
                    path_by_object.get(_identity(child))
                    if child is not None
                    else None
                )
                if child_path is None:
                    break
                path = child_path
                prefix = f"{token}."
                remaining = [
                    value[len(prefix) :] if value.startswith(prefix) else value
                    for value in remaining
                ]
                current = child
            if not path:
                if remaining and remaining[0]:
                    first = remaining[0].split(".", 1)[0]
                    occurrence = occurrence_by_name.get(first)
                    if occurrence is not None:
                        path = (first,)
                        prefix = f"{first}."
                        remaining = [
                            value[len(prefix) :] if value.startswith(prefix) else value
                            for value in remaining
                        ]
            if not path:
                raise AssemblyHierarchyError(
                    f"Assembly {node['identity']['object_name']!r} joint reference has no "
                    "stable occurrence path."
                )
            return list(path), remaining

        grounded = []
        for target in _grounded_objects(assembly):
            path = path_by_object.get(_identity(target))
            if path is None:
                raise AssemblyHierarchyError(
                    f"Assembly {node['identity']['object_name']!r} grounds unmapped object "
                    f"{getattr(target, 'Name', '')!r}."
                )
            grounded.append("/".join(path))
        node["grounded_occurrence_paths"] = grounded
        joints = []
        for joint in _joint_objects(assembly):
            total_joints += 1
            if total_joints > MAX_HIERARCHY_JOINTS:
                raise AssemblyHierarchyError(
                    f"Assembly hierarchy exceeds {MAX_HIERARCHY_JOINTS} native joints. "
                    "Split the source into reusable modules."
                )
            record = _joint_record(joint)
            for index in (1, 2):
                target, subelements = _reference_value(joint, f"Reference{index}")
                path, normalized = normalize_target(target, subelements)
                record[f"reference{index}"] = {
                    "occurrence_path": "/".join(path),
                    "subelements": normalized,
                }
            joints.append(record)
        node["joints"] = joints

    # Stable copy-ready paths and source metadata are included explicitly so a
    # provider does not have to recursively interpret the node graph.
    flattened: list[dict[str, Any]] = []
    visiting: set[tuple[str, str]] = set()

    def flatten(node_id: str, prefix: tuple[str, ...], depth: int) -> None:
        node = node_by_id[node_id]
        for occurrence in list(node["occurrences"]):
            path = (*prefix, str(occurrence["name"]))
            key = (node_id, "/".join(path))
            if key in visiting:
                raise AssemblyHierarchyError(
                    f"Assembly occurrence traversal is cyclic at {'/'.join(path)!r}."
                )
            visiting.add(key)
            source = node_by_id[str(occurrence["source_node_id"])]
            flattened.append(
                {
                    "path": "/".join(path),
                    "label": str(occurrence["label"]),
                    "type_id": str(occurrence["type_id"]),
                    "link_mode": str(occurrence["link_mode"]),
                    "rigid": bool(occurrence["rigid"]),
                    "source_node_id": str(source["node_id"]),
                    "source_kind": str(source["kind"]),
                    "source_label": str(source["label"]),
                    "source_type_id": str(source["type_id"]),
                    "bom_properties": list(source["bom_properties"]),
                    "depth": depth + 1,
                }
            )
            if len(flattened) > MAX_HIERARCHY_OCCURRENCES:
                raise AssemblyHierarchyError(
                    "Assembly source reuse expands to more than "
                    f"{MAX_HIERARCHY_OCCURRENCES} stable occurrence paths. Split the "
                    "design into smaller modules before using detailed BOMs or internal "
                    "connector paths."
                )
            if str(source["kind"]) in {"assembly", "part"}:
                flatten(str(source["node_id"]), path, depth + 1)
            visiting.remove(key)

    flatten(root_node_id, (), 0)
    maximum_depth = max((int(item["depth"]) for item in flattened), default=0)
    result = {
        "schema": ASSEMBLY_HIERARCHY_SCHEMA,
        "root_node_id": root_node_id,
        "nodes": nodes,
        "occurrence_paths": flattened,
        "counts": {
            "nodes": len(nodes),
            "occurrences": occurrence_count,
            "joints": total_joints,
            "shape_artifacts": shape_count,
            "maximum_depth": maximum_depth,
        },
        "limits": {
            "maximum_depth": MAX_HIERARCHY_DEPTH,
            "nodes": MAX_HIERARCHY_NODES,
            "occurrences": MAX_HIERARCHY_OCCURRENCES,
            "joints": MAX_HIERARCHY_JOINTS,
            "shape_artifacts": MAX_HIERARCHY_SHAPES,
        },
    }
    if detach_shapes:
        result["_detached_shapes"] = detached_shapes
    return result


def hierarchy_context(hierarchy: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded provider view with exact copy-ready occurrence paths."""

    paths = [dict(item) for item in list(hierarchy.get("occurrence_paths") or [])]
    nodes = [dict(item) for item in list(hierarchy.get("nodes") or [])]
    joints = []
    grounded = []
    for node in nodes:
        assembly_prefixes = [
            str(item["path"])
            for item in paths
            if str(item.get("source_node_id") or "") == str(node.get("node_id") or "")
        ]
        prefix = assembly_prefixes[0] if assembly_prefixes else ""
        for path in list(node.get("grounded_occurrence_paths") or []):
            grounded.append("/".join(item for item in (prefix, str(path)) if item))
        for item in list(node.get("joints") or []):
            record = {
                "assembly_path": prefix,
                "name": str(item.get("name") or ""),
                "label": str(item.get("label") or ""),
                "native_type": str(item.get("native_type") or ""),
                "suppressed": bool(item.get("suppressed")),
                "first_occurrence_path": "/".join(
                    value
                    for value in (
                        prefix,
                        str(dict(item.get("reference1") or {}).get("occurrence_path") or ""),
                    )
                    if value
                ),
                "second_occurrence_path": "/".join(
                    value
                    for value in (
                        prefix,
                        str(dict(item.get("reference2") or {}).get("occurrence_path") or ""),
                    )
                    if value
                ),
            }
            joints.append(record)
    return {
        "schema": ASSEMBLY_HIERARCHY_SCHEMA,
        "available": True,
        "counts": dict(hierarchy.get("counts") or {}),
        "limits": dict(hierarchy.get("limits") or {}),
        "occurrence_paths": paths[:MAX_CONTEXT_OCCURRENCES],
        "occurrence_paths_truncated": len(paths) > MAX_CONTEXT_OCCURRENCES,
        "occurrence_paths_omitted": max(0, len(paths) - MAX_CONTEXT_OCCURRENCES),
        "joints": joints[:MAX_CONTEXT_JOINTS],
        "joints_truncated": len(joints) > MAX_CONTEXT_JOINTS,
        "joints_omitted": max(0, len(joints) - MAX_CONTEXT_JOINTS),
        "grounded_occurrence_paths": grounded[:MAX_CONTEXT_OCCURRENCES],
        "path_contract": (
            "Copy occurrence_paths[].path exactly into api.connector(..., "
            "occurrence_path='...') or BOM row overrides. Paths use stable source "
            "occurrence names, never generated AssemblyLink child names."
        ),
    }
