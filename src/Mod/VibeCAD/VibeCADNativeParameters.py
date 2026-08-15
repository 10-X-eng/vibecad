# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional spreadsheet mutations for the Parameters ribbon."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeParametersState import (
    NativeParametersStateError,
    envelope_range,
    normalize_cell_address,
    normalize_range,
    parameter_cell_state,
    parameter_range_state,
    parameter_sheet_identity_state,
    parameter_sheet_summary,
)
from VibeCADNativeTargets import object_identity, read_current_selection


PARAMETERS_CREATE_OPERATIONS = frozenset({"create"})
PARAMETERS_CELL_OPERATIONS = frozenset(
    {
        "write_values",
        "write_formulas",
        "set_alias",
        "merge",
        "split",
        "set_properties",
    }
)
PARAMETERS_FORMAT_OPERATIONS = frozenset(
    {
        "align_left",
        "align_center",
        "align_right",
        "align_top",
        "align_vertical_center",
        "align_bottom",
        "set_bold",
        "set_italic",
        "set_underline",
    }
)
_ALIGNMENTS = {
    "align_left": "left",
    "align_center": "center",
    "align_right": "right",
    "align_top": "top",
    "align_vertical_center": "vcenter",
    "align_bottom": "bottom",
}
_STYLES = {
    "set_bold": "bold",
    "set_italic": "italic",
    "set_underline": "underline",
}
_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class NativeParametersError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "NATIVE_PARAMETERS_INVALID",
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code)
        self.repair = dict(repair) if repair is not None else None

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": str(self),
        }
        if self.repair is not None:
            result["repair"] = dict(self.repair)
        return result


@dataclass(frozen=True, slots=True)
class PreparedParametersMutation:
    operation: str
    sheet: Any | None
    label: str | None
    payload: Mapping[str, Any]
    sheet_identity_before: Mapping[str, Any] | None
    cells_before: tuple[Mapping[str, Any], ...]
    range_before: Mapping[str, Any] | None
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: Mapping[str, Any]
    dependent_names: tuple[str, ...]


