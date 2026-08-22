# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact component and new-part insertion for the active Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    document_uid,
    object_identity,
    object_reference,
    resolve_object,
)


_ASSEMBLY_RESOURCE_TYPES = frozenset(
    {
        "Assembly::JointGroup",
        "Assembly::BomGroup",
        "Assembly::ViewGroup",
        "Assembly::SimulationGroup",
    }
)
_INSTANCE_TYPES = frozenset(
    {"App::Link", "App::LinkElement", "Assembly::AssemblyLink"}
)
_INTERNAL_TIMELINE_ROLES = frozenset({"internal", "resource"})
_INTERNAL_SCRIPTED_ROLES = frozenset({"implementation", "model"})


class NativeAssemblyComponentError(RuntimeError):
    """An Assembly component request or postcondition failed safely."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_COMPONENT_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class AssemblySourceRef:
    document_uid: str
    document_name: str
    object_name: str
    object_id: int


@dataclass(frozen=True, slots=True)
class InsertComponentSpec:
    assembly_ref: NativeObjectRef
    source_ref: AssemblySourceRef
    label: str
    placement: Any
    rigid: bool | None
    expected_component_count: int


@dataclass(frozen=True, slots=True)
class CreatePartSpec:
    assembly_ref: NativeObjectRef
    label: str
    placement: Any
    expected_component_count: int


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


def _is_component_source(obj: Any) -> bool:
    return (
        obj is not None
        and str(getattr(obj, "TypeId", "") or "") not in _INSTANCE_TYPES
        and str(getattr(obj, "VibeCADTimelineRole", "") or "")
        not in _INTERNAL_TIMELINE_ROLES
        and str(getattr(obj, "VibeCADScriptedRole", "") or "")
        not in _INTERNAL_SCRIPTED_ROLES
        and (_is_derived(obj, "App::Part") or _is_derived(obj, "Part::Feature"))
    )


def _is_component_instance(obj: Any) -> bool:
    """Match the native Assembly component classes, excluding joint resources."""

    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in _ASSEMBLY_RESOURCE_TYPES:
        return False
    if type_id == "App::LinkElement":
        return True
    if any(
        _is_derived(obj, expected)
        for expected in (
            "Assembly::AssemblyObject",
            "Assembly::AssemblyLink",
            "App::Link",
            "App::Part",
            "App::GeoFeature",
            "Part::Feature",
        )
    ):
        return not _is_derived(obj, "App::LocalCoordinateSystem")
    return False


def assembly_components(assembly: Any) -> tuple[Any, ...]:
    return tuple(
        child
        for child in list(getattr(assembly, "Group", ()) or ())
        if _is_component_instance(child) and _timeline_active(child)
    )


def _contains_recursive(container: Any, obj: Any) -> bool:
    if getattr(container, "Document", None) is not getattr(obj, "Document", None):
        return False
    has_object = getattr(container, "hasObject", None)
    if callable(has_object):
        try:
            return bool(has_object(obj, True))
        except TypeError:
            try:
                return bool(has_object(obj))
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                return True
        except (AttributeError, ReferenceError, RuntimeError):
            return True
    pending = list(getattr(container, "Group", ()) or ())
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        if candidate is obj:
            return True
        pending.extend(list(getattr(candidate, "Group", ()) or ()))
    return False


def _source_would_cycle(assembly: Any, source: Any) -> bool:
    if source is assembly or _contains_recursive(assembly, source):
        return True
    try:
        return source in list(getattr(assembly, "InListRecursive", ()) or ())
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return True


def _source_summary(source: Any) -> dict[str, Any]:
    result = object_reference(source)
    result["document_name"] = str(source.Document.Name)
    result["object_id"] = int(source.ID)
    label = str(getattr(source, "Label", "") or "").strip()
    if label and label != result["object_name"]:
        result["label"] = label[:160]
    result["subassembly"] = _is_derived(source, "Assembly::AssemblyObject")
    return result


def available_component_sources(
    target_document: Any,
    assembly: Any | None,
    *,
    limit: int = 48,
    before_first_assembly: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a bounded exact inventory of linkable objects in open documents."""

    if (assembly is None and not before_first_assembly) or limit < 1:
        return [], False
    try:
        import FreeCAD as App

        documents = tuple(App.listDocuments().values())
    except (ImportError, AttributeError, RuntimeError):
        documents = (target_document,)
    sources: list[dict[str, Any]] = []
    truncated = False
    for document in documents:
        external = document is not target_document
        if external and (
            not str(getattr(target_document, "FileName", "") or "")
            or not str(getattr(document, "FileName", "") or "")
        ):
            continue
        for candidate in list(getattr(document, "Objects", ()) or ()):
            if (
                not _is_component_source(candidate)
                or not _timeline_active(candidate)
                or (
                    assembly is not None
                    and _source_would_cycle(assembly, candidate)
                )
            ):
                continue
            if len(sources) >= limit:
                truncated = True
                return sources, truncated
            sources.append(_source_summary(candidate))
    return sources, truncated


