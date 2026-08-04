# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared immutable component-occurrence values for VibeScript domains."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from pathlib import PurePosixPath
import re
from typing import Any

from vibescript_domain_api import DomainValue

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_DOCUMENT_PATH_LENGTH = 2048


def component_placement_contract() -> dict[str, Any]:
    """Return the exact public placement vocabulary used by every domain.

    Keep this description beside the validator so ``read_api`` cannot drift
    away from the values the worker accepts.
    """

    return {
        "forms": {
            "translation": "[x,y,z] millimetres",
            "quaternion": "{'position':[x,y,z], 'rotation':[x,y,z,w]}",
            "axis_angle": (
                "{'position':[x,y,z], 'axis':[x,y,z], 'angle_degrees':n}"
            ),
        },
        "allowed_keys": ["position", "rotation", "axis", "angle_degrees"],
        "defaults": "position=[0,0,0], rotation=[0,0,0,1]",
        "rule": (
            "Use rotation alone, or axis with angle_degrees. Frames and 4x4 "
            "matrices are invalid."
        ),
    }


def _error(operation: str, parameter: str, reason: str, value: Any = None) -> ValueError:
    suffix = "" if value is None else f"; received {value!r}"
    return ValueError(f"api.{operation}: {parameter} {reason}{suffix}.")


def component_label(operation: str, value: Any) -> str:
    label = str(value or "").strip()
    if len(label) > 120:
        raise _error(operation, "label", "must contain at most 120 characters", value)
    return label


def component_reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error(
            operation,
            "source",
            "must be a component reference containing document_uid and object_name",
            value,
        )
    extra = set(value) - {"document_uid", "object_name", "document_path"}
    if extra:
        raise _error(
            operation,
            "source",
            f"contains unsupported keys {sorted(str(item) for item in extra)}",
            value,
        )
    document_uid = str(value.get("document_uid") or "").strip()
    object_name = str(value.get("object_name") or "").strip()
    if not document_uid:
        raise _error(operation, "source.document_uid", "must be non-empty", value)
    if not object_name:
        raise _error(operation, "source.object_name", "must be non-empty", value)
    result = {"document_uid": document_uid, "object_name": object_name}
    if "document_path" in value:
        raw_document_path = value["document_path"]
        if not isinstance(raw_document_path, str):
            raise _error(
                operation,
                "source.document_path",
                "must be a string",
                raw_document_path,
            )
        document_path = raw_document_path.strip()
        if not document_path:
            raise _error(
                operation,
                "source.document_path",
                "must be non-empty",
                raw_document_path,
            )
        if len(document_path) > _MAX_DOCUMENT_PATH_LENGTH:
            raise _error(
                operation,
                "source.document_path",
                f"must contain at most {_MAX_DOCUMENT_PATH_LENGTH} characters",
                document_path,
            )
        if "\x00" in document_path:
            raise _error(
                operation,
                "source.document_path",
                "must not contain a NUL character",
            )
        if "\\" in document_path:
            raise _error(
                operation,
                "source.document_path",
                "must use portable forward slashes",
                document_path,
            )
        if document_path.startswith("/") or _WINDOWS_DRIVE.match(document_path):
            raise _error(
                operation,
                "source.document_path",
                "must be relative to the owning CAD document",
                document_path,
            )
        parts = document_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise _error(
                operation,
                "source.document_path",
                "must not contain empty, current, or parent segments",
                document_path,
            )
        normalized = PurePosixPath(document_path).as_posix()
        if not normalized.casefold().endswith(".fcstd"):
            raise _error(
                operation,
                "source.document_path",
                "must name an .FCStd document",
                document_path,
            )
        result["document_path"] = normalized
    return result


def _vector(
    operation: str,
    parameter: str,
    value: Any,
    *,
    size: int,
) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, parameter, f"must contain exactly {size} numbers", value)
    if len(value) != size:
        raise _error(operation, parameter, f"must contain exactly {size} numbers", value)
    result: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool):
            raise _error(operation, f"{parameter}[{index}]", "must be a finite number", item)
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must be a finite number",
                item,
            ) from exc
        if not math.isfinite(number):
            raise _error(operation, f"{parameter}[{index}]", "must be finite", item)
        result.append(number)
    return result