def _timeline(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _selection(document: Any) -> Mapping[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def resolve_exact_parameter_sheet(
    document: Any,
    target: Any,
) -> tuple[Any, Mapping[str, Any]]:
    if not isinstance(target, Mapping) or set(target) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeParametersError(
            "The exact spreadsheet target is malformed.",
            error_code="NATIVE_PARAMETERS_TARGET_INVALID",
        )
    name = str(target["object_name"] or "")
    sheet = document.getObject(name) if name else None
    if (
        sheet is None
        or str(getattr(sheet, "TypeId", "")) != "Spreadsheet::Sheet"
        or getattr(sheet, "Document", None) is not document
    ):
        raise NativeParametersError(
            "The exact spreadsheet is no longer available.",
            error_code="NATIVE_PARAMETERS_TARGET_UNAVAILABLE",
        )
    state = parameter_sheet_identity_state(sheet)
    if str(target["expected_state_sha256"] or "") != state["state_sha256"]:
        raise NativeParametersError(
            "The spreadsheet identity changed after it was inspected.",
            error_code="NATIVE_PARAMETERS_TARGET_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    if not state["timeline_usable"]:
        raise NativeParametersError(
            "The spreadsheet is not usable at the current History position.",
            error_code="NATIVE_PARAMETERS_HISTORY_TARGET_UNAVAILABLE",
        )
    return sheet, state


def _label(value: Any) -> str:
    result = str(value or "")
    if result != result.strip() or not 1 <= len(result) <= 160:
        raise NativeParametersError(
            "Spreadsheet labels must contain 1 to 160 non-padding characters.",
            error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
        )
    return result


def _exact_range(sheet: Any, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "range",
        "expected_range_state_sha256",
    }:
        raise NativeParametersError(
            "The exact spreadsheet range target is malformed.",
            error_code="NATIVE_PARAMETERS_RANGE_INVALID",
        )
    state = parameter_range_state(sheet, value["range"])
    if str(value["expected_range_state_sha256"] or "") != state[
        "range_state_sha256"
    ]:
        raise NativeParametersError(
            "The spreadsheet range changed after it was read.",
            error_code="NATIVE_PARAMETERS_RANGE_STALE",
            repair={
                "range": state["range"],
                "current_range_state_sha256": state["range_state_sha256"],
            },
        )
    return state


def _updates(sheet: Any, values: Any, *, formulas: bool) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 256:
        raise NativeParametersError(
            "A spreadsheet write requires 1 to 256 exact cell updates.",
            error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
        )
    result = []
    addresses = set()
    expected_key = "formula" if formulas else "value"
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) != {
            "address",
            expected_key,
            "expected_cell_state_sha256",
        }:
            raise NativeParametersError(
                "A spreadsheet cell update is malformed.",
                error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
            )
        address = normalize_cell_address(raw["address"])
        if address in addresses:
            raise NativeParametersError(
                f"Spreadsheet cell {address} is repeated in one batch.",
                error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
            )
        addresses.add(address)
        text = str(raw[expected_key])
        if (
            len(text) > 4096
            or (formulas and not text.startswith("="))
            or (not formulas and text.startswith("="))
        ):
            raise NativeParametersError(
                "Spreadsheet formulas must begin with '='; raw values must not. Cell updates are limited to 4096 characters.",
                error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
            )
        before = parameter_cell_state(sheet, address)
        if str(raw["expected_cell_state_sha256"] or "") != before[
            "cell_state_sha256"
        ]:
            raise NativeParametersError(
                f"Spreadsheet cell {address} changed after it was read.",
                error_code="NATIVE_PARAMETERS_CELL_STALE",
                repair={
                    "address": address,
                    "current_cell_state_sha256": before["cell_state_sha256"],
                },
            )
        result.append({"address": address, expected_key: text, "before": before})
    return tuple(result)


def _color(value: Any, noun: str) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NativeParametersError(
            f"Spreadsheet {noun} must be null or exactly three RGB components.",
            error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
        )
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise NativeParametersError(
            f"Spreadsheet {noun} RGB components must be numeric.",
            error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
        ) from exc
    if any(not math.isfinite(item) or not 0.0 <= item <= 1.0 for item in result):
        raise NativeParametersError(
            f"Spreadsheet {noun} RGB components must be between 0 and 1.",
            error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
        )
    return result  # type: ignore[return-value]


def _dependents(sheet: Any) -> tuple[str, ...]:
    result = []
    pending = list(tuple(getattr(sheet, "InList", ()) or ()))
    seen = {str(sheet.Name)}
    while pending and len(result) < 64:
        value = pending.pop(0)
        name = str(getattr(value, "Name", "") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
        pending.extend(tuple(getattr(value, "InList", ()) or ()))
    return tuple(result)


def prepare_parameters_mutation(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedParametersMutation:
    if operation in PARAMETERS_CREATE_OPERATIONS:
        return PreparedParametersMutation(
            operation=operation,
            sheet=None,
            label=_label(values["label"]),
            payload={},
            sheet_identity_before=None,
            cells_before=(),
            range_before=None,
            objects_before=tuple(document.Objects),
            timeline_before=_timeline(document),
            selection_before=_selection(document),
            dependent_names=(),
        )
    if operation not in PARAMETERS_CELL_OPERATIONS | PARAMETERS_FORMAT_OPERATIONS:
        raise NativeParametersError("The Parameters operation is unsupported.")
    sheet, identity = resolve_exact_parameter_sheet(document, values["sheet"])
    payload: dict[str, Any] = {}
    cells_before: tuple[Mapping[str, Any], ...] = ()
    range_before = None
    if operation in {"write_values", "write_formulas"}:
        updates = _updates(
            sheet,
            values["updates"],
            formulas=operation == "write_formulas",
        )
        payload["updates"] = updates
        cells_before = tuple(item["before"] for item in updates)
    elif operation == "set_alias":
        raw = values["cell"]
        if not isinstance(raw, Mapping) or set(raw) != {
            "address",
            "expected_cell_state_sha256",
        }:
            raise NativeParametersError("The exact alias cell is malformed.")
        address = normalize_cell_address(raw["address"])
        before = parameter_cell_state(sheet, address)
        if str(raw["expected_cell_state_sha256"] or "") != before[
            "cell_state_sha256"
        ]:
            raise NativeParametersError(
                f"Spreadsheet cell {address} changed after it was read.",
                error_code="NATIVE_PARAMETERS_CELL_STALE",
            )
        alias = str(values["alias"] or "")
        if alias and _ALIAS.fullmatch(alias) is None:
            raise NativeParametersError(
                "Spreadsheet aliases must be empty or use a letter/underscore followed by letters, digits, or underscores (64 maximum).",
                error_code="NATIVE_PARAMETERS_ALIAS_INVALID",
            )
        payload.update(address=address, alias=alias)
        cells_before = (before,)
    elif operation == "split":
        raw = values["cell"]
        if not isinstance(raw, Mapping) or set(raw) != {
            "address",
            "expected_cell_state_sha256",
        }:
            raise NativeParametersError("The exact split cell is malformed.")
        address = normalize_cell_address(raw["address"])
        before = parameter_cell_state(sheet, address)
        if str(raw["expected_cell_state_sha256"] or "") != before[
            "cell_state_sha256"
        ]:
            raise NativeParametersError(
                f"Spreadsheet cell {address} changed after it was read.",
                error_code="NATIVE_PARAMETERS_CELL_STALE",
            )
        if before["merge"]["rows"] * before["merge"]["columns"] <= 1:
            raise NativeParametersError(
                f"Spreadsheet cell {address} is not part of a merged range.",
                error_code="NATIVE_PARAMETERS_SPLIT_INVALID",
            )
        payload["address"] = address
        cells_before = (before,)
    else:
        range_before = _exact_range(sheet, values["target"])
        payload["range"] = range_before["range"]
        if operation == "merge" and range_before["cell_count"] < 2:
            raise NativeParametersError(
                "Merge requires an exact range containing at least two cells.",
                error_code="NATIVE_PARAMETERS_MERGE_INVALID",
            )
        if operation == "set_properties":
            properties = values["properties"]
            allowed = {"display_unit", "foreground_rgb", "background_rgb"}
            if not isinstance(properties, Mapping) or not properties or not set(properties) <= allowed:
                raise NativeParametersError(
                    "Cell properties must set display_unit, foreground_rgb, or background_rgb.",
                    error_code="NATIVE_PARAMETERS_PARAMETERS_INVALID",
                )
            normalized = dict(properties)
            if "display_unit" in normalized:
                unit = normalized["display_unit"]
                if unit is not None and (not isinstance(unit, str) or len(unit) > 128):
                    raise NativeParametersError("Spreadsheet display_unit is invalid.")
            if "foreground_rgb" in normalized:
                normalized["foreground_rgb"] = _color(
                    normalized["foreground_rgb"], "foreground"
                )
            if "background_rgb" in normalized:
                normalized["background_rgb"] = _color(
                    normalized["background_rgb"], "background"
                )
            payload["properties"] = normalized
        elif operation in _STYLES:
            if type(values["enabled"]) is not bool:
                raise NativeParametersError("Spreadsheet style enabled must be boolean.")
            payload["enabled"] = values["enabled"]
    return PreparedParametersMutation(
        operation=operation,
        sheet=sheet,
        label=None,
        payload=payload,
        sheet_identity_before=identity,
        cells_before=cells_before,
        range_before=range_before,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline(document),
        selection_before=_selection(document),
        dependent_names=_dependents(sheet),
    )


def _assert_current(prepared: PreparedParametersMutation) -> None:
    sheet = prepared.sheet
    if sheet is None:
        return
    current = parameter_sheet_identity_state(sheet)
    if current != prepared.sheet_identity_before:
        raise NativeParametersError(
            "The spreadsheet identity changed before mutation.",
            error_code="NATIVE_PARAMETERS_TARGET_STALE",
        )
    if prepared.cells_before:
        for before in prepared.cells_before:
            if parameter_cell_state(sheet, before["address"]) != before:
                raise NativeParametersError(
                    f"Spreadsheet cell {before['address']} changed before mutation.",
                    error_code="NATIVE_PARAMETERS_CELL_STALE",
                )
    if prepared.range_before is not None:
        if parameter_range_state(sheet, prepared.range_before["range"]) != prepared.range_before:
            raise NativeParametersError(
                "The spreadsheet range changed before mutation.",
                error_code="NATIVE_PARAMETERS_RANGE_STALE",
            )


def mutate_parameters(
    document: Any,
    *,
    prepared: PreparedParametersMutation,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedParametersMutation):
        raise TypeError("prepared must be a PreparedParametersMutation")
    _assert_current(prepared)
    if prepared.operation == "create":
        sheet = document.addObject(
            "Spreadsheet::Sheet",
            document.getUniqueObjectName("Spreadsheet"),
        )
        if sheet is None:
            raise NativeMutationError(
                "NATIVE_PARAMETERS_CREATE_FAILED",
                "The spreadsheet could not be created.",
            )
        sheet.Label = prepared.label
        import SpreadsheetGui

        SpreadsheetGui.publishCreatedSheet(sheet)
        created = tuple(
            object_identity(value)
            for value in document.Objects
            if value not in prepared.objects_before
        )
        return NativeMutationDraft(
            value={"prepared": prepared, "sheet": sheet},
            recompute_targets=(sheet,),
            created=created,
        )
    sheet = prepared.sheet
    operation = prepared.operation
    try:
        if operation in {"write_values", "write_formulas"}:
            key = "formula" if operation == "write_formulas" else "value"
            for item in prepared.payload["updates"]:
                sheet.set(item["address"], item[key])
        elif operation == "set_alias":
            sheet.setAlias(prepared.payload["address"], prepared.payload["alias"])
        elif operation == "merge":
            sheet.mergeCells(prepared.payload["range"])
        elif operation == "split":
            sheet.splitCell(prepared.payload["address"])
        elif operation == "set_properties":
            range_value = prepared.payload["range"]
            properties = prepared.payload["properties"]
            if "display_unit" in properties:
                sheet.setDisplayUnit(range_value, properties["display_unit"] or "")
            if "foreground_rgb" in properties:
                value = properties["foreground_rgb"]
                if value is None:
                    sheet.clearForeground(range_value)
                else:
                    sheet.setForeground(range_value, value)
            if "background_rgb" in properties:
                value = properties["background_rgb"]
                if value is None:
                    sheet.clearBackground(range_value)
                else:
                    sheet.setBackground(range_value, value)
        elif operation in _ALIGNMENTS:
            sheet.setAlignment(
                prepared.payload["range"],
                _ALIGNMENTS[operation],
                "keep",
            )
        elif operation in _STYLES:
            sheet.setStyle(
                prepared.payload["range"],
                _STYLES[operation],
                "add" if prepared.payload["enabled"] else "remove",
            )
        else:
            raise RuntimeError("unsupported Parameters mutation")
    except NativeParametersError:
        raise
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_PARAMETERS_MUTATION_FAILED",
            f"Spreadsheet operation {operation!r} was rejected: {str(exc).strip()}",
        ) from exc
    recompute_targets = tuple(document.Objects) if operation in {
        "write_values",
        "write_formulas",
        "set_alias",
    } else (sheet,)
    return NativeMutationDraft(
        value={"prepared": prepared, "sheet": sheet},
        recompute_targets=recompute_targets,
        changed=(object_identity(sheet),),
    )


def _compact_cell(state: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "address": state["address"],
        "cell_state_sha256": state["cell_state_sha256"],
        "contents": state["contents"],
        "evaluated": state["evaluated"],
    }
    for name in ("alias", "formula_references", "formula_error"):
        if state[name]:
            result[name] = state[name]
    return result


def _compact_formula(value: str) -> str:
    result = []
    quote = None
    escaped = False
    for character in value:
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\" and quote is not None:
            result.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            result.append(character)
            continue
        if quote is None and character.isspace():
            continue
        result.append(character)
    return "".join(result)


def parameter_cell_retains_input(
    state: Mapping[str, Any],
    requested: str,
    *,
    formula: bool,
) -> bool:
    """Verify Spreadsheet's canonical storage against one requested input."""

    contents = str(state["contents"] or "")
    if formula:
        return contents.startswith("=") and _compact_formula(contents) == _compact_formula(requested)
    if requested == "":
        return contents == ""
    literal = requested[1:] if requested.startswith("'") else requested
    evaluated = state["evaluated"]
    if isinstance(evaluated, str) and evaluated == literal:
        return True
    try:
        import FreeCAD as App

        expected_quantity = App.Units.Quantity(requested)
        actual_quantity = App.Units.Quantity(str(evaluated))
        if expected_quantity.Unit != actual_quantity.Unit:
            return False
        scale = max(1.0, abs(float(expected_quantity.Value)))
        return abs(float(actual_quantity.Value) - float(expected_quantity.Value)) <= 1.0e-12 * scale
    except (AttributeError, TypeError, ValueError):
        return contents == requested


def verify_parameters_mutation(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, Mapping) else None
    sheet = draft.value.get("sheet") if isinstance(draft.value, Mapping) else None
    if not isinstance(prepared, PreparedParametersMutation) or sheet is None:
        raise NativeMutationError(
            "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
            "The spreadsheet mutation lost its exact result identity.",
        )
    if _selection(document) != prepared.selection_before:
        raise NativeMutationError(
            "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
            "The spreadsheet mutation changed the human selection.",
        )
    if prepared.operation == "create":
        new_objects = tuple(value for value in document.Objects if value not in prepared.objects_before)
        if (
            sheet not in new_objects
            or any(
                value is not sheet
                and str(getattr(value, "TypeId", "")) != "App::DocumentTimeline"
                for value in new_objects
            )
            or _timeline(document) != (*prepared.timeline_before, sheet)
            or str(getattr(sheet, "VibeCADTimelineRole", "") or "") != "operation"
        ):
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "Spreadsheet creation changed objects or History outside its exact result.",
            )
        return {"operation": "create", "sheet": parameter_sheet_summary(sheet)}
    if (
        tuple(document.Objects) != prepared.objects_before
        or _timeline(document) != prepared.timeline_before
        or parameter_sheet_identity_state(sheet) != prepared.sheet_identity_before
    ):
        raise NativeMutationError(
            "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
            "The spreadsheet operation changed objects, identity, or History outside its target.",
        )
    operation = prepared.operation
    result: dict[str, Any] = {
        "operation": operation,
        "sheet": {
            "object_name": str(sheet.Name),
            "state_sha256": prepared.sheet_identity_before["state_sha256"],
        },
    }
    if operation in {"write_values", "write_formulas"}:
        key = "formula" if operation == "write_formulas" else "value"
        states = tuple(
            parameter_cell_state(sheet, item["address"])
            for item in prepared.payload["updates"]
        )
        if any(
            not parameter_cell_retains_input(
                state,
                item[key],
                formula=operation == "write_formulas",
            )
            for state, item in zip(states, prepared.payload["updates"], strict=True)
        ):
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "A spreadsheet cell did not retain its requested contents.",
            )
        result["changed_range"] = envelope_range(state["address"] for state in states)
        result["cells"] = [_compact_cell(state) for state in states]
        formula_errors = [
            {"address": state["address"], "error": state["formula_error"]}
            for state in states
            if state["formula_error"]
        ]
        if formula_errors:
            result["formula_errors"] = formula_errors
    elif operation == "set_alias":
        state = parameter_cell_state(sheet, prepared.payload["address"])
        if (state["alias"] or "") != prepared.payload["alias"]:
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "The spreadsheet alias did not retain its requested value.",
            )
        result["cell"] = _compact_cell(state)
    elif operation == "split":
        state = parameter_cell_state(sheet, prepared.payload["address"])
        if state["merge"]["rows"] != 1 or state["merge"]["columns"] != 1:
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "The spreadsheet cell remained merged after split.",
            )
        result["range"] = {
            "range": state["address"],
            "range_state_sha256": parameter_range_state(sheet, state["address"])[
                "range_state_sha256"
            ],
            "cell_count": 1,
        }
    else:
        state = parameter_range_state(sheet, prepared.payload["range"])
        if operation == "merge" and not all(
            item["merge"]["anchor"] == state["cells"][0]["address"]
            and item["merge"]["rows"] * item["merge"]["columns"]
            == state["cell_count"]
            for item in state["cells"]
        ):
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "The spreadsheet range did not retain one exact merge.",
            )
        if operation in _ALIGNMENTS and any(
            _ALIGNMENTS[operation] not in item["alignment"] for item in state["cells"]
        ):
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "The spreadsheet range did not retain its requested alignment.",
            )
        if operation in _STYLES and any(
            (_STYLES[operation] in item["styles"]) != prepared.payload["enabled"]
            for item in state["cells"]
        ):
            raise NativeMutationError(
                "NATIVE_PARAMETERS_POSTCONDITION_FAILED",
                "The spreadsheet range did not retain its requested text style.",
            )
        result["range"] = {
            "range": state["range"],
            "range_state_sha256": state["range_state_sha256"],
            "cell_count": state["cell_count"],
        }
    result["recompute"] = {
        "dependent_object_names": list(prepared.dependent_names),
        "dependent_object_count": len(prepared.dependent_names),
    }
    return result


def parameter_read_range(document: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    try:
        sheet, identity = resolve_exact_parameter_sheet(document, values["sheet"])
        state = parameter_range_state(sheet, values["range"])
    except NativeParametersStateError as exc:
        raise NativeParametersError(str(exc), error_code=exc.error_code) from exc
    return {
        "operation": "read_range",
        "sheet": {
            "object_name": str(sheet.Name),
            "state_sha256": identity["state_sha256"],
        },
        **state,
    }
