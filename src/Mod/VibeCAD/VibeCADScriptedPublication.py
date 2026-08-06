# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stable publication boundary for generated CAD models.

Scripted engines are free to replace their private implementation history, but
objects consumed by Assembly, TechDraw, FEM, CAM, materials, or user-authored
links must retain their FreeCAD identity.  This module owns that boundary.
"""

from __future__ import annotations

from typing import Any


PROP_ROLE = "VibeCADScriptedRole"
PROP_ENGINE = "VibeCADScriptedEngine"
PROP_MODEL_ID = "VibeCADScriptedModelId"
PROP_OUTPUT_KEY = "VibeCADScriptedOutputKey"
PROP_REVISION = "VibeCADPublishedRevision"
PROP_IMPLEMENTATION = "VibeCADImplementationObject"
PROP_INTERFACES = "VibeCADPublishedInterfaces"
PROP_PARAMETER_NAMES = "VibeCADParameterNames"

ROLE_MODEL = "model"
ROLE_PUBLICATION = "publication"
ROLE_PUBLICATION_TARGET = "publication_target"
ROLE_IMPLEMENTATION = "implementation"
ROLE_PARAMETERS = "parameters"

_LINK_PROPERTY_TYPES = {
    "App::PropertyLink",
    "App::PropertyXLink",
    "App::PropertyLinkHidden",
    "App::PropertyLinkChild",
    "App::PropertyLinkGlobal",
}
_LINK_LIST_PROPERTY_TYPES = {
    "App::PropertyLinkList",
    "App::PropertyXLinkList",
    "App::PropertyLinkListChild",
    "App::PropertyLinkListGlobal",
    "App::PropertyLinkListHidden",
}
_LINK_SUB_PROPERTY_TYPES = {
    "App::PropertyLinkSub",
    "App::PropertyXLinkSub",
    "App::PropertyLinkSubChild",
    "App::PropertyLinkSubGlobal",
    "App::PropertyLinkSubHidden",
    "App::PropertyXLinkSubHidden",
}
_LINK_SUB_LIST_PROPERTY_TYPES = {
    "App::PropertyLinkSubList",
    "App::PropertyXLinkSubList",
    "App::PropertyLinkSubListChild",
    "App::PropertyLinkSubListGlobal",
    "App::PropertyLinkSubListHidden",
}


class PublicationError(RuntimeError):
    """A publication mutation could not be completed without reference loss."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


def ensure_string_property(obj: Any, name: str) -> None:
    if name not in list(getattr(obj, "PropertiesList", []) or []):
        obj.addProperty("App::PropertyString", name, "VibeCAD Publication")


def tag_object(
    obj: Any,
    *,
    role: str,
    engine: str,
    model_id: str,
    output_key: str = "",
    revision: str = "",
) -> None:
    values = {
        PROP_ROLE: role,
        PROP_ENGINE: engine,
        PROP_MODEL_ID: model_id,
        PROP_OUTPUT_KEY: output_key,
        PROP_REVISION: revision,
    }
    for name, value in values.items():
        ensure_string_property(obj, name)
        setattr(obj, name, str(value or ""))


def role_of(obj: Any) -> str:
    return str(getattr(obj, PROP_ROLE, "") or "")


def is_publication(obj: Any) -> bool:
    return role_of(obj) == ROLE_PUBLICATION


