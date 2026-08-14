# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong scalar and vector preparation for FEM mechanical loads."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class PreparedLoadValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(value: Any, *, field: str, positive: bool = False) -> float:
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
    return number


def _boolean(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAnalyzeError(f"{field} must be true or false.")
    return value


def _unit_vector(value: Any, *, field: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(f"{field} must contain exactly x, y, and z.")
    raw = [
        _finite(value[axis], field=f"{field}.{axis}")
        for axis in ("x", "y", "z")
    ]
    length = math.sqrt(sum(component * component for component in raw))
    if length <= 1.0e-15:
        raise NativeAnalyzeError(f"{field} must have non-zero length.")
    normalized = [float(format(component / length, ".15g")) for component in raw]
    return dict(zip(("x", "y", "z"), normalized))


def prepare_load_values(kind: str, value: Any) -> PreparedLoadValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("load must be one typed FEM mechanical-load object.")
    raw = dict(value)
    if kind == "force":
        if set(raw) != {"force_n", "reversed"}:
            raise NativeAnalyzeError("Force values must contain only force_n and reversed.")
        force = _finite(raw["force_n"], field="load.force_n", positive=True)
        reversed_value = _boolean(raw["reversed"], field="load.reversed")
        definition = {"force_n": force, "reversed": reversed_value}
        return PreparedLoadValues(kind, dict(definition), definition)
    if kind == "pressure":
        if set(raw) != {"pressure_pa", "reversed"}:
            raise NativeAnalyzeError(
                "Pressure values must contain only pressure_pa and reversed."
            )
        pressure = _finite(
            raw["pressure_pa"],
            field="load.pressure_pa",
            positive=True,
        )
        reversed_value = _boolean(raw["reversed"], field="load.reversed")
        definition = {"pressure_pa": pressure, "reversed": reversed_value}
        return PreparedLoadValues(kind, dict(definition), definition)
    if kind == "centrifugal":
        if set(raw) != {"rotation_frequency_hz"}:
            raise NativeAnalyzeError(
                "Centrifugal values must contain only rotation_frequency_hz."
            )
        frequency = _finite(
            raw["rotation_frequency_hz"],
            field="load.rotation_frequency_hz",
            positive=True,
        )
        definition = {"rotation_frequency_hz": frequency}
        return PreparedLoadValues(kind, dict(definition), definition)
    if kind == "gravity":
        if set(raw) != {"acceleration_m_s2", "direction"}:
            raise NativeAnalyzeError(
                "Gravity values must contain only acceleration_m_s2 and direction."
            )
        acceleration = _finite(
            raw["acceleration_m_s2"],
            field="load.acceleration_m_s2",
            positive=True,
        )
        direction = _unit_vector(raw["direction"], field="load.direction")
        definition = {
            "acceleration_m_s2": acceleration,
            "direction": direction,
        }
        return PreparedLoadValues(kind, dict(definition), definition)
    raise NativeAnalyzeError("The requested FEM mechanical-load kind is unavailable.")


def apply_load_values(obj: Any, prepared: PreparedLoadValues) -> None:
    if not isinstance(prepared, PreparedLoadValues):
        raise TypeError("prepared must be PreparedLoadValues")
    native = prepared.native
    if prepared.kind == "force":
        obj.Force = f"{native['force_n']} N"
        obj.Reversed = native["reversed"]
        return
    if prepared.kind == "pressure":
        obj.Pressure = f"{native['pressure_pa']} Pa"
        obj.Reversed = native["reversed"]
        return
    if prepared.kind == "centrifugal":
        obj.RotationFrequency = f"{native['rotation_frequency_hz']} 1/s"
        return
    import FreeCAD

    obj.GravityAcceleration = f"{native['acceleration_m_s2']} m/s^2"
    direction = native["direction"]
    obj.GravityDirection = FreeCAD.Vector(
        direction["x"],
        direction["y"],
        direction["z"],
    )
