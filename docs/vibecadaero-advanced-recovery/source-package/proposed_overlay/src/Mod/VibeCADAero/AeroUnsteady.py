# SPDX-License-Identifier: LGPL-2.1-or-later
"""Two-way pitch/plunge coupling for the strip dynamic-stall model.

This reference coupler fixes two bugs from the discussion drafts:
* ``run()`` no longer silently resets caller-specified initial conditions.
* prescribed motion carries velocity/rate explicitly; setting theta alone no
  longer erases the pitch rate that dynamic stall needs.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

from AeroStripTheory import StripTheoryResult, StripWing


@dataclass
class PitchPlungeState:
    h_m: float = 0.0
    h_dot_mps: float = 0.0
    theta_rad: float = 0.0
    theta_dot_rad_s: float = 0.0
    time_s: float = 0.0


@dataclass(frozen=True)
class PitchPlungeParams:
    mass_kg: float = 5.0
    pitch_inertia_kg_m2: float = 0.15
    plunge_stiffness_n_m: float = 0.0
    pitch_stiffness_nm_rad: float = 0.0
    plunge_damping_ns_m: float = 0.0
    pitch_damping_nms_rad: float = 0.0

    def validate(self) -> None:
        if self.mass_kg <= 0.0 or self.pitch_inertia_kg_m2 <= 0.0:
            raise ValueError("mass and pitch inertia must be positive")


@dataclass(frozen=True)
class PrescribedMotion:
    h_m: float | None = None
    h_dot_mps: float | None = None
    theta_rad: float | None = None
    theta_dot_rad_s: float | None = None


class PitchPlungeCoupler:
    def __init__(self, wing: StripWing, *, speed_mps: float, params: PitchPlungeParams | None = None) -> None:
        if speed_mps <= 0.0:
            raise ValueError("speed_mps must be positive")
        self.wing = wing
        self.speed_mps = float(speed_mps)
        self.params = params or PitchPlungeParams()
        self.params.validate()
        self.state = PitchPlungeState()
        self.history: list[dict[str, float | bool]] = []

    def reset(self, state: PitchPlungeState | None = None) -> None:
        self.state = state or PitchPlungeState()
        alpha = self.state.theta_rad - self.state.h_dot_mps / self.speed_mps
        self.wing.reset(math.degrees(alpha))
        self.history.clear()

    def _aero(self, dt_s: float) -> StripTheoryResult:
        alpha = self.state.theta_rad - self.state.h_dot_mps / self.speed_mps
        return self.wing.step(
            alpha_root_deg=math.degrees(alpha),
            pitch_rate_deg_s=math.degrees(self.state.theta_dot_rad_s),
            speed_mps=self.speed_mps,
            dt_s=dt_s,
        )

    def step(self, dt_s: float, prescribed: PrescribedMotion | None = None) -> dict[str, float | bool]:
        if dt_s <= 0.0:
            raise ValueError("dt_s must be positive")
        p = self.params
        s = self.state
        motion = prescribed or PrescribedMotion()

        # Apply explicitly prescribed kinematics before aerodynamic evaluation so
        # the stateful aero model sees the correct alpha *and* rates.
        if motion.h_m is not None:
            s.h_m = float(motion.h_m)
        if motion.h_dot_mps is not None:
            s.h_dot_mps = float(motion.h_dot_mps)
        if motion.theta_rad is not None:
            s.theta_rad = float(motion.theta_rad)
        if motion.theta_dot_rad_s is not None:
            s.theta_dot_rad_s = float(motion.theta_dot_rad_s)

        aero = self._aero(dt_s)
        if motion.h_m is None:
            h_ddot = (
                aero.lift_n
                - p.plunge_damping_ns_m * s.h_dot_mps
                - p.plunge_stiffness_n_m * s.h_m
            ) / p.mass_kg
            s.h_dot_mps += h_ddot * dt_s
            s.h_m += s.h_dot_mps * dt_s
        elif motion.h_dot_mps is None:
            raise ValueError("prescribed h_m requires h_dot_mps for unsteady aerodynamics")

        if motion.theta_rad is None:
            theta_ddot = (
                aero.pitch_moment_nm
                - p.pitch_damping_nms_rad * s.theta_dot_rad_s
                - p.pitch_stiffness_nm_rad * s.theta_rad
            ) / p.pitch_inertia_kg_m2
            s.theta_dot_rad_s += theta_ddot * dt_s
            s.theta_rad += s.theta_dot_rad_s * dt_s
        elif motion.theta_dot_rad_s is None:
            raise ValueError("prescribed theta_rad requires theta_dot_rad_s for dynamic stall")

        s.time_s += dt_s
        alpha = s.theta_rad - s.h_dot_mps / self.speed_mps
        record: dict[str, float | bool] = {
            "time_s": s.time_s,
            "h_m": s.h_m,
            "h_dot_mps": s.h_dot_mps,
            "theta_rad": s.theta_rad,
            "theta_dot_rad_s": s.theta_dot_rad_s,
            "alpha_rad": alpha,
            "cl": aero.cl,
            "cd": aero.cd,
            "cm": aero.cm,
            "lift_n": aero.lift_n,
            "drag_n": aero.drag_n,
            "pitch_moment_nm": aero.pitch_moment_nm,
            "vortex_active": aero.vortex_active,
        }
        self.history.append(record)
        return record

    def run(
        self,
        *,
        duration_s: float,
        dt_s: float,
        prescribed_fn: Callable[[float, PitchPlungeState], PrescribedMotion | None] | None = None,
        reset: bool = False,
    ) -> list[dict[str, float | bool]]:
        if reset:
            self.reset()
        steps = int(math.ceil(duration_s / dt_s))
        for _ in range(steps):
            motion = prescribed_fn(self.state.time_s, self.state) if prescribed_fn else None
            self.step(dt_s, motion)
        return list(self.history)
