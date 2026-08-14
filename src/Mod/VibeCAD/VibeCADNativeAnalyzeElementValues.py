# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validation and native property mapping for FEM element definitions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class PreparedElementValues:
    definition: tuple[tuple[str, Any], ...]
    native_values: tuple[tuple[str, Any], ...]

    def normalized(self) -> dict[str, Any]:
        result = dict(self.definition)
        if "curve" in result:
            result["curve"] = [dict(point) for point in result["curve"]]
        return result


def _mapping(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        fields = ", ".join(sorted(expected))
        raise NativeAnalyzeError(f"{field} must contain exactly: {fields}.")
    return dict(value)


def _number(
    value: Any,
    field: str,
    *,
    minimum: float = -1.0e12,
    maximum: float = 1.0e12,
    exclusive_minimum: bool = False,
) -> float:
    if type(value) not in {int, float}:
        raise NativeAnalyzeError(f"{field} must be one finite number.")
    result = float(value)
    invalid_minimum = result <= minimum if exclusive_minimum else result < minimum
    if not math.isfinite(result) or invalid_minimum or result > maximum:
        qualifier = "greater than" if exclusive_minimum else "at least"
        raise NativeAnalyzeError(
            f"{field} must be {qualifier} {minimum:g} and at most {maximum:g}."
        )
    return result


def _positive(value: Any, field: str) -> float:
    return _number(value, field, minimum=0.0, exclusive_minimum=True)


def _nonnegative(value: Any, field: str) -> float:
    return _number(value, field, minimum=0.0)


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise NativeAnalyzeError(f"{field} must be true or false.")
    return value


def _prepared(
    definition: dict[str, Any], native: dict[str, Any]
) -> PreparedElementValues:
    frozen_definition = []
    for key, value in definition.items():
        if key == "curve":
            value = tuple(tuple(point.items()) for point in value)
        frozen_definition.append((key, value))
    return PreparedElementValues(
        tuple(frozen_definition),
        tuple(native.items()),
    )


def prepare_beam_section(value: Any) -> PreparedElementValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("section must be one typed beam section object.")
    kind = str(value.get("kind", "") or "")
    contracts = {
        "rectangular": {"kind", "width_mm", "height_mm"},
        "circular": {"kind", "diameter_mm"},
        "pipe": {"kind", "outer_diameter_mm", "wall_thickness_mm"},
        "elliptical": {"kind", "axis_1_mm", "axis_2_mm"},
        "box": {"kind", "width_mm", "height_mm", "t1_mm", "t2_mm", "t3_mm", "t4_mm"},
    }
    expected = contracts.get(kind)
    if expected is None:
        raise NativeAnalyzeError(
            "section.kind must be rectangular, circular, pipe, elliptical, or box."
        )
    section = _mapping(value, expected, "section")
    dimensions = {
        name: _positive(raw, f"section.{name}")
        for name, raw in section.items()
        if name != "kind"
    }
    native: dict[str, Any] = {
        "SectionType": {
            "rectangular": "Rectangular",
            "circular": "Circular",
            "pipe": "Pipe",
            "elliptical": "Elliptical",
            "box": "Box",
        }[kind]
    }
    names = {
        "width_mm": "RectWidth",
        "height_mm": "RectHeight",
        "diameter_mm": "CircDiameter",
        "outer_diameter_mm": "PipeDiameter",
        "wall_thickness_mm": "PipeThickness",
        "axis_1_mm": "Axis1Length",
        "axis_2_mm": "Axis2Length",
    }
    if kind == "box":
        names = {
            "width_mm": "BoxWidth",
            "height_mm": "BoxHeight",
            "t1_mm": "BoxT1",
            "t2_mm": "BoxT2",
            "t3_mm": "BoxT3",
            "t4_mm": "BoxT4",
        }
        if (
            dimensions["t1_mm"] + dimensions["t3_mm"] >= dimensions["width_mm"]
            or dimensions["t2_mm"] + dimensions["t4_mm"] >= dimensions["height_mm"]
        ):
            raise NativeAnalyzeError(
                "Opposing box wall thicknesses must sum to less than their outer dimension."
            )
    if (
        kind == "pipe"
        and dimensions["wall_thickness_mm"] * 2.0 >= dimensions["outer_diameter_mm"]
    ):
        raise NativeAnalyzeError(
            "section.wall_thickness_mm must be smaller than the pipe radius."
        )
    native.update({names[name]: value for name, value in dimensions.items()})
    return _prepared({"kind": kind, **dimensions}, native)


def prepare_beam_rotation(value: Any) -> PreparedElementValues:
    angle = _number(value, "rotation_degrees")
    return _prepared({"rotation_degrees": angle}, {"Rotation": f"{angle:.17g} deg"})


def prepare_shell_thickness(value: Any) -> PreparedElementValues:
    thickness = _positive(value, "thickness_mm")
    return _prepared(
        {"thickness_mm": thickness},
        {"Thickness": f"{thickness:.17g} mm"},
    )


def _fluid_contract(kind: str) -> set[str] | None:
    return {
        "pipe_manning": {
            "kind",
            "area_mm2",
            "hydraulic_radius_mm",
            "manning_coefficient",
        },
        "pipe_enlargement": {"kind", "initial_area_mm2", "enlarged_area_mm2"},
        "pipe_contraction": {"kind", "initial_area_mm2", "contracted_area_mm2"},
        "pipe_inlet": {
            "kind",
            "pressure_mpa",
            "mass_flow_rate_kg_s",
            "pressure_active",
            "mass_flow_rate_active",
        },
        "pipe_outlet": {
            "kind",
            "pressure_mpa",
            "mass_flow_rate_kg_s",
            "pressure_active",
            "mass_flow_rate_active",
        },
        "pipe_entrance": {"kind", "pipe_area_mm2", "entrance_area_mm2"},
        "pipe_diaphragm": {"kind", "pipe_area_mm2", "aperture_area_mm2"},
        "pipe_bend": {
            "kind",
            "pipe_area_mm2",
            "bend_radius_to_diameter",
            "angle_degrees",
            "loss_coefficient",
        },
        "pipe_gate_valve": {"kind", "pipe_area_mm2", "closing_coefficient"},
        "liquid_pump": {"kind", "curve"},
        "pipe_white_colebrook": {
            "kind",
            "pipe_area_mm2",
            "hydraulic_radius_mm",
            "grain_diameter_mm",
            "form_factor",
        },
    }.get(kind)


def _pump_curve(value: Any) -> tuple[list[dict[str, float]], list[float], list[float]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 128:
        raise NativeAnalyzeError("section.curve must contain 2 to 128 pump points.")
    points = []
    rates = []
    heads = []
    previous_rate = -1.0
    for index, raw in enumerate(value):
        point = _mapping(
            raw,
            {"flow_rate_mm3_s", "head_loss_mm"},
            f"section.curve[{index}]",
        )
        rate = _nonnegative(
            point["flow_rate_mm3_s"], f"section.curve[{index}].flow_rate_mm3_s"
        )
        head = _nonnegative(
            point["head_loss_mm"], f"section.curve[{index}].head_loss_mm"
        )
        if rate <= previous_rate:
            raise NativeAnalyzeError(
                "section.curve flow_rate_mm3_s values must increase strictly."
            )
        previous_rate = rate
        points.append({"flow_rate_mm3_s": rate, "head_loss_mm": head})
        rates.append(rate)
        heads.append(head)
    return points, rates, heads


def prepare_fluid_section(value: Any) -> PreparedElementValues:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("section must be one typed 1D fluid section object.")
    kind = str(value.get("kind", "") or "")
    contract = _fluid_contract(kind)
    if contract is None:
        raise NativeAnalyzeError(
            "section.kind is not one supported CalculiX liquid section type."
        )
    section = _mapping(value, contract, "section")
    native_kind = {
        "pipe_manning": "PIPE MANNING",
        "pipe_enlargement": "PIPE ENLARGEMENT",
        "pipe_contraction": "PIPE CONTRACTION",
        "pipe_inlet": "PIPE INLET",
        "pipe_outlet": "PIPE OUTLET",
        "pipe_entrance": "PIPE ENTRANCE",
        "pipe_diaphragm": "PIPE DIAPHRAGM",
        "pipe_bend": "PIPE BEND",
        "pipe_gate_valve": "PIPE GATE VALVE",
        "liquid_pump": "LIQUID PUMP",
        "pipe_white_colebrook": "PIPE WHITE-COLEBROOK",
    }[kind]
    native: dict[str, Any] = {"SectionType": "Liquid", "LiquidSectionType": native_kind}
    definition: dict[str, Any] = {"kind": kind}
    if kind == "pipe_manning":
        definition.update(
            area_mm2=_positive(section["area_mm2"], "section.area_mm2"),
            hydraulic_radius_mm=_positive(
                section["hydraulic_radius_mm"], "section.hydraulic_radius_mm"
            ),
            manning_coefficient=_number(
                section["manning_coefficient"],
                "section.manning_coefficient",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        native.update(
            ManningArea=f"{definition['area_mm2']:.17g} mm^2",
            ManningRadius=f"{definition['hydraulic_radius_mm']:.17g} mm",
            ManningCoefficient=definition["manning_coefficient"],
        )
    elif kind in {"pipe_enlargement", "pipe_contraction"}:
        second = (
            "enlarged_area_mm2" if kind == "pipe_enlargement" else "contracted_area_mm2"
        )
        definition["initial_area_mm2"] = _positive(
            section["initial_area_mm2"], "section.initial_area_mm2"
        )
        definition[second] = _positive(section[second], f"section.{second}")
        if (
            kind == "pipe_enlargement"
            and definition[second] <= definition["initial_area_mm2"]
        ):
            raise NativeAnalyzeError(
                "section.enlarged_area_mm2 must exceed initial_area_mm2."
            )
        if (
            kind == "pipe_contraction"
            and definition[second] >= definition["initial_area_mm2"]
        ):
            raise NativeAnalyzeError(
                "section.contracted_area_mm2 must be smaller than initial_area_mm2."
            )
        prefix = "Enlarge" if kind == "pipe_enlargement" else "Contract"
        native.update(
            {
                f"{prefix}Area1": f"{definition['initial_area_mm2']:.17g} mm^2",
                f"{prefix}Area2": f"{definition[second]:.17g} mm^2",
            }
        )
    elif kind in {"pipe_inlet", "pipe_outlet"}:
        definition.update(
            pressure_mpa=_number(section["pressure_mpa"], "section.pressure_mpa"),
            mass_flow_rate_kg_s=_number(
                section["mass_flow_rate_kg_s"], "section.mass_flow_rate_kg_s"
            ),
            pressure_active=_boolean(
                section["pressure_active"], "section.pressure_active"
            ),
            mass_flow_rate_active=_boolean(
                section["mass_flow_rate_active"], "section.mass_flow_rate_active"
            ),
        )
        prefix = "Inlet" if kind == "pipe_inlet" else "Outlet"
        native.update(
            {
                f"{prefix}Pressure": definition["pressure_mpa"],
                f"{prefix}FlowRate": definition["mass_flow_rate_kg_s"],
                f"{prefix}PressureActive": definition["pressure_active"],
                f"{prefix}FlowRateActive": definition["mass_flow_rate_active"],
            }
        )
    elif kind in {"pipe_entrance", "pipe_diaphragm"}:
        second = "entrance_area_mm2" if kind == "pipe_entrance" else "aperture_area_mm2"
        definition["pipe_area_mm2"] = _positive(
            section["pipe_area_mm2"], "section.pipe_area_mm2"
        )
        definition[second] = _positive(section[second], f"section.{second}")
        if (
            kind == "pipe_diaphragm"
            and definition[second] > definition["pipe_area_mm2"]
        ):
            raise NativeAnalyzeError(
                "section.aperture_area_mm2 must not exceed pipe_area_mm2."
            )
        prefix = "Entrance" if kind == "pipe_entrance" else "Diaphragm"
        native.update(
            {
                f"{prefix}PipeArea": f"{definition['pipe_area_mm2']:.17g} mm^2",
                f"{prefix}Area": f"{definition[second]:.17g} mm^2",
            }
        )
    elif kind == "pipe_bend":
        definition.update(
            pipe_area_mm2=_positive(section["pipe_area_mm2"], "section.pipe_area_mm2"),
            bend_radius_to_diameter=_number(
                section["bend_radius_to_diameter"],
                "section.bend_radius_to_diameter",
                minimum=1.0,
                maximum=9_999_999.0,
            ),
            angle_degrees=_number(
                section["angle_degrees"],
                "section.angle_degrees",
                minimum=0.0,
                maximum=360.0,
            ),
            loss_coefficient=_number(
                section["loss_coefficient"],
                "section.loss_coefficient",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        native.update(
            BendPipeArea=f"{definition['pipe_area_mm2']:.17g} mm^2",
            BendRadiusDiameter=definition["bend_radius_to_diameter"],
            BendAngle=definition["angle_degrees"],
            BendLossCoefficient=definition["loss_coefficient"],
        )
    elif kind == "pipe_gate_valve":
        definition.update(
            pipe_area_mm2=_positive(section["pipe_area_mm2"], "section.pipe_area_mm2"),
            closing_coefficient=_number(
                section["closing_coefficient"],
                "section.closing_coefficient",
                minimum=0.125,
                maximum=1.0,
            ),
        )
        native.update(
            GateValvePipeArea=f"{definition['pipe_area_mm2']:.17g} mm^2",
            GateValveClosingCoeff=definition["closing_coefficient"],
        )
    elif kind == "liquid_pump":
        points, rates, heads = _pump_curve(section["curve"])
        definition["curve"] = points
        native.update(PumpFlowRate=rates, PumpHeadLoss=heads)
    else:
        definition.update(
            pipe_area_mm2=_positive(section["pipe_area_mm2"], "section.pipe_area_mm2"),
            hydraulic_radius_mm=_positive(
                section["hydraulic_radius_mm"], "section.hydraulic_radius_mm"
            ),
            grain_diameter_mm=_nonnegative(
                section["grain_diameter_mm"], "section.grain_diameter_mm"
            ),
            form_factor=_number(
                section["form_factor"], "section.form_factor", minimum=0.0, maximum=1.0
            ),
        )
        if definition["grain_diameter_mm"] >= 2.0 * definition["hydraulic_radius_mm"]:
            raise NativeAnalyzeError(
                "section.grain_diameter_mm must be smaller than the hydraulic diameter."
            )
        native.update(
            ColebrookeArea=f"{definition['pipe_area_mm2']:.17g} mm^2",
            ColebrookeRadius=f"{definition['hydraulic_radius_mm']:.17g} mm",
            ColebrookeGrainDiameter=f"{definition['grain_diameter_mm']:.17g} mm",
            ColebrookeFormFactor=definition["form_factor"],
        )
    return _prepared(definition, native)


def apply_element_values(obj: Any, prepared: PreparedElementValues) -> None:
    if not isinstance(prepared, PreparedElementValues):
        raise TypeError("prepared must be PreparedElementValues")
    for name, value in prepared.native_values:
        setattr(obj, name, value)
