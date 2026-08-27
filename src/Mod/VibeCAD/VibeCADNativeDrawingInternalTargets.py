# SPDX-License-Identifier: LGPL-2.1-or-later

"""Materialize Drawing state guards at the Native execution boundary."""

from __future__ import annotations

import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from VibeCADNativeTargets import resolve_object


_INTERNAL_STATE_FIELD = re.compile(r"^expected_[A-Za-z0-9_]*sha256$")


class NativeDrawingInternalTargetError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(str(message).strip())

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_DRAWING_INTERNAL_TARGET_INVALID",
            "message": str(self),
        }


def _provider_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_provider_schema(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    result = {str(key): _provider_schema(item) for key, item in value.items()}
    properties = result.get("properties")
    if isinstance(properties, dict):
        internal = {name for name in properties if _INTERNAL_STATE_FIELD.fullmatch(name)}
        for name in internal:
            properties.pop(name, None)
        if isinstance(result.get("required"), list):
            result["required"] = [
                name for name in result["required"] if name not in internal
            ]
    return result


def _provider_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _provider_value(item)
            for key, item in value.items()
            if not _INTERNAL_STATE_FIELD.fullmatch(str(key))
        }
    if isinstance(value, list):
        return [_provider_value(item) for item in value]
    return value


def _matching_branch(schema: Mapping[str, Any], value: Any) -> Mapping[str, Any] | None:
    branches = schema.get("oneOf") or schema.get("anyOf")
    if not isinstance(branches, list):
        return None
    visible_value = _provider_value(value)
    matches = [
        branch
        for branch in branches
        if isinstance(branch, Mapping)
        and Draft202012Validator(_provider_schema(branch)).is_valid(visible_value)
    ]
    return matches[0] if len(matches) == 1 else None


def _materialize(
    document: Any,
    tool_name: str,
    schema: Mapping[str, Any],
    value: Any,
    path: tuple[str, ...],
    arguments: Mapping[str, Any],
) -> Any:
    branch = _matching_branch(schema, value)
    if branch is not None:
        return _materialize(
            document,
            tool_name,
            branch,
            value,
            path,
            arguments,
        )
    if isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return list(value)
        return [
            _materialize(
                document,
                tool_name,
                item_schema,
                item,
                (*path, "[]"),
                arguments,
            )
            for item in value
        ]
    if not isinstance(value, Mapping):
        return value
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(value)
    result = dict(value)
    ordered_properties = sorted(
        properties.items(),
        key=lambda item: item[0] != "expected_projection_state_sha256",
    )
    for name, child_schema in ordered_properties:
        if not isinstance(child_schema, Mapping):
            continue
        if name in result:
            result[name] = _materialize(
                document,
                tool_name,
                child_schema,
                result[name],
                (*path, str(name)),
                arguments,
            )
        elif _INTERNAL_STATE_FIELD.fullmatch(str(name)):
            result[name] = _resolve_internal_state_field(
                document,
                tool_name,
                (*path, str(name)),
                result,
                arguments,
            )
    return result