def model_root_for(obj: Any) -> Any:
    """Resolve the one owning scripted-model root from exact persisted identity."""

    if role_of(obj) == ROLE_MODEL:
        return obj
    model_id = str(getattr(obj, PROP_MODEL_ID, "") or "")
    linked = getattr(obj, "LinkedObject", None)
    linked_root = linked[0] if isinstance(linked, (tuple, list)) and linked else None
    if (
        linked_root is not None
        and role_of(linked_root) == ROLE_MODEL
        and str(getattr(linked_root, PROP_MODEL_ID, "") or "") == model_id
    ):
        return linked_root
    matches = [
        owner
        for owner in list(getattr(obj, "InList", []) or [])
        if role_of(owner) == ROLE_MODEL
        and str(getattr(owner, PROP_MODEL_ID, "") or "") == model_id
    ]
    if not matches:
        # Reusable component occurrences are independent top-level History
        # operations by design. They cannot carry a native link back to their
        # earlier program root without turning bookkeeping into a forward
        # modeling dependency. Both objects already persist the same globally
        # unique model ID, so resolve that exact identity in the owning
        # document rather than inventing a containment relationship.
        doc = getattr(obj, "Document", None)
        matches = [
            candidate
            for candidate in list(getattr(doc, "Objects", []) or [])
            if role_of(candidate) == ROLE_MODEL
            and str(getattr(candidate, PROP_MODEL_ID, "") or "") == model_id
        ]
    if len(matches) != 1:
        raise PublicationError(
            "A scripted publication must belong to exactly one model root.",
            details={
                "object": str(getattr(obj, "Name", "") or ""),
                "model_id": model_id,
                "roots": [str(getattr(item, "Name", "") or "") for item in matches],
            },
        )
    return matches[0]


def model_publications(root: Any) -> dict[str, Any]:
    """Return top-level links that publish an exact subobject of this model root."""

    result: dict[str, Any] = {}
    doc = getattr(root, "Document", None)
    if doc is None:
        raise PublicationError(
            "A scripted model root has no owning document.",
            details={"model_root": str(getattr(root, "Name", "") or "")},
        )
    model_id = str(getattr(root, PROP_MODEL_ID, "") or "")
    for child in list(doc.findObjects(Type="App::Link") or []):
        linked = getattr(child, "LinkedObject", None)
        linked_root = (
            linked[0] if isinstance(linked, (tuple, list)) and linked else None
        )
        if linked_root is not root or not is_publication(child):
            continue
        if str(getattr(child, PROP_MODEL_ID, "") or "") != model_id:
            raise PublicationError(
                "A scripted publication is registered to the wrong model.",
                details={
                    "model_root": root.Name,
                    "published_object": child.Name,
                    "model_id": model_id,
                    "published_model_id": str(
                        getattr(child, PROP_MODEL_ID, "") or ""
                    ),
                },
            )
        key = str(getattr(child, PROP_OUTPUT_KEY, "") or "")
        if not key:
            raise PublicationError(
                "A scripted publication has no output key.",
                details={"model_root": root.Name, "published_object": child.Name},
            )
        if key in result:
            raise PublicationError(
                f"Multiple published objects claim output key {key!r}.",
                details={
                    "output_key": key,
                    "objects": [result[key].Name, child.Name],
                },
            )
        result[key] = child
    return result


def model_implementations(root: Any) -> list[Any]:
    return [
        child
        for child in list(getattr(root, "Group", []) or [])
        if role_of(child) == ROLE_IMPLEMENTATION
    ]


def model_publication_targets(root: Any) -> list[Any]:
    return [
        child
        for child in list(getattr(root, "Group", []) or [])
        if role_of(child) == ROLE_PUBLICATION_TARGET
    ]


def model_parameter_object(root: Any) -> Any | None:
    matches = [
        child
        for child in list(getattr(root, "Group", []) or [])
        if role_of(child) == ROLE_PARAMETERS
    ]
    if len(matches) > 1:
        raise PublicationError(
            "A scripted model contains multiple parameter objects.",
            details={"objects": [item.Name for item in matches]},
        )
    return matches[0] if matches else None


def global_placement(obj: Any) -> Any | None:
    getter = getattr(obj, "getGlobalPlacement", None)
    if callable(getter):
        try:
            return getter()
        except Exception as exc:
            raise PublicationError(
                f"Could not read the global placement of "
                f"{getattr(obj, 'Name', '<object>')}.",
                details={"native_error": str(exc)},
            ) from exc
    return getattr(obj, "Placement", None)


