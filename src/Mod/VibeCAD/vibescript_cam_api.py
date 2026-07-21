# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for CAM VibeScript programs.

The provider surface deliberately exposes one operation for each graph role.
Tool shapes, machining strategies, and postprocessors are selectors on those
operations rather than parallel aliases with drifting schemas.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = (
    "job",
    "stock",
    "tool",
    "operation",
    "generate_toolpath",
    "postprocess",
)
_OUTPUT_TYPES = ("job", "stock", "tool", "operation", "toolpath")
_TOOL_KINDS = ("endmill", "ballend", "drill", "chamfer", "vbit")
_STRATEGIES = ("profile", "pocket", "drilling", "face")
_PROCESSORS = ("grbl", "linuxcnc")
_FIXTURES = tuple(f"G{number}" for number in range(54, 60))
_OBJECT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FACE_NAME = re.compile(r"Face[1-9][0-9]*\Z")
_INTERFACE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
_MAX_LABEL_CHARS = 256
_MAX_DESCRIPTION_CHARS = 4096
_MAX_MODELS = 16
_MAX_TOOLS = 32
_MAX_OPERATIONS = 128
_MAX_SELECTIONS = 128
_MAX_LENGTH_MM = 1.0e9
_MISSING = object()


class CAMAPIError(ValueError):
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
) -> CAMAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return CAMAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


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


