# SPDX-License-Identifier: LGPL-2.1-or-later
"""Spanwise strip integration for the canonical dynamic-stall engineering model."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Sequence

from AeroDynamicStall import (
    AirfoilDynamicParams,
    DynamicStallEngineeringModel,
    VectorizedDynamicStallEngineeringModel,
)


@dataclass(frozen=True)
class StripTheoryResult:
    cl: float
    cd: float
    cm: float
    lift_n: float
    drag_n: float
    pitch_moment_nm: float
    area_m2: float
    vortex_active: bool
    section_cl: tuple[float, ...]
    section_cd: tuple[float, ...]
    section_cm: tuple[float, ...]
    model_id: str = "strip_dynamic_stall_v1"


class StripWing:
    """Piecewise-linear wing planform over non-negative semi-span stations.

    ``mirror=True`` represents both left and right wing halves.  That explicit
    setting fixes an ambiguity in the old snippets, where span arrays looked like
    semi-span coordinates but loads/reference area were integrated only once.
    """

    def __init__(
        self,
        y_m: Sequence[float],
        chord_m: Sequence[float],
        *,
        twist_deg: Sequence[float] | None = None,
        airfoil: AirfoilDynamicParams | None = None,
        mirror: bool = True,
        density_kg_m3: float = 1.225,
        vectorized: bool = False,
    ) -> None:
        if len(y_m) < 2 or len(y_m) != len(chord_m):
            raise ValueError("y_m and chord_m require matching lengths >= 2")
        self.y = tuple(float(v) for v in y_m)
        self.chord = tuple(float(v) for v in chord_m)
        if any(self.y[i + 1] <= self.y[i] for i in range(len(self.y) - 1)):
            raise ValueError("y stations must be strictly increasing")
        if any(c <= 0.0 for c in self.chord):
            raise ValueError("all chords must be positive")
        twist = tuple(float(v) for v in (twist_deg or [0.0] * len(self.y)))
        if len(twist) != len(self.y):
            raise ValueError("twist_deg length mismatch")
        self.twist_deg = twist
        self.mirror = bool(mirror)
        self.density = float(density_kg_m3)
        if self.density <= 0.0:
            raise ValueError("density must be positive")
        base = airfoil or AirfoilDynamicParams()
        self._section_params = [
            replace(base, chord_m=0.5 * (self.chord[i] + self.chord[i + 1]))
            for i in range(len(self.y) - 1)
        ]
        self._scalar_models = [DynamicStallEngineeringModel(p) for p in self._section_params]
        self._vector_model = VectorizedDynamicStallEngineeringModel(self._section_params)
        self.vectorized = bool(vectorized)

    @property
    def area_m2(self) -> float:
        half = sum(
            0.5 * (self.chord[i] + self.chord[i + 1]) * (self.y[i + 1] - self.y[i])
            for i in range(len(self.y) - 1)
        )
        return half * (2.0 if self.mirror else 1.0)

    @property
    def mean_aerodynamic_chord_approx_m(self) -> float:
        # Area-weighted strip chord; sufficient as a reference for this reduced
        # engineering model.  The builder paper separately calls for exact CAD MAC.
        num = 0.0
        den = 0.0
        for i in range(len(self.y) - 1):
            dy = self.y[i + 1] - self.y[i]
            c = 0.5 * (self.chord[i] + self.chord[i + 1])
            num += c * c * dy
            den += c * dy
        return num / den

    def reset(self, alpha_root_deg: float = 0.0) -> None:
        alpha = math.radians(float(alpha_root_deg))
        for model in self._scalar_models:
            model.reset(alpha_rad=alpha)
        import numpy as np

        self._vector_model.reset(np.full(len(self._section_params), alpha))

    def _section_geometry(self) -> tuple[list[float], list[float], list[float]]:
        dy = [self.y[i + 1] - self.y[i] for i in range(len(self.y) - 1)]
        chord = [0.5 * (self.chord[i] + self.chord[i + 1]) for i in range(len(dy))]
        twist = [math.radians(0.5 * (self.twist_deg[i] + self.twist_deg[i + 1])) for i in range(len(dy))]
        return dy, chord, twist

    def step(self, *, alpha_root_deg: float, pitch_rate_deg_s: float, speed_mps: float, dt_s: float) -> StripTheoryResult:
        if speed_mps <= 0.0:
            raise ValueError("speed_mps must be positive")
        dy, chord, twist = self._section_geometry()
        alpha_root = math.radians(float(alpha_root_deg))
        pitch_rate = math.radians(float(pitch_rate_deg_s))
        alpha = [alpha_root + v for v in twist]

        if self.vectorized:
            import numpy as np

            raw = self._vector_model.step(
                alpha_rad=np.asarray(alpha),
                pitch_rate_rad_s=pitch_rate,
                speed_mps=speed_mps,
                dt_s=dt_s,
            )
            cls = [float(v) for v in raw["cl"]]
            cds = [float(v) for v in raw["cd"]]
            cms = [float(v) for v in raw["cm"]]
            vortex = bool(np.any(raw["vortex_active"]))
        else:
            outputs = [
                model.step(alpha_rad=a, pitch_rate_rad_s=pitch_rate, speed_mps=speed_mps, dt_s=dt_s)
                for model, a in zip(self._scalar_models, alpha)
            ]
            cls = [v.cl for v in outputs]
            cds = [v.cd for v in outputs]
            cms = [v.cm for v in outputs]
            vortex = any(v.vortex_active for v in outputs)

        q = 0.5 * self.density * speed_mps**2
        multiplier = 2.0 if self.mirror else 1.0
        lift = multiplier * sum(cl * q * c * width for cl, c, width in zip(cls, chord, dy))
        drag = multiplier * sum(cd * q * c * width for cd, c, width in zip(cds, chord, dy))
        moment = multiplier * sum(cm * q * c**2 * width for cm, c, width in zip(cms, chord, dy))
        area = self.area_m2
        mac = self.mean_aerodynamic_chord_approx_m
        return StripTheoryResult(
            cl=lift / (q * area),
            cd=drag / (q * area),
            cm=moment / (q * area * mac),
            lift_n=lift,
            drag_n=drag,
            pitch_moment_nm=moment,
            area_m2=area,
            vortex_active=vortex,
            section_cl=tuple(cls),
            section_cd=tuple(cds),
            section_cm=tuple(cms),
        )
