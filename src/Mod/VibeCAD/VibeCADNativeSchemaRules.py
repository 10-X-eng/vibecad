# SPDX-License-Identifier: LGPL-2.1-or-later

"""Recursive bounded-JSON rules for Native provider parameter schemas."""

from __future__ import annotations

from typing import Any, Mapping


MAX_NATIVE_PARAMETER_TEXT_CHARACTERS = 4096
MAX_NATIVE_PARAMETER_ARRAY_ITEMS = 256


class NativeSchemaRuleError(ValueError):
    pass


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "parameters"


def _validate_object(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
    properties = node.get("properties")
    if not isinstance(properties, Mapping):
        raise NativeSchemaRuleError(
            f"{_path_text(path)} object must declare properties."
        )
    if node.get("additionalProperties") is not False:
        raise NativeSchemaRuleError(
            f"{_path_text(path)} object must reject additional properties."
        )
    required = node.get("required", [])
    if (
        not isinstance(required, list)
        or len(required) != len(set(required))
        or any(
            not isinstance(name, str) or name not in properties for name in required
        )
    ):
        raise NativeSchemaRuleError(
            f"{_path_text(path)} required fields must name declared properties."
        )
    for name, child in properties.items():
        if not isinstance(name, str) or not isinstance(child, Mapping):
            raise NativeSchemaRuleError(
                f"{_path_text(path)} properties must contain schema objects."
            )
        _validate_node(child, (*path, name))


def _validate_array(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
    maximum = node.get("maxItems")
    minimum = node.get("minItems", 0)
    if (
        type(maximum) is not int
        or maximum < 1
        or maximum > MAX_NATIVE_PARAMETER_ARRAY_ITEMS
        or type(minimum) is not int
        or minimum < 0
        or minimum > maximum
    ):
        raise NativeSchemaRuleError(
            f"{_path_text(path)} array must declare bounded minItems/maxItems."
        )
    items = node.get("items")
    if not isinstance(items, Mapping):
        raise NativeSchemaRuleError(
            f"{_path_text(path)} array must declare one item schema."
        )
    _validate_node(items, (*path, "items"))


def _validate_string(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
    if "const" in node:
        values = [node["const"]]
    elif "enum" in node:
        values = node["enum"]
        if not isinstance(values, list) or not values:
            raise NativeSchemaRuleError(
                f"{_path_text(path)} string enum must be a non-empty array."
            )
    else:
        maximum = node.get("maxLength")
        if (
            type(maximum) is not int
            or maximum < 1
            or maximum > MAX_NATIVE_PARAMETER_TEXT_CHARACTERS
        ):
            raise NativeSchemaRuleError(
                f"{_path_text(path)} string must declare a bounded maxLength."
            )
        return
    if any(
        not isinstance(value, str)
        or len(value) > MAX_NATIVE_PARAMETER_TEXT_CHARACTERS
        for value in values
    ):
        raise NativeSchemaRuleError(
            f"{_path_text(path)} string choices exceed their bound."
        )


def _validate_node(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
    if "$ref" in node or "$defs" in node or "definitions" in node:
        raise NativeSchemaRuleError(
            f"{_path_text(path)} must be explicit and cannot use schema references."
        )
    compositions = tuple(
        keyword for keyword in ("oneOf", "anyOf", "allOf") if keyword in node
    )
    for keyword in compositions:
        if keyword not in node:
            continue
        branches = node[keyword]
        if not isinstance(branches, list) or not branches:
            raise NativeSchemaRuleError(
                f"{_path_text(path)} {keyword} must contain schema branches."
            )
        for index, branch in enumerate(branches):
            if not isinstance(branch, Mapping):
                raise NativeSchemaRuleError(
                    f"{_path_text(path)} {keyword} branch is not an object."
                )
            _validate_node(branch, (*path, f"{keyword}[{index}]"))
    kind = node.get("type")
    if kind is None and not compositions:
        raise NativeSchemaRuleError(
            f"{_path_text(path)} must declare a supported type or composition."
        )
    if kind == "object":
        _validate_object(node, path)
    elif kind == "array":
        _validate_array(node, path)
    elif kind == "string":
        _validate_string(node, path)
    elif kind not in {None, "boolean", "integer", "number", "null"}:
        raise NativeSchemaRuleError(
            f"{_path_text(path)} has unsupported schema type {kind!r}."
        )


def validate_bounded_parameter_schema(schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping):
        raise NativeSchemaRuleError("Native parameters must be a schema object.")
    _validate_node(schema, ("parameters",))
