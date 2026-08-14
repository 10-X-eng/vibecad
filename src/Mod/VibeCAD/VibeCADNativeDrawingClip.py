# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional clip-group mutations for Native Drawing."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingClipState import (
    drawing_clip_group_state,
    drawing_clip_member_state,
    is_clip_drawing_view,
    is_projection_group_item,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


@dataclass(frozen=True, slots=True)
class ClipConfiguration:
    label: str
    position_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    show_frame: bool
    clip_children: bool


@dataclass(frozen=True, slots=True)
class PreparedClipMutation:
    operation: str
    page: Any
    page_state_before: dict[str, Any]
    clip_group: Any | None
    clip_state_before: dict[str, Any] | None
    members: tuple[Any, ...]
    member_states_before: tuple[dict[str, Any], ...]
    member_positions_mm: tuple[tuple[float, float], ...]
    configuration: ClipConfiguration | None
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


def _error(message: str, code: str, *, repair: Mapping[str, Any] | None = None) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _finite(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"{name} must be a finite number.",
            error_code="NATIVE_DRAWING_CLIP_PARAMETERS_INVALID",
        ) from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        _error(
            f"{name} must be between {minimum:g} and {maximum:g} mm.",
            "NATIVE_DRAWING_CLIP_PARAMETERS_INVALID",
        )
    return result


def _position(value: Mapping[str, Any], *, name: str) -> tuple[float, float]:
    return (
        _finite(value["x_mm"], name=f"{name} x_mm", minimum=-1_000_000, maximum=1_000_000),
        _finite(value["y_mm"], name=f"{name} y_mm", minimum=-1_000_000, maximum=1_000_000),
    )


def _configuration(values: Mapping[str, Any]) -> ClipConfiguration:
    label = str(values["label"]).strip()
    if not label or len(label) > 128:
        _error(
            "Drawing clip-group label must contain 1 to 128 characters.",
            "NATIVE_DRAWING_CLIP_PARAMETERS_INVALID",
        )
    frame = values["frame"]
    return ClipConfiguration(
        label=label,
        position_mm=_position(values["position_on_page_mm"], name="Clip-group position"),
        width_mm=_finite(
            frame["width_mm"],
            name="Clip-group width",
            minimum=1.0e-9,
            maximum=1_000_000,
        ),
        height_mm=_finite(
            frame["height_mm"],
            name="Clip-group height",
            minimum=1.0e-9,
            maximum=1_000_000,
        ),
        show_frame=bool(frame["show_frame"]),
        clip_children=bool(frame["clip_children"]),
    )


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    result = []
    for obj in tuple(document.Objects):
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            result.append((obj, bool(getattr(view_object, "Visibility", False))))
    return tuple(result)


