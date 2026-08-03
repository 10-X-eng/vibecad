# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for Robot VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_component_api import component_value, instance_values


_EXPORTS = (
    "component",
    "instances",
    "robot",
    "waypoint",
    "trajectory",
    "dressup",
    "simulate",
)
_LEGACY_EXPORTS = ("robot", "waypoint", "trajectory", "dressup", "simulate")
_OUTPUT_TYPES = (
    "component_link",
    "robot",
    "trajectory",
    "dressup",
    "simulation",
)
_LEGACY_OUTPUT_TYPES = ("robot", "trajectory", "dressup", "simulation")
_MAX_LABEL_CHARS = 256
_MAX_WAYPOINTS = 512
_MAX_COORDINATE = 1.0e9
_MAX_AXIS_LENGTH = 1.0e7
_MAX_ANGLE = 36000.0
_MAX_VELOCITY = 1.0e7
_MISSING = object()
_WAYPOINT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")

_KUKA_IR500 = (
    {
        "a": 500.0,
        "alpha": -90.0,
        "d": 1045.0,
        "theta": 0.0,
        "rotation_direction": -1,
        "maximum_angle": 185.0,
        "minimum_angle": -185.0,
        "maximum_velocity": 156.0,
    },
    {
        "a": 1300.0,
        "alpha": 0.0,
        "d": 0.0,
        "theta": 0.0,
        "rotation_direction": 1,
        "maximum_angle": 35.0,
        "minimum_angle": -155.0,
        "maximum_velocity": 156.0,
    },
    {
        "a": 55.0,
        "alpha": 90.0,
        "d": 0.0,
        "theta": -90.0,
        "rotation_direction": 1,
        "maximum_angle": 154.0,
        "minimum_angle": -130.0,
        "maximum_velocity": 156.0,
    },
    {
        "a": 0.0,
        "alpha": -90.0,
        "d": -1025.0,
        "theta": 0.0,
        "rotation_direction": 1,
        "maximum_angle": 350.0,
        "minimum_angle": -350.0,
        "maximum_velocity": 330.0,
    },
    {
        "a": 0.0,
        "alpha": 90.0,
        "d": 0.0,
        "theta": 0.0,
        "rotation_direction": 1,
        "maximum_angle": 130.0,
        "minimum_angle": -130.0,
        "maximum_velocity": 330.0,
    },
    {
        "a": 0.0,
        "alpha": 180.0,
        "d": -300.0,
        "theta": 0.0,
        "rotation_direction": 1,
        "maximum_angle": 350.0,
        "minimum_angle": -350.0,
        "maximum_velocity": 615.0,
    },
)
_KINEMATIC_FIELDS = frozenset(_KUKA_IR500[0])


class RobotAPIError(ValueError):
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
) -> RobotAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return RobotAPIError(
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


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LABEL_CHARS or "\0" in value:
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            value,
        )
    return value


def _vector(
    operation: str,
    parameter: str,
    value: Any,
    *,
    size: int,
    limit: float = _MAX_COORDINATE,
) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise _error(operation, parameter, f"must contain exactly {size} numbers", value)
    return tuple(
        _number(
            operation,
            f"{parameter}[{index}]",
            item,
            minimum=-limit,
            maximum=limit,
        )
        for index, item in enumerate(value)
    )


def _placement(operation: str, parameter: str, value: Any) -> dict[str, tuple[float, ...]]:
    if value is None:
        position = (0.0, 0.0, 0.0)
        rotation = (0.0, 0.0, 0.0, 1.0)
    elif isinstance(value, (list, tuple)):
        position = _vector(operation, parameter, value, size=3)
        rotation = (0.0, 0.0, 0.0, 1.0)
    elif isinstance(value, Mapping) and set(value) <= {"position", "rotation"}:
        position = _vector(
            operation,
            f"{parameter}.position",
            value.get("position", (0.0, 0.0, 0.0)),
            size=3,
        )
        rotation = _vector(
            operation,
            f"{parameter}.rotation",
            value.get("rotation", (0.0, 0.0, 0.0, 1.0)),
            size=4,
            limit=1.0e6,
        )
    else:
        raise _error(
            operation,
            parameter,
            "must be [x, y, z] or an object containing only position and rotation",
            value,
        )
    magnitude = math.sqrt(sum(component * component for component in rotation))
    if magnitude <= 1.0e-12:
        raise _error(operation, f"{parameter}.rotation", "must be a non-zero quaternion")
    return {
        "position": position,
        "rotation": tuple(component / magnitude for component in rotation),
    }


