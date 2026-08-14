# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native creation of the shipped CAM Property Bag."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePropertyBagValues import (
    PropertyBagValue,
    apply_property_bag_values,
    clean_property_bag_label,
    is_property_bag,
    normalize_property_bag_values,
    property_bag_summary,
)
from VibeCADNativeManufactureState import (
    persistent_configuration_sha256,
    persistent_resource_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
    read_current_selection,
)


MAX_PROPERTY_BAG_SNAPSHOT_ITEMS = 12
MAX_PROPERTY_BAG_DESTINATIONS = 24


@dataclass(frozen=True, slots=True)
class PropertyBagCreateSpec:
    label: Any
    destination_body: Mapping[str, Any] | None
    properties: Any


@dataclass(frozen=True, slots=True)
class PropertyBagTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PropertyBagBodyBoundary:
    body: Any
    state_before: Mapping[str, Any]
    members_before: tuple[Any, ...]
    invariant_before: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedPropertyBagCreate:
    label: str
    properties: tuple[PropertyBagValue, ...]
    destination: PropertyBagBodyBoundary | None
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Mapping[str, Any]
    timeline_before: PropertyBagTimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> PropertyBagTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "Property Bag creation requires valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(timeline.Operations or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "Document History has inconsistent Property Bag insertion state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return PropertyBagTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _body_invariant(body: Any) -> dict[str, Any]:
    resource = persistent_resource_state(body)
    return {
        "object_name": str(body.Name),
        "type_id": str(body.TypeId),
        "label": str(body.Label),
        "configuration_without_group_sha256": persistent_configuration_sha256(
            body,
            excluded_names=("Group",),
        ),
        "shape_sha256": resource.get("shape_sha256"),
        "placement": resource.get("placement"),
    }


def property_bag_destination_state(body: Any) -> dict[str, Any]:
    document = getattr(body, "Document", None)
    name = str(getattr(body, "Name", "") or "")
    try:
        from Path.CommandBoundary import is_timeline_input_usable

        usable = bool(is_timeline_input_usable(body, document))
    except Exception:
        usable = False
    if (
        document is None
        or not name
        or document.getObject(name) is not body
        or str(getattr(body, "TypeId", "")) != "PartDesign::Body"
        or not usable
    ):
        _error(
            "The Property Bag destination must be one exact current Part Design Body.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    resource = persistent_resource_state(body)
    return {
        "object_name": name,
        "type_id": "PartDesign::Body",
        "label": str(body.Label),
        "member_count": len(tuple(body.Group or ())),
        "state_sha256": resource["state_sha256"],
    }


def _resolve_destination(
    document: Any,
    target: Mapping[str, Any] | None,
) -> PropertyBagBodyBoundary | None:
    if target is None:
        return None
    if not isinstance(target, Mapping) or set(target) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error(
            "Property Bag destination_body must be null or one exact Body target.",
            repair={"field": "destination_body"},
        )
    name = target.get("object_name")
    expected = target.get("expected_state_sha256")
    if not isinstance(name, str) or not isinstance(expected, str):
        _error(
            "Property Bag destination_body identity is invalid.",
            repair={"field": "destination_body"},
        )
    body = document.getObject(name)
    if body is None or getattr(body, "Document", None) is not document:
        _error(
            "The exact Property Bag destination Body no longer exists.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
        )
    state = property_bag_destination_state(body)
    if state["state_sha256"] != expected:
        _error(
            "The exact Property Bag destination Body changed after it was read.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": name,
                "current_state_sha256": state["state_sha256"],
                "retry_from_current_state": True,
            },
        )
    return PropertyBagBodyBoundary(
        body=body,
        state_before=state,
        members_before=tuple(body.Group or ()),
        invariant_before=_body_invariant(body),
    )