def materialize_drawing_internal_targets(
    document: Any,
    tool_name: str,
    internal_schema: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy with every internal Drawing state guard resolved live."""

    if not str(tool_name).startswith("drawing."):
        raise ValueError("Drawing target materialization requires a Drawing tool")
    if not isinstance(internal_schema, Mapping) or not isinstance(arguments, Mapping):
        raise TypeError("Drawing target materialization requires schema and arguments")
    result = _materialize(
        document,
        str(tool_name),
        internal_schema,
        dict(arguments),
        (),
        dict(arguments),
    )
    if not isinstance(result, dict):
        raise NativeDrawingInternalTargetError(
            "The Drawing tool arguments did not materialize to one object."
        )
    return result


def _object(
    document: Any,
    target: Mapping[str, Any],
    *,
    expected_types: tuple[str, ...] = (),
) -> Any:
    name = str(target.get("object_name") or "")
    if not name:
        raise NativeDrawingInternalTargetError(
            "An internal Drawing state target has no object_name."
        )
    return resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": name},
        expected_types=expected_types,
    )


def _argument_object(
    document: Any,
    arguments: Mapping[str, Any],
    name: str,
    *,
    expected_types: tuple[str, ...] = (),
) -> Any:
    target = arguments.get(name)
    if not isinstance(target, Mapping):
        raise NativeDrawingInternalTargetError(
            f"Drawing arguments have no exact {name} target."
        )
    return _object(document, target, expected_types=expected_types)


def _page(document: Any, arguments: Mapping[str, Any]) -> Any:
    return _argument_object(
        document,
        arguments,
        "page",
        expected_types=("TechDraw::DrawPage",),
    )


def _view(document: Any, arguments: Mapping[str, Any]) -> Any:
    return _argument_object(
        document,
        arguments,
        "view",
        expected_types=("TechDraw::DrawView",),
    )


def _line_state(
    inventory: Mapping[str, Any],
    target: Mapping[str, Any],
    hash_name: str,
) -> str:
    kind = str(target.get("kind") or "")
    identity = str(target.get("tag") or target.get("subelement") or "")
    matches = [
        item
        for item in list(inventory.get("lines") or ())
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == kind
        and str(item.get("tag") or item.get("subelement") or "") == identity
    ]
    if len(matches) != 1:
        raise NativeDrawingInternalTargetError(
            "The requested Drawing line is absent from the current view inventory."
        )
    return str(matches[0].get(hash_name) or "")


def _standard_state_hash(
    document: Any,
    tool_name: str,
    path: tuple[str, ...],
    container: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str:
    scope = path[:-1]
    if scope == ("viewport",):
        from VibeCADNativeDrawingActiveView import drawing_active_viewport_state

        return str(drawing_active_viewport_state(document)["state_sha256"])
    obj = _object(
        document,
        container,
        expected_types=("TechDraw::DrawPage",) if scope == ("page",) else (),
    )
    if scope == ("page",):
        from VibeCADNativeDrawingState import drawing_page_state

        return str(drawing_page_state(obj)["state_sha256"])
    if scope[:2] == ("sources", "[]"):
        from VibeCADNativeDrawingViewState import drawing_source_state

        return str(drawing_source_state(obj)["state_sha256"])
    if scope[:2] == ("breaks", "[]"):
        from VibeCADNativeDrawingViewState import drawing_break_state

        return str(drawing_break_state(obj)["state_sha256"])
    if scope == ("source",) and tool_name == "drawing.draft_source_view":
        from VibeCADNativeDrawingDraftState import drawing_draft_source_state

        return str(drawing_draft_source_state(obj)["state_sha256"])
    if scope == ("profile",) and tool_name == "drawing.complex_section":
        from VibeCADNativeDrawingComplexSection import _profile_state

        return str(
            _profile_state(obj, str(arguments.get("projection_strategy") or ""))[
                "state_sha256"
            ]
        )
    if scope == ("balloon",):
        from VibeCADNativeDrawingBalloonState import drawing_balloon_state

        return str(drawing_balloon_state(obj)["state_sha256"])
    if scope == ("clip_group",):
        from VibeCADNativeDrawingClipState import drawing_clip_group_state

        return str(drawing_clip_group_state(obj)["state_sha256"])
    if scope[:3] == ("members", "[]", "view"):
        from VibeCADNativeDrawingClipState import drawing_clip_member_state

        return str(drawing_clip_member_state(obj)["state_sha256"])
    if scope[:2] == ("views", "[]") and tool_name == "drawing.stack":
        from VibeCADNativeDrawingStackState import drawing_stack_state

        return str(drawing_stack_state(obj)["state_sha256"])
    from VibeCADNativeDrawingViewState import drawing_source_state, drawing_view_state

    if bool(getattr(obj, "isDerivedFrom", lambda _name: False)("TechDraw::DrawView")):
        return str(drawing_view_state(obj)["state_sha256"])
    return str(drawing_source_state(obj)["state_sha256"])


def _resolve_internal_state_field(
    document: Any,
    tool_name: str,
    path: tuple[str, ...],
    container: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> str:
    field = path[-1]
    obj = None
    if field == "expected_state_sha256":
        digest = _standard_state_hash(
            document,
            tool_name,
            path,
            container,
            arguments,
        )
    elif field == "expected_projection_state_sha256":
        from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state

        digest = str(
            drawing_projected_geometry_state(
                _object(
                    document,
                    container,
                    expected_types=("TechDraw::DrawViewPart",),
                )
            )["projection_state_sha256"]
        )
    elif field == "expected_repair_state_sha256":
        from VibeCADNativeDrawingDimensionState import drawing_dimension_repair_state

        digest = str(drawing_dimension_repair_state(_object(document, container))[
            "repair_state_sha256"
        ])
    elif field == "expected_edit_state_sha256":
        from VibeCADNativeDrawingDimensionEditState import drawing_dimension_edit_state

        digest = str(drawing_dimension_edit_state(_object(document, container))[
            "edit_state_sha256"
        ])
    elif field == "expected_format_state_sha256":
        from VibeCADNativeDrawingFormatState import drawing_format_state

        digest = str(drawing_format_state(_object(document, container))[
            "format_state_sha256"
        ])
    elif field == "expected_owner_state_sha256":
        owner = _object(document, container)
        page = _page(document, arguments)
        if tool_name == "drawing.leader_line":
            from VibeCADNativeDrawingLeaderState import drawing_leader_owner_state

            digest = str(drawing_leader_owner_state(owner, page=page)[
                "owner_state_sha256"
            ])
        else:
            from VibeCADNativeDrawingRichAnnotationState import (
                drawing_rich_annotation_owner_state,
            )

            digest = str(drawing_rich_annotation_owner_state(owner, page=page)[
                "owner_state_sha256"
            ])
    elif field == "expected_inventory_state_sha256":
        if tool_name == "drawing.line_attributes":
            from VibeCADNativeDrawingLineAttributeState import (
                drawing_line_attribute_inventory_state,
            )

            digest = str(drawing_line_attribute_inventory_state(_view(document, arguments))[
                "inventory_state_sha256"
            ])
        elif tool_name == "drawing.line_length":
            from VibeCADNativeDrawingLineLengthState import (
                drawing_line_length_inventory_state,
            )

            digest = str(drawing_line_length_inventory_state(_view(document, arguments))[
                "inventory_state_sha256"
            ])
        else:
            from VibeCADNativeDrawingViewLockState import (
                drawing_view_lock_inventory_state,
            )

            digest = str(drawing_view_lock_inventory_state(_page(document, arguments))[
                "inventory_state_sha256"
            ])
    elif field == "expected_line_defaults_state_sha256":
        from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state

        digest = str(drawing_line_defaults_state()["state_sha256"])
    elif field == "expected_line_state_sha256":
        from VibeCADNativeDrawingLineAttributeState import (
            drawing_line_attribute_inventory_state,
        )

        digest = _line_state(
            drawing_line_attribute_inventory_state(_view(document, arguments)),
            container,
            "line_state_sha256",
        )
    elif field == "expected_line_length_state_sha256":
        from VibeCADNativeDrawingLineLengthState import (
            drawing_line_length_inventory_state,
        )

        digest = _line_state(
            drawing_line_length_inventory_state(_view(document, arguments)),
            container,
            "line_length_state_sha256",
        )
    elif field == "expected_frame_visibility_state_sha256":
        from VibeCADNativeDrawingPresentationState import drawing_frame_visibility_state

        digest = str(drawing_frame_visibility_state(_object(
            document,
            container,
            expected_types=("TechDraw::DrawPage",),
        ))[
            "frame_visibility_state_sha256"
        ])
    elif field == "expected_grid_visibility_state_sha256":
        from VibeCADNativeDrawingPresentationState import drawing_grid_visibility_state

        digest = str(drawing_grid_visibility_state(_object(
            document,
            container,
            expected_types=("TechDraw::DrawPage",),
        ))[
            "grid_visibility_state_sha256"
        ])
    elif field == "expected_hidden_edge_visibility_state_sha256":
        from VibeCADNativeDrawingPresentationState import (
            drawing_hidden_edge_visibility_state,
        )

        digest = str(drawing_hidden_edge_visibility_state(_object(
            document,
            container,
            expected_types=("TechDraw::DrawView",),
        ))[
            "hidden_edge_visibility_state_sha256"
        ])
    elif field == "expected_section_position_state_sha256":
        from VibeCADNativeDrawingSectionPositionState import (
            drawing_section_position_state,
        )

        digest = str(drawing_section_position_state(_object(document, container))[
            "section_position_state_sha256"
        ])
    elif field == "expected_alignment_base_state_sha256":
        from VibeCADNativeDrawingSectionPositionState import drawing_alignment_base_state

        digest = str(drawing_alignment_base_state(_object(document, container))[
            "alignment_base_state_sha256"
        ])
    elif field == "expected_view_lock_state_sha256":
        from VibeCADNativeDrawingViewLockState import drawing_view_lock_state

        digest = str(drawing_view_lock_state(_object(document, container))[
            "view_lock_state_sha256"
        ])
    elif field == "expected_placement_state_sha256":
        from VibeCADNativeDrawingDimensionState import is_drawing_dimension
        from VibeCADNativeDrawingPlacementState import (
            NativeDrawingPlacementStateError,
            drawing_dimension_label_placement_state,
            drawing_note_placement_state,
            drawing_view_placement_owner,
            drawing_view_placement_state,
            is_positionable_drawing_note,
        )

        target = _object(document, container)
        try:
            if tool_name == "drawing.place_views":
                target = drawing_view_placement_owner(target)
            state = (
                drawing_dimension_label_placement_state(target)
                if is_drawing_dimension(target)
                else drawing_note_placement_state(target)
                if is_positionable_drawing_note(target)
                else drawing_view_placement_state(target)
            )
        except (
            AttributeError,
            NativeDrawingPlacementStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            name = str(getattr(target, "Name", "") or "")
            raise NativeDrawingInternalTargetError(
                f"Drawing item {name!r} is not a page.views[].placement target."
            ) from exc
        digest = str(state["placement_state_sha256"])
    elif field == "expected_leader_state_sha256":
        from VibeCADNativeDrawingLeaderState import drawing_leader_state

        digest = str(drawing_leader_state(_object(document, container))[
            "leader_state_sha256"
        ])
    elif field == "expected_symbol_state_sha256":
        from VibeCADNativeDrawingSymbolState import drawing_weld_symbol_state

        digest = str(drawing_weld_symbol_state(_object(document, container))[
            "symbol_state_sha256"
        ])
    elif field == "expected_catalog_sha256":
        from VibeCADNativeDrawingSymbol import drawing_weld_catalog_state

        digest = str(drawing_weld_catalog_state()["catalog_sha256"])
    else:
        raise NativeDrawingInternalTargetError(
            f"Drawing tool {tool_name!r} has no resolver for {field!r}."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise NativeDrawingInternalTargetError(
            f"Drawing tool {tool_name!r} resolved an invalid {field!r}."
        )
    return digest


__all__ = [
    "NativeDrawingInternalTargetError",
    "materialize_drawing_internal_targets",
]
