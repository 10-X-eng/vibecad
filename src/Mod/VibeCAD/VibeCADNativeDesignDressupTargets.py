# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Body and subelement targeting shared by Design dress-up operations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_SUBELEMENT = re.compile(r"^(Edge|Face)[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class DesignDressupTarget:
    body: NativeObjectRef
    subelements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesignDressupSelection:
    targets: tuple[DesignDressupTarget, ...]
    use_all_edges: bool


def _target(
    document_uid: str,
    value: Any,
    *,
    operation: str,
    use_all_edges: bool,
    allowed_subelement_types: frozenset[str],
) -> DesignDressupTarget:
    expected = {"object_name"} if use_all_edges else {"object_name", "subelements"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise NativeModelError(f"A {operation} target definition is invalid.")
    body = NativeObjectRef(document_uid, str(value["object_name"]))
    if use_all_edges:
        return DesignDressupTarget(body, ())
    raw = value["subelements"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
        raise NativeModelError(
            f"A {operation} target needs 1 to 64 exact edges or faces."
        )
    subelements = tuple(str(name) for name in raw)
    if (
        len(subelements) != len(set(subelements))
        or any(_SUBELEMENT.fullmatch(name) is None for name in subelements)
        or any(
            not any(name.startswith(prefix) for prefix in allowed_subelement_types)
            for name in subelements
        )
    ):
        allowed = " or ".join(f"{name}N" for name in sorted(allowed_subelement_types))
        raise NativeModelError(
            f"{operation} targets must be distinct exact {allowed} names."
        )
    return DesignDressupTarget(body, subelements)


def prepare_dressup_selection(
    document_uid: str,
    value: Any,
    *,
    operation: str,
    allow_all_edges: bool = True,
    allowed_subelement_types: frozenset[str] = frozenset({"Edge", "Face"}),
) -> DesignDressupSelection:
    if not isinstance(value, Mapping) or set(value) != {"kind", "targets"}:
        raise NativeModelError(f"A {operation} selection definition is invalid.")
    kind = str(value["kind"])
    allowed_kinds = {"explicit", "all_edges"} if allow_all_edges else {"explicit"}
    if kind not in allowed_kinds:
        raise NativeModelError(f"That {operation} selection mode is unavailable.")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 16:
        raise NativeModelError(f"A {operation} requires 1 to 16 exact target Bodies.")
    use_all_edges = kind == "all_edges"
    targets = tuple(
        _target(
            document_uid,
            item,
            operation=operation,
            use_all_edges=use_all_edges,
            allowed_subelement_types=allowed_subelement_types,
        )
        for item in raw_targets
    )
    body_names = tuple(target.body.object_name for target in targets)
    if len(body_names) != len(set(body_names)):
        raise NativeModelError(f"A {operation} cannot repeat the same target Body.")
    return DesignDressupSelection(targets, use_all_edges)


def preflight_dressup_selection(
    document: Any,
    selection: DesignDressupSelection,
    *,
    operation: str,
) -> tuple[Any, ...]:
    bodies = []
    for target in selection.targets:
        body = resolve_object(
            document,
            target.body,
            expected_types=("PartDesign::Body",),
        )
        shape = getattr(body, "Shape", None)
        if (
            shape is None
            or shape.isNull()
            or not shape.isValid()
            or len(shape.Solids) != 1
        ):
            raise NativeModelError(
                f"Every {operation} target Body must contain one valid solid."
            )
        for name in target.subelements:
            try:
                element = shape.getElement(name)
            except Exception as exc:
                raise NativeModelError(
                    f"{operation} target {target.body.object_name}.{name} no longer exists."
                ) from exc
            expected_type = "Edge" if name.startswith("Edge") else "Face"
            if str(getattr(element, "ShapeType", "")) != expected_type:
                raise NativeModelError(
                    f"A {operation} subelement changed geometric type."
                )
        bodies.append(body)
    return tuple(bodies)


def dressup_target_elements(
    targets: tuple[DesignDressupTarget, ...],
) -> tuple[list[int], list[str]]:
    offsets = [0]
    elements: list[str] = []
    for target in targets:
        elements.extend(target.subelements)
        offsets.append(len(elements))
    return offsets, elements
