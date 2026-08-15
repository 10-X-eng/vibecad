# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed typed values for Native CAM Property Bags."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeTargets import object_reference


MAX_PROPERTY_BAG_PROPERTIES = 64
MAX_PROPERTY_BAG_TEXT = 4096
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY_FIELDS = frozenset({"name", "group", "description", "typed_value"})
_TYPE_BY_KIND = {
    "angle_degrees": "App::PropertyAngle",
    "boolean": "App::PropertyBool",
    "distance_mm": "App::PropertyDistance",
    "enumeration": "App::PropertyEnumeration",
    "number": "App::PropertyFloat",
    "integer": "App::PropertyInteger",
    "length_mm": "App::PropertyLength",
    "percent": "App::PropertyPercent",
    "string": "App::PropertyString",
}
_KIND_BY_TYPE = {
    **{value: key for key, value in _TYPE_BY_KIND.items()},
    # The human editor deliberately retains File properties. Native state may
    # fingerprint their value, but the provider creation surface never accepts
    # or returns the path.
    "App::PropertyFile": "human_file",
}


@dataclass(frozen=True, slots=True)
class PropertyBagValue:
    kind: str
    name: str
    group: str
    description: str
    value: bool | float | int | str
    options: tuple[str, ...] = ()

    @property
    def native_type(self) -> str:
        return _TYPE_BY_KIND[self.kind]