def placement_relative_to(root: Any, obj: Any) -> Any | None:
    object_global = global_placement(obj)
    root_global = global_placement(root)
    if object_global is None:
        return None
    if root_global is None:
        return object_global
    try:
        return root_global.inverse().multiply(object_global)
    except Exception as exc:
        raise PublicationError(
            f"Could not transform {getattr(obj, 'Name', '<object>')} into the "
            "published model coordinate system.",
            details={"native_error": str(exc)},
        ) from exc


def add_to_root_preserving_global(root: Any, obj: Any) -> None:
    relative = placement_relative_to(root, obj)
    root.addObject(obj)
    if relative is not None and hasattr(obj, "Placement"):
        obj.Placement = relative


def create_publication(
    doc: Any,
    root: Any,
    source: Any,
    *,
    internal_name: str,
    label: str,
    engine: str,
    model_id: str,
    output_key: str,
    revision: str,
) -> Any:
    target = doc.addObject("Part::Feature", f"{internal_name}_Source")
    tag_object(
        target,
        role=ROLE_PUBLICATION_TARGET,
        engine=engine,
        model_id=model_id,
        output_key=output_key,
        revision=revision,
    )
    root.addObject(target)
    target_view = getattr(target, "ViewObject", None)
    if target_view is not None and hasattr(target_view, "Visibility"):
        target_view.Visibility = False

    published = doc.addObject("App::Link", internal_name)
    published.Label = str(label or output_key)
    tag_object(
        published,
        role=ROLE_PUBLICATION,
        engine=engine,
        model_id=model_id,
        output_key=output_key,
        revision=revision,
    )
    published.LinkedObject = (root, f"{target.Name}.")
    published.LinkTransform = True
    ensure_string_property(published, PROP_IMPLEMENTATION)
    setattr(published, PROP_IMPLEMENTATION, target.Name)
    update_publication(published, root, source, revision=revision)
    _copy_presentation(source, published)
    return published


def update_publication(
    published: Any,
    root: Any,
    source: Any,
    *,
    revision: str,
) -> None:
    shape = getattr(source, "Shape", None)
    if shape is None:
        raise PublicationError(
            f"Output {getattr(source, 'Name', '<object>')} has no Shape to publish."
        )
    target = publication_target(published, root)
    relative = placement_relative_to(root, source)
    # Candidate BREP is already imported and validated off-thread.  Assign the
    # detached TopoShape directly so publication does not make a second deep
    # geometry copy on FreeCAD's document/UI thread.
    target.Shape = shape
    if relative is not None and hasattr(target, "Placement"):
        target.Placement = relative
    tag_object(
        target,
        role=ROLE_PUBLICATION_TARGET,
        engine=str(getattr(published, PROP_ENGINE, "") or ""),
        model_id=str(getattr(published, PROP_MODEL_ID, "") or ""),
        output_key=str(getattr(published, PROP_OUTPUT_KEY, "") or ""),
        revision=revision,
    )
    published.LinkedObject = (root, f"{target.Name}.")
    published.LinkTransform = True
    ensure_string_property(published, PROP_REVISION)
    setattr(published, PROP_REVISION, str(revision or ""))
    ensure_string_property(published, PROP_IMPLEMENTATION)
    setattr(
        published,
        PROP_IMPLEMENTATION,
        target.Name,
    )
    _copy_presentation(source, published)


def publication_target(published: Any, root: Any | None = None) -> Any:
    owner = root if root is not None else model_root_for(published)
    target_name = str(getattr(published, PROP_IMPLEMENTATION, "") or "")
    doc = getattr(owner, "Document", None)
    target = doc.getObject(target_name) if doc is not None and target_name else None
    if target is None or role_of(target) != ROLE_PUBLICATION_TARGET:
        raise PublicationError(
            "A scripted publication has no valid private shape target.",
            details={
                "model_root": str(getattr(owner, "Name", "") or ""),
                "published_object": str(getattr(published, "Name", "") or ""),
                "target": target_name,
            },
        )
    owned = {id(item) for item in list(getattr(owner, "OutListRecursive", []) or [])}
    if id(target) not in owned:
        raise PublicationError(
            "A scripted publication target is outside its model root.",
            details={
                "model_root": owner.Name,
                "published_object": published.Name,
                "target": target.Name,
            },
        )
    return target


