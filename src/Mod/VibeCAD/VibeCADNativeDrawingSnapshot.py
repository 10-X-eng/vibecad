# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Drawing ribbon."""

from __future__ import annotations

import re
from typing import Any, Mapping

from VibeCADNativeDrawingActiveView import (
    drawing_active_view_image_state,
    safe_drawing_active_viewport_state,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingPresentationState import (
    NativeDrawingPresentationStateError,
    drawing_page_presentation_state,
    drawing_frame_visibility_state,
    drawing_grid_visibility_state,
    drawing_hidden_edge_visibility_state,
)
from VibeCADNativeDrawingHatch import drawing_hatch_defaults_state
from VibeCADNativeDrawingHatchState import (
    NativeDrawingHatchStateError,
    drawing_hatch_inventory_state,
)
from VibeCADNativeDrawingRichAnnotation import (
    drawing_rich_annotation_defaults_state,
)
from VibeCADNativeDrawingRichAnnotationState import (
    NativeDrawingRichAnnotationStateError,
    drawing_rich_annotation_owner_state,
    drawing_rich_annotation_state,
    is_drawing_rich_annotation,
)
from VibeCADNativeDrawingSymbol import drawing_weld_catalog_state
from VibeCADNativeDrawingSymbolState import (
    NativeDrawingSymbolStateError,
    drawing_surface_finish_symbol_state,
    drawing_weld_symbol_state,
    is_drawing_surface_finish_symbol,
    is_drawing_weld_symbol,
)
from VibeCADNativeDrawingLeader import drawing_leader_defaults_state
from VibeCADNativeDrawingLeaderState import (
    NativeDrawingLeaderStateError,
    drawing_leader_owner_state,
    drawing_leader_state,
    is_drawing_leader,
)
from VibeCADNativeDrawingDraftState import (
    drawing_draft_view_state,
    is_draft_drawing_view,
)
from VibeCADNativeDrawingClipState import (
    drawing_clip_group_state,
    drawing_clip_member_state,
    is_clip_drawing_view,
)
from VibeCADNativeDrawingStackState import drawing_stack_state
from VibeCADNativeDrawingGeometryState import (
    MAX_SELECTED_DRAWING_PROJECTED_ELEMENTS,
    NativeDrawingGeometryStateError,
    drawing_projected_geometry_state,
    selected_projected_geometry_state,
)
from VibeCADNativeDrawingProjectionGroup import projection_group_summary
from VibeCADNativeDrawingPlacementState import (
    NativeDrawingPlacementStateError,
    drawing_view_placement_state,
    is_positionable_drawing_view,
)
from VibeCADNativeDrawingLineAttributeState import (
    MAX_DRAWING_LINE_ATTRIBUTE_TARGETS,
    NativeDrawingLineAttributeStateError,
    drawing_line_attribute_inventory_state,
)
from VibeCADNativeDrawingLineDefaults import drawing_line_defaults_state
from VibeCADNativeDrawingLineLengthState import (
    NativeDrawingLineLengthStateError,
    drawing_line_length_inventory_state,
)
from VibeCADNativeDrawingViewLockState import (
    NativeDrawingViewLockStateError,
    drawing_view_lock_inventory_state,
    drawing_view_lock_state,
)
from VibeCADNativeDrawingSectionPositionState import (
    NativeDrawingSectionPositionStateError,
    drawing_section_position_state,
)
from VibeCADNativeDrawingDimensionState import (
    drawing_dimension_state,
    drawing_dimension_repair_state,
    drawing_extent_state,
    drawing_axonometric_dimension_state,
    is_drawing_axonometric_dimension,
    is_drawing_dimension,
    is_drawing_extent,
)
from VibeCADNativeDrawingDimensionEditState import drawing_dimension_edit_state
from VibeCADNativeDrawingSpecialDimensionState import (
    drawing_arc_length_dimension_state,
    drawing_chamfer_dimension_state,
    is_drawing_arc_length_dimension,
    is_drawing_chamfer_dimension,
)
from VibeCADNativeDrawingBalloonState import (
    drawing_balloon_state,
    is_drawing_balloon,
)
from VibeCADNativeDrawingMeasurementAnnotationState import (
    NativeDrawingMeasurementAnnotationStateError,
    drawing_measurement_annotation_state,
    is_drawing_measurement_annotation,
)
from VibeCADNativeDrawingFormatState import drawing_format_state
from VibeCADNativeDrawingSourceCatalog import drawing_source_catalog_state_page
from VibeCADNativeDrawingViewState import (
    MAX_DRAWING_BREAKS,
    MAX_DRAWING_VIEW_SOURCES,
    drawing_source_catalog_identity_state,
    drawing_view_state,
    is_drawing_view,
    is_part_drawing_view,
)
from VibeCADNativeGeometrySources import is_potential_design_geometry_source
from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_PAGES = 16
MAX_SELECTED_CLIP_GROUPS = 4
MAX_SELECTED_PROJECTED_VIEWS = 4
MAX_SELECTED_DIMENSIONS = 16
MAX_UNRESOLVED_DRAWING_REFERENCES = 16
MAX_DRAWING_SOURCES = 48
_PROJECTED_ELEMENT_NAME = re.compile(r"^(?:Edge|Vertex|Face)(?:0|[1-9][0-9]*)$")


def _line_defaults_summary() -> dict[str, Any] | None:
    try:
        state = drawing_line_defaults_state()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return {
        name: state[name]
        for name in (
            "state_sha256",
            "scope",
            "line_standard",
            "standards_body",
            "line_number",
            "style_code",
            "style_name",
            "width_mm",
            "width_choice",
            "available_widths",
            "color_rgb",
            "visible",
            "cascade_spacing_mm",
            "delta_distance_mm",
            "available_style_count",
            "valid",
            "issues",
        )
    }


def _hatch_defaults_summary() -> dict[str, Any] | None:
    try:
        return drawing_hatch_defaults_state()
    except (AttributeError, NativeDrawingError, RuntimeError, TypeError, ValueError):
        return None


def _rich_annotation_defaults_summary() -> dict[str, Any] | None:
    try:
        return drawing_rich_annotation_defaults_state()
    except (AttributeError, NativeDrawingError, RuntimeError, TypeError, ValueError):
        return None


def _weld_catalog_summary() -> dict[str, Any] | None:
    try:
        state = drawing_weld_catalog_state()
    except (AttributeError, NativeDrawingError, RuntimeError, TypeError, ValueError):
        return None
    return {
        "catalog_sha256": state["catalog_sha256"],
        "item_count": len(state["items"]),
    }


def _leader_defaults_summary() -> dict[str, Any] | None:
    try:
        return drawing_leader_defaults_state()
    except (AttributeError, NativeDrawingError, RuntimeError, TypeError, ValueError):
        return None


def _dimension_repair_summary(
    state: Mapping[str, Any],
    *,
    include_references: bool,
) -> dict[str, Any]:
    result = {
        "object_name": state["object_name"],
        "expected_repair_state_sha256": state["repair_state_sha256"],
        "repair_kind": state["repair_kind"],
        "dimension_type": state["dimension_type"],
        "measure_type": state["measure_type"],
        "page_name": state["page_name"],
        "repairable": state["repairable"],
        "timeline_usable": state["timeline_usable"],
        "valid": state["valid"],
        "error": state["error"],
        "issues": state["issues"],
    }
    if include_references:
        result["references_2d"] = state["references_2d"]
        result["references_3d"] = state["references_3d"]
    return result


def _view_summary(
    view: Any,
    projection_names_by_view: dict[str, frozenset[str] | None],
    line_inventories_by_view: dict[str, dict[str, Any] | None],
    line_length_inventories_by_view: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    result = concise_object(view)
    if is_positionable_drawing_view(view):
        try:
            placement = drawing_view_placement_state(view)
            result["placement"] = {
                "placement_target": {"object_name": placement["object_name"]},
                "position_on_page_mm": placement["position_on_page_mm"],
                "locked": placement["locked"],
            }
        except (
            AttributeError,
            NativeDrawingPlacementStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if str(getattr(view, "TypeId", "") or "") == "TechDraw::DrawProjGroup":
        result["projection_group"] = projection_group_summary(view)
    if is_drawing_surface_finish_symbol(view):
        try:
            result["surface_finish_symbol"] = drawing_surface_finish_symbol_state(view)
        except (
            AttributeError,
            NativeDrawingSymbolStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if is_drawing_weld_symbol(view):
        try:
            result["weld_symbol"] = drawing_weld_symbol_state(view)
        except (
            AttributeError,
            NativeDrawingSymbolStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if is_drawing_rich_annotation(view):
        try:
            state = drawing_rich_annotation_state(view)
            result["rich_annotation"] = {
                name: state[name]
                for name in (
                    "annotation_state_sha256",
                    "page_name",
                    "owner",
                    "content",
                    "placement_on_page_mm",
                    "width",
                    "frame",
                    "timeline_usable",
                    "valid",
                )
            }
        except (
            AttributeError,
            NativeDrawingRichAnnotationStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if is_drawing_leader(view):
        try:
            state = drawing_leader_state(view)
            result["leader"] = {
                name: state[name]
                for name in (
                    "leader_state_sha256",
                    "page_name",
                    "owner",
                    "point_count",
                    "anchor_on_page_mm",
                    "rendered_points_on_page_mm",
                    "rendered_points_sha256",
                    "symbols",
                    "behavior",
                    "line",
                    "timeline_usable",
                    "valid",
                )
            }
        except (
            AttributeError,
            NativeDrawingLeaderStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if is_drawing_measurement_annotation(view):
        try:
            state = drawing_measurement_annotation_state(view)
            result["measurement_annotation"] = {
                name: state[name]
                for name in (
                    "measurement_state_sha256",
                    "source_view_name",
                    "kind",
                    "unit",
                    "value",
                    "current_source_value",
                    "measurement_current",
                    "source_elements",
                    "anchor_in_source_mm",
                    "derived_anchor_in_source_mm",
                    "anchor_matches_source",
                    "bubble_offset_in_view_mm",
                    "default_placement",
                    "text",
                    "style",
                    "timeline_usable",
                    "valid",
                )
            }
        except (
            AttributeError,
            NativeDrawingMeasurementAnnotationStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    elif is_drawing_balloon(view):
        try:
            state = drawing_balloon_state(view)
            result["balloon"] = {
                name: state[name]
                for name in (
                    "state_sha256",
                    "source_view_name",
                    "anchor",
                    "bubble_offset_in_view_mm",
                    "text",
                    "style",
                    "timeline_usable",
                    "valid",
                )
            }
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    if is_drawing_dimension(view):
        repair_state = drawing_dimension_repair_state(
            view,
            projection_names_by_view=projection_names_by_view,
        )
        try:
            state = (
                drawing_extent_state(view)
                if is_drawing_extent(view)
                else drawing_axonometric_dimension_state(view)
                if is_drawing_axonometric_dimension(view)
                else drawing_arc_length_dimension_state(view)
                if is_drawing_arc_length_dimension(view)
                else drawing_chamfer_dimension_state(view)
                if is_drawing_chamfer_dimension(view)
                else drawing_dimension_state(view)
            )
            detail_fields = (
                ("target", "extent_direction")
                if is_drawing_extent(view)
                else ("references", "axonometric")
                if is_drawing_axonometric_dimension(view)
                else ("arc_length",)
                if is_drawing_arc_length_dimension(view)
                else ("references", "chamfer")
                if is_drawing_chamfer_dimension(view)
                else ("references",)
            )
            result["dimension"] = {
                name: state[name]
                for name in (
                    "state_sha256",
                    "dimension_type",
                    "measure_type",
                    *detail_fields,
                    "label_position_in_view_mm",
                    "measured_value",
                    "formatted_text",
                    "timeline_usable",
                    "valid",
                )
            }
            result["dimension"]["repair_target"] = _dimension_repair_summary(
                repair_state,
                include_references=False,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            result["dimension_repair"] = _dimension_repair_summary(
                repair_state,
                include_references=True,
            )
    if is_drawing_view(view):
        try:
            owner_state = drawing_rich_annotation_owner_state(view)
            result["annotation_owner"] = {
                "owner_state_sha256": owner_state["owner_state_sha256"],
                "timeline_usable": owner_state["timeline_usable"],
                "valid": owner_state["valid"],
            }
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        try:
            owner_state = drawing_leader_owner_state(view)
            result["leader_owner"] = {
                name: owner_state[name]
                for name in (
                    "owner_state_sha256",
                    "position_on_page_mm",
                    "scale",
                    "rotation_degrees",
                    "timeline_usable",
                    "valid",
                )
            }
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        try:
            state = drawing_stack_state(view)
            result["stack"] = {
                name: state[name]
                for name in (
                    "state_sha256",
                    "stack_order",
                    "scope_kind",
                    "scope_item_count",
                    "available",
                )
            }
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            pass
    if is_clip_drawing_view(view):
        try:
            state = drawing_clip_group_state(view)
            result["clip_group"] = {
                name: state[name]
                for name in (
                    "state_sha256",
                    "position_on_page_mm",
                    "frame",
                    "member_count",
                    "timeline_usable",
                    "valid",
                )
            }
            result["clip_group"]["member_names"] = [
                member["object_name"] for member in state["members"]
            ]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    elif is_drawing_view(view):
        try:
            result["clip_member"] = drawing_clip_member_state(view)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    if is_part_drawing_view(view):
        try:
            hatch_inventory = drawing_hatch_inventory_state(view)
            result["hatches"] = {
                "inventory_state_sha256": hatch_inventory["inventory_state_sha256"],
                "hatch_count": hatch_inventory["hatch_count"],
                "items": [
                    {
                        name: hatch[name]
                        for name in (
                            "object_name",
                            "label",
                            "kind",
                            "faces",
                            "pattern",
                            "style",
                            "timeline_usable",
                            "valid",
                            "hatch_state_sha256",
                        )
                    }
                    for hatch in hatch_inventory["hatches"]
                ],
            }
        except (
            AttributeError,
            NativeDrawingHatchStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
        if str(getattr(view, "TypeId", "")) == "TechDraw::DrawViewSection":
            try:
                position_state = drawing_section_position_state(view)
                alignment = position_state["alignment_base"]
                result["section_position"] = {
                    "section_position_state_sha256": position_state[
                        "section_position_state_sha256"
                    ],
                    "base_view_name": position_state["base_view_name"],
                    "alignment_base": {
                        "object_name": alignment["object_name"],
                        "type_id": alignment["type_id"],
                        "position_on_page_mm": alignment["position_on_page_mm"],
                        "alignment_base_state_sha256": alignment[
                            "alignment_base_state_sha256"
                        ],
                    },
                    "position_on_page_mm": position_state["position_on_page_mm"],
                    "locked": position_state["locked"],
                    "timeline_usable": position_state["timeline_usable"],
                    "valid": position_state["valid"],
                }
            except (
                AttributeError,
                NativeDrawingSectionPositionStateError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                pass
        try:
            lock_state = drawing_view_lock_state(view)
            result["view_lock"] = {
                name: lock_state[name]
                for name in (
                    "view_lock_state_sha256",
                    "page_name",
                    "position_on_page_mm",
                    "locked",
                    "timeline_usable",
                    "valid",
                )
            }
        except (
            AttributeError,
            NativeDrawingViewLockStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
        try:
            state = drawing_view_state(view)
            result["state_sha256"] = state["state_sha256"]
            result["visible_edge_count"] = state["visible_edge_count"]
            result["hidden_edge_count"] = state["hidden_edge_count"]
            if "section" in state:
                result["section"] = state["section"]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        try:
            result["hidden_edge_visibility"] = drawing_hidden_edge_visibility_state(
                view
            )
        except (
            AttributeError,
            ImportError,
            NativeDrawingPresentationStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
        try:
            inventory = _line_inventory(view, line_inventories_by_view)
            if inventory is not None:
                line_summary = {
                    name: inventory[name]
                    for name in (
                        "inventory_state_sha256",
                        "line_count",
                        "projected_edge_count",
                        "cosmetic_edge_count",
                        "centerline_count",
                        "valid",
                        "issues",
                    )
                }
                if inventory["line_count"]:
                    line_summary["projection_state_sha256"] = (
                        drawing_projected_geometry_state(view)[
                            "projection_state_sha256"
                        ]
                    )
                result["line_attributes"] = line_summary
        except (
            AttributeError,
            NativeDrawingGeometryStateError,
            NativeDrawingLineAttributeStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
        try:
            length_inventory = _line_length_inventory(
                view,
                line_length_inventories_by_view,
            )
            if length_inventory is not None:
                result["line_lengths"] = {
                    name: length_inventory[name]
                    for name in (
                        "coordinate_space",
                        "axis_convention",
                        "inventory_state_sha256",
                        "line_count",
                        "cosmetic_edge_count",
                        "centerline_count",
                        "valid",
                        "issues",
                    )
                }
        except (
            AttributeError,
            NativeDrawingLineLengthStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            pass
    if is_draft_drawing_view(view):
        try:
            state = drawing_draft_view_state(view)
            result["state_sha256"] = state["state_sha256"]
            result["draft_source"] = state["source"]
            result["svg_bytes"] = state["svg_bytes"]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    try:
        if bool(view.isDerivedFrom("TechDraw::DrawViewImage")):
            result["active_view_image"] = drawing_active_view_image_state(view)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass
    # DrawViewDimExtent.Source is a LinkSubList target descriptor, not the
    # ordinary list of source objects used by projected drawing views. Its
    # exact target is already reported in the dimension block above.
    if not is_drawing_dimension(view):
        sources = list(getattr(view, "Source", []) or [])
        if sources:
            result["sources"] = [concise_object(value) for value in sources[:12]]
    for name in ("X", "Y", "Scale"):
        if hasattr(view, name):
            try:
                result[name.lower()] = float(getattr(view, name))
            except Exception:
                continue
    return result


def _page_summary(
    page: Any,
    projection_names_by_view: dict[str, frozenset[str] | None],
    line_inventories_by_view: dict[str, dict[str, Any] | None],
    line_length_inventories_by_view: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    state = drawing_page_state(page)
    result = {
        name: state[name]
        for name in (
            "object_name",
            "label",
            "type_id",
            "state_sha256",
            "keep_updated",
            "projection_type",
            "scale",
            "view_count",
            "template",
            "template_geometry",
            "template_content",
            "editable_field_count",
            "editable_fields_supported",
        )
    }
    views = list(getattr(page, "Views", []) or [])
    checker = getattr(page.Document, "isObjectUsableAtCurrentTimelinePosition", None)
    timeline_usable = bool(not callable(checker) or checker(page))
    state_messages = [
        str(value or "").strip()[:256]
        for value in tuple(getattr(page, "State", ()) or ())
        if str(value or "").strip()
    ][:16]
    unresolved = []
    for view in views:
        if not is_drawing_dimension(view):
            continue
        try:
            dimension_state = drawing_dimension_repair_state(
                view,
                projection_names_by_view=projection_names_by_view,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            unresolved.append(
                {
                    "object_name": str(getattr(view, "Name", "") or ""),
                    "issues": ["Dimension reference state is unavailable."],
                }
            )
            continue
        if dimension_state["valid"] and not dimension_state["issues"]:
            continue
        unresolved.append(
            {
                "object_name": dimension_state["object_name"],
                "issues": dimension_state["issues"],
            }
        )
    template = getattr(page, "Template", None)
    output_issues = []
    if not timeline_usable:
        output_issues.append("Page is unavailable at the current History position.")
    if not bool(page.isValid()):
        output_issues.append("Page is invalid.")
    if template is None or getattr(template, "Document", None) is not page.Document:
        output_issues.append("Page has no live template.")
    if int(page.Document.getBookedTransactionID()) != 0:
        output_issues.append("A document transaction is open.")
    result["update_status"] = {
        "keep_updated": state["keep_updated"],
        "current": bool(not state_messages and page.isValid()),
        "state_messages": state_messages,
    }
    result["unresolved_references"] = unresolved[:MAX_UNRESOLVED_DRAWING_REFERENCES]
    if len(unresolved) > MAX_UNRESOLVED_DRAWING_REFERENCES:
        result["unresolved_references_truncated"] = True
    result["export_readiness"] = {
        "ready": not output_issues,
        "issues": output_issues,
    }
    try:
        lock_inventory = drawing_view_lock_inventory_state(page)
        result["view_locks"] = {
            name: lock_inventory[name]
            for name in (
                "inventory_state_sha256",
                "view_count",
                "locked_count",
                "unlocked_count",
                "valid",
                "issues",
            )
        }
    except (
        AttributeError,
        NativeDrawingViewLockStateError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        pass
    result["views"] = [
        _view_summary(
            value,
            projection_names_by_view,
            line_inventories_by_view,
            line_length_inventories_by_view,
        )
        for value in views[:48]
    ]
    return result


def _active_page_state(page: Any) -> dict[str, Any]:
    state = drawing_page_state(page)
    try:
        state["presentation"] = drawing_page_presentation_state(page)
    except (
        AttributeError,
        ImportError,
        NativeDrawingPresentationStateError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        pass
    try:
        state["frame_visibility"] = drawing_frame_visibility_state(page)
    except (
        AttributeError,
        ImportError,
        NativeDrawingPresentationStateError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        pass
    try:
        state["grid_visibility"] = drawing_grid_visibility_state(page)
    except (
        AttributeError,
        ImportError,
        NativeDrawingPresentationStateError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        pass
    return state


_COMPACT_VIEW_KEYS = (
    "document_uid",
    "object_name",
    "type_id",
    "label",
    "state",
    "state_sha256",
    "placement",
    "x",
    "y",
    "scale",
)
_COMPACT_PRIMARY_VIEW_DETAILS = (
    "projection_group",
    "surface_finish_symbol",
    "weld_symbol",
    "rich_annotation",
    "leader",
    "measurement_annotation",
    "balloon",
    "dimension",
    "dimension_repair",
    "clip_group",
    "active_view_image",
    # These owner/readiness states are fallbacks for drawing objects without
    # their own primary revision block.
    "annotation_owner",
    "leader_owner",
    "stack",
    "clip_member",
    "hatches",
    "section_position",
    "view_lock",
    "hidden_edge_visibility",
    "line_attributes",
    "line_lengths",
)
_COMPACT_DETAIL_SCALARS = frozenset(
    {
        "kind",
        "dimension_type",
        "measure_type",
        "source_view_name",
        "page_name",
        "object_name",
        "view_count",
        "member_count",
        "line_count",
        "hatch_count",
        "measured_value",
        "formatted_text",
        "timeline_usable",
        "valid",
        "available",
        "locked",
        "repairable",
        "error",
    }
)


def _compact_view_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Keep revision and readiness facts while deferring repeated view detail."""

    result = {}
    for name, value in detail.items():
        if name.endswith("sha256") or name in _COMPACT_DETAIL_SCALARS:
            result[str(name)] = value
    return result


def compact_drawing_snapshot_for_bound(
    domain: Mapping[str, Any],
) -> dict[str, Any]:
    """Compact repeated page-view detail after an active snapshot exceeds its budget.

    The complete detail remains in selected target blocks and in explicit Drawing
    read tools. Small snapshots never call this function and retain their established
    shape verbatim.
    """

    result = dict(domain)
    pages = []
    compacted = False
    for raw_page in list(domain.get("pages") or []):
        if not isinstance(raw_page, Mapping):
            pages.append(raw_page)
            continue
        page = dict(raw_page)
        views = []
        page_compacted = False
        for raw_view in list(raw_page.get("views") or []):
            if not isinstance(raw_view, Mapping):
                views.append(raw_view)
                continue
            view = {
                name: raw_view[name]
                for name in _COMPACT_VIEW_KEYS
                if name in raw_view
            }
            # Projected views already carry their authoritative state hash at
            # the top level. Other objects (dimensions, annotations, symbols)
            # keep one primary revision/readiness block. Repeating every
            # secondary inventory hash is what made one ordinary page consume
            # almost the entire active-snapshot allowance.
            if "state_sha256" not in view:
                for name in _COMPACT_PRIMARY_VIEW_DETAILS:
                    detail = raw_view.get(name)
                    if not isinstance(detail, Mapping):
                        continue
                    compact_detail = _compact_view_detail(detail)
                    if compact_detail:
                        view[name] = compact_detail
                        break
            views.append(view)
            view_compacted = view != dict(raw_view)
            page_compacted = page_compacted or view_compacted
            compacted = compacted or view_compacted
        page["views"] = views
        if views and page_compacted:
            page["views_detail_deferred"] = True
        pages.append(page)
    result["pages"] = pages
    if compacted:
        result["snapshot_compacted"] = True
        result["deferred_details"] = ["pages.views"]
    return result


def _line_inventory(
    view: Any,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    name = str(getattr(view, "Name", "") or "")
    if name not in cache:
        try:
            cache[name] = drawing_line_attribute_inventory_state(view)
        except (
            AttributeError,
            NativeDrawingLineAttributeStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            cache[name] = None
    return cache[name]


def _line_length_inventory(
    view: Any,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    name = str(getattr(view, "Name", "") or "")
    if name not in cache:
        try:
            cache[name] = drawing_line_length_inventory_state(view)
        except (
            AttributeError,
            NativeDrawingLineLengthStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            cache[name] = None
    return cache[name]


def _selection_names(selection: Mapping[str, Any] | None) -> tuple[str, ...]:
    result = []
    for item in list((selection or {}).get("items") or []):
        reference = item.get("object") if isinstance(item, Mapping) else None
        name = (
            str(reference.get("object_name") or "")
            if isinstance(reference, Mapping)
            else ""
        )
        if name and name not in result:
            result.append(name)
    return tuple(result)


def _selected_pages(
    document: Any,
    pages: list[Any],
    selection: Mapping[str, Any] | None,
) -> tuple[Any, ...]:
    matched = []
    for name in _selection_names(selection)[:MAX_DRAWING_VIEW_SOURCES]:
        selected = document.getObject(name)
        if selected is None:
            continue
        for page in pages:
            if (
                selected is page
                or selected is getattr(page, "Template", None)
                or selected in tuple(getattr(page, "Views", ()) or ())
            ) and page not in matched:
                matched.append(page)
    return tuple(matched)


def _selected_sources(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection):
        selected = document.getObject(name)
        if selected is None or not is_potential_design_geometry_source(
            document,
            selected,
        ):
            continue
        try:
            result.append(drawing_source_catalog_identity_state(selected))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _drawing_sources(
    document: Any,
    *,
    structural_revision: int | None = None,
    detached_sources: list[Mapping[str, Any]] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Return each active design shape once at its public Body boundary."""

    if detached_sources is not None:
        sources = [dict(source) for source in detached_sources]
        return len(sources), sources[:MAX_DRAWING_SOURCES]
    page = drawing_source_catalog_state_page(
        document,
        offset=0,
        page_size=MAX_DRAWING_SOURCES,
        structural_revision=structural_revision,
    )
    return int(page["source_count"]), [dict(source) for source in page["sources"]]


def _selected_break_definitions(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_DRAWING_BREAKS]:
        selected = document.getObject(name)
        if selected is None or not is_potential_design_geometry_source(
            document,
            selected,
        ):
            continue
        try:
            state = drawing_source_catalog_identity_state(selected)
            state["break_details_deferred"] = True
            result.append(state)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_draft_sources(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_DRAWING_VIEW_SOURCES]:
        selected = document.getObject(name)
        if selected is None or not is_potential_design_geometry_source(
            document,
            selected,
        ):
            continue
        try:
            state = drawing_source_catalog_identity_state(selected)
            state["draft_details_deferred"] = True
            result.append(state)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_clip_groups(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_CLIP_GROUPS]:
        selected = document.getObject(name)
        if selected is None or not is_clip_drawing_view(selected):
            continue
        try:
            result.append(drawing_clip_group_state(selected))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_clip_views(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_DRAWING_VIEW_SOURCES]:
        selected = document.getObject(name)
        if (
            selected is None
            or not is_drawing_view(selected)
            or is_clip_drawing_view(selected)
        ):
            continue
        try:
            result.append(drawing_clip_member_state(selected))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_stack_views(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_DRAWING_VIEW_SOURCES]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_view(selected):
            continue
        try:
            result.append(drawing_stack_state(selected))
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_dimensions(
    document: Any,
    selection: Mapping[str, Any] | None,
    projection_names_by_view: dict[str, frozenset[str] | None],
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_dimension(selected):
            continue
        repair_state = drawing_dimension_repair_state(
            selected,
            projection_names_by_view=projection_names_by_view,
        )
        try:
            state = (
                drawing_extent_state(selected)
                if is_drawing_extent(selected)
                else drawing_axonometric_dimension_state(selected)
                if is_drawing_axonometric_dimension(selected)
                else drawing_arc_length_dimension_state(selected)
                if is_drawing_arc_length_dimension(selected)
                else drawing_chamfer_dimension_state(selected)
                if is_drawing_chamfer_dimension(selected)
                else drawing_dimension_state(selected)
            )
            state["repair_target"] = _dimension_repair_summary(
                repair_state,
                include_references=False,
            )
            state["edit_target"] = drawing_dimension_edit_state(selected)
            result.append(state)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            result.append(
                {
                    "object_name": repair_state["object_name"],
                    "label": repair_state["label"],
                    "type_id": repair_state["type_id"],
                    "repair_target": _dimension_repair_summary(
                        repair_state,
                        include_references=True,
                    ),
                }
            )
    return result


def _selected_balloons(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if (
            selected is None
            or not is_drawing_balloon(selected)
            or is_drawing_measurement_annotation(selected)
        ):
            continue
        try:
            result.append(drawing_balloon_state(selected))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_measurement_annotations(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_measurement_annotation(selected):
            continue
        try:
            result.append(drawing_measurement_annotation_state(selected))
        except (
            AttributeError,
            NativeDrawingMeasurementAnnotationStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result


def _selected_rich_annotations(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_rich_annotation(selected):
            continue
        try:
            result.append(drawing_rich_annotation_state(selected))
        except (
            AttributeError,
            NativeDrawingRichAnnotationStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result


def _selected_leaders(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_leader(selected):
            continue
        try:
            result.append(drawing_leader_state(selected))
        except (
            AttributeError,
            NativeDrawingLeaderStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result


def _selected_engineering_symbols(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        try:
            if selected is not None and is_drawing_surface_finish_symbol(selected):
                result.append(
                    {
                        "kind": "surface_finish",
                        **drawing_surface_finish_symbol_state(selected),
                    }
                )
            elif selected is not None and is_drawing_weld_symbol(selected):
                result.append({"kind": "weld", **drawing_weld_symbol_state(selected)})
        except (
            AttributeError,
            NativeDrawingSymbolStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result


def _selected_leader_owners(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not is_drawing_view(selected):
            continue
        try:
            result.append(drawing_leader_owner_state(selected))
        except (
            AttributeError,
            NativeDrawingLeaderStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result


def _selected_format_targets(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    result = []
    for name in _selection_names(selection)[:MAX_SELECTED_DIMENSIONS]:
        selected = document.getObject(name)
        if selected is None or not (
            is_drawing_dimension(selected) or is_drawing_balloon(selected)
        ):
            continue
        try:
            result.append(drawing_format_state(selected))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return result


def _selected_projected_geometry(
    document: Any,
    selection: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool]:
    grouped: dict[str, list[str]] = {}
    selected_count = 0
    truncated = False
    for item in list((selection or {}).get("items") or []):
        if not isinstance(item, Mapping):
            continue
        reference = item.get("object")
        if not isinstance(reference, Mapping):
            continue
        name = str(reference.get("object_name") or "")
        view = document.getObject(name) if name else None
        if view is None or not is_part_drawing_view(view):
            continue
        projected_names = []
        for raw_name in list(item.get("subelements") or []):
            subelement = str(raw_name or "")
            if (
                _PROJECTED_ELEMENT_NAME.fullmatch(subelement) is not None
                and subelement not in projected_names
            ):
                projected_names.append(subelement)
        if not projected_names:
            continue
        if name not in grouped and len(grouped) >= MAX_SELECTED_PROJECTED_VIEWS:
            truncated = True
            continue
        accepted = grouped.setdefault(name, [])
        for subelement in projected_names:
            if subelement in accepted:
                continue
            if selected_count >= MAX_SELECTED_DRAWING_PROJECTED_ELEMENTS:
                truncated = True
                continue
            accepted.append(subelement)
            selected_count += 1

    result = []
    for name, subelements in grouped.items():
        view = document.getObject(name)
        if view is None:
            continue
        try:
            result.append(selected_projected_geometry_state(view, tuple(subelements)))
        except (
            AttributeError,
            NativeDrawingGeometryStateError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return result, truncated


def _selected_line_attributes(
    document: Any,
    selection: Mapping[str, Any] | None,
    cache: dict[str, dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], bool]:
    groups = []
    for item in list((selection or {}).get("items") or []):
        if not isinstance(item, Mapping):
            continue
        reference = item.get("object")
        if not isinstance(reference, Mapping):
            continue
        name = str(reference.get("object_name") or "")
        view = document.getObject(name) if name else None
        if view is None or not is_part_drawing_view(view):
            continue
        inventory = _line_inventory(view, cache)
        if inventory is None:
            continue
        requested = {str(value or "") for value in list(item.get("subelements") or [])}
        selected = [
            line for line in inventory["lines"] if line["subelement"] in requested
        ]
        if selected:
            groups.append((inventory, selected))

    result = []
    selected_count = 0
    total_count = sum(len(selected) for _inventory, selected in groups)
    for inventory, selected in groups:
        remaining = MAX_DRAWING_LINE_ATTRIBUTE_TARGETS - selected_count
        accepted = selected[: max(remaining, 0)]
        if accepted:
            result.append(
                {
                    "view_object_name": inventory["view_object_name"],
                    "inventory_state_sha256": inventory["inventory_state_sha256"],
                    "selected_lines": accepted,
                }
            )
            selected_count += len(accepted)
        if selected_count >= MAX_DRAWING_LINE_ATTRIBUTE_TARGETS:
            break
    return result, total_count > MAX_DRAWING_LINE_ATTRIBUTE_TARGETS


def _selected_line_lengths(
    document: Any,
    selection: Mapping[str, Any] | None,
    cache: dict[str, dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], bool]:
    groups = []
    for item in list((selection or {}).get("items") or []):
        if not isinstance(item, Mapping):
            continue
        reference = item.get("object")
        if not isinstance(reference, Mapping):
            continue
        name = str(reference.get("object_name") or "")
        view = document.getObject(name) if name else None
        if view is None or not is_part_drawing_view(view):
            continue
        inventory = _line_length_inventory(view, cache)
        if inventory is None:
            continue
        requested = {str(value or "") for value in list(item.get("subelements") or [])}
        selected = [
            line for line in inventory["lines"] if line["subelement"] in requested
        ]
        if selected:
            groups.append((inventory, selected))

    result = []
    selected_count = 0
    total_count = sum(len(selected) for _inventory, selected in groups)
    for inventory, selected in groups:
        remaining = MAX_DRAWING_LINE_ATTRIBUTE_TARGETS - selected_count
        accepted = selected[: max(remaining, 0)]
        if accepted:
            result.append(
                {
                    "view_object_name": inventory["view_object_name"],
                    "coordinate_space": inventory["coordinate_space"],
                    "axis_convention": inventory["axis_convention"],
                    "inventory_state_sha256": inventory["inventory_state_sha256"],
                    "selected_lines": accepted,
                }
            )
            selected_count += len(accepted)
        if selected_count >= MAX_DRAWING_LINE_ATTRIBUTE_TARGETS:
            break
    return result, total_count > MAX_DRAWING_LINE_ATTRIBUTE_TARGETS


def build_drawing_snapshot(
    document: Any,
    *,
    selection: Mapping[str, Any] | None = None,
    structural_revision: int | None = None,
    detached_sources: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    pages = objects_of_type(document, "TechDraw::DrawPage")
    source_count, sources = _drawing_sources(
        document,
        structural_revision=structural_revision,
        detached_sources=detached_sources,
    )
    selected = _selected_pages(document, pages, selection)
    if len(selected) == 1:
        active = selected[0]
        resolution = "selection"
    elif len(selected) > 1:
        active = None
        resolution = "ambiguous_selection"
    elif len(pages) == 1:
        active = pages[0]
        resolution = "only_page"
    elif pages:
        active = None
        resolution = "choose_page"
    else:
        active = None
        resolution = "no_page"
    selected_projected_geometry, projected_geometry_truncated = (
        _selected_projected_geometry(document, selection)
    )
    projection_names_by_view: dict[str, frozenset[str] | None] = {}
    line_inventories_by_view: dict[str, dict[str, Any] | None] = {}
    line_length_inventories_by_view: dict[str, dict[str, Any] | None] = {}
    selected_line_attributes, line_attributes_truncated = _selected_line_attributes(
        document,
        selection,
        line_inventories_by_view,
    )
    selected_line_lengths, line_lengths_truncated = _selected_line_lengths(
        document,
        selection,
        line_length_inventories_by_view,
    )
    result = {
        "kind": "drawing",
        "line_defaults": _line_defaults_summary(),
        "hatch_defaults": _hatch_defaults_summary(),
        "rich_annotation_defaults": _rich_annotation_defaults_summary(),
        "weld_symbol_catalog": _weld_catalog_summary(),
        "leader_defaults": _leader_defaults_summary(),
        "source_count": source_count,
        "sources": sources,
        "page_count": len(pages),
        "pages": [
            _page_summary(
                value,
                projection_names_by_view,
                line_inventories_by_view,
                line_length_inventories_by_view,
            )
            for value in pages[:MAX_PAGES]
        ],
        "active_page_resolution": resolution,
        "active_page": _active_page_state(active) if active is not None else None,
        "selected_sources": _selected_sources(document, selection),
        "selected_break_definitions": _selected_break_definitions(
            document,
            selection,
        ),
        "selected_draft_sources": _selected_draft_sources(document, selection),
        "selected_clip_groups": _selected_clip_groups(document, selection),
        "selected_clip_views": _selected_clip_views(document, selection),
        "selected_stack_views": _selected_stack_views(document, selection),
        "selected_dimensions": _selected_dimensions(
            document,
            selection,
            projection_names_by_view,
        ),
        "selected_balloons": _selected_balloons(document, selection),
        "selected_measurement_annotations": _selected_measurement_annotations(
            document,
            selection,
        ),
        "selected_rich_annotations": _selected_rich_annotations(
            document,
            selection,
        ),
        "selected_leaders": _selected_leaders(document, selection),
        "selected_engineering_symbols": _selected_engineering_symbols(
            document, selection
        ),
        "selected_leader_owners": _selected_leader_owners(
            document,
            selection,
        ),
        "selected_format_targets": _selected_format_targets(
            document,
            selection,
        ),
        "selected_projected_geometry": selected_projected_geometry,
        "selected_line_attributes": selected_line_attributes,
        "selected_line_lengths": selected_line_lengths,
        "active_3d_viewport": safe_drawing_active_viewport_state(document),
    }
    if len(pages) > MAX_PAGES:
        result["pages_truncated"] = True
    if source_count > len(sources):
        result["sources_truncated"] = True
        result["source_next_offset"] = len(sources)
    if projected_geometry_truncated:
        result["selected_projected_geometry_truncated"] = True
    if line_attributes_truncated:
        result["selected_line_attributes_truncated"] = True
    if line_lengths_truncated:
        result["selected_line_lengths_truncated"] = True
    return result