def _error(
    message: str,
    *,
    field: str | None = None,
    code: str = "NATIVE_ARGUMENTS_INVALID",
) -> None:
    repair = {"field": field} if field else None
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _clean_single_line(
    value: Any,
    field: str,
    *,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        _error(f"Property Bag {field} must be one string.", field=field)
    result = value.strip()
    if (
        not result
        or len(result) > maximum
        or any(not character.isprintable() for character in result)
    ):
        _error(
            f"Property Bag {field} must contain 1 through {maximum} printable "
            "characters on one line after trimming.",
            field=field,
        )
    return result


def clean_property_bag_label(value: Any) -> str:
    return _clean_single_line(value, "label", maximum=160)


def _clean_multiline(
    value: Any,
    field: str,
    *,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        _error(f"Property Bag {field} must be one string.", field=field)
    if (not allow_empty and not value) or len(value) > maximum:
        qualifier = "0" if allow_empty else "1"
        _error(
            f"Property Bag {field} must contain {qualifier} through {maximum} characters.",
            field=field,
        )
    rejected = next(
        (
            character
            for character in value
            if not character.isprintable() and character not in "\n\t"
        ),
        None,
    )
    if rejected is not None:
        _error(
            f"Property Bag {field} contains an unsupported control character.",
            field=field,
        )
    return value


def _finite_number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(f"Property Bag {field} must be one finite number.", field=field)
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        _error(
            f"Property Bag {field} must be from {minimum:g} through {maximum:g}.",
            field=field,
        )
    return float(format(result, ".15g"))


def _integer(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not -(2**31) <= value <= 2**31 - 1
    ):
        _error(
            f"Property Bag {field} must be one signed 32-bit integer.",
            field=field,
        )
    return value


def _common(raw: Mapping[str, Any], index: int) -> tuple[str, str, str]:
    prefix = f"properties[{index}]"
    name = raw.get("name")
    if (
        not isinstance(name, str)
        or len(name) > 128
        or not _PROPERTY_NAME.fullmatch(name)
        or name == "CustomPropertyGroups"
    ):
        _error(
            f"Property Bag {prefix}.name must be a non-reserved property identifier.",
            field=f"{prefix}.name",
        )
    group = _clean_single_line(
        raw.get("group"),
        f"{prefix}.group",
        maximum=128,
    )
    if group == "Base":
        _error(
            "Property Bag custom properties cannot use the reserved Base group.",
            field=f"{prefix}.group",
        )
    description = _clean_multiline(
        raw.get("description"),
        f"{prefix}.description",
        maximum=1024,
        allow_empty=True,
    )
    return name, group, description


def _enumeration(
    raw: Mapping[str, Any],
    index: int,
) -> tuple[str, tuple[str, ...]]:
    prefix = f"properties[{index}].typed_value"
    options = raw.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 64:
        _error(
            f"Property Bag {prefix}.options must contain 1 through 64 choices.",
            field=f"{prefix}.options",
        )
    cleaned = tuple(
        _clean_single_line(
            option,
            f"{prefix}.options[{option_index}]",
            maximum=160,
        )
        for option_index, option in enumerate(options)
    )
    if len(cleaned) != len(set(cleaned)):
        _error(
            f"Property Bag {prefix}.options must be unique after trimming.",
            field=f"{prefix}.options",
        )
    selected = _clean_single_line(
        raw.get("selected"),
        f"{prefix}.selected",
        maximum=160,
    )
    if selected not in cleaned:
        _error(
            f"Property Bag {prefix}.selected must equal one declared option.",
            field=f"{prefix}.selected",
        )
    return selected, cleaned


def normalize_property_bag_values(value: Any) -> tuple[PropertyBagValue, ...]:
    if not isinstance(value, list) or len(value) > MAX_PROPERTY_BAG_PROPERTIES:
        _error(
            "Property Bag properties must be an array of at most 64 typed properties.",
            field="properties",
        )
    result = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            _error(
                f"Property Bag properties[{index}] must be one typed object.",
                field=f"properties[{index}]",
            )
        if set(raw) != _PROPERTY_FIELDS:
            _error(
                f"Property Bag properties[{index}] fields are incorrect.",
                field=f"properties[{index}]",
            )
        name, group, description = _common(raw, index)
        prefix = f"properties[{index}]"
        typed = raw.get("typed_value")
        if not isinstance(typed, Mapping):
            _error(
                f"Property Bag {prefix}.typed_value must be one typed object.",
                field=f"{prefix}.typed_value",
            )
        kind = str(typed.get("kind", "") or "")
        if kind not in _TYPE_BY_KIND:
            _error(
                f"Property Bag {prefix}.typed_value.kind is unavailable.",
                field=f"{prefix}.typed_value.kind",
            )
        if kind == "enumeration":
            if set(typed) != {"kind", "options", "selected"}:
                _error(
                    f"Property Bag {prefix}.typed_value enumeration fields are incorrect.",
                    field=f"{prefix}.typed_value",
                )
            selected, options = _enumeration(typed, index)
            result.append(
                PropertyBagValue(
                    kind,
                    name,
                    group,
                    description,
                    selected,
                    options,
                )
            )
            continue
        if set(typed) != {"kind", "value"}:
            _error(
                f"Property Bag {prefix}.typed_value fields are incorrect for {kind}.",
                field=f"{prefix}.typed_value",
            )
        raw_value = typed["value"]
        if kind == "boolean":
            if type(raw_value) is not bool:
                _error(
                    f"Property Bag {prefix}.typed_value.value must be true or false.",
                    field=f"{prefix}.typed_value.value",
                )
            normalized: bool | float | int | str = raw_value
        elif kind == "integer":
            normalized = _integer(raw_value, f"{prefix}.typed_value.value")
        elif kind == "percent":
            normalized = _integer(raw_value, f"{prefix}.typed_value.value")
            if not 0 <= normalized <= 100:
                _error(
                    f"Property Bag {prefix}.typed_value.value must be from 0 through 100.",
                    field=f"{prefix}.typed_value.value",
                )
        elif kind == "string":
            normalized = _clean_multiline(
                raw_value,
                f"{prefix}.typed_value.value",
                maximum=MAX_PROPERTY_BAG_TEXT,
                allow_empty=True,
            )
        else:
            limits = {
                "angle_degrees": (-360_000.0, 360_000.0),
                "distance_mm": (-1_000_000.0, 1_000_000.0),
                "number": (-1.0e12, 1.0e12),
                "length_mm": (0.0, 1_000_000.0),
            }
            minimum, maximum = limits[kind]
            normalized = _finite_number(
                raw_value,
                f"{prefix}.typed_value.value",
                minimum=minimum,
                maximum=maximum,
            )
        result.append(PropertyBagValue(kind, name, group, description, normalized))
    names = [item.name for item in result]
    folded = [name.casefold() for name in names]
    if len(folded) != len(set(folded)):
        _error(
            "Property Bag property names must be unique without case ambiguity.",
            field="properties",
        )
    return tuple(sorted(result, key=lambda item: item.name))


def apply_property_bag_values(
    bag: Any,
    values: tuple[PropertyBagValue, ...],
) -> None:
    for item in values:
        if item.name in tuple(getattr(bag, "PropertiesList", ()) or ()):
            _error(
                f"Property Bag property name {item.name!r} is reserved by the object.",
                field="properties",
            )
        created_name = bag.Proxy.addCustomProperty(
            item.native_type,
            item.name,
            item.group,
            item.description,
        )
        if created_name != item.name:
            raise RuntimeError(
                "The Property Bag factory changed an exact property name"
            )
        if item.kind == "enumeration":
            setattr(bag, item.name, list(item.options))
            setattr(bag, item.name, item.value)
        elif item.kind == "angle_degrees":
            setattr(bag, item.name, f"{item.value:.17g} deg")
        elif item.kind in {"distance_mm", "length_mm"}:
            setattr(bag, item.name, f"{item.value:.17g} mm")
        elif item.kind == "percent":
            setattr(bag, item.name, item.value)
        else:
            setattr(bag, item.name, item.value)


def _quantity_value(value: Any, unit: str) -> float:
    converted = value.getValueAs(unit)
    return float(format(float(getattr(converted, "Value", converted)), ".15g"))


def _actual_property_value(bag: Any, item: PropertyBagValue) -> Any:
    value = bag.getPropertyByName(item.name)
    if item.kind == "human_file":
        return {"path_sha256": hashlib.sha256(str(value).encode("utf-8")).hexdigest()}
    if item.kind == "angle_degrees":
        return _quantity_value(value, "deg")
    if item.kind in {"distance_mm", "length_mm"}:
        return _quantity_value(value, "mm")
    if item.kind == "percent":
        return float(format(float(getattr(value, "Value", value)), ".15g"))
    if item.kind == "number":
        return float(format(float(value), ".15g"))
    if item.kind == "integer":
        return int(value)
    if item.kind == "boolean":
        return bool(value)
    return str(value)


def property_bag_state(
    bag: Any,
    expected: tuple[PropertyBagValue, ...] | None = None,
) -> dict[str, Any]:
    custom_names = tuple(str(name) for name in bag.Proxy.getCustomProperties())
    if expected is not None and custom_names != tuple(item.name for item in expected):
        raise NativeManufactureError(
            "The Property Bag custom-property order changed before commit.",
            error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    items = []
    for index, name in enumerate(custom_names):
        native_type = str(bag.getTypeIdOfProperty(name) or "")
        kind = _KIND_BY_TYPE.get(native_type)
        if kind is None:
            raise NativeManufactureError(
                f"Property Bag property {name!r} has unsupported type {native_type!r}.",
                error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
        template = (
            expected[index]
            if expected is not None
            else PropertyBagValue(
                kind,
                name,
                str(bag.getGroupOfProperty(name)),
                str(bag.getDocumentationOfProperty(name)),
                "",
                tuple(bag.getEnumerationsOfProperty(name))
                if kind == "enumeration"
                else (),
            )
        )
        expected_native_type = (
            template.native_type if expected is not None else native_type
        )
        if (
            native_type != expected_native_type
            or str(bag.getGroupOfProperty(name)) != template.group
            or str(bag.getDocumentationOfProperty(name)) != template.description
        ):
            raise NativeManufactureError(
                f"Property Bag property {name!r} metadata changed before commit.",
                error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
        options = (
            tuple(str(value) for value in bag.getEnumerationsOfProperty(name))
            if kind == "enumeration"
            else ()
        )
        actual_value = _actual_property_value(bag, template)
        if expected is not None and (
            actual_value != template.value or options != template.options
        ):
            raise NativeManufactureError(
                f"Property Bag property {name!r} value changed before commit.",
                error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
        item_state = {
            "name": name,
            "kind": kind,
            "group": template.group,
            "description": template.description,
            "value": actual_value,
        }
        if options:
            item_state["options"] = list(options)
        items.append(item_state)
    groups = []
    for item in items:
        group = item["group"]
        if group not in groups:
            groups.append(group)
    state = {
        "object_name": str(bag.Name),
        "type_id": str(bag.TypeId),
        "label": str(bag.Label),
        "groups": groups,
        "properties": items,
    }
    encoded = json.dumps(
        state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    state["state_sha256"] = hashlib.sha256(encoded).hexdigest()
    return state


def property_bag_summary(
    bag: Any,
    expected: tuple[PropertyBagValue, ...] | None = None,
    *,
    property_limit: int = MAX_PROPERTY_BAG_PROPERTIES,
) -> dict[str, Any]:
    if not 1 <= property_limit <= MAX_PROPERTY_BAG_PROPERTIES:
        raise ValueError("property_limit must be from 1 through 64")
    state = property_bag_state(bag, expected)
    properties = state["properties"]
    result = {
        "object": object_reference(bag),
        "label": state["label"],
        "property_count": len(properties),
        "groups": list(state["groups"]),
        "properties": [
            {
                "name": item["name"],
                "kind": item["kind"],
                "group": item["group"],
            }
            for item in properties[:property_limit]
        ],
        "state_sha256": state["state_sha256"],
    }
    if len(properties) > property_limit:
        result["properties_truncated"] = True
    return result


def is_property_bag(obj: Any) -> bool:
    try:
        import Path.Base.PropertyBag as PathPropertyBag

        return PathPropertyBag.IsPropertyBag(obj)
    except Exception:
        return False