def _kinematics(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None or value == "kuka_ir500":
        return tuple(dict(row) for row in _KUKA_IR500)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            "robot",
            "kinematics",
            "must be 'kuka_ir500' or a six-row definition, never a file path",
            value,
        )
    if len(value) != 6:
        raise _error("robot", "kinematics", "must contain exactly six axis rows", value)
    result = []
    for index, raw in enumerate(value):
        parameter = f"kinematics[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _KINEMATIC_FIELDS:
            raise _error(
                "robot",
                parameter,
                f"must contain exactly {sorted(_KINEMATIC_FIELDS)!r}",
                raw,
            )
        direction = raw["rotation_direction"]
        if type(direction) is not int or direction not in {-1, 1}:
            raise _error(
                "robot",
                f"{parameter}.rotation_direction",
                "must be -1 or 1",
                direction,
            )
        minimum_angle = _number(
            "robot",
            f"{parameter}.minimum_angle",
            raw["minimum_angle"],
            minimum=-_MAX_ANGLE,
            maximum=_MAX_ANGLE,
        )
        maximum_angle = _number(
            "robot",
            f"{parameter}.maximum_angle",
            raw["maximum_angle"],
            minimum=-_MAX_ANGLE,
            maximum=_MAX_ANGLE,
        )
        if minimum_angle >= maximum_angle:
            raise _error(
                "robot",
                parameter,
                "minimum_angle must be less than maximum_angle",
            )
        result.append(
            {
                "a": _number(
                    "robot",
                    f"{parameter}.a",
                    raw["a"],
                    minimum=-_MAX_AXIS_LENGTH,
                    maximum=_MAX_AXIS_LENGTH,
                ),
                "alpha": _number(
                    "robot",
                    f"{parameter}.alpha",
                    raw["alpha"],
                    minimum=-_MAX_ANGLE,
                    maximum=_MAX_ANGLE,
                ),
                "d": _number(
                    "robot",
                    f"{parameter}.d",
                    raw["d"],
                    minimum=-_MAX_AXIS_LENGTH,
                    maximum=_MAX_AXIS_LENGTH,
                ),
                "theta": _number(
                    "robot",
                    f"{parameter}.theta",
                    raw["theta"],
                    minimum=-_MAX_ANGLE,
                    maximum=_MAX_ANGLE,
                ),
                "rotation_direction": direction,
                "maximum_angle": maximum_angle,
                "minimum_angle": minimum_angle,
                "maximum_velocity": _number(
                    "robot",
                    f"{parameter}.maximum_velocity",
                    raw["maximum_velocity"],
                    minimum=0.0,
                    maximum=_MAX_VELOCITY,
                    strict_minimum=True,
                ),
            }
        )
    return tuple(result)