def resolve_component_source(
    target_document: Any,
    reference: AssemblySourceRef,
) -> Any:
    if document_uid(target_document) == reference.document_uid:
        documents = (target_document,)
    else:
        try:
            import FreeCAD as App

            documents = tuple(App.listDocuments().values())
        except (ImportError, AttributeError, RuntimeError) as exc:
            raise NativeAssemblyComponentError(
                "The exact source document is not open."
            ) from exc
    matches = [
        document
        for document in documents
        if str(getattr(document, "Name", "") or "") == reference.document_name
        and str(getattr(document, "Uid", "") or "") == reference.document_uid
    ]
    if len(matches) != 1:
        raise NativeAssemblyComponentError(
            "The exact source document is not open."
        )
    source_document = matches[0]
    get_object = getattr(source_document, "getObject", None)
    source = get_object(reference.object_name) if callable(get_object) else None
    if (
        source is None
        or getattr(source, "Document", None) is not source_document
        or int(getattr(source, "ID", 0) or 0) != reference.object_id
    ):
        raise NativeAssemblyComponentError(
            "The exact source object changed; read current Assemble state and retry."
        )
    return source


def _resolve_active_target(
    document: Any,
    assembly_ref: NativeObjectRef,
    expected_component_count: int,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    assembly = resolve_object(
        document,
        assembly_ref,
        expected_types=("Assembly::AssemblyObject",),
    )
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblyComponentError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not _timeline_active(assembly):
        raise NativeAssemblyComponentError(
            "The human-active Assembly is outside the current document history."
        )
    if len(assembly_components(assembly)) != expected_component_count:
        raise NativeAssemblyComponentError(
            "The active Assembly component count changed; read current Assemble state and retry."
        )
    return assembly


def preflight_insert_component(
    document: Any,
    spec: InsertComponentSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    source_resolver: Callable[[Any, AssemblySourceRef], Any] = resolve_component_source,
) -> tuple[Any, Any]:
    assembly = _resolve_active_target(
        document,
        spec.assembly_ref,
        spec.expected_component_count,
        active_reader,
    )
    source = source_resolver(document, spec.source_ref)
    if not _is_component_source(source) or not _timeline_active(source):
        raise NativeAssemblyComponentError(
            "The exact source is not an active Part, Body, primitive, or Assembly."
        )
    if _source_would_cycle(assembly, source):
        raise NativeAssemblyComponentError(
            "The exact source would create an Assembly dependency cycle."
        )
    source_document = source.Document
    if source_document is not document and (
        not str(getattr(document, "FileName", "") or "")
        or not str(getattr(source_document, "FileName", "") or "")
    ):
        raise NativeAssemblyComponentError(
            "Both documents must be saved before inserting an external component."
        )
    subassembly = _is_derived(source, "Assembly::AssemblyObject")
    if subassembly != (spec.rigid is not None):
        raise NativeAssemblyComponentError(
            "rigid must be true or false for a subassembly source and null otherwise."
        )
    if (
        subassembly
        and spec.rigid is False
        and not _placement_is_identity(spec.placement)
        and not any(
            getattr(child, "Placement", None) is not None
            for child in assembly_components(source)
        )
    ):
        raise NativeAssemblyComponentError(
            "A non-identity flexible insertion requires a placeable source component."
        )
    return assembly, source


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    old = {id(obj) for obj in before}
    return tuple(
        obj for obj in list(getattr(document, "Objects", ()) or ()) if id(obj) not in old
    )


def _intrinsic_origin_resources(objects: tuple[Any, ...]) -> set[Any]:
    """Return auto-created Origin geometry owned by objects in *objects*."""

    resources: set[Any] = set()
    for obj in objects:
        origin = getattr(obj, "Origin", None)
        if origin is None:
            continue
        resources.add(origin)
        resources.update(list(getattr(origin, "OriginFeatures", ()) or ()))
        resources.update(list(getattr(origin, "Group", ()) or ()))
    return resources


def _tracked_occurrence_graph(
    created_objects: tuple[Any, ...],
    occurrence: Any,
) -> tuple[Any, ...]:
    return tuple(
        obj
        for obj in created_objects
        if obj is occurrence
        or (
            str(getattr(obj, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(obj, "VibeCADTimelineOwner", None) is occurrence
        )
    )


def _finalize_inserted_component(occurrence: Any) -> None:
    import UtilsAssembly

    UtilsAssembly.finalizeInsertedComponentTimeline(occurrence)


def insert_component(
    document: Any,
    spec: InsertComponentSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    source_resolver: Callable[[Any, AssemblySourceRef], Any] = resolve_component_source,
    finalizer: Callable[[Any], None] = _finalize_inserted_component,
) -> NativeMutationDraft:
    assembly, source = preflight_insert_component(
        document,
        spec,
        active_reader=active_reader,
        source_resolver=source_resolver,
    )
    before = tuple(document.Objects)
    active_before = active_reader(document)
    subassembly = _is_derived(source, "Assembly::AssemblyObject")
    occurrence_type = "Assembly::AssemblyLink" if subassembly else "App::Link"
    occurrence = assembly.newObject(occurrence_type, source.Label)
    occurrence.LinkedObject = source
    occurrence.Label = spec.label
    occurrence_label = str(occurrence.Label)
    occurrence.Placement = spec.placement
    if subassembly:
        occurrence.Rigid = bool(spec.rigid)
    recompute = getattr(occurrence, "recompute", None)
    if callable(recompute):
        recompute()
    finalizer(occurrence)
    created_objects = _new_document_objects(document, before)
    if occurrence not in created_objects:
        raise NativeAssemblyComponentError(
            "The inserted occurrence was not created in the active document."
        )
    tracked_objects = _tracked_occurrence_graph(created_objects, occurrence)
    return NativeMutationDraft(
        value={
            "assembly": assembly,
            "source": source,
            "occurrence": occurrence,
            "created_objects": created_objects,
            "before_objects": before,
            "before_count": spec.expected_component_count,
            "active_before": active_before,
            "label": occurrence_label,
            "placement": spec.placement,
            "rigid": spec.rigid,
        },
        recompute_targets=(*tracked_objects, assembly),
        created=tuple(object_identity(obj) for obj in tracked_objects),
        changed=(object_identity(assembly),),
    )


def _placement_matches(actual: Any, expected: Any) -> bool:
    is_same = getattr(actual, "isSame", None)
    if callable(is_same):
        try:
            return bool(is_same(expected, 1.0e-12))
        except (AttributeError, RuntimeError, TypeError):
            return False
    return actual == expected


def _placement_is_identity(placement: Any) -> bool:
    reader = getattr(placement, "isIdentity", None)
    if callable(reader):
        try:
            return bool(reader())
        except (AttributeError, RuntimeError, TypeError):
            return False
    return False


def _flexible_placement_matches(
    occurrence: Any,
    requested: Any,
) -> bool:
    """Verify the transform AssemblyLink distributes into flexible children."""

    actual = getattr(occurrence, "Placement", None)
    if not _placement_is_identity(actual):
        return False
    if _placement_is_identity(requested):
        return True
    try:
        import UtilsAssembly
    except ImportError:
        return False
    candidates: list[Any] = []
    for child in list(getattr(occurrence, "Group", ()) or ()):
        is_link_group = getattr(child, "isLinkGroup", None)
        if callable(is_link_group) and bool(is_link_group()):
            candidates.extend(list(getattr(child, "ElementList", ()) or ()))
        else:
            candidates.append(child)
    compared = 0
    for candidate in candidates:
        placement = getattr(candidate, "Placement", None)
        if placement is None:
            continue
        try:
            source = UtilsAssembly._resolveAssemblyLinkManagedSource(candidate)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue
        source_placement = getattr(source, "Placement", None)
        if source_placement is None:
            continue
        try:
            expected = requested * source_placement
        except (AttributeError, RuntimeError, TypeError):
            return False
        compared += 1
        if not _placement_matches(placement, expected):
            return False
    return compared > 0


def _component_placement_matches(
    occurrence: Any,
    source: Any,
    requested: Any,
    rigid: bool | None,
) -> bool:
    if _is_derived(source, "Assembly::AssemblyObject") and rigid is False:
        return _flexible_placement_matches(occurrence, requested)
    return _placement_matches(getattr(occurrence, "Placement", None), requested)


def _created_graph_is_exact(document: Any, value: dict[str, Any], owner: Any) -> bool:
    before = tuple(value["before_objects"])
    created = tuple(value["created_objects"])
    if _new_document_objects(document, before) != created:
        return False
    get_object = getattr(document, "getObject", None)
    if not callable(get_object):
        return False
    intrinsic = _intrinsic_origin_resources(created)
    for obj in created:
        if get_object(str(obj.Name)) is not obj:
            return False
        if obj is owner:
            if (
                str(getattr(obj, "VibeCADTimelineRole", "") or "") != "operation"
                or not _timeline_active(obj)
            ):
                return False
        elif (
            str(getattr(obj, "VibeCADTimelineRole", "") or "") == "resource"
            and getattr(obj, "VibeCADTimelineOwner", None) is owner
        ):
            if not _timeline_active(obj):
                return False
        elif obj not in intrinsic or str(
            getattr(obj, "VibeCADTimelineRole", "") or ""
        ):
            return False
    return True


def verify_inserted_component(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
) -> dict[str, Any]:
    value = draft.value
    assembly = value["assembly"]
    source = value["source"]
    occurrence = value["occurrence"]
    subassembly = _is_derived(source, "Assembly::AssemblyObject")
    if (
        getattr(occurrence, "LinkedObject", None) is not source
        or str(getattr(occurrence, "TypeId", "") or "")
        != ("Assembly::AssemblyLink" if subassembly else "App::Link")
        or str(getattr(occurrence, "Label", "") or "") != value["label"]
        or not _component_placement_matches(
            occurrence,
            source,
            value["placement"],
            value["rigid"],
        )
        or (subassembly and bool(getattr(occurrence, "Rigid", False)) != value["rigid"])
        or occurrence not in assembly_components(assembly)
        or len(assembly_components(assembly)) != int(value["before_count"]) + 1
        or not _created_graph_is_exact(document, value, occurrence)
        or not same_assembly(value["active_before"], active_reader(document))
    ):
        raise NativeAssemblyComponentError(
            "The component insertion failed its exact graph, placement, or activation postcondition."
        )
    result: dict[str, Any] = {
        "assembly": object_reference(assembly),
        "source": _source_summary(source),
        "occurrence": object_reference(occurrence),
        "component_count": len(assembly_components(assembly)),
        "subassembly": subassembly,
        "active_assembly_unchanged": True,
        "grounded": False,
        "label": str(occurrence.Label),
    }
    if subassembly:
        result["rigid"] = bool(occurrence.Rigid)
    return result


def preflight_create_part(
    document: Any,
    spec: CreatePartSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
) -> Any:
    assembly = _resolve_active_target(
        document,
        spec.assembly_ref,
        spec.expected_component_count,
        active_reader,
    )
    if any(
        str(getattr(obj, "Label", "") or "") == spec.label
        for obj in list(getattr(document, "Objects", ()) or ())
    ):
        raise NativeAssemblyComponentError(
            "The requested new Part label is already in use."
        )
    return assembly


def _create_part_definition(label: str, document: Any) -> tuple[Any, Any]:
    import UtilsAssembly

    return UtilsAssembly.createPart(label, document)


def _finalize_new_part(part: Any, body: Any, occurrence: Any) -> None:
    import UtilsAssembly

    UtilsAssembly.finalizeNewPartTimeline(part, body, occurrence)


def create_part(
    document: Any,
    spec: CreatePartSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    part_factory: Callable[[str, Any], tuple[Any, Any]] = _create_part_definition,
    finalizer: Callable[[Any, Any, Any], None] = _finalize_new_part,
) -> NativeMutationDraft:
    assembly = preflight_create_part(document, spec, active_reader=active_reader)
    before = tuple(document.Objects)
    active_before = active_reader(document)
    part, body = part_factory(spec.label, document)
    if (
        part is None
        or body is None
        or getattr(part, "Document", None) is not document
        or getattr(body, "Document", None) is not document
        or str(getattr(part, "TypeId", "") or "") != "App::Part"
        or str(getattr(body, "TypeId", "") or "") != "PartDesign::Body"
        or body not in list(getattr(part, "Group", ()) or ())
    ):
        raise NativeAssemblyComponentError(
            "The new-part factory returned the wrong native Part and Body graph."
        )
    part.Label = spec.label
    occurrence = assembly.newObject("App::Link", part.Label)
    occurrence.LinkedObject = part
    occurrence.Label = spec.label
    occurrence_label = str(occurrence.Label)
    occurrence.Placement = spec.placement
    finalizer(part, body, occurrence)
    created_objects = _new_document_objects(document, before)
    explicit = {part, body, occurrence}
    intrinsic = _intrinsic_origin_resources(created_objects)
    if not explicit.issubset(set(created_objects)) or any(
        obj not in explicit and obj not in intrinsic for obj in created_objects
    ):
        raise NativeAssemblyComponentError(
            "New Part created objects outside its exact Part, Body, and occurrence graph."
        )
    return NativeMutationDraft(
        value={
            "assembly": assembly,
            "part": part,
            "body": body,
            "occurrence": occurrence,
            "created_objects": created_objects,
            "before_objects": before,
            "before_count": spec.expected_component_count,
            "active_before": active_before,
            "label": spec.label,
            "occurrence_label": occurrence_label,
            "placement": spec.placement,
        },
        recompute_targets=(body, part, occurrence, assembly),
        created=tuple(object_identity(obj) for obj in (part, body, occurrence)),
        changed=(object_identity(assembly),),
    )


def verify_created_part(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
) -> dict[str, Any]:
    value = draft.value
    assembly = value["assembly"]
    part = value["part"]
    body = value["body"]
    occurrence = value["occurrence"]
    created = tuple(value["created_objects"])
    explicit = {part, body, occurrence}
    intrinsic = _intrinsic_origin_resources(created)
    if (
        not explicit.issubset(set(created))
        or any(obj not in explicit and obj not in intrinsic for obj in created)
        or _new_document_objects(document, tuple(value["before_objects"])) != created
        or str(getattr(part, "Label", "") or "") != value["label"]
        or body not in list(getattr(part, "Group", ()) or ())
        or occurrence not in assembly_components(assembly)
        or getattr(occurrence, "LinkedObject", None) is not part
        or str(getattr(occurrence, "Label", "") or "")
        != value["occurrence_label"]
        or not _placement_matches(getattr(occurrence, "Placement", None), value["placement"])
        or len(assembly_components(assembly)) != int(value["before_count"]) + 1
        or str(getattr(part, "VibeCADTimelineRole", "") or "") != "operation"
        or str(getattr(body, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(body, "VibeCADTimelineOwner", None) is not part
        or str(getattr(occurrence, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(occurrence, "VibeCADTimelineOwner", None) is not part
        or not all(_timeline_active(obj) for obj in explicit)
        or not same_assembly(value["active_before"], active_reader(document))
    ):
        raise NativeAssemblyComponentError(
            "New Part failed its exact graph, placement, timeline, or activation postcondition."
        )
    return {
        "assembly": object_reference(assembly),
        "part": object_reference(part),
        "body": object_reference(body),
        "occurrence": object_reference(occurrence),
        "component_count": len(assembly_components(assembly)),
        "active_assembly_unchanged": True,
        "body_activation_changed": False,
        "occurrence_label": str(occurrence.Label),
    }
