# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for FEM fluid constraints."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


_AXES = (("x", "X"), ("y", "Y"), ("z", "Z"))


@dataclass(frozen=True, slots=True)
class PreparedFluidValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]
    allowed_reference_kinds: frozenset[str]
    allow_mixed_reference_kinds: bool
    allow_empty_references: bool

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    return number


def _formula(value: Any, *, field: str) -> str:
    expression = str(value or "")
    if not expression or len(expression) > 512:
        raise NativeAnalyzeError(f"{field} must contain 1 to 512 characters.")
    if any(character in expression for character in ("\r", "\n", "\x00")):
        raise NativeAnalyzeError(
            f"{field} must be one line and contain no null character."
        )
    return expression


def _components(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value or not set(value) <= {"x", "y", "z"}:
        raise NativeAnalyzeError(
            "constraint.components must contain one or more of x, y, and z only."
        )
    result = {}
    for axis, raw in value.items():
        if not isinstance(raw, Mapping):
            raise NativeAnalyzeError(
                f"constraint.components.{axis} must be one object."
            )
        component = dict(raw)
        mode = str(component.get("kind", "") or "")
        if mode == "value" and set(component) == {"kind", "value_m_s"}:
            result[axis] = {
                "kind": mode,
                "value_m_s": _finite(
                    component["value_m_s"],
                    field=f"constraint.components.{axis}.value_m_s",
                ),
            }
        elif mode == "formula" and set(component) == {"kind", "expression"}:
            result[axis] = {
                "kind": mode,
                "expression": _formula(
                    component["expression"],
                    field=f"constraint.components.{axis}.expression",
                ),
            }
        else:
            raise NativeAnalyzeError(
                f"constraint.components.{axis} must be either {{kind: value, value_m_s}} "
                "or {kind: formula, expression}."
            )
    return result


def _velocity_native(components: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    native: dict[str, Any] = {}
    for axis, suffix in _AXES:
        component = components.get(axis)
        native[f"Velocity{suffix}"] = "0 m/s"
        native[f"Velocity{suffix}Formula"] = ""
        native[f"Velocity{suffix}Unspecified"] = component is None
        native[f"Velocity{suffix}HasFormula"] = bool(
            component is not None and component["kind"] == "formula"
        )
        if component is None:
            continue
        if component["kind"] == "value":
            native[f"Velocity{suffix}"] = f"{component['value_m_s']} m/s"
        else:
            native[f"Velocity{suffix}Formula"] = component["expression"]
    return native


def prepare_fluid_values(kind: str, value: Any) -> PreparedFluidValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(
            "constraint must be one typed FEM fluid constraint object."
        )
    raw = dict(value)
    if kind == "initial_pressure":
        if set(raw) != {"pressure_pa"}:
            raise NativeAnalyzeError("constraint must contain only pressure_pa.")
        pressure = _finite(raw["pressure_pa"], field="constraint.pressure_pa")
        return PreparedFluidValues(
            kind,
            {"Pressure": f"{pressure} Pa"},
            {"pressure_pa": pressure},
            frozenset({"Solid", "Face"}),
            True,
            True,
        )
    expected = (
        {"components"}
        if kind == "initial_flow_velocity"
        else {
            "components",
            "normal_to_boundary",
        }
    )
    if set(raw) != expected:
        raise NativeAnalyzeError(
            "constraint fields do not match the selected FEM fluid constraint type."
        )
    components = _components(raw["components"])
    native = _velocity_native(components)
    definition: dict[str, Any] = {"components": components}
    if kind == "flow_velocity":
        normal = raw["normal_to_boundary"]
        if type(normal) is not bool:
            raise NativeAnalyzeError(
                "constraint.normal_to_boundary must be true or false."
            )
        native["NormalToBoundary"] = normal
        definition["normal_to_boundary"] = normal
        allowed = frozenset({"Solid", "Face", "Edge", "Vertex"})
        allow_empty = False
    elif kind == "initial_flow_velocity":
        allowed = frozenset({"Solid", "Face"})
        allow_empty = True
    else:
        raise NativeAnalyzeError(
            "The requested FEM fluid constraint kind is unavailable."
        )
    return PreparedFluidValues(
        kind,
        native,
        definition,
        allowed,
        True,
        allow_empty,
    )


def apply_fluid_values(obj: Any, prepared: PreparedFluidValues) -> None:
    if not isinstance(prepared, PreparedFluidValues):
        raise TypeError("prepared must be PreparedFluidValues")
    for name, value in prepared.native.items():
        setattr(obj, name, value)