def clear_implementation_pointer(published: Any) -> None:
    target = publication_target(published)
    ensure_string_property(published, PROP_IMPLEMENTATION)
    setattr(published, PROP_IMPLEMENTATION, target.Name)


def delete_publication(doc: Any, root: Any, published: Any) -> list[str]:
    target = publication_target(published, root)
    deleted: list[str] = []
    for obj in (published, target):
        name = str(getattr(obj, "Name", "") or "")
        if name and doc.getObject(name) is not None:
            doc.removeObject(name)
            deleted.append(name)
    return deleted


def group_implementation(
    root: Any,
    new_objects: list[Any],
    *,
    engine: str,
    model_id: str,
    revision: str,
) -> list[Any]:
    new_names = {str(getattr(obj, "Name", "") or "") for obj in new_objects}
    contained_names: set[str] = set()
    for obj in new_objects:
        for child in list(getattr(obj, "OutListRecursive", []) or []):
            name = str(getattr(child, "Name", "") or "")
            if name in new_names:
                contained_names.add(name)
    roots = [
        obj
        for obj in new_objects
        if str(getattr(obj, "Name", "") or "") not in contained_names
    ]
    for obj in new_objects:
        tag_object(
            obj,
            role=ROLE_IMPLEMENTATION,
            engine=engine,
            model_id=model_id,
            revision=revision,
        )
    for obj in roots:
        add_to_root_preserving_global(root, obj)
        view = getattr(obj, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
    return roots


def implementation_closure(root: Any) -> list[Any]:
    roots = model_implementations(root)
    result: dict[str, Any] = {}
    for implementation in roots:
        name = str(getattr(implementation, "Name", "") or "")
        if name:
            result[name] = implementation
        for child in list(getattr(implementation, "OutListRecursive", []) or []):
            child_name = str(getattr(child, "Name", "") or "")
            if child_name:
                result[child_name] = child
    return list(result.values())


def delete_implementation(doc: Any, root: Any) -> list[str]:
    objects = implementation_closure(root)
    names = {str(getattr(obj, "Name", "") or "") for obj in objects}

    def descendant_count(obj: Any) -> int:
        return sum(
            1
            for child in list(getattr(obj, "OutListRecursive", []) or [])
            if str(getattr(child, "Name", "") or "") in names
        )

    deleted: list[str] = []
    for obj in sorted(objects, key=lambda item: (descendant_count(item), item.Name)):
        name = str(obj.Name)
        if doc.getObject(name) is None:
            continue
        doc.removeObject(name)
        deleted.append(name)
    return deleted


def external_reference_uses(
    doc: Any,
    targets: list[Any],
    *,
    internal_objects: list[Any] | None = None,
) -> list[dict[str, Any]]:
    def is_non_consuming_index(owner: Any) -> bool:
        # App::DocumentTimeline is the document's persisted operation index. Its
        # hidden links preserve ordering; they do not consume geometry or claim
        # that a generated implementation must retain object identity. Treating
        # those bookkeeping backlinks as downstream uses would prevent every
        # scripted implementation from regenerating. Keep this exemption exact:
        # all other link owners remain protected by the normal reference guard.
        return str(getattr(owner, "TypeId", "") or "") == "App::DocumentTimeline"

    target_by_id = {id(item): item for item in targets}
    target_ids = set(target_by_id)
    internal_ids = {id(item) for item in list(internal_objects or [])}
    uses: list[dict[str, Any]] = []
    owners: dict[int, Any] = {}
    for target in targets:
        for owner in list(getattr(target, "InList", []) or []):
            owners[id(owner)] = owner
    for owner in owners.values():
        if (
            id(owner) in target_ids
            or id(owner) in internal_ids
            or is_non_consuming_index(owner)
        ):
            continue
        for property_name in list(getattr(owner, "PropertiesList", []) or []):
            property_type = _property_type(owner, property_name)
            if property_type not in (
                _LINK_PROPERTY_TYPES
                | _LINK_LIST_PROPERTY_TYPES
                | _LINK_SUB_PROPERTY_TYPES
                | _LINK_SUB_LIST_PROPERTY_TYPES
            ):
                continue
            try:
                value = getattr(owner, property_name)
            except Exception as exc:
                raise PublicationError(
                    f"Could not inspect reference property {owner.Name}.{property_name}.",
                    details={
                        "owner": str(getattr(owner, "Name", "") or ""),
                        "property": property_name,
                        "property_type": property_type,
                        "native_error": str(exc),
                    },
                ) from exc
            matches = _references_in_value(value, property_type, target_ids)
            for target_id, subelements in matches:
                uses.append(
                    {
                        "owner": owner,
                        "owner_name": str(getattr(owner, "Name", "") or ""),
                        "owner_type": str(getattr(owner, "TypeId", "") or ""),
                        "property": property_name,
                        "property_type": property_type,
                        "subelements": subelements,
                        "target_name": str(
                            getattr(target_by_id[target_id], "Name", "") or ""
                        ),
                        "_target_id": target_id,
                    }
                )
    known_pairs = {
        (id(item["owner"]), int(item["_target_id"]))
        for item in uses
        if item.get("_target_id") is not None
    }
    for target in targets:
        for owner in list(getattr(target, "InList", []) or []):
            if (
                id(owner) in target_ids
                or id(owner) in internal_ids
                or is_non_consuming_index(owner)
                or (id(owner), id(target)) in known_pairs
            ):
                continue
            uses.append(
                {
                    "owner": owner,
                    "owner_name": str(getattr(owner, "Name", "") or ""),
                    "owner_type": str(getattr(owner, "TypeId", "") or ""),
                    "property": "<native_inbound_reference>",
                    "property_type": "unknown",
                    "subelements": ["<unclassified>"],
                }
            )
    return uses


def retarget_references(
    doc: Any,
    old: Any,
    new: Any,
    *,
    internal_objects: list[Any] | None = None,
) -> list[dict[str, Any]]:
    internal_ids = {id(item) for item in list(internal_objects or [])}
    changed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    owners = {
        id(owner): owner for owner in list(getattr(old, "InList", []) or [])
    }
    for owner in owners.values():
        if owner is old or owner is new or id(owner) in internal_ids:
            continue
        for property_name in list(getattr(owner, "PropertiesList", []) or []):
            property_type = _property_type(owner, property_name)
            try:
                value = getattr(owner, property_name)
            except Exception as exc:
                failures.append(
                    {
                        "owner": str(getattr(owner, "Name", "") or ""),
                        "property": property_name,
                        "property_type": property_type,
                        "stage": "read",
                        "native_error": str(exc),
                    }
                )
                continue
            replacement, count = _replace_reference_value(
                value, property_type, old, new
            )
            if count == 0:
                continue
            try:
                setattr(owner, property_name, replacement)
                changed.append(
                    {
                        "owner": str(getattr(owner, "Name", "") or ""),
                        "property": property_name,
                        "property_type": property_type,
                        "reference_count": count,
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "owner": str(getattr(owner, "Name", "") or ""),
                        "property": property_name,
                        "property_type": property_type,
                        "native_error": str(exc),
                    }
                )
    remaining = external_reference_uses(
        doc,
        [old],
        internal_objects=[new, *(internal_objects or [])],
    )
    if failures or remaining:
        raise PublicationError(
            "Legacy output references could not all be migrated to the stable "
            "published object.",
            details={
                "old_object": str(getattr(old, "Name", "") or ""),
                "new_object": str(getattr(new, "Name", "") or ""),
                "assignment_failures": failures,
                "remaining_references": [_json_use(item) for item in remaining],
                "migrated_references": changed,
            },
        )
    return changed


def json_reference_uses(uses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_json_use(item) for item in uses]


def _property_type(owner: Any, property_name: str) -> str:
    getter = getattr(owner, "getTypeIdOfProperty", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(property_name) or "")
    except Exception as exc:
        raise PublicationError(
            f"Could not inspect the type of reference property "
            f"{getattr(owner, 'Name', '<object>')}.{property_name}.",
            details={
                "owner": str(getattr(owner, "Name", "") or ""),
                "property": property_name,
                "native_error": str(exc),
            },
        ) from exc


def _references_in_value(
    value: Any, property_type: str, target_ids: set[int]
) -> list[tuple[int, list[str]]]:
    matches: list[tuple[int, list[str]]] = []
    if property_type in _LINK_PROPERTY_TYPES:
        if id(value) in target_ids:
            matches.append((id(value), []))
    elif property_type in _LINK_LIST_PROPERTY_TYPES:
        for item in list(value or []):
            if id(item) in target_ids:
                matches.append((id(item), []))
    elif property_type in _LINK_SUB_PROPERTY_TYPES:
        if isinstance(value, (tuple, list)) and value and id(value[0]) in target_ids:
            matches.append(
                (
                    id(value[0]),
                    _subelement_names(value[1] if len(value) > 1 else []),
                )
            )
    elif property_type in _LINK_SUB_LIST_PROPERTY_TYPES:
        for item in list(value or []):
            if isinstance(item, (tuple, list)) and item and id(item[0]) in target_ids:
                matches.append(
                    (
                        id(item[0]),
                        _subelement_names(item[1] if len(item) > 1 else []),
                    )
                )
    return matches


def _replace_reference_value(
    value: Any, property_type: str, old: Any, new: Any
) -> tuple[Any, int]:
    if property_type in _LINK_PROPERTY_TYPES:
        return (new, 1) if value is old else (value, 0)
    if property_type in _LINK_LIST_PROPERTY_TYPES:
        values = list(value or [])
        count = sum(item is old for item in values)
        return ([new if item is old else item for item in values], count)
    if property_type in _LINK_SUB_PROPERTY_TYPES:
        if not isinstance(value, (tuple, list)) or not value or value[0] is not old:
            return value, 0
        suffix = list(value[1:])
        return tuple([new, *suffix]), 1
    if property_type in _LINK_SUB_LIST_PROPERTY_TYPES:
        values = list(value or [])
        count = 0
        replaced = []
        for item in values:
            if isinstance(item, (tuple, list)) and item and item[0] is old:
                replaced.append(tuple([new, *list(item[1:])]))
                count += 1
            else:
                replaced.append(item)
        return replaced, count
    return value, 0


def _subelement_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in list(value or []) if str(item)]