def restore_clip_selection(document: Any, snapshot: Mapping[str, Any]) -> None:
    """Restore selection after TechDraw tree reparenting clears a member."""

    import FreeCADGui as Gui

    Gui.Selection.clearSelection(str(document.Name))
    for item in tuple(snapshot.get("items", ()) or ()):
        reference = item.get("object") if isinstance(item, Mapping) else None
        name = str(reference.get("object_name", "")) if isinstance(reference, Mapping) else ""
        obj = document.getObject(name) if name else None
        if obj is None:
            _error(
                "The human selection target disappeared during Drawing clip-group reparenting.",
                "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
            )
        subelements = tuple(item.get("subelements", ()) or ())
        if subelements:
            for subelement in subelements:
                Gui.Selection.addSelection(obj, str(subelement))
        else:
            Gui.Selection.addSelection(obj)


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _resolve_page(document: Any, target: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _resolve_clip(
    document: Any,
    page: Any,
    target: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    clip = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawViewClip",),
    )
    state = drawing_clip_group_state(clip)
    if str(target["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing clip group changed after it was inspected.",
            "NATIVE_DRAWING_CLIP_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    if clip not in tuple(getattr(page, "Views", ()) or ()) or clip.findParentPage() is not page:
        _error(
            "The exact Drawing clip group does not belong to the exact page.",
            "NATIVE_DRAWING_CLIP_PAGE_MISMATCH",
        )
    _require_usable(document, clip, "Drawing clip group")
    return clip, state


def _resolve_members(
    document: Any,
    page: Any,
    entries: tuple[Mapping[str, Any], ...],
    *,
    position_field: str,
    clip_group: Any | None,
    expect_members: bool,
) -> tuple[tuple[Any, ...], tuple[dict[str, Any], ...], tuple[tuple[float, float], ...]]:
    names = tuple(str(entry["view"]["object_name"]) for entry in entries)
    if len(names) != len(set(names)):
        _error(
            "Each Drawing view may appear only once in one clip-group operation.",
            "NATIVE_DRAWING_CLIP_MEMBERS_INVALID",
        )
    members = []
    states = []
    positions = []
    for entry in entries:
        target = entry["view"]
        view = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": target["object_name"]},
            expected_types=("TechDraw::DrawView",),
        )
        if is_clip_drawing_view(view):
            _error(
                "Drawing clip groups cannot be nested.",
                "NATIVE_DRAWING_CLIP_MEMBERS_INVALID",
            )
        if is_projection_group_item(view):
            _error(
                "Projection-group items must remain owned by their projection group.",
                "NATIVE_DRAWING_CLIP_MEMBERS_INVALID",
            )
        if view not in tuple(getattr(page, "Views", ()) or ()) or view.findParentPage() is not page:
            _error(
                "Every Drawing clip member must belong to the exact same page.",
                "NATIVE_DRAWING_CLIP_PAGE_MISMATCH",
            )
        _require_usable(document, view, "Drawing clip member")
        state = drawing_clip_member_state(view)
        if str(target["expected_state_sha256"]) != state["state_sha256"]:
            _error(
                f"Drawing view {view.Name!r} changed after it was inspected.",
                "NATIVE_DRAWING_CLIP_MEMBER_STALE",
                repair={
                    "object_name": str(view.Name),
                    "current_state_sha256": state["state_sha256"],
                },
            )
        group_names = tuple(state["clip_group_names"])
        expected_name = str(getattr(clip_group, "Name", "") or "")
        if expect_members:
            if group_names != (expected_name,):
                _error(
                    f"Drawing view {view.Name!r} is not an exclusive member of the exact clip group.",
                    "NATIVE_DRAWING_CLIP_MEMBERSHIP_MISMATCH",
                )
        elif group_names:
            _error(
                f"Drawing view {view.Name!r} already belongs to clip group {group_names[0]!r}.",
                "NATIVE_DRAWING_CLIP_ALREADY_GROUPED",
            )
        members.append(view)
        states.append(state)
        positions.append(_position(entry[position_field], name=position_field))
    return tuple(members), tuple(states), tuple(positions)


def prepare_clip_mutation(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedClipMutation:
    if operation not in {
        "create_clip_group",
        "add_views",
        "remove_views",
        "configure_clip_group",
    }:
        raise ValueError("operation is not a Drawing clip-group operation")
    page, page_state = _resolve_page(document, values["page"])
    clip = None
    clip_state = None
    if operation != "create_clip_group":
        clip, clip_state = _resolve_clip(document, page, values["clip_group"])
    entries = tuple(values.get("members", ()))
    members: tuple[Any, ...] = ()
    member_states: tuple[dict[str, Any], ...] = ()
    positions: tuple[tuple[float, float], ...] = ()
    if entries:
        members, member_states, positions = _resolve_members(
            document,
            page,
            entries,
            position_field=(
                "position_on_page_mm" if operation == "remove_views" else "position_in_clip_mm"
            ),
            clip_group=clip,
            expect_members=operation == "remove_views",
        )
    configuration = (
        _configuration(values)
        if operation in {"create_clip_group", "configure_clip_group"}
        else None
    )
    selection_before = _selection(document)
    if (
        bool(selection_before.get("truncated"))
        or int(selection_before.get("selected_count", 0))
        != len(tuple(selection_before.get("items", ()) or ()))
    ):
        _error(
            "Reduce the current selection to at most 32 exact objects before changing a Drawing clip group.",
            "NATIVE_DRAWING_CLIP_SELECTION_TOO_LARGE",
        )
    return PreparedClipMutation(
        operation=operation,
        page=page,
        page_state_before=page_state,
        clip_group=clip,
        clip_state_before=clip_state,
        members=members,
        member_states_before=member_states,
        member_positions_mm=positions,
        configuration=configuration,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        page_views_before=tuple(page.Views or ()),
        selection_before=selection_before,
        visibility_before=_visibility(document),
    )


def _apply_configuration(clip: Any, configuration: ClipConfiguration) -> None:
    clip.Label = configuration.label
    clip.X, clip.Y = configuration.position_mm
    clip.Width = configuration.width_mm
    clip.Height = configuration.height_mm
    clip.ShowFrame = configuration.show_frame
    clip.ViewObject.ClipChildren = configuration.clip_children


def mutate_clip_group(
    document: Any,
    *,
    prepared: PreparedClipMutation,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedClipMutation):
        raise TypeError("prepared must be a PreparedClipMutation")
    operation = prepared.operation
    clip = prepared.clip_group
    created = ()
    if operation == "create_clip_group":
        clip = document.addObject("TechDraw::DrawViewClip", "Clip")
        if not is_clip_drawing_view(clip):
            _error(
                "The Drawing clip-group factory returned the wrong object type.",
                "NATIVE_DRAWING_CLIP_CREATE_FAILED",
            )
        _apply_configuration(clip, prepared.configuration)
        document.publishProvisionalTimelineOperationBlock(clip, (), ())
        if int(prepared.page.addView(clip)) < 1:
            _error(
                "The Drawing clip group could not join its exact page.",
                "NATIVE_DRAWING_CLIP_CREATE_FAILED",
            )
        # addView may choose an automatic page position; the provider contract is exact.
        clip.X, clip.Y = prepared.configuration.position_mm
        created = (object_identity(clip),)
    if operation in {"create_clip_group", "add_views"}:
        for member, position in zip(
            prepared.members,
            prepared.member_positions_mm,
            strict=True,
        ):
            clip.addView(member)
            member.X, member.Y = position
    elif operation == "remove_views":
        for member, position in zip(
            prepared.members,
            prepared.member_positions_mm,
            strict=True,
        ):
            clip.removeView(member)
            member.X, member.Y = position
    elif operation == "configure_clip_group":
        _apply_configuration(clip, prepared.configuration)
    changed_objects = [prepared.page, *prepared.members]
    if operation != "create_clip_group":
        changed_objects.append(clip)
    changed = tuple(object_identity(obj) for obj in dict.fromkeys(changed_objects))
    # Membership and X/Y changes are immediate property state. Recompute only
    # the clip itself: recomputing the touched page after restoring selection
    # would make its tree reparenting clear that selection a second time.
    recompute = (clip,)
    return NativeMutationDraft(
        value={"prepared": prepared, "clip_group": clip},
        recompute_targets=recompute,
        created=created,
        changed=changed,
        after_recompute=lambda current_document: restore_clip_selection(
            current_document,
            prepared.selection_before,
        ),
    )


def _identities(objects: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(getattr(obj, "Name", "") or ""), str(getattr(obj, "TypeId", "") or ""))
        for obj in objects
    )


def _assert_presentation(document: Any, prepared: PreparedClipMutation) -> None:
    if _selection(document) != prepared.selection_before:
        _error(
            "The Drawing clip-group operation changed the human selection.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )
    visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if visibility != prepared.visibility_before:
        _error(
            "The Drawing clip-group operation changed existing object visibility.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )


def _same_configuration(state: Mapping[str, Any], wanted: ClipConfiguration) -> bool:
    return (
        state["label"] == wanted.label
        and state["position_on_page_mm"] == [
            round(wanted.position_mm[0], 9),
            round(wanted.position_mm[1], 9),
        ]
        and state["frame"]
        == {
            "width_mm": round(wanted.width_mm, 9),
            "height_mm": round(wanted.height_mm, 9),
            "show_frame": wanted.show_frame,
            "clip_children": wanted.clip_children,
        }
    )


def verify_clip_mutation(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedClipMutation = draft.value["prepared"]
    clip = draft.value["clip_group"]
    operation = prepared.operation
    objects_now = tuple(document.Objects)
    new_objects = tuple(obj for obj in objects_now if obj not in prepared.objects_before)
    expected_new = {clip} if operation == "create_clip_group" else set()
    timeline = document.getObject("VibeCADTimeline")
    if timeline is not None and timeline not in prepared.objects_before:
        expected_new.add(timeline)
    if set(new_objects) != expected_new or len(new_objects) != len(expected_new):
        _error(
            "The Drawing clip-group operation changed objects outside its exact target set.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )
    page_views = tuple(prepared.page.Views or ())
    expected_page_views = (
        (*prepared.page_views_before, clip)
        if operation == "create_clip_group"
        else prepared.page_views_before
    )
    expected_timeline = (
        (*prepared.timeline_before, clip)
        if operation == "create_clip_group"
        else prepared.timeline_before
    )
    if (
        not is_drawing_page(prepared.page)
        or not is_clip_drawing_view(clip)
        or _identities(page_views) != _identities(tuple(expected_page_views))
        or _identities(_timeline_operations(document)) != _identities(tuple(expected_timeline))
        or clip.findParentPage() is not prepared.page
        or not bool(clip.isValid())
        or (
            operation == "create_clip_group"
            and (
                str(getattr(clip, "VibeCADTimelineRole", "") or "") != "operation"
                or getattr(clip, "VibeCADTimelineOwner", None) is not None
            )
        )
    ):
        _error(
            "The Drawing clip group did not retain its exact page and History identity.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )
    before_names = tuple(
        member["object_name"]
        for member in (prepared.clip_state_before or {}).get("members", ())
    )
    changed_names = tuple(str(member.Name) for member in prepared.members)
    if operation == "create_clip_group":
        expected_member_names = changed_names
    elif operation == "add_views":
        expected_member_names = (*before_names, *changed_names)
    elif operation == "remove_views":
        removed = set(changed_names)
        expected_member_names = tuple(name for name in before_names if name not in removed)
    else:
        expected_member_names = before_names
    state = drawing_clip_group_state(clip)
    actual_member_names = tuple(member["object_name"] for member in state["members"])
    if actual_member_names != expected_member_names:
        _error(
            "The Drawing clip group did not retain its exact ordered membership.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )
    if prepared.configuration is not None and not _same_configuration(
        state,
        prepared.configuration,
    ):
        _error(
            "The Drawing clip group did not retain its exact frame configuration.",
            "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
        )
    changed_states = tuple(drawing_clip_member_state(member) for member in prepared.members)
    expected_group_names = () if operation == "remove_views" else (str(clip.Name),)
    for member_state, position in zip(
        changed_states,
        prepared.member_positions_mm,
        strict=True,
    ):
        if (
            tuple(member_state["clip_group_names"]) != expected_group_names
            or member_state["position_mm"]
            != [round(position[0], 9), round(position[1], 9)]
            or member_state["page_name"] != str(prepared.page.Name)
            or not member_state["timeline_usable"]
            or not member_state["valid"]
        ):
            _error(
                f"Drawing view {member_state['object_name']!r} did not retain its exact clip placement.",
                "NATIVE_DRAWING_CLIP_POSTCONDITION_FAILED",
            )
    _assert_presentation(document, prepared)
    return {
        "page": drawing_page_state(prepared.page),
        "clip_group": state,
        "changed_views": list(changed_states),
    }
