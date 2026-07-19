# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Material VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any
import uuid

from vibescript_domain_api import DomainValue


_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_REQUIREMENTS = 64
_MAX_PROPERTY_NAME_CHARS = 128
_MAX_LABEL_CHARS = 256
_MAX_DISPLAY_MODE_CHARS = 128
_MISSING = object()


class MaterialAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> MaterialAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return MaterialAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS:
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters",
            value,
        )
    if "\0" in value:
        raise _error(operation, "label", "cannot contain a null character")
    return value


def _material_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _error("material", "material_uuid", "must be a UUID string", value)
    try:
        parsed = uuid.UUID(value.strip())
    except (AttributeError, ValueError) as exc:
        raise _error("material", "material_uuid", "must be a valid UUID", value) from exc
    if parsed.version is None:
        raise _error("material", "material_uuid", "must be a valid UUID", value)
    return str(parsed)


def _requirements(operation: str, parameter: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise _error(
            operation,
            parameter,
            "must be a sequence of exact native material property names",
            value,
        )
    if len(value) > _MAX_REQUIREMENTS:
        raise _error(
            operation,
            parameter,
            f"may contain at most {_MAX_REQUIREMENTS} names",
        )
    result: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, str):
            raise _error(operation, f"{parameter}[{index}]", "must be a string", raw)
        clean = raw.strip()
        if (
            not clean
            or len(clean) > _MAX_PROPERTY_NAME_CHARS
            or any(ord(character) < 32 for character in clean)
        ):
            raise _error(
                operation,
                f"{parameter}[{index}]",
                f"must be a printable name of at most {_MAX_PROPERTY_NAME_CHARS} characters",
                raw,
            )
        if clean in result:
            raise _error(
                operation,
                parameter,
                f"contains duplicate property name {clean!r}",
            )
        result.append(clean)
    return tuple(result)


def _reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"document_uid", "object_name"}:
        raise _error(
            operation,
            "target",
            "must be a stable reference with exactly document_uid and object_name",
            value,
        )
    document_uid = value.get("document_uid")
    object_name = value.get("object_name")
    if (
        not isinstance(document_uid, str)
        or not document_uid.strip()
        or len(document_uid) > 128
        or "\0" in document_uid
    ):
        raise _error(
            operation,
            "target.document_uid",
            "must be a non-empty string of at most 128 characters",
            document_uid,
        )
    if (
        not isinstance(object_name, str)
        or _OBJECT_NAME.fullmatch(object_name.strip()) is None
        or len(object_name.strip()) > 128
    ):
        raise _error(
            operation,
            "target.object_name",
            "must be an exact FreeCAD internal object name",
            object_name,
        )
    return {
        "document_uid": document_uid.strip(),
        "object_name": object_name.strip(),
    }


def _card(
    operation: str,
    value: Any,
    *,
    optional: bool = False,
) -> DomainValue | None:
    if value is None and optional:
        return None
    if not isinstance(value, DomainValue) or (
        value.domain,
        value.operation,
        value.output_type,
    ) != ("material", "material", "material_card"):
        raise _error(operation, "card", "must be returned by api.material", value)
    return value


def _color(operation: str, parameter: str, value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(
            operation,
            parameter,
            "must be RGB [r, g, b] with channels from 0 to 1",
            value,
        )
    result: list[float] = []
    for index, raw in enumerate(value):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must be a finite number from 0 to 1",
                raw,
            )
        channel = float(raw)
        if not math.isfinite(channel) or not 0.0 <= channel <= 1.0:
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must be in the inclusive range 0-1",
                raw,
            )
        result.append(channel)
    return tuple(result)


def _optional_percent(operation: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or type(value) is not int or not 0 <= value <= 100:
        raise _error(
            operation,
            "transparency",
            "must be an integer percentage from 0 through 100 or None",
            value,
        )
    return value


def _optional_size(operation: str, parameter: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number or None", value)
    clean = float(value)
    if not math.isfinite(clean) or not 1.0 <= clean <= 64.0:
        raise _error(
            operation,
            parameter,
            "must be in the inclusive range 1-64",
            value,
        )
    return clean


def _optional_bool(operation: str, parameter: str, value: Any) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise _error(operation, parameter, "must be true, false, or None", value)
    return value


def _display_mode(operation: str, value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > _MAX_DISPLAY_MODE_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise _error(
            operation,
            "display_mode",
            f"must be a printable non-empty string of at most {_MAX_DISPLAY_MODE_CHARS} characters or None",
            value,
        )
    return value.strip()


class MaterialDomainAPI:
    """Exact material-card, physical-assignment, and display-style API."""

    __slots__ = ()

    domain = "material"
    exported_names = ("material", "assign", "appearance")

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        declared_outputs = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_exports != self.exported_names:
            raise RuntimeError(
                "Material pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared_exports!r}."
            )
        if declared_outputs != ("material_assignment", "appearance"):
            raise RuntimeError(
                "Material pack must publish exactly material_assignment and appearance."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="material",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def material(
        self,
        material_uuid: str,
        *,
        require_physical_properties: Sequence[str] = (),
        require_appearance_properties: Sequence[str] = (),
        label: str = "",
    ) -> DomainValue:
        """Select one exact catalog card and declare every property the design consumes."""

        return self._value(
            "material",
            "material_card",
            _material_uuid(material_uuid),
            require_physical_properties=_requirements(
                "material",
                "require_physical_properties",
                require_physical_properties,
            ),
            require_appearance_properties=_requirements(
                "material",
                "require_appearance_properties",
                require_appearance_properties,
            ),
            label=_label("material", label),
        )

    def assign(
        self,
        target: Mapping[str, str],
        card: DomainValue,
        *,
        label: str = "",
    ) -> DomainValue:
        """Own the target's physical ShapeMaterial while preserving all display styling."""

        return self._value(
            "assign",
            "material_assignment",
            _reference("assign", target),
            _card("assign", card),
            label=_label("assign", label),
        )

    def appearance(
        self,
        target: Mapping[str, str],
        card: DomainValue | None = None,
        *,
        shape_color: Sequence[float] | None = None,
        line_color: Sequence[float] | None = None,
        point_color: Sequence[float] | None = None,
        transparency: int | None = None,
        line_width: float | None = None,
        point_size: float | None = None,
        display_mode: str | None = None,
        visibility: bool | None = None,
        selectable: bool | None = None,
        label: str = "",
    ) -> DomainValue:
        """Own one card-derived and/or explicit display subset without changing ShapeMaterial."""

        appearance_card = _card("appearance", card, optional=True)
        properties = {
            "shape_color": _color("appearance", "shape_color", shape_color),
            "line_color": _color("appearance", "line_color", line_color),
            "point_color": _color("appearance", "point_color", point_color),
            "transparency": _optional_percent("appearance", transparency),
            "line_width": _optional_size("appearance", "line_width", line_width),
            "point_size": _optional_size("appearance", "point_size", point_size),
            "display_mode": _display_mode("appearance", display_mode),
            "visibility": _optional_bool("appearance", "visibility", visibility),
            "selectable": _optional_bool("appearance", "selectable", selectable),
            "label": _label("appearance", label),
        }
        if appearance_card is None and all(
            value is None for key, value in properties.items() if key != "label"
        ):
            raise _error(
                "appearance",
                "appearance properties",
                "must include a material card or at least one display change",
            )
        return self._value(
            "appearance",
            "appearance",
            _reference("appearance", target),
            appearance_card,
            **properties,
        )