def _json_use(use: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in use.items()
        if key not in {"owner", "_target_id"}
    }


def _copy_presentation(source: Any, target: Any) -> None:
    if hasattr(source, "ShapeMaterial") and hasattr(target, "ShapeMaterial"):
        try:
            target.ShapeMaterial = source.ShapeMaterial
        except Exception as exc:
            raise PublicationError(
                f"Could not copy ShapeMaterial from "
                f"{getattr(source, 'Name', '<object>')} to "
                f"{getattr(target, 'Name', '<object>')}.",
                details={"native_error": str(exc)},
            ) from exc
    source_view = getattr(source, "ViewObject", None)
    target_view = getattr(target, "ViewObject", None)
    if source_view is None or target_view is None:
        return
    for name in (
        "ShapeColor",
        "LineColor",
        "PointColor",
        "Transparency",
        "DisplayMode",
        "LineWidth",
        "PointSize",
    ):
        if not hasattr(source_view, name) or not hasattr(target_view, name):
            continue
        try:
            setattr(target_view, name, getattr(source_view, name))
        except Exception as exc:
            raise PublicationError(
                f"Could not copy view property {name!r} from "
                f"{getattr(source, 'Name', '<object>')} to "
                f"{getattr(target, 'Name', '<object>')}.",
                details={"property": name, "native_error": str(exc)},
            ) from exc
    if hasattr(target_view, "Visibility"):
        target_view.Visibility = True