def _domain_value(
    operation: str,
    parameter: str,
    value: Any,
    output_types: set[str],
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "robot":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this Robot api",
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


class RobotDomainAPI:
    """Exact six-axis model, linear trajectory, dress-up, and simulation API."""

    __slots__ = ()

    domain = "robot"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        declared_outputs = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_exports not in {_LEGACY_EXPORTS, _EXPORTS}:
            raise RuntimeError(
                "Robot pack exports do not match the production runtime contract: "
                f"expected {_EXPORTS!r}, received {declared_exports!r}."
            )
        if declared_outputs not in {_LEGACY_OUTPUT_TYPES, _OUTPUT_TYPES}:
            raise RuntimeError(
                "Robot pack publication types do not match the runtime contract."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="robot",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def component(
        self,
        source: Mapping[str, str],
        *,
        placement: Sequence[float] | Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Place one reusable component in a robot or automation layout."""

        return component_value(
            self.domain,
            source,
            placement=placement,
            label=label,
        )

    def instances(
        self,
        source: Mapping[str, str],
        placements: Sequence[
            Sequence[float] | Mapping[str, Any] | None
        ],
        *,
        labels: Sequence[str] | None = None,
    ) -> tuple[DomainValue, ...]:
        """Place repeated lightweight occurrences in a robot or cell layout."""

        return instance_values(
            self.domain,
            source,
            placements,
            labels=labels,
        )

    def robot(
        self,
        *,
        kinematics: str | Sequence[Mapping[str, Any]] | None = "kuka_ir500",
        axis_positions: Sequence[float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        base: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
        tool: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
        home: Sequence[float] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Define one native six-axis robot without filesystem inputs."""

        axes = _kinematics(kinematics)
        positions = _vector(
            "robot", "axis_positions", axis_positions, size=6, limit=_MAX_ANGLE
        )
        home_positions = (
            positions
            if home is None
            else _vector("robot", "home", home, size=6, limit=_MAX_ANGLE)
        )
        for parameter, values in (
            ("axis_positions", positions),
            ("home", home_positions),
        ):
            for index, (position, axis) in enumerate(zip(values, axes)):
                if not axis["minimum_angle"] <= position <= axis["maximum_angle"]:
                    raise _error(
                        "robot",
                        f"{parameter}[{index}]",
                        "must lie inside the corresponding kinematic joint limits",
                        position,
                    )
        return self._value(
            "robot",
            "robot",
            kinematics=axes,
            axis_positions=positions,
            base=_placement("robot", "base", base),
            tool=_placement("robot", "tool", tool),
            home=home_positions,
            label=_label("robot", label),
        )

    def waypoint(
        self,
        position: Mapping[str, Sequence[float]] | Sequence[float],
        *,
        name: str,
        velocity: float,
        acceleration: float,
        continuous: bool = False,
        tool: int = 0,
        base: int = 0,
    ) -> DomainValue:
        """Define one linear native Robot waypoint for use by ``trajectory``."""

        if not isinstance(name, str) or not _WAYPOINT_NAME.fullmatch(name):
            raise _error(
                "waypoint",
                "name",
                "must be a 1-64 character ASCII identifier beginning with a letter or underscore",
                name,
            )
        if type(continuous) is not bool:
            raise _error("waypoint", "continuous", "must be true or false", continuous)
        return self._value(
            "waypoint",
            "waypoint",
            position=_placement("waypoint", "position", position),
            motion="LIN",
            name=name,
            velocity=_number(
                "waypoint",
                "velocity",
                velocity,
                minimum=0.0,
                maximum=_MAX_VELOCITY,
                strict_minimum=True,
            ),
            acceleration=_number(
                "waypoint",
                "acceleration",
                acceleration,
                minimum=0.0,
                maximum=_MAX_VELOCITY,
                strict_minimum=True,
            ),
            continuous=continuous,
            tool=_integer("waypoint", "tool", tool, minimum=0, maximum=255),
            base=_integer("waypoint", "base", base, minimum=0, maximum=255),
        )

    def trajectory(
        self,
        waypoints: Sequence[DomainValue],
        *,
        base: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Build one native trajectory from two or more linear waypoints."""

        if isinstance(waypoints, (str, bytes)) or not isinstance(waypoints, Sequence):
            raise _error(
                "trajectory", "waypoints", "must be a sequence returned by api.waypoint"
            )
        if not 2 <= len(waypoints) <= _MAX_WAYPOINTS:
            raise _error(
                "trajectory",
                "waypoints",
                f"must contain 2-{_MAX_WAYPOINTS} values",
            )
        values = tuple(
            _domain_value("trajectory", f"waypoints[{index}]", item, {"waypoint"})
            for index, item in enumerate(waypoints)
        )
        return self._value(
            "trajectory",
            "trajectory",
            values,
            base=_placement("trajectory", "base", base),
            label=_label("trajectory", label),
        )

    def dressup(
        self,
        source: DomainValue,
        *,
        speed: float | None = None,
        acceleration: float | None = None,
        continuous: bool | None = None,
        offset: Mapping[str, Sequence[float]] | Sequence[float] | None = None,
        offset_mode: str = "none",
        label: str = "",
    ) -> DomainValue:
        """Apply one native trajectory dress-up without duplicating trajectory creation."""

        source_value = _domain_value("dressup", "source", source, {"trajectory"})
        if continuous is not None and type(continuous) is not bool:
            raise _error("dressup", "continuous", "must be true, false, or null", continuous)
        if not isinstance(offset_mode, str) or offset_mode not in {
            "none",
            "use_orientation",
            "add_position",
            "add_orientation",
            "add_position_and_orientation",
        }:
            raise _error(
                "dressup",
                "offset_mode",
                "must be one of none, use_orientation, add_position, add_orientation, or add_position_and_orientation",
                offset_mode,
            )
        if (offset is None) != (offset_mode == "none"):
            raise _error(
                "dressup",
                "offset",
                "must be omitted exactly when offset_mode is 'none'",
            )
        if speed is None and acceleration is None and continuous is None and offset is None:
            raise _error("dressup", "changes", "must configure at least one change")
        return self._value(
            "dressup",
            "dressup",
            source_value,
            speed=(
                None
                if speed is None
                else _number(
                    "dressup",
                    "speed",
                    speed,
                    minimum=0.0,
                    maximum=_MAX_VELOCITY,
                    strict_minimum=True,
                )
            ),
            acceleration=(
                None
                if acceleration is None
                else _number(
                    "dressup",
                    "acceleration",
                    acceleration,
                    minimum=0.0,
                    maximum=_MAX_VELOCITY,
                    strict_minimum=True,
                )
            ),
            continuous=continuous,
            offset=(
                None if offset is None else _placement("dressup", "offset", offset)
            ),
            offset_mode=offset_mode,
            label=_label("dressup", label),
        )

    def simulate(
        self,
        robot: DomainValue,
        trajectory: DomainValue,
        *,
        sample_period: float = 0.1,
        maximum_samples: int = 10000,
        require_reachable: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Run native trajectory interpolation and inverse kinematics in the worker."""

        robot_value = _domain_value("simulate", "robot", robot, {"robot"})
        trajectory_value = _domain_value(
            "simulate", "trajectory", trajectory, {"trajectory", "dressup"}
        )
        if type(require_reachable) is not bool:
            raise _error(
                "simulate",
                "require_reachable",
                "must be true or false",
                require_reachable,
            )
        return self._value(
            "simulate",
            "simulation",
            robot_value,
            trajectory_value,
            sample_period=_number(
                "simulate",
                "sample_period",
                sample_period,
                minimum=1.0e-4,
                maximum=3600.0,
            ),
            maximum_samples=_integer(
                "simulate",
                "maximum_samples",
                maximum_samples,
                minimum=2,
                maximum=100_000,
            ),
            require_reachable=require_reachable,
            label=_label("simulate", label),
        )
