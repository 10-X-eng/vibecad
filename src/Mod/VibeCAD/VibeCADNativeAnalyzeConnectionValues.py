# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for paired FEM mechanical connections."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class PreparedConnectionValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(
    value: Any,
    *,
    field: str,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    if positive and number <= 0.0:
        raise NativeAnalyzeError(f"{field} must be greater than zero.")
    if nonnegative and number < 0.0:
        raise NativeAnalyzeError(f"{field} must be zero or positive.")
    return number


def _contact(value: Mapping[str, Any]) -> PreparedConnectionValues:
    required = {
        "contact_stiffness_gpa_per_m",
        "clearance_adjustment_mm",
        "friction",
    }
    if set(value) != required:
        raise NativeAnalyzeError(
            "Contact settings must contain contact_stiffness_gpa_per_m, "
            "clearance_adjustment_mm, and friction."
        )
    stiffness = _finite(
        value["contact_stiffness_gpa_per_m"],
        field="connection.contact_stiffness_gpa_per_m",
        positive=True,
    )
    adjustment = _finite(
        value["clearance_adjustment_mm"],
        field="connection.clearance_adjustment_mm",
        nonnegative=True,
    )
    friction = value["friction"]
    if not isinstance(friction, Mapping):
        raise NativeAnalyzeError("connection.friction must be one typed object.")
    friction = dict(friction)
    friction_kind = str(friction.get("kind", "") or "")
    if friction_kind == "frictionless" and set(friction) == {"kind"}:
        native_friction = {
            "Friction": False,
            "FrictionCoefficient": 0.0,
            "StickSlope": 0.0,
        }
        normalized_friction = {"kind": "frictionless"}
    elif friction_kind == "coulomb" and set(friction) == {
        "kind",
        "coefficient",
        "stick_stiffness_gpa_per_m",
    }:
        coefficient = _finite(
            friction["coefficient"],
            field="connection.friction.coefficient",
            positive=True,
        )
        stick = _finite(
            friction["stick_stiffness_gpa_per_m"],
            field="connection.friction.stick_stiffness_gpa_per_m",
            positive=True,
        )
        native_friction = {
            "Friction": True,
            "FrictionCoefficient": coefficient,
            "StickSlope": stick,
        }
        normalized_friction = {
            "kind": "coulomb",
            "coefficient": coefficient,
            "stick_stiffness_gpa_per_m": stick,
        }
    else:
        raise NativeAnalyzeError(
            "connection.friction must be frictionless or coulomb with a positive "
            "coefficient and stick_stiffness_gpa_per_m."
        )
    definition = {
        "contact_stiffness_gpa_per_m": stiffness,
        "clearance_adjustment_mm": adjustment,
        "friction": normalized_friction,
    }
    return PreparedConnectionValues(
        "contact",
        {
            "Slope": stiffness,
            "Adjust": adjustment,
            **native_friction,
        },
        definition,
    )


def _tie(value: Mapping[str, Any]) -> PreparedConnectionValues:
    if set(value) != {"tolerance_mm", "adjust"}:
        raise NativeAnalyzeError(
            "Tie settings must contain only tolerance_mm and adjust."
        )
    tolerance = _finite(
        value["tolerance_mm"],
        field="connection.tolerance_mm",
        nonnegative=True,
    )
    adjust = value["adjust"]
    if type(adjust) is not bool:
        raise NativeAnalyzeError("connection.adjust must be true or false.")
    definition = {"tolerance_mm": tolerance, "adjust": adjust}
    return PreparedConnectionValues("tie", dict(definition), definition)


def prepare_connection_values(kind: str, value: Any) -> PreparedConnectionValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("connection must be one typed FEM connection object.")
    if kind == "contact":
        return _contact(dict(value))
    if kind == "tie":
        return _tie(dict(value))
    raise NativeAnalyzeError("The requested FEM connection kind is unavailable.")


def apply_connection_values(obj: Any, prepared: PreparedConnectionValues) -> None:
    if not isinstance(prepared, PreparedConnectionValues):
        raise TypeError("prepared must be PreparedConnectionValues")
    native = prepared.native
    if prepared.kind == "contact":
        obj.Slope = f"{native['Slope']} GPa/m"
        obj.Adjust = f"{native['Adjust']} mm"
        obj.Friction = native["Friction"]
        obj.FrictionCoefficient = native["FrictionCoefficient"]
        obj.StickSlope = f"{native['StickSlope']} GPa/m"
        return
    obj.Tolerance = f"{native['tolerance_mm']} mm"
    obj.Adjust = native["adjust"]
