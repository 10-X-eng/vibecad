# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Drawing engineering-symbol operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingLeaderState import drawing_leader_state
from VibeCADNativeDrawingRichAnnotationState import (
    drawing_rich_annotation_owner_state,
)
from VibeCADNativeDrawingState import drawing_page_invariants, drawing_page_state
from VibeCADNativeDrawingSymbolState import (
    drawing_surface_finish_symbol_state,
    drawing_weld_symbol_state,
)
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity, resolve_object


_HOST_ERROR = "NATIVE_DRAWING_SYMBOL_RUNTIME_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PreparedDrawingSymbolTarget:
    page: Any
    page_state_before: dict[str, Any]
    page_invariants_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    owner: Any | None = None
    owner_state_before: dict[str, Any] | None = None
    leader: Any | None = None
    leader_state_before: dict[str, Any] | None = None
    symbol: Any | None = None
    symbol_state_before: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PreparedDrawingSymbol:
    operation: str
    target: PreparedDrawingSymbolTarget
    spec: dict[str, Any]
    host_plan: dict[str, Any]
    catalog: dict[str, Any] | None = None


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _finite(value: Any, noun: str) -> float:
    if isinstance(value, bool):
        _error(f"Drawing symbol {noun} must be numeric.", "NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing symbol {noun} must be numeric.",
            error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
        ) from exc
    if not math.isfinite(result) or abs(result) > 1_000_000.0:
        _error(
            f"Drawing symbol {noun} is outside its documented range.",
            "NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
        )
    return round(result, 12)


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        _error(
            f"The exact {noun} is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _page_target(document: Any, raw: Any) -> tuple[Any, dict[str, Any]]:
    exact = exact_drawing_mapping(
        raw,
        frozenset({"object_name", "expected_state_sha256"}),
        "page target",
        family="engineering symbol",
        error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
    )
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    return page, state


def _base_target(page: Any, page_state: dict[str, Any]) -> dict[str, Any]:
    document = page.Document
    return {
        "page": page,
        "page_state_before": page_state,
        "page_invariants_before": drawing_page_invariants(page),
        "page_views_before": tuple(page.Views or ()),
        "objects_before": tuple(document.Objects),
        "timeline_before": drawing_timeline_operations(document),
        "selection_before": drawing_selection_state(document),
        "visibility_before": drawing_visibility_state(document),
    }


def _surface_target(document: Any, values: Mapping[str, Any]) -> PreparedDrawingSymbolTarget:
    page, page_state = _page_target(document, values["page"])
    raw = values["owner"]
    if not isinstance(raw, Mapping):
        _error("Drawing symbol owner is malformed.", "NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID")
    kind = str(raw.get("kind") or "")
    fields = (
        frozenset({"kind"})
        if kind == "page"
        else frozenset({"kind", "object_name", "expected_owner_state_sha256"})
    )
    exact = exact_drawing_mapping(
        raw,
        fields,
        "owner target",
        family="engineering symbol",
        error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
    )
    owner = None
    owner_state = None
    if kind == "view":
        owner = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
            expected_types=("TechDraw::DrawView",),
        )
        owner_state = drawing_rich_annotation_owner_state(owner, page=page)
        if str(exact["expected_owner_state_sha256"]) != owner_state["owner_state_sha256"]:
            _error(
                "The exact Drawing symbol owner changed after it was inspected.",
                "NATIVE_DRAWING_SYMBOL_OWNER_STALE",
                repair={"current_owner_state_sha256": owner_state["owner_state_sha256"]},
            )
        _require_usable(document, owner, "Drawing symbol owner")
    elif kind != "page":
        _error(
            "Drawing symbol owner kind must be page or view.",
            "NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
        )
    return PreparedDrawingSymbolTarget(
        **_base_target(page, page_state), owner=owner, owner_state_before=owner_state
    )


def _surface_spec(operation: str, values: Mapping[str, Any]) -> dict[str, Any]:
    placement = exact_drawing_mapping(
        values["placement_on_page_mm"],
        frozenset({"x_mm", "y_mm"}),
        "placement",
        family="engineering symbol",
        error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
    )
    result = {
        "standard": "iso" if operation == "create_iso_surface_finish" else "asme",
        "symbol_type": str(values["symbol_type"]),
        "method": str(values["method"]),
        "machining_allowance": str(values["machining_allowance"]),
        "lay": str(values["lay"]),
        "placement": {name: _finite(placement[name], name) for name in ("x_mm", "y_mm")},
        "rotation_degrees": _finite(values["rotation_degrees"], "rotation"),
        "label": str(values["label"]),
        "roughness": str(values.get("roughness") or ""),
        "sampling_length": str(values.get("sampling_length") or ""),
        "minimum_roughness_grade": str(values.get("minimum_roughness_grade") or ""),
        "maximum_roughness_grade": str(values.get("maximum_roughness_grade") or ""),
    }
    return result