def component_placement(
    operation: str,
    parameter: str,
    value: Any,
) -> dict[str, list[float]]:
    """Normalize the one placement syntax shared by component-capable domains."""

    if value is None:
        position = [0.0, 0.0, 0.0]
        rotation = [0.0, 0.0, 0.0, 1.0]
    elif isinstance(value, (list, tuple)):
        position = _vector(operation, parameter, value, size=3)
        rotation = [0.0, 0.0, 0.0, 1.0]
    elif isinstance(value, Mapping):
        extra = set(value) - {"position", "rotation", "axis", "angle_degrees"}
        if extra:
            raise _error(
                operation,
                parameter,
                f"contains unsupported keys {sorted(str(item) for item in extra)}",
                value,
            )
        position = _vector(
            operation,
            f"{parameter}.position",
            value.get("position", [0.0, 0.0, 0.0]),
            size=3,
        )
        has_rotation = "rotation" in value
        has_axis = "axis" in value
        has_angle = "angle_degrees" in value
        if has_rotation and (has_axis or has_angle):
            raise _error(
                operation,
                parameter,
                "rotation cannot be combined with axis or angle_degrees",
                value,
            )
        if has_axis != has_angle:
            missing = "angle_degrees" if has_axis else "axis"
            raise _error(
                operation,
                parameter,
                f"axis and angle_degrees must be supplied together; missing {missing}",
                value,
            )
        if has_axis:
            axis = _vector(operation, f"{parameter}.axis", value["axis"], size=3)
            magnitude = math.sqrt(sum(item * item for item in axis))
            if magnitude <= 1.0e-12:
                raise _error(
                    operation,
                    f"{parameter}.axis",
                    "must be non-zero",
                    value["axis"],
                )
            angle = _vector(
                operation,
                f"{parameter}.angle_degrees",
                [value["angle_degrees"]],
                size=1,
            )[0]
            half_angle = math.radians(angle) / 2.0
            scale = math.sin(half_angle) / magnitude
            rotation = [
                axis[0] * scale,
                axis[1] * scale,
                axis[2] * scale,
                math.cos(half_angle),
            ]
        else:
            rotation = _vector(
                operation,
                f"{parameter}.rotation",
                value.get("rotation", [0.0, 0.0, 0.0, 1.0]),
                size=4,
            )
    else:
        raise _error(
            operation,
            parameter,
            "must be [x,y,z], position/rotation, or position/axis/angle_degrees",
            value,
        )
    magnitude = math.sqrt(sum(item * item for item in rotation))
    if magnitude <= 1.0e-12:
        raise _error(operation, f"{parameter}.rotation", "must be non-zero", rotation)
    return {
        "position": position,
        "rotation": [item / magnitude for item in rotation],
    }


def component_value(
    domain: str,
    source: Mapping[str, str],
    *,
    placement: Sequence[float] | Mapping[str, Any] | None = None,
    label: str = "",
    **properties: Any,
) -> DomainValue:
    """Create one declarative linked occurrence in ``domain``."""

    operation = "component"
    return DomainValue(
        domain=domain,
        operation=operation,
        output_type="component_link",
        arguments=(component_reference(operation, source),),
        properties={
            "placement": component_placement(operation, "placement", placement),
            "placement_authored": placement is not None,
            **properties,
            "label": component_label(operation, label),
        },
    )


def instance_values(
    domain: str,
    source: Mapping[str, str],
    placements: Sequence[Sequence[float] | Mapping[str, Any] | None],
    *,
    labels: Sequence[str] | None = None,
    **properties_for_index: Any,
) -> tuple[DomainValue, ...]:
    """Create independently placed occurrences of one reusable definition."""

    operation = "instances"
    reference = component_reference(operation, source)
    if isinstance(placements, (str, bytes)) or not isinstance(placements, Sequence):
        raise _error(
            operation,
            "placements",
            "must be an array of 1-64 placements",
            placements,
        )
    raw_placements = list(placements)
    if not 1 <= len(raw_placements) <= 64:
        raise _error(
            operation,
            "placements",
            "must contain 1-64 placements",
            len(raw_placements),
        )
    if labels is None:
        clean_labels = [""] * len(raw_placements)
    else:
        if isinstance(labels, (str, bytes)) or not isinstance(labels, Sequence):
            raise _error(operation, "labels", "must contain one label per placement", labels)
        if len(labels) != len(raw_placements):
            raise _error(operation, "labels", "must contain one label per placement", labels)
        clean_labels = [component_label(operation, item) for item in labels]
    values = []
    for index, (placement, label) in enumerate(zip(raw_placements, clean_labels)):
        per_item = {
            name: value(index) if callable(value) else value
            for name, value in properties_for_index.items()
        }
        values.append(
            DomainValue(
                domain=domain,
                operation="component",
                output_type="component_link",
                arguments=(dict(reference),),
                properties={
                    "placement": component_placement(
                        operation,
                        f"placements[{index}]",
                        placement,
                    ),
                    "placement_authored": placement is not None,
                    **per_item,
                    "label": label,
                },
            )
        )
    return tuple(values)