def _integer(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(
            operation,
            parameter,
            f"must be an integer from {minimum} through {maximum}",
            value,
        )
    return value


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


def _bounded_text(operation: str, parameter: str, value: Any, maximum: int) -> str:
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
        or len(object_name) > 128
        or _OBJECT_NAME.fullmatch(object_name) is None
    ):
        raise _error(
            operation,
            f"{parameter}.object_name",
            "must be an exact FreeCAD internal object name",
            object_name,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _references(operation: str, parameter: str, value: Any) -> tuple[dict[str, str], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, parameter, "must be a sequence of document references")
    if not 1 <= len(value) <= _MAX_MODELS:
        raise _error(operation, parameter, f"must contain 1-{_MAX_MODELS} references")
    result = tuple(
        _reference(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(value)
    )
    keys = [(item["document_uid"], item["object_name"]) for item in result]
    if len(keys) != len(set(keys)):
        raise _error(operation, parameter, "cannot contain duplicate references")
    return result


def _selector(operation: str, parameter: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _error(
            operation,
            parameter,
            "must be a subelement or published_interface selector",
            value,
        )
    kind = value.get("type")
    if kind == "subelement" and set(value) == {"type", "name"}:
        name = value.get("name")
        if not isinstance(name, str) or _FACE_NAME.fullmatch(name) is None:
            raise _error(
                operation,
                f"{parameter}.name",
                "must be an exact FaceN name",
                name,
            )
        return {"type": "subelement", "name": name}
    if kind == "published_interface" and set(value) == {
        "type",
        "interface_name",
    }:
        name = value.get("interface_name")
        if not isinstance(name, str) or _INTERFACE_NAME.fullmatch(name) is None:
            raise _error(
                operation,
                f"{parameter}.interface_name",
                "must be a 1-128 character stable interface identifier",
                name,
            )
        return {"type": "published_interface", "interface_name": name}
    raise _error(
        operation,
        parameter,
        "must contain exactly type/name or type/interface_name",
        value,
    )


def _selections(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error("operation", "selections", "must be a sequence")
    if len(value) > _MAX_SELECTIONS:
        raise _error(
            "operation",
            "selections",
            f"must contain at most {_MAX_SELECTIONS} entries",
        )
    result = []
    for index, item in enumerate(value):
        parameter = f"selections[{index}]"
        if not isinstance(item, Mapping) or set(item) != {"target", "selection"}:
            raise _error(
                "operation",
                parameter,
                "must contain exactly target and selection",
                item,
            )
        result.append(
            {
                "target": _reference("operation", f"{parameter}.target", item["target"]),
                "selection": _selector(
                    "operation", f"{parameter}.selection", item["selection"]
                ),
            }
        )
    keys = [
        json.dumps(item, sort_keys=True, separators=(",", ":")) for item in result
    ]
    if len(keys) != len(set(keys)):
        raise _error("operation", "selections", "cannot contain duplicates")
    return tuple(result)


def _domain_value(
    operation: str,
    parameter: str,
    value: Any,
    output_types: set[str],
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "cam":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this CAM api",
            type(value).__name__,
        )
    if value.output_type not in output_types:
        raise _error(
            operation,
            parameter,
            f"must have type {sorted(output_types)!r}",
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


def _domain_sequence(
    operation: str,
    parameter: str,
    value: Any,
    output_types: set[str],
    *,
    minimum: int,
    maximum: int,
) -> tuple[DomainValue, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(operation, parameter, "must be a sequence of CAM api values")
    if not minimum <= len(value) <= maximum:
        raise _error(
            operation,
            parameter,
            f"must contain {minimum}-{maximum} values",
        )
    result = tuple(
        _domain_value(operation, f"{parameter}[{index}]", item, output_types)
        for index, item in enumerate(value)
    )
    keys = [_definition_key(item) for item in result]
    if len(keys) != len(set(keys)):
        raise _error(operation, parameter, "cannot contain duplicate definitions")
    return result


def _optional_number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> float | None:
    return (
        None
        if value is None
        else _number(
            operation,
            parameter,
            value,
            minimum=minimum,
            maximum=maximum,
            strict_minimum=strict_minimum,
        )
    )


class CAMDomainAPI:
    """Exact native CAM job, operation, toolpath, and postprocessing graph."""

    __slots__ = ()

    domain = "cam"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        declared_outputs = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_exports != _EXPORTS:
            raise RuntimeError(
                "CAM pack exports do not match the production runtime contract: "
                f"expected {_EXPORTS!r}, received {declared_exports!r}."
            )
        if declared_outputs != _OUTPUT_TYPES:
            raise RuntimeError(
                "CAM pack must publish exactly job, stock, tool, operation, and toolpath."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="cam",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def stock(
        self,
        models: Sequence[Mapping[str, str]],
        *,
        x_negative_mm: float,
        x_positive_mm: float,
        y_negative_mm: float,
        y_positive_mm: float,
        z_negative_mm: float,
        z_positive_mm: float,
        label: str = "",
    ) -> DomainValue:
        """Define one native FromBase stock around exact model snapshots."""

        margins = {
            name: _number(
                "stock",
                name,
                value,
                minimum=0.0,
                maximum=_MAX_LENGTH_MM,
            )
            for name, value in (
                ("x_negative_mm", x_negative_mm),
                ("x_positive_mm", x_positive_mm),
                ("y_negative_mm", y_negative_mm),
                ("y_positive_mm", y_positive_mm),
                ("z_negative_mm", z_negative_mm),
                ("z_positive_mm", z_positive_mm),
            )
        }
        return self._value(
            "stock",
            "stock",
            _references("stock", "models", models),
            **margins,
            label=_label("stock", label),
        )

    def tool(
        self,
        kind: str,
        *,
        diameter_mm: float,
        length_mm: float,
        flutes: int,
        tool_number: int,
        spindle_rpm: float,
        horizontal_feed_mm_per_min: float,
        vertical_feed_mm_per_min: float,
        cutting_edge_height_mm: float | None = None,
        shank_diameter_mm: float | None = None,
        tip_angle_deg: float | None = None,
        cutting_edge_angle_deg: float | None = None,
        tip_diameter_mm: float | None = None,
        spindle_direction: str = "forward",
        label: str = "",
    ) -> DomainValue:
        """Define one native tool bit/controller selected by ``kind``."""

        clean_kind = _choice("tool", "kind", kind, _TOOL_KINDS)
        diameter = _number(
            "tool",
            "diameter_mm",
            diameter_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
            strict_minimum=True,
        )
        length = _number(
            "tool",
            "length_mm",
            length_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
            strict_minimum=True,
        )
        cutting_height = _optional_number(
            "tool",
            "cutting_edge_height_mm",
            cutting_edge_height_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
            strict_minimum=True,
        )
        shank = _optional_number(
            "tool",
            "shank_diameter_mm",
            shank_diameter_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
            strict_minimum=True,
        )
        tip_angle = _optional_number(
            "tool",
            "tip_angle_deg",
            tip_angle_deg,
            minimum=0.0,
            maximum=180.0,
            strict_minimum=True,
        )
        cutting_angle = _optional_number(
            "tool",
            "cutting_edge_angle_deg",
            cutting_edge_angle_deg,
            minimum=0.0,
            maximum=180.0,
            strict_minimum=True,
        )
        tip_diameter = _optional_number(
            "tool",
            "tip_diameter_mm",
            tip_diameter_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
        )
        if clean_kind in {"endmill", "ballend"}:
            if cutting_height is None or shank is None:
                raise _error(
                    "tool",
                    "geometry",
                    f"{clean_kind} requires cutting_edge_height_mm and shank_diameter_mm",
                )
            if any(value is not None for value in (tip_angle, cutting_angle, tip_diameter)):
                raise _error(
                    "tool",
                    "geometry",
                    f"{clean_kind} cannot set tip or cutting-angle parameters",
                )
            if cutting_height > length:
                raise _error(
                    "tool",
                    "cutting_edge_height_mm",
                    "cannot exceed length_mm",
                    cutting_height,
                )
            if clean_kind == "ballend" and cutting_height <= diameter / 2.0:
                raise _error(
                    "tool",
                    "cutting_edge_height_mm",
                    "must be greater than the ball radius",
                    cutting_height,
                )
        elif clean_kind == "drill":
            if tip_angle is None:
                raise _error("tool", "tip_angle_deg", "is required for drill")
            if any(
                value is not None
                for value in (cutting_height, shank, cutting_angle, tip_diameter)
            ):
                raise _error(
                    "tool",
                    "geometry",
                    "drill cannot set mill or conical-tool parameters",
                )
            if tip_angle >= 180.0:
                raise _error("tool", "tip_angle_deg", "must be below 180", tip_angle)
            tip_height = (diameter / 2.0) / math.tan(
                math.radians(tip_angle / 2.0)
            )
            if tip_height >= length:
                raise _error(
                    "tool",
                    "length_mm",
                    f"must exceed the {tip_height:.12g} mm drill-tip height",
                    length,
                )
        else:
            if cutting_height is None or shank is None or cutting_angle is None:
                raise _error(
                    "tool",
                    "geometry",
                    f"{clean_kind} requires cutting edge height, shank diameter, and angle",
                )
            if tip_diameter is None:
                raise _error(
                    "tool", "tip_diameter_mm", f"is required for {clean_kind}"
                )
            if tip_angle is not None:
                raise _error(
                    "tool", "tip_angle_deg", f"is not used by {clean_kind}", tip_angle
                )
            if cutting_height > length:
                raise _error(
                    "tool",
                    "cutting_edge_height_mm",
                    "cannot exceed length_mm",
                    cutting_height,
                )
            if cutting_angle >= 180.0:
                raise _error(
                    "tool",
                    "cutting_edge_angle_deg",
                    "must be below 180",
                    cutting_angle,
                )
            if tip_diameter >= diameter:
                raise _error(
                    "tool",
                    "tip_diameter_mm",
                    "must be smaller than diameter_mm",
                    tip_diameter,
                )
            cone_height = ((diameter - tip_diameter) / 2.0) / math.tan(
                math.radians(cutting_angle / 2.0)
            )
            if cone_height > cutting_height:
                raise _error(
                    "tool",
                    "cutting_edge_height_mm",
                    f"must be at least {cone_height:.12g} for the cutting cone",
                    cutting_height,
                )
        return self._value(
            "tool",
            "tool",
            clean_kind,
            diameter_mm=diameter,
            length_mm=length,
            flutes=_integer("tool", "flutes", flutes, minimum=1, maximum=100),
            tool_number=_integer(
                "tool", "tool_number", tool_number, minimum=1, maximum=10_000
            ),
            spindle_rpm=_number(
                "tool",
                "spindle_rpm",
                spindle_rpm,
                minimum=0.0,
                maximum=10_000_000.0,
                strict_minimum=True,
            ),
            horizontal_feed_mm_per_min=_number(
                "tool",
                "horizontal_feed_mm_per_min",
                horizontal_feed_mm_per_min,
                minimum=0.0,
                maximum=1.0e9,
                strict_minimum=True,
            ),
            vertical_feed_mm_per_min=_number(
                "tool",
                "vertical_feed_mm_per_min",
                vertical_feed_mm_per_min,
                minimum=0.0,
                maximum=1.0e9,
                strict_minimum=True,
            ),
            cutting_edge_height_mm=cutting_height,
            shank_diameter_mm=shank,
            tip_angle_deg=tip_angle,
            cutting_edge_angle_deg=cutting_angle,
            tip_diameter_mm=tip_diameter,
            spindle_direction=_choice(
                "tool",
                "spindle_direction",
                spindle_direction,
                ("forward", "reverse"),
            ),
            label=_label("tool", label),
        )

    def operation(
        self,
        strategy: str,
        tool: DomainValue,
        *,
        selections: Sequence[Mapping[str, Any]] = (),
        start_depth_mm: float,
        final_depth_mm: float,
        step_down_mm: float | None = None,
        step_over_percent: int | None = None,
        side: str | None = None,
        boundary: str | None = None,
        peck_depth_mm: float | None = None,
        coolant: str = "none",
        label: str = "",
    ) -> DomainValue:
        """Define one strategy-selected native CAM operation."""

        clean_strategy = _choice("operation", "strategy", strategy, _STRATEGIES)
        clean_tool = _domain_value("operation", "tool", tool, {"tool"})
        clean_selections = _selections(selections)
        start = _number(
            "operation",
            "start_depth_mm",
            start_depth_mm,
            minimum=-_MAX_LENGTH_MM,
            maximum=_MAX_LENGTH_MM,
        )
        final = _number(
            "operation",
            "final_depth_mm",
            final_depth_mm,
            minimum=-_MAX_LENGTH_MM,
            maximum=_MAX_LENGTH_MM,
        )
        if final >= start:
            raise _error(
                "operation",
                "final_depth_mm",
                "must be below start_depth_mm",
                final,
            )
        clean_step_down = _optional_number(
            "operation",
            "step_down_mm",
            step_down_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
            strict_minimum=True,
        )
        clean_peck = _optional_number(
            "operation",
            "peck_depth_mm",
            peck_depth_mm,
            minimum=0.0,
            maximum=_MAX_LENGTH_MM,
        )
        if step_over_percent is not None:
            clean_step_over = _integer(
                "operation",
                "step_over_percent",
                step_over_percent,
                minimum=1,
                maximum=100,
            )
        else:
            clean_step_over = None
        tool_kind = str(clean_tool.arguments[0])
        allowed_tools = {
            "profile": {"endmill", "ballend", "chamfer", "vbit"},
            "pocket": {"endmill", "ballend"},
            "drilling": {"drill"},
            "face": {"endmill", "ballend"},
        }[clean_strategy]
        if tool_kind not in allowed_tools:
            raise _error(
                "operation",
                "tool",
                f"kind {tool_kind!r} cannot run {clean_strategy}; allowed {sorted(allowed_tools)!r}",
            )
        if clean_strategy == "profile":
            if clean_step_down is None or side not in {"inside", "outside"}:
                raise _error(
                    "operation",
                    "strategy parameters",
                    "profile requires step_down_mm and side='inside'|'outside'",
                )
            if any(value is not None for value in (clean_step_over, boundary, clean_peck)):
                raise _error(
                    "operation",
                    "strategy parameters",
                    "profile cannot set step_over_percent, boundary, or peck_depth_mm",
                )
        elif clean_strategy == "pocket":
            if not clean_selections or clean_step_down is None or clean_step_over is None:
                raise _error(
                    "operation",
                    "strategy parameters",
                    "pocket requires selections, step_down_mm, and step_over_percent",
                )
            if any(value is not None for value in (side, boundary, clean_peck)):
                raise _error(
                    "operation",
                    "strategy parameters",
                    "pocket cannot set side, boundary, or peck_depth_mm",
                )
        elif clean_strategy == "drilling":
            if not clean_selections or clean_peck is None:
                raise _error(
                    "operation",
                    "strategy parameters",
                    "drilling requires selections and peck_depth_mm",
                )
            if any(
                value is not None
                for value in (clean_step_down, clean_step_over, side, boundary)
            ):
                raise _error(
                    "operation",
                    "strategy parameters",
                    "drilling cannot set milling parameters",
                )
        else:
            if clean_selections:
                raise _error(
                    "operation",
                    "selections",
                    "face uses its boundary selector and cannot receive face selections",
                )
            if (
                clean_step_down is None
                or clean_step_over is None
                or boundary not in {"boundbox", "stock", "perimeter"}
            ):
                raise _error(
                    "operation",
                    "strategy parameters",
                    "face requires step_down_mm, step_over_percent, and a boundary",
                )
            if side is not None or clean_peck is not None:
                raise _error(
                    "operation",
                    "strategy parameters",
                    "face cannot set side or peck_depth_mm",
                )
        return self._value(
            "operation",
            "operation",
            clean_strategy,
            clean_tool,
            clean_selections,
            start_depth_mm=start,
            final_depth_mm=final,
            step_down_mm=clean_step_down,
            step_over_percent=clean_step_over,
            side=side,
            boundary=boundary,
            peck_depth_mm=clean_peck,
            coolant=_choice(
                "operation", "coolant", coolant, ("none", "flood", "mist")
            ),
            label=_label("operation", label),
        )

    def generate_toolpath(
        self,
        stock: DomainValue,
        operations: Sequence[DomainValue],
        *,
        simulation_resolution_mm: float,
        require_collision_free: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Generate and simulate exact native operation paths in the worker."""

        return self._value(
            "generate_toolpath",
            "toolpath",
            _domain_value("generate_toolpath", "stock", stock, {"stock"}),
            _domain_sequence(
                "generate_toolpath",
                "operations",
                operations,
                {"operation"},
                minimum=1,
                maximum=_MAX_OPERATIONS,
            ),
            simulation_resolution_mm=_number(
                "generate_toolpath",
                "simulation_resolution_mm",
                simulation_resolution_mm,
                minimum=0.0,
                maximum=_MAX_LENGTH_MM,
                strict_minimum=True,
            ),
            require_collision_free=_boolean(
                "generate_toolpath",
                "require_collision_free",
                require_collision_free,
            ),
            label=_label("generate_toolpath", label),
        )

    def postprocess(
        self,
        toolpath: DomainValue,
        *,
        processor: str,
        units: str = "metric",
        comments: bool = True,
        line_numbers: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Postprocess a generated path through one allowlisted native processor."""

        generated = _domain_value(
            "postprocess", "toolpath", toolpath, {"toolpath"}
        )
        if generated.operation != "generate_toolpath":
            raise _error(
                "postprocess",
                "toolpath",
                "must be returned directly by api.generate_toolpath",
            )
        return self._value(
            "postprocess",
            "toolpath",
            generated,
            processor=_choice("postprocess", "processor", processor, _PROCESSORS),
            units=_choice("postprocess", "units", units, ("metric", "imperial")),
            comments=_boolean("postprocess", "comments", comments),
            line_numbers=_boolean(
                "postprocess", "line_numbers", line_numbers
            ),
            label=_label("postprocess", label),
        )

    def job(
        self,
        models: Sequence[Mapping[str, str]],
        stock: DomainValue,
        tools: Sequence[DomainValue],
        operations: Sequence[DomainValue],
        toolpath: DomainValue,
        *,
        geometry_tolerance_mm: float = 0.01,
        fixtures: Sequence[str] = ("G54",),
        description: str = "",
        label: str = "",
    ) -> DomainValue:
        """Bind one complete returned CAM graph into a native Path Job."""

        clean_models = _references("job", "models", models)
        clean_stock = _domain_value("job", "stock", stock, {"stock"})
        clean_tools = _domain_sequence(
            "job",
            "tools",
            tools,
            {"tool"},
            minimum=1,
            maximum=_MAX_TOOLS,
        )
        clean_operations = _domain_sequence(
            "job",
            "operations",
            operations,
            {"operation"},
            minimum=1,
            maximum=_MAX_OPERATIONS,
        )
        clean_toolpath = _domain_value("job", "toolpath", toolpath, {"toolpath"})
        if clean_toolpath.operation != "postprocess":
            raise _error(
                "job",
                "toolpath",
                "must be returned directly by api.postprocess",
            )
        if tuple(clean_stock.arguments[0]) != clean_models:
            raise _error(
                "job", "models", "must exactly match the models used by stock"
            )
        numbers = [int(item.properties["tool_number"]) for item in clean_tools]
        if len(numbers) != len(set(numbers)):
            raise _error("job", "tools", "must use unique tool_number values")
        tool_keys = {_definition_key(item) for item in clean_tools}
        for index, item in enumerate(clean_operations):
            operation_tool = item.arguments[1]
            if _definition_key(operation_tool) not in tool_keys:
                raise _error(
                    "job",
                    f"operations[{index}].tool",
                    "must be returned in the tools sequence",
                )
        if isinstance(fixtures, (str, bytes)) or not isinstance(fixtures, Sequence):
            raise _error("job", "fixtures", "must be a sequence of G54-G59 values")
        clean_fixtures = tuple(
            _choice("job", f"fixtures[{index}]", item, _FIXTURES)
            for index, item in enumerate(fixtures)
        )
        if not 1 <= len(clean_fixtures) <= len(_FIXTURES):
            raise _error("job", "fixtures", "must contain 1-6 entries")
        if len(clean_fixtures) != len(set(clean_fixtures)):
            raise _error("job", "fixtures", "cannot contain duplicates")
        return self._value(
            "job",
            "job",
            clean_models,
            clean_stock,
            clean_tools,
            clean_operations,
            clean_toolpath,
            geometry_tolerance_mm=_number(
                "job",
                "geometry_tolerance_mm",
                geometry_tolerance_mm,
                minimum=0.0,
                maximum=1000.0,
                strict_minimum=True,
            ),
            fixtures=clean_fixtures,
            description=_bounded_text(
                "job", "description", description, _MAX_DESCRIPTION_CHARS
            ),
            label=_label("job", label),
        )