def _normalize_surface_plan(raw: Any) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        raw,
        frozenset({"object_name", "label", "x_mm", "y_mm", "svg_sha256"}),
        "host surface-finish plan",
        family="engineering symbol",
        error_code=_HOST_ERROR,
    )
    result = {
        "object_name": str(exact["object_name"]),
        "label": str(exact["label"]),
        "placement": {"x_mm": _finite(exact["x_mm"], "host X"), "y_mm": _finite(exact["y_mm"], "host Y")},
        "svg_sha256": str(exact["svg_sha256"]),
    }
    if len(result["svg_sha256"]) != 64:
        _error("TechDraw returned an invalid surface-finish SVG hash.", _HOST_ERROR)
    return result


def _host_surface(
    target: PreparedDrawingSymbolTarget,
    spec: Mapping[str, Any],
    *,
    apply: bool,
) -> tuple[dict[str, Any], Any | None]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.createDrawingSurfaceFinishSymbol
            if apply
            else TechDrawGui.validateDrawingSurfaceFinishSymbol
        )
        raw = function(
            target.page,
            target.owner,
            spec["placement"]["x_mm"],
            spec["placement"]["y_mm"],
            spec["standard"],
            spec["symbol_type"],
            spec["method"],
            spec["machining_allowance"],
            spec["lay"],
            spec["roughness"],
            spec["sampling_length"],
            spec["minimum_roughness_grade"],
            spec["maximum_roughness_grade"],
            spec["rotation_degrees"],
            spec["label"],
        )
    except Exception as exc:
        if apply:
            raise NativeMutationError(
                "NATIVE_DRAWING_SURFACE_FINISH_CREATE_FAILED",
                f"TechDraw rejected the surface-finish symbol: {str(exc).strip()}",
            ) from exc
        _error(
            f"TechDraw rejected the surface-finish symbol: {str(exc).strip()}",
            "NATIVE_DRAWING_SURFACE_FINISH_INVALID",
        )
    symbol = raw.get("symbol") if apply and isinstance(raw, Mapping) else None
    if apply and isinstance(raw, Mapping):
        raw = {key: value for key, value in raw.items() if key != "symbol"}
    return _normalize_surface_plan(raw), symbol


def drawing_weld_catalog_state() -> dict[str, Any]:
    try:
        import TechDrawGui

        raw = TechDrawGui.drawingWeldSymbolCatalog()
    except Exception as exc:
        _error(f"TechDraw weld catalog is unavailable: {str(exc).strip()}", _HOST_ERROR)
    exact = exact_drawing_mapping(
        raw,
        frozenset({"catalog_sha256", "items"}),
        "weld catalog",
        family="engineering symbol",
        error_code=_HOST_ERROR,
    )
    items = []
    for index, raw_item in enumerate(tuple(exact["items"] or ())):
        item = exact_drawing_mapping(
            raw_item,
            frozenset({"key", "svg_sha256"}),
            f"weld catalog item {index}",
            family="engineering symbol",
            error_code=_HOST_ERROR,
        )
        items.append({"key": str(item["key"]), "svg_sha256": str(item["svg_sha256"])})
    catalog_hash = str(exact["catalog_sha256"])
    if not items or len(catalog_hash) != 64 or any(len(item["svg_sha256"]) != 64 for item in items):
        _error("TechDraw returned a malformed weld-symbol catalog.", _HOST_ERROR)
    return {"catalog_sha256": catalog_hash, "items": items}


