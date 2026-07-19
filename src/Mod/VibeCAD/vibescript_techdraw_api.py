# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for TechDraw VibeScript programs.

Each graph role has one operation.  Sheet formats, view orientations,
projection directions, dimension kinds, and annotation alignment are explicit
selectors rather than parallel aliases with drifting behavior.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("page", "template", "view", "projection", "dimension", "annotation")
_OUTPUT_TYPES = ("page", "template", "view", "projection", "dimension", "annotation")
_SHEET_SIZES = (
    "a0_landscape",
    "a0_portrait",
    "a1_landscape",
    "a1_portrait",
    "a2_landscape",
    "a2_portrait",
    "a3_landscape",
    "a3_portrait",
    "a4_landscape",
    "a4_portrait",
    "a5_landscape",
    "a5_portrait",
    "letter_landscape",
    "letter_portrait",
    "ledger_landscape",
    "ledger_portrait",
)
_ORIENTATIONS = (
    "front",
    "rear",
    "left",
    "right",
    "top",
    "bottom",
    "isometric",
)
_PROJECTION_DIRECTIONS = (
    "front",
    "left",
    "right",
    "rear",
    "top",
    "bottom",
    "front_top_left",
    "front_top_right",
    "front_bottom_left",
    "front_bottom_right",
)
_DIMENSION_KINDS = (
    "distance",
    "distance_x",
    "distance_y",
    "radius",
    "diameter",
    "angle",
    "angle_3_point",
    "area",
)
_MEASURE_TYPES = ("projected", "true")
_CONVENTIONS = ("first_angle", "third_angle")
_ALIGNMENTS = ("left", "center", "right")
_PROJECTED_ELEMENT = re.compile(r"(?:Edge|Vertex|Face)[0-9]+\Z")
_MAX_LABEL_CHARS = 256
_MAX_TEXT_CHARS = 4096
_MAX_FORMAT_CHARS = 512
_MAX_SOURCES = 32
_MAX_CONTENTS = 128
_MAX_ANNOTATION_LINES = 64
_MAX_EDITABLE_TEXTS = 64
_MAX_REFERENCES = 3
_MAX_COORDINATE_MM = 1.0e7
_MISSING = object()


class TechDrawAPIError(ValueError):
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
) -> TechDrawAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return TechDrawAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _bounded_text(
    operation: str,
    parameter: str,
    value: Any,
    maximum: int,
) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\0" in value:
        raise _error(
            operation,
            parameter,
            f"must be a string of at most {maximum} characters without nulls",
            value,
        )
    return value