def preflight_property_bag_create(
    document: Any,
    spec: PropertyBagCreateSpec,
) -> PreparedPropertyBagCreate:
    if not isinstance(spec, PropertyBagCreateSpec):
        raise TypeError("spec must be a PropertyBagCreateSpec")
    if _transaction_open(document):
        _error(
            "Finish or cancel the open task before creating a Property Bag.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for document recompute to finish before creating a Property Bag.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    return PreparedPropertyBagCreate(
        label=clean_property_bag_label(spec.label),
        properties=normalize_property_bag_values(spec.properties),
        destination=_resolve_destination(document, spec.destination_body),
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def _destination_is_current(
    destination: PropertyBagBodyBoundary | None,
) -> bool:
    if destination is None:
        return True
    try:
        body = destination.body
        return (
            body.Document.getObject(body.Name) is body
            and tuple(body.Group or ()) == destination.members_before
            and property_bag_destination_state(body)["state_sha256"]
            == destination.state_before["state_sha256"]
            and _body_invariant(body) == destination.invariant_before
        )
    except Exception:
        return False


def _boundary_is_current(
    document: Any,
    prepared: PreparedPropertyBagCreate,
) -> bool:
    return bool(
        tuple(document.Objects) == prepared.objects_before
        and _timeline_state(document) == prepared.timeline_before
        and read_current_selection(document) == prepared.selection_before
        and all(
            bool(obj.ViewObject.Visibility) is visible
            for obj, visible in prepared.visibility_before
        )
        and _destination_is_current(prepared.destination)
    )


def create_property_bag(
    document: Any,
    *,
    prepared: PreparedPropertyBagCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPropertyBagCreate):
        raise TypeError("prepared must be a PreparedPropertyBagCreate")
    if not _boundary_is_current(document, prepared):
        _error(
            "The exact document, History, Body, selection, or visibility changed "
            "after Property Bag preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )

    import Path.Base.Gui.PropertyBag as PathPropertyBagGui

    bag = PathPropertyBagGui.CreateInTransaction(
        "PropertyBag",
        document=document,
    )
    if (
        bag is None
        or document.getObject(str(getattr(bag, "Name", ""))) is not bag
        or not is_property_bag(bag)
    ):
        raise RuntimeError("The shared Property Bag factory returned the wrong object")
    bag.Label = prepared.label
    apply_property_bag_values(bag, prepared.properties)

    destination = prepared.destination
    if destination is not None:
        destination.body.Group = [*destination.members_before, bag]

    if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(bag):
        raise RuntimeError("The Property Bag was not provisionally enrolled in History")
    document.publishProvisionalTimelineOperationBlock(bag, (), ())

    return NativeMutationDraft(
        value={"prepared": prepared, "property_bag": bag},
        recompute_targets=(bag,),
        created=(object_identity(bag),),
        changed=(
            (object_identity(destination.body),) if destination is not None else ()
        ),
    )


def _verify_timeline(
    document: Any,
    prepared: PreparedPropertyBagCreate,
    bag: Any,
) -> None:
    before = prepared.timeline_before
    after = _timeline_state(document)
    expected = (
        *before.operations[: before.position],
        bag,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected
        or after.position != before.position + 1
    ):
        _error(
            "The Property Bag was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = old_index if old_index < before.position else old_index + 1
        if (
            after.visibility[new_index] is not before.visibility[old_index]
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "Property Bag creation changed existing History state.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    if after.suppression[before.position]:
        _error(
            "The created Property Bag was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def _verify_destination(
    prepared: PreparedPropertyBagCreate,
    bag: Any,
) -> dict[str, str] | None:
    destination = prepared.destination
    parent = bag.getParentGeoFeatureGroup()
    if destination is None:
        if parent is not None:
            _error(
                "The root Property Bag was unexpectedly attached to a container.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
        return None
    body = destination.body
    if (
        parent is not body
        or tuple(body.Group or ()) != (*destination.members_before, bag)
        or _body_invariant(body) != destination.invariant_before
    ):
        _error(
            "The Property Bag changed its exact destination Body outside Group membership.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return object_reference(body)


def verify_created_property_bag(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    prepared: PreparedPropertyBagCreate = value["prepared"]
    bag = value["property_bag"]
    if tuple(document.Objects) != (*prepared.objects_before, bag):
        _error(
            "Property Bag creation changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    import Path.Base.Gui.PropertyBag as PathPropertyBagGui
    import Path.Base.PropertyBag as PathPropertyBag

    identity_failures = tuple(
        name
        for name, valid in (
            ("document_identity", document.getObject(str(bag.Name)) is bag),
            ("type", str(bag.TypeId) == "App::FeaturePython"),
            ("validity", bool(bag.isValid())),
            ("label", str(bag.Label) == prepared.label),
            (
                "proxy",
                isinstance(getattr(bag, "Proxy", None), PathPropertyBag.PropertyBag),
            ),
            (
                "view_provider",
                isinstance(
                    getattr(getattr(bag, "ViewObject", None), "Proxy", None),
                    PathPropertyBagGui.ViewProvider,
                ),
            ),
            (
                "history_role",
                str(getattr(bag, "VibeCADTimelineRole", "") or "") == "operation",
            ),
            (
                "history_owner",
                getattr(bag, "VibeCADTimelineOwner", None) is None,
            ),
            (
                "replacement_state",
                not tuple(getattr(bag, "VibeCADTimelineReplacedInputs", ()) or ()),
            ),
        )
        if not valid
    )
    if identity_failures:
        _error(
            "The Property Bag failed exact identity checks: "
            f"{', '.join(identity_failures)}.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"failed_invariants": list(identity_failures)},
        )
    _verify_timeline(document, prepared, bag)
    destination = _verify_destination(prepared, bag)
    if read_current_selection(document) != prepared.selection_before or any(
        bool(obj.ViewObject.Visibility) is not visible
        for obj, visible in prepared.visibility_before
    ):
        _error(
            "Property Bag creation changed human selection or existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    result = property_bag_summary(bag, prepared.properties)
    result["destination_body"] = destination
    result["history_position"] = prepared.timeline_before.position + 1
    return result


def property_bag_snapshot(document: Any) -> dict[str, Any]:
    bags = [obj for obj in tuple(document.Objects) if is_property_bag(obj)]
    summaries = [
        property_bag_summary(obj, property_limit=16)
        for obj in bags[:MAX_PROPERTY_BAG_SNAPSHOT_ITEMS]
    ]
    bodies = []
    for obj in tuple(document.Objects):
        if str(getattr(obj, "TypeId", "")) != "PartDesign::Body":
            continue
        try:
            bodies.append(property_bag_destination_state(obj))
        except NativeManufactureError:
            continue
    return {
        "property_bag_count": len(bags),
        "property_bags": summaries,
        "property_bags_truncated": len(bags) > MAX_PROPERTY_BAG_SNAPSHOT_ITEMS,
        "property_bag_destination_count": len(bodies),
        "property_bag_destinations": bodies[:MAX_PROPERTY_BAG_DESTINATIONS],
        "property_bag_destinations_truncated": len(bodies)
        > MAX_PROPERTY_BAG_DESTINATIONS,
    }