def _weld_target(
    document: Any,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingSymbolTarget:
    if operation == "create_weld":
        exact = exact_drawing_mapping(
            values["leader"],
            frozenset({"object_name", "expected_leader_state_sha256"}),
            "weld leader target",
            family="engineering symbol",
            error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
        )
        leader = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
            expected_types=("TechDraw::DrawLeaderLine",),
        )
        leader_state = drawing_leader_state(leader)
        if str(exact["expected_leader_state_sha256"]) != leader_state["leader_state_sha256"]:
            _error(
                "The exact Drawing leader changed after it was inspected.",
                "NATIVE_DRAWING_WELD_LEADER_STALE",
                repair={"current_leader_state_sha256": leader_state["leader_state_sha256"]},
            )
        symbol = None
        symbol_state = None
        page = leader.findParentPage()
    else:
        exact = exact_drawing_mapping(
            values["symbol"],
            frozenset({"object_name", "expected_symbol_state_sha256"}),
            "weld symbol target",
            family="engineering symbol",
            error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
        )
        symbol = resolve_object(
            document,
            {"document_uid": str(document.Uid), "object_name": exact["object_name"]},
            expected_types=("TechDraw::DrawWeldSymbol",),
        )
        symbol_state = drawing_weld_symbol_state(symbol)
        if str(exact["expected_symbol_state_sha256"]) != symbol_state["symbol_state_sha256"]:
            _error(
                "The exact Drawing weld symbol changed after it was inspected.",
                "NATIVE_DRAWING_WELD_SYMBOL_STALE",
                repair={"current_symbol_state_sha256": symbol_state["symbol_state_sha256"]},
            )
        leader = symbol.Leader
        leader_state = drawing_leader_state(leader)
        page = symbol.findParentPage()
    _require_usable(document, leader, "Drawing weld leader")
    if symbol is not None:
        _require_usable(document, symbol, "Drawing weld symbol")
    page_state = drawing_page_state(page)
    return PreparedDrawingSymbolTarget(
        **_base_target(page, page_state),
        leader=leader,
        leader_state_before=leader_state,
        symbol=symbol,
        symbol_state_before=symbol_state,
    )


def _weld_tile(raw: Any, noun: str) -> dict[str, str]:
    exact = exact_drawing_mapping(
        raw,
        frozenset({"left_text", "center_text", "right_text", "symbol_key"}),
        noun,
        family="engineering symbol",
        error_code="NATIVE_DRAWING_SYMBOL_PARAMETERS_INVALID",
    )
    return {name: str(exact[name]) for name in exact}


def _weld_spec(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "all_around": bool(values["all_around"]),
        "field_weld": bool(values["field_weld"]),
        "alternating_weld": bool(values["alternating_weld"]),
        "tail_text": str(values["tail_text"]),
        "arrow_side": _weld_tile(values["arrow_side"], "arrow-side tile"),
        "other_side": _weld_tile(values["other_side"], "other-side tile"),
        "label": str(values["label"]),
    }


def _normalize_weld_plan(raw: Any) -> dict[str, Any]:
    exact = exact_drawing_mapping(
        raw,
        frozenset({"mode", "object_name", "label", "catalog_sha256"}),
        "host weld-symbol plan",
        family="engineering symbol",
        error_code=_HOST_ERROR,
    )
    return {name: str(exact[name]) for name in exact}


def _host_weld(
    target: PreparedDrawingSymbolTarget,
    spec: Mapping[str, Any],
    *,
    create: bool,
    apply: bool,
) -> tuple[dict[str, Any], Any | None]:
    try:
        import TechDrawGui

        function = TechDrawGui.changeDrawingWeldSymbol if apply else TechDrawGui.validateDrawingWeldSymbol
        raw = function(
            target.leader if create else target.symbol,
            create,
            spec["all_around"],
            spec["field_weld"],
            spec["alternating_weld"],
            spec["tail_text"],
            spec["arrow_side"]["left_text"],
            spec["arrow_side"]["center_text"],
            spec["arrow_side"]["right_text"],
            spec["arrow_side"]["symbol_key"],
            spec["other_side"]["left_text"],
            spec["other_side"]["center_text"],
            spec["other_side"]["right_text"],
            spec["other_side"]["symbol_key"],
            spec["label"],
        )
    except Exception as exc:
        if apply:
            raise NativeMutationError(
                "NATIVE_DRAWING_WELD_CHANGE_FAILED",
                f"TechDraw rejected the weld symbol: {str(exc).strip()}",
            ) from exc
        _error(
            f"TechDraw rejected the weld symbol: {str(exc).strip()}",
            "NATIVE_DRAWING_WELD_INVALID",
            repair={"read_operation": "read_weld_catalog"},
        )
    symbol = raw.get("symbol") if apply and isinstance(raw, Mapping) else None
    if apply and isinstance(raw, Mapping):
        raw = {key: value for key, value in raw.items() if key != "symbol"}
    return _normalize_weld_plan(raw), symbol