def _label(operation: str, value: Any) -> str:
    return _bounded_text(operation, "label", value, _MAX_LABEL_CHARS)


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(operation, parameter, "must be finite", value)
    if clean < minimum or (strict_minimum and clean == minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if clean > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return clean


def _boolean(operation: str, parameter: str, value: Any) -> bool:
    if type(value) is not bool:
        raise _error(operation, parameter, "must be true or false", value)
    return value


def _choice(
    operation: str,
    parameter: str,
    value: Any,
    choices: Sequence[str],
) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _error(operation, parameter, f"must be one of {list(choices)!r}", value)
    return value


def _reference(operation: str, parameter: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            parameter,
            "must contain exactly document_uid and object_name",
            value,
        )
    document_uid = value.get("document_uid")
    object_name = value.get("object_name")
    if (
        not isinstance(document_uid, str)
        or not document_uid
        or document_uid != document_uid.strip()
        or len(document_uid) > 256
        or "\0" in document_uid
    ):
        raise _error(
            operation,
            f"{parameter}.document_uid",
            "must be a non-empty trimmed string of at most 256 characters",
            document_uid,
        )
    if (
        not isinstance(object_name, str)
        or not object_name
        or object_name != object_name.strip()
        or len(object_name) > 128
        or "\0" in object_name
    ):
        raise _error(
            operation,
            f"{parameter}.object_name",
            "must be an exact non-empty FreeCAD object name",
            object_name,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _references(operation: str, value: Any) -> tuple[dict[str, str], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, "sources", "must be a sequence of document references")
    if not 1 <= len(value) <= _MAX_SOURCES:
        raise _error(
            operation,
            "sources",
            f"must contain 1-{_MAX_SOURCES} references",
        )
    result = tuple(
        _reference(operation, f"sources[{index}]", item)
        for index, item in enumerate(value)
    )
    keys = [(item["document_uid"], item["object_name"]) for item in result]
    if len(keys) != len(set(keys)):
        raise _error(operation, "sources", "cannot contain duplicate references")
    return result


def _nested_value(
    operation: str,
    parameter: str,
    value: Any,
    output_types: Sequence[str],
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "techdraw":
        raise _error(
            operation,
            parameter,
            "must be a value returned by the active TechDraw api",
            value,
        )
    if value.output_type not in output_types:
        raise _error(
            operation,
            parameter,
            f"must have output type in {list(output_types)!r}",
            value.output_type,
        )
    return value


def _definition_key(value: DomainValue) -> str:
    return json.dumps(
        value.to_payload(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _editable_texts(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > _MAX_EDITABLE_TEXTS:
        raise _error(
            "template",
            "editable_texts",
            f"must be an object with at most {_MAX_EDITABLE_TEXTS} entries",
            value,
        )
    result: dict[str, str] = {}
    for raw_name, raw_text in value.items():
        name = _bounded_text("template", "editable_texts key", raw_name, 128)
        if not name or name != name.strip():
            raise _error(
                "template",
                "editable_texts key",
                "must be non-empty and trimmed",
                raw_name,
            )
        result[name] = _bounded_text(
            "template", f"editable_texts[{name!r}]", raw_text, 1024
        )
    return result


def _projection_directions(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error("projection", "directions", "must be a sequence")
    if not 1 <= len(value) <= 10:
        raise _error("projection", "directions", "must contain 1-10 directions")
    result = tuple(
        _choice(
            "projection",
            f"directions[{index}]",
            item,
            _PROJECTION_DIRECTIONS,
        )
        for index, item in enumerate(value)
    )
    if result[0] != "front" or "front" not in result:
        raise _error(
            "projection",
            "directions",
            "must begin with front so the native group has one deterministic anchor",
        )
    if len(result) != len(set(result)):
        raise _error("projection", "directions", "cannot contain duplicates")
    return result


def _dimension_references(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error("dimension", "references", "must be a sequence")
    if not 1 <= len(value) <= _MAX_REFERENCES:
        raise _error(
            "dimension",
            "references",
            f"must contain 1-{_MAX_REFERENCES} projected element names",
        )
    result = tuple(str(item) for item in value)
    if any(_PROJECTED_ELEMENT.fullmatch(item) is None for item in result):
        raise _error(
            "dimension",
            "references",
            "must contain exact EdgeN, VertexN, or FaceN names",
            value,
        )
    if len(result) != len(set(result)):
        raise _error("dimension", "references", "cannot contain duplicates")
    return result


def _validate_dimension_reference_shape(
    kind: str,
    references: Sequence[str],
) -> None:
    """Reject impossible kind/element combinations before worker execution."""

    element_types = tuple(re.sub(r"[0-9]+\Z", "", item) for item in references)
    valid = False
    required = ""
    if kind in {"distance", "distance_x", "distance_y"}:
        valid = element_types in {("Edge",), ("Vertex", "Vertex")}
        required = "one EdgeN or exactly two VertexN references"
    elif kind in {"radius", "diameter"}:
        valid = element_types == ("Edge",)
        required = "exactly one circular EdgeN reference"
    elif kind == "angle":
        valid = element_types == ("Edge", "Edge")
        required = "exactly two straight EdgeN references"
    elif kind == "angle_3_point":
        valid = element_types == ("Vertex", "Vertex", "Vertex")
        required = "exactly three VertexN references"
    elif kind == "area":
        valid = element_types == ("Face",)
        required = "exactly one FaceN reference"
    if not valid:
        raise _error(
            "dimension",
            "references",
            f"must contain {required} for kind {kind!r}",
            list(references),
        )


def _annotation_lines(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        values = tuple(value)
    else:
        raise _error("annotation", "text", "must be a string or sequence of strings")
    if not 1 <= len(values) <= _MAX_ANNOTATION_LINES:
        raise _error(
            "annotation",
            "text",
            f"must contain 1-{_MAX_ANNOTATION_LINES} lines",
        )
    return tuple(
        _bounded_text("annotation", f"text[{index}]", item, _MAX_TEXT_CHARS)
        for index, item in enumerate(values)
    )


def _domain_value(
    operation: str,
    output_type: str,
    arguments: Sequence[Any],
    properties: Mapping[str, Any],
) -> DomainValue:
    return DomainValue(
        domain="techdraw",
        operation=operation,
        output_type=output_type,
        arguments=tuple(arguments),
        properties=dict(properties),
    )


class TechDrawDomainAPI:
    """Exact provider-visible TechDraw graph builder."""

    __slots__ = ("_locked",)

    def __init__(
        self,
        exports: Iterable[str] = _EXPORTS,
        output_types: Iterable[str] = _OUTPUT_TYPES,
    ) -> None:
        object.__setattr__(self, "_locked", False)
        if tuple(exports) != _EXPORTS or tuple(output_types) != _OUTPUT_TYPES:
            raise ValueError("The TechDraw API registry does not match its canonical contract.")
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, _name: str, _value: Any) -> None:
        if self._locked:
            raise TypeError("The TechDraw VibeScript api is immutable.")
        object.__setattr__(self, _name, _value)

    @property
    def exported_names(self) -> tuple[str, ...]:
        return _EXPORTS

    def template(
        self,
        sheet_size: str = "a4_landscape",
        *,
        editable_texts: Mapping[str, str] | None = None,
        label: str = "Template",
    ) -> DomainValue:
        """Define one path-free native template by sheet size and editable fields."""

        return _domain_value(
            "template",
            "template",
            (),
            {
                "sheet_size": _choice(
                    "template", "sheet_size", sheet_size, _SHEET_SIZES
                ),
                "editable_texts": _editable_texts(editable_texts),
                "label": _label("template", label),
            },
        )

    def view(
        self,
        sources: Sequence[Mapping[str, str]],
        *,
        orientation: str = "front",
        x_mm: float = 100.0,
        y_mm: float = 100.0,
        scale: float = 1.0,
        hidden_lines: bool = False,
        smooth_lines: bool = True,
        label: str = "View",
    ) -> DomainValue:
        """Define one worker-projected native view of stable document objects."""

        return _domain_value(
            "view",
            "view",
            (_references("view", sources),),
            {
                "orientation": _choice(
                    "view", "orientation", orientation, _ORIENTATIONS
                ),
                "x_mm": _number(
                    "view",
                    "x_mm",
                    x_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "y_mm": _number(
                    "view",
                    "y_mm",
                    y_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "scale": _number(
                    "view",
                    "scale",
                    scale,
                    minimum=0.0,
                    maximum=1.0e6,
                    strict_minimum=True,
                ),
                "hidden_lines": _boolean("view", "hidden_lines", hidden_lines),
                "smooth_lines": _boolean("view", "smooth_lines", smooth_lines),
                "label": _label("view", label),
            },
        )

    def projection(
        self,
        sources: Sequence[Mapping[str, str]],
        *,
        directions: Sequence[str] = ("front", "top", "right"),
        convention: str = "third_angle",
        x_mm: float = 100.0,
        y_mm: float = 100.0,
        scale: float = 1.0,
        spacing_x_mm: float = 15.0,
        spacing_y_mm: float = 15.0,
        hidden_lines: bool = False,
        smooth_lines: bool = True,
        label: str = "Projection Group",
    ) -> DomainValue:
        """Define one native projection group with explicit ordered directions."""

        return _domain_value(
            "projection",
            "projection",
            (_references("projection", sources),),
            {
                "directions": _projection_directions(directions),
                "convention": _choice(
                    "projection", "convention", convention, _CONVENTIONS
                ),
                "x_mm": _number(
                    "projection",
                    "x_mm",
                    x_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "y_mm": _number(
                    "projection",
                    "y_mm",
                    y_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "scale": _number(
                    "projection",
                    "scale",
                    scale,
                    minimum=0.0,
                    maximum=1.0e6,
                    strict_minimum=True,
                ),
                "spacing_x_mm": _number(
                    "projection",
                    "spacing_x_mm",
                    spacing_x_mm,
                    minimum=0.0,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "spacing_y_mm": _number(
                    "projection",
                    "spacing_y_mm",
                    spacing_y_mm,
                    minimum=0.0,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "hidden_lines": _boolean(
                    "projection", "hidden_lines", hidden_lines
                ),
                "smooth_lines": _boolean(
                    "projection", "smooth_lines", smooth_lines
                ),
                "label": _label("projection", label),
            },
        )

    def dimension(
        self,
        source_view: DomainValue,
        kind: str,
        references: Sequence[str],
        *,
        projection_direction: str = "",
        measure: str = "projected",
        x_mm: float = 100.0,
        y_mm: float = 100.0,
        format_spec: str = "",
        over_tolerance: float = 0.0,
        under_tolerance: float = 0.0,
        show_units: bool = True,
        label: str = "Dimension",
    ) -> DomainValue:
        """Define a typed native dimension against projected element names."""

        source = _nested_value(
            "dimension", "source_view", source_view, ("view", "projection")
        )
        if source.output_type == "projection":
            direction = _choice(
                "dimension",
                "projection_direction",
                projection_direction,
                _PROJECTION_DIRECTIONS,
            )
            available = tuple(source.properties.get("directions") or ())
            if direction not in available:
                raise _error(
                    "dimension",
                    "projection_direction",
                    f"must select one of this projection's directions {list(available)!r}",
                    direction,
                )
        elif projection_direction != "":
            raise _error(
                "dimension",
                "projection_direction",
                "must be empty when source_view is a regular view",
                projection_direction,
            )
        else:
            direction = ""
        clean_kind = _choice("dimension", "kind", kind, _DIMENSION_KINDS)
        clean_references = _dimension_references(references)
        _validate_dimension_reference_shape(clean_kind, clean_references)
        return _domain_value(
            "dimension",
            "dimension",
            (source, clean_kind),
            {
                "references": clean_references,
                "projection_direction": direction,
                "measure": _choice("dimension", "measure", measure, _MEASURE_TYPES),
                "x_mm": _number(
                    "dimension",
                    "x_mm",
                    x_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "y_mm": _number(
                    "dimension",
                    "y_mm",
                    y_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "format_spec": _bounded_text(
                    "dimension", "format_spec", format_spec, _MAX_FORMAT_CHARS
                ),
                "over_tolerance": _number(
                    "dimension",
                    "over_tolerance",
                    over_tolerance,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "under_tolerance": _number(
                    "dimension",
                    "under_tolerance",
                    under_tolerance,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "show_units": _boolean("dimension", "show_units", show_units),
                "label": _label("dimension", label),
            },
        )

    def annotation(
        self,
        text: str | Sequence[str],
        *,
        x_mm: float = 100.0,
        y_mm: float = 100.0,
        text_size_mm: float = 4.0,
        alignment: str = "left",
        label: str = "Annotation",
    ) -> DomainValue:
        """Define bounded native annotation text, placement, size, and alignment."""

        return _domain_value(
            "annotation",
            "annotation",
            (_annotation_lines(text),),
            {
                "x_mm": _number(
                    "annotation",
                    "x_mm",
                    x_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "y_mm": _number(
                    "annotation",
                    "y_mm",
                    y_mm,
                    minimum=-_MAX_COORDINATE_MM,
                    maximum=_MAX_COORDINATE_MM,
                ),
                "text_size_mm": _number(
                    "annotation",
                    "text_size_mm",
                    text_size_mm,
                    minimum=0.0,
                    maximum=1000.0,
                    strict_minimum=True,
                ),
                "alignment": _choice(
                    "annotation", "alignment", alignment, _ALIGNMENTS
                ),
                "label": _label("annotation", label),
            },
        )

    def page(
        self,
        page_template: DomainValue,
        contents: Sequence[DomainValue],
        *,
        convention: str = "third_angle",
        scale: float = 1.0,
        label: str = "Page",
    ) -> DomainValue:
        """Compose one template and drawing contents into a native page graph."""

        template_value = _nested_value(
            "page", "page_template", page_template, ("template",)
        )
        if isinstance(contents, (str, bytes)) or not isinstance(contents, Sequence):
            raise _error("page", "contents", "must be a sequence of TechDraw values")
        if not 1 <= len(contents) <= _MAX_CONTENTS:
            raise _error(
                "page",
                "contents",
                f"must contain 1-{_MAX_CONTENTS} values",
            )
        clean_contents = tuple(
            _nested_value(
                "page",
                f"contents[{index}]",
                value,
                ("view", "projection", "dimension", "annotation"),
            )
            for index, value in enumerate(contents)
        )
        if not any(value.output_type in {"view", "projection"} for value in clean_contents):
            raise _error(
                "page",
                "contents",
                "must contain at least one view or projection",
            )
        keys = [_definition_key(value) for value in clean_contents]
        if len(keys) != len(set(keys)):
            raise _error("page", "contents", "cannot contain duplicate definitions")
        content_keys = set(keys)
        for index, value in enumerate(clean_contents):
            if value.output_type == "dimension":
                source_key = _definition_key(value.arguments[0])
                if source_key not in content_keys:
                    raise _error(
                        "page",
                        f"contents[{index}]",
                        "is a dimension whose exact source view/projection is not in the same page contents",
                    )
        clean_convention = _choice(
            "page", "convention", convention, _CONVENTIONS
        )
        for index, value in enumerate(clean_contents):
            if (
                value.output_type == "projection"
                and value.properties.get("convention") != clean_convention
            ):
                raise _error(
                    "page",
                    f"contents[{index}].convention",
                    f"must match the page convention {clean_convention!r}",
                    value.properties.get("convention"),
                )
        return _domain_value(
            "page",
            "page",
            (template_value, clean_contents),
            {
                "convention": clean_convention,
                "scale": _number(
                    "page",
                    "scale",
                    scale,
                    minimum=0.0,
                    maximum=1.0e6,
                    strict_minimum=True,
                ),
                "label": _label("page", label),
            },
        )
