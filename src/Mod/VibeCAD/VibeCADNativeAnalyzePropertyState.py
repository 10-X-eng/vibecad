# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded serialization of FEM document-property values for exact state."""

from __future__ import annotations

import math
from typing import Any, Collection

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


MAX_PROPERTY_ITEMS = 256
MAX_PROPERTY_TEXT = 4096
MAX_PROPERTIES = 256
_SKIPPED_PROPERTY_TYPES = frozenset(
    {
        "App::PropertyLink",
        "App::PropertyLinkChild",
        "App::PropertyLinkGlobal",
        "App::PropertyLinkList",
        "App::PropertyLinkSub",
        "App::PropertyLinkSubList",
        "App::PropertyPythonObject",
    }
)


def stable_fem_property_value(value: Any) -> Any:
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, str):
        if len(value) > MAX_PROPERTY_TEXT:
            raise NativeAnalyzeError("A FEM property exceeds its bounded text size.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NativeAnalyzeError("A FEM property contains a non-finite number.")
        return float(format(value, ".15g"))
    if hasattr(value, "Value"):
        return stable_fem_property_value(float(value.Value))
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PROPERTY_ITEMS:
            raise NativeAnalyzeError("A FEM property exceeds its bounded list size.")
        return [stable_fem_property_value(item) for item in value]
    raise NativeAnalyzeError("A FEM object contains an unsupported property value.")


def bounded_fem_properties(
    obj: Any,
    *,
    included_groups: Collection[str] | None = None,
    excluded_names: Collection[str] = (),
) -> dict[str, Any]:
    groups = frozenset(included_groups) if included_groups is not None else None
    excluded = frozenset(excluded_names)
    result = {}
    for raw_name in tuple(getattr(obj, "PropertiesList", ()) or ()):
        name = str(raw_name)
        if name in excluded:
            continue
        property_type = ""
        try:
            if groups is not None and str(obj.getGroupOfProperty(name)) not in groups:
                continue
            property_type = str(obj.getTypeIdOfProperty(name))
            if property_type in _SKIPPED_PROPERTY_TYPES or property_type.startswith(
                "App::PropertyLink"
            ):
                continue
            if len(result) >= MAX_PROPERTIES:
                raise NativeAnalyzeError("A FEM object exceeds its bounded property count.")
            result[name] = stable_fem_property_value(obj.getPropertyByName(name))
        except NativeAnalyzeError as exc:
            raise NativeAnalyzeError(
                f"FEM property {name!r} ({property_type or 'unknown type'}) "
                f"cannot be represented safely: {exc}"
            ) from exc
        except Exception:
            continue
    return result