def prepare_drawing_symbol(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDrawingSymbol:
    if operation.startswith("create_") and operation.endswith("surface_finish"):
        target = _surface_target(document, values)
        spec = _surface_spec(operation, values)
        plan, _ = _host_surface(target, spec, apply=False)
        if plan["placement"] != spec["placement"]:
            _error("TechDraw changed the requested surface-finish placement.", _HOST_ERROR)
        return PreparedDrawingSymbol(operation, target, spec, plan)

    target = _weld_target(document, operation, values)
    spec = _weld_spec(values)
    catalog = drawing_weld_catalog_state()
    if str(values["expected_catalog_sha256"]) != catalog["catalog_sha256"]:
        _error(
            "The weld-symbol catalog changed after it was inspected.",
            "NATIVE_DRAWING_WELD_CATALOG_STALE",
            repair={"current_catalog_sha256": catalog["catalog_sha256"]},
        )
    plan, _ = _host_weld(target, spec, create=operation == "create_weld", apply=False)
    if plan["catalog_sha256"] != catalog["catalog_sha256"]:
        _error("TechDraw validated against a different weld-symbol catalog.", _HOST_ERROR)
    if operation == "edit_weld" and target.symbol_state_before is not None:
        current = target.symbol_state_before
        keys = {item["key"]: item["svg_sha256"] for item in catalog["items"]}
        requested = (
            current["label"] == spec["label"]
            and current["all_around"] is spec["all_around"]
            and current["field_weld"] is spec["field_weld"]
            and current["alternating_weld"] is spec["alternating_weld"]
            and current["tail_text"] == spec["tail_text"]
            and all(
                current["tiles"][index]["text"] == {
                    "left": spec[side]["left_text"],
                    "center": spec[side]["center_text"],
                    "right": spec[side]["right_text"],
                }
                and current["tiles"][index]["source_svg_sha256"] == keys[spec[side]["symbol_key"]]
                for index, side in enumerate(("arrow_side", "other_side"))
            )
        )
        if requested:
            _error("The Drawing weld symbol already has the requested complete state.", "NATIVE_DRAWING_NO_CHANGE")
    return PreparedDrawingSymbol(operation, target, spec, plan, catalog)


def mutate_drawing_symbol(
    document: Any,
    *,
    prepared: PreparedDrawingSymbol,
) -> NativeMutationDraft:
    if prepared.operation.endswith("surface_finish"):
        applied, symbol = _host_surface(prepared.target, prepared.spec, apply=True)
    else:
        applied, symbol = _host_weld(
            prepared.target,
            prepared.spec,
            create=prepared.operation == "create_weld",
            apply=True,
        )
    if applied != prepared.host_plan or symbol is None or symbol.Document is not document:
        raise NativeMutationError(
            "NATIVE_DRAWING_SYMBOL_CHANGE_FAILED",
            "TechDraw produced a Drawing symbol inconsistent with preflight.",
        )
    created = ()
    if prepared.operation != "edit_weld":
        resources = ()
        if prepared.operation == "create_weld":
            weld_state = drawing_weld_symbol_state(symbol)
            resources = tuple(
                document.getObject(tile["object_name"])
                for tile in weld_state["tiles"]
            )
            if any(resource is None for resource in resources):
                raise NativeMutationError(
                    "NATIVE_DRAWING_SYMBOL_CHANGE_FAILED",
                    "TechDraw did not retain the weld symbol's generated tiles.",
                )
        created = tuple(object_identity(obj) for obj in (*resources, symbol))
    changed = (object_identity(symbol),) if prepared.operation == "edit_weld" else ()
    return NativeMutationDraft(
        value={"prepared": prepared, "symbol": symbol},
        recompute_targets=(),
        created=created,
        changed=changed,
    )


def _postcondition(message: str) -> None:
    raise NativeMutationError("NATIVE_DRAWING_SYMBOL_POSTCONDITION_FAILED", message)


def _verify_boundaries(target: PreparedDrawingSymbolTarget) -> None:
    document = target.page.Document
    if drawing_selection_state(document) != target.selection_before:
        _postcondition("Drawing symbol editing changed the human selection.")
    if tuple((obj, bool(obj.ViewObject.Visibility)) for obj, _ in target.visibility_before) != target.visibility_before:
        _postcondition("Drawing symbol editing changed existing visibility.")
    if target.owner is not None and drawing_rich_annotation_owner_state(
        target.owner, page=target.page
    ) != target.owner_state_before:
        _postcondition("Drawing symbol creation changed its owner view.")
    if (
        target.leader is not None
        and drawing_leader_state(target.leader) != target.leader_state_before
    ):
        _postcondition("Drawing weld-symbol editing changed its leader.")


def verify_drawing_symbol(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedDrawingSymbol = draft.value["prepared"]
    symbol = draft.value["symbol"]
    target = prepared.target
    _verify_boundaries(target)

    if prepared.operation.endswith("surface_finish"):
        before = {drawing_object_key(obj) for obj in target.objects_before}
        new_objects = tuple(obj for obj in document.Objects if drawing_object_key(obj) not in before)
        if tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(symbol),):
            _postcondition("Surface-finish creation changed objects outside its result.")
        if tuple(target.page.Views or ()) != (*target.page_views_before, symbol):
            _postcondition("Surface-finish creation did not append one exact page view.")
        if drawing_timeline_operations(document) != (*target.timeline_before, symbol):
            _postcondition("Surface-finish creation was not one exact History operation.")
        state = drawing_surface_finish_symbol_state(symbol)
        if (
            not matches_preferred_document_label(state["label"], prepared.spec["label"])
            or state["page_name"] != str(target.page.Name)
            or state["owner_name"] != (str(target.owner.Name) if target.owner else None)
            or state["placement_on_page_mm"] != prepared.spec["placement"]
            or state["rotation_degrees"] != prepared.spec["rotation_degrees"]
            or state["svg_sha256"] != prepared.host_plan["svg_sha256"]
            or not state["timeline_usable"]
            or not state["valid"]
        ):
            _postcondition("The surface-finish result does not match its exact specification.")
        return {"operation": prepared.operation, "surface_finish_symbol": state}

    state = drawing_weld_symbol_state(symbol)
    if prepared.operation == "create_weld":
        before = {drawing_object_key(obj) for obj in target.objects_before}
        new_objects = tuple(obj for obj in document.Objects if drawing_object_key(obj) not in before)
        expected_names = [tile["object_name"] for tile in state["tiles"]] + [state["object_name"]]
        if {str(obj.Name) for obj in new_objects} != set(expected_names):
            _postcondition("Weld-symbol creation changed objects outside its generated block.")
        if tuple(target.page.Views or ()) != (*target.page_views_before, symbol):
            _postcondition("Weld-symbol creation did not append one exact page view.")
        expected_timeline_names = [
            *(str(obj.Name) for obj in target.timeline_before),
            *expected_names,
        ]
        if [str(obj.Name) for obj in drawing_timeline_operations(document)] != expected_timeline_names:
            _postcondition("Weld-symbol creation did not retain one exact History block.")
    else:
        if (
            tuple(map(drawing_object_key, document.Objects))
            != tuple(map(drawing_object_key, target.objects_before))
            or tuple(target.page.Views or ()) != target.page_views_before
            or drawing_timeline_operations(document) != target.timeline_before
        ):
            _postcondition("Weld-symbol editing changed document structure or History.")

    catalog_hashes = {item["key"]: item["svg_sha256"] for item in prepared.catalog["items"]}
    expected_tiles = []
    for side in ("arrow_side", "other_side"):
        spec = prepared.spec[side]
        expected_tiles.append(
            {
                "text": {
                    "left": spec["left_text"],
                    "center": spec["center_text"],
                    "right": spec["right_text"],
                },
                "svg_sha256": catalog_hashes[spec["symbol_key"]],
            }
        )
    if (
        not matches_preferred_document_label(state["label"], prepared.spec["label"])
        or state["all_around"] is not prepared.spec["all_around"]
        or state["field_weld"] is not prepared.spec["field_weld"]
        or state["alternating_weld"] is not prepared.spec["alternating_weld"]
        or state["tail_text"] != prepared.spec["tail_text"]
        or any(
            tile["text"] != expected["text"]
            or tile["source_svg_sha256"] != expected["svg_sha256"]
            or tile["embedded_svg_sha256"] != expected["svg_sha256"]
            or tile["timeline_owner_name"] != state["object_name"]
            for tile, expected in zip(state["tiles"], expected_tiles, strict=True)
        )
        or not state["timeline_usable"]
        or not state["valid"]
    ):
        _postcondition("The weld-symbol result does not match its complete requested state.")
    return {"operation": prepared.operation, "weld_symbol": state}
