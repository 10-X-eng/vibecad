# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stable model-facing names for every VibeCAD MCP tool."""

from __future__ import annotations

import re
from typing import Any, Iterable


MCP_TOOL_NAME_MAX_LENGTH = 128
_CANONICAL_TOOL_NAME = re.compile(r"[A-Za-z0-9._-]+")
_TOOL_NAME_PART = re.compile(r"[A-Za-z0-9]+")


class MCPToolNameError(ValueError):
    """Raised when canonical tools cannot form one unambiguous MCP surface."""


def _pascal_case(tokens: Iterable[str]) -> str:
    return "".join(token[:1].upper() + token[1:] for token in tokens if token)


def mcp_tool_wire_name(canonical_name: str) -> str:
    """Return one concise PascalCase name for an internal dotted tool name.

    Multi-word operations already describe their target and do not repeat the
    internal namespace. Single-word operations retain their namespace because
    names such as ``Inspect`` or ``Export`` are otherwise ambiguous.
    """

    canonical = str(canonical_name or "").strip()
    if not canonical or _CANONICAL_TOOL_NAME.fullmatch(canonical) is None:
        raise MCPToolNameError(
            f"MCP tool has an invalid canonical name: {canonical_name!r}."
        )
    operation = canonical.rsplit(".", 1)[-1]
    operation_tokens = _TOOL_NAME_PART.findall(operation)
    selected_tokens = (
        operation_tokens
        if len(operation_tokens) > 1
        else _TOOL_NAME_PART.findall(canonical)
    )
    wire_name = _pascal_case(selected_tokens)
    if not wire_name or not wire_name[0].isalpha():
        raise MCPToolNameError(
            f"MCP tool {canonical!r} does not produce a letter-prefixed wire name."
        )
    if len(wire_name) > MCP_TOOL_NAME_MAX_LENGTH:
        raise MCPToolNameError(
            f"MCP tool {canonical!r} produces a wire name longer than "
            f"{MCP_TOOL_NAME_MAX_LENGTH} characters."
        )
    return wire_name


def mcp_wire_tool_schemas(
    schemas: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Rename one complete MCP surface and return wire-to-canonical routing."""

    advertised: list[dict[str, Any]] = []
    canonical_by_wire: dict[str, str] = {}
    seen_canonical: set[str] = set()
    for index, schema in enumerate(schemas):
        if not isinstance(schema, dict):
            raise MCPToolNameError(f"MCP tool schema {index} must be an object.")
        canonical = str(schema.get("name") or "").strip()
        if canonical in seen_canonical:
            raise MCPToolNameError(
                f"MCP surface contains duplicate canonical tool {canonical!r}."
            )
        seen_canonical.add(canonical)
        wire_name = mcp_tool_wire_name(canonical)
        previous = canonical_by_wire.get(wire_name)
        if previous is not None:
            raise MCPToolNameError(
                "MCP tool name collision: "
                f"{previous!r} and {canonical!r} both advertise as {wire_name!r}."
            )
        canonical_by_wire[wire_name] = canonical
        advertised_schema = dict(schema)
        advertised_schema["name"] = wire_name
        advertised.append(advertised_schema)
    return advertised, canonical_by_wire
