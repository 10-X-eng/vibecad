# SPDX-License-Identifier: LGPL-2.1-or-later
"""Stateful engineering dynamic-stall model for the VibeCADAero overlay.

This is intentionally labelled an *engineering/reference* model.  Earlier chat
versions called a heavily simplified implementation "full Leishman-Beddoes";
that claim is not retained.  The canonical target is a literature-calibrated
Leishman-Beddoes-class implementation with published validation datasets.

The scalar and NumPy-vector forms below implement the same reduced equations so
batch/Monte-Carlo acceleration cannot silently change the physics model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any


@dataclass(frozen=True)
class AirfoilDynamicParams:
    cl_alpha_per_rad: float = 2.0 * math.pi
    alpha_zero_lift_rad: float = 0.0
    alpha_static_stall_rad: float = math.radians(12.0)
    cl_max_static: float = 1.4
    cd0: float = 0.012
    cm0: float = -0.05
    chord_m: float = 0.25
    separation_width_rad: float = math.radians(5.5)
    # Reduced-model lag constants in convective-time units.
    t_sep: float = 3.0
    t_vortex_decay: float = 6.0
    t_vortex_life: float = 7.0
    t_impulse: float = 0.75
    a1: float = 0.3
    a2: float = 0.7
    b1: float = 0.14
    b2: float = 0.53

    def validate(self) -> None:
        if self.chord_m <= 0.0:
            raise ValueError("chord must be positive")
        if self.cl_alpha_per_rad <= 0.0:
            raise ValueError("lift slope must be positive")
        if self.separation_width_rad <= 0.0:
            raise ValueError("separation width must be positive")


@dataclass
class DynamicStallState:
    x_deficiency: float = 0.0
    y_deficiency: float = 0.0
    impulse: float = 0.0
    separation: float = 1.0
    vortex_age: float = 0.0
    vortex_strength: float = 0.0
    alpha_prev_rad: float = 0.0
    initialized: bool = False
    time_s: float = 0.0


@dataclass(frozen=True)
class DynamicStallOutput:
    cl: float
    cd: float
    cm: float
    cn: float
    alpha_effective_rad: float
    separation: float
    vortex_active: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class DynamicStallEngineeringModel:
    """Reduced stateful dynamic-stall model with explicit pitch-rate input."""

    model_id = "dynamic_stall_engineering_v1"

    def __init__(self, params: AirfoilDynamicParams | None = None) -> None:
        self.params = params or AirfoilDynamicParams()
        self.params.validate()
        self.state = DynamicStallState()

    def reset(self, *, alpha_rad: float = 0.0) -> None:
        self.state = DynamicStallState(alpha_prev_rad=float(alpha_rad), initialized=True)

    def _separation_target(self, alpha_eff: float) -> float:
        p = self.params
        excess = abs(alpha_eff) - p.alpha_static_stall_rad
        if excess <= 0.0:
            # Approaches 1.0 well below stall and remains continuous at stall.
            value = 1.0 - 0.30 * math.exp(excess / p.separation_width_rad)
        else:
            value = 0.04 + 0.66 * math.exp(-excess / p.separation_width_rad)
        return min(1.0, max(0.02, value))

    def step(self, *, alpha_rad: float, pitch_rate_rad_s: float, speed_mps: float, dt_s: float) -> DynamicStallOutput:
        p = self.params
        s = self.state
        if speed_mps <= 1e-6:
            raise ValueError("dynamic stall model requires positive local airspeed")
        if dt_s <= 0.0:
            raise ValueError("dt_s must be positive")
        if not s.initialized:
            self.reset(alpha_rad=alpha_rad)
            s = self.state

        ds = 2.0 * float(speed_mps) * float(dt_s) / p.chord_m
        ds = max(ds, 1e-9)
        delta_alpha = float(alpha_rad) - s.alpha_prev_rad

        exp1 = math.exp(-p.b1 * ds)
        exp2 = math.exp(-p.b2 * ds)
        s.x_deficiency = s.x_deficiency * exp1 + p.a1 * delta_alpha * math.exp(-0.5 * p.b1 * ds)
        s.y_deficiency = s.y_deficiency * exp2 + p.a2 * delta_alpha * math.exp(-0.5 * p.b2 * ds)

        # Pitch-rate term is explicit.  The previous chat accepted q but largely
        # ignored it; this version includes the standard reduced-rate quantity.
        reduced_pitch = float(pitch_rate_rad_s) * p.chord_m / (2.0 * float(speed_mps))
        alpha_eff = float(alpha_rad) - s.x_deficiency - s.y_deficiency + reduced_pitch

        dalpha_ds = delta_alpha / ds
        exp_imp = math.exp(-ds / p.t_impulse)
        s.impulse = s.impulse * exp_imp + dalpha_ds * math.exp(-0.5 * ds / p.t_impulse)
        cn_attached = p.cl_alpha_per_rad * (alpha_eff - p.alpha_zero_lift_rad) + 4.0 * s.impulse

        f_target = self._separation_target(alpha_eff)
        lag_fraction = 1.0 - math.exp(-ds / p.t_sep)
        s.separation += (f_target - s.separation) * lag_fraction
        s.separation = min(1.0, max(0.02, s.separation))
        kirchhoff = ((1.0 + math.sqrt(s.separation)) / 2.0) ** 2
        cn_separated = p.cl_alpha_per_rad * (alpha_eff - p.alpha_zero_lift_rad) * kirchhoff

        trigger = abs(cn_attached) > abs(p.cl_max_static) * 1.05
        cn_vortex = 0.0
        vortex_active = False
        if trigger:
            if s.vortex_age <= 0.0:
                s.vortex_strength = 0.55 * (cn_attached - cn_separated)
            if s.vortex_age < p.t_vortex_life:
                s.vortex_age += ds
                cn_vortex = s.vortex_strength * math.exp(-s.vortex_age / p.t_vortex_decay)
                vortex_active = True
        else:
            s.vortex_age = 0.0
            s.vortex_strength = 0.0

        cn = cn_separated + cn_vortex
        cl = cn * math.cos(alpha_eff)
        cd = p.cd0 + abs(cn * math.sin(alpha_eff)) * 0.60
        cm = p.cm0 - 0.22 * (cn - cn_attached) - 0.12 * cn_vortex

        s.alpha_prev_rad = float(alpha_rad)
        s.time_s += float(dt_s)
        return DynamicStallOutput(
            cl=cl,
            cd=cd,
            cm=cm,
            cn=cn,
            alpha_effective_rad=alpha_eff,
            separation=s.separation,
            vortex_active=vortex_active,
            metadata={
                "model_id": self.model_id,
                "reduced_pitch_rate": reduced_pitch,
                "cn_attached": cn_attached,
                "cn_separated": cn_separated,
                "cn_vortex": cn_vortex,
            },
        )


class VectorizedDynamicStallEngineeringModel:
    """NumPy vectorization of the *same* reduced equations across sections."""

    model_id = DynamicStallEngineeringModel.model_id

    def __init__(self, params: list[AirfoilDynamicParams]) -> None:
        import numpy as np

        if not params:
            raise ValueError("at least one section is required")
        for p in params:
            p.validate()
        self.params = list(params)
        n = len(params)
        self.x = np.zeros(n)
        self.y = np.zeros(n)
        self.impulse = np.zeros(n)
        self.separation = np.ones(n)
        self.vortex_age = np.zeros(n)
        self.vortex_strength = np.zeros(n)
        self.alpha_prev = np.zeros(n)
        self.initialized = False
        self.time_s = 0.0

    def reset(self, alpha_rad=None) -> None:
        import numpy as np

        n = len(self.params)
        alpha = np.zeros(n) if alpha_rad is None else np.asarray(alpha_rad, dtype=float)
        if alpha.shape != (n,):
            raise ValueError("alpha_rad shape mismatch")
        self.x.fill(0.0)
        self.y.fill(0.0)
        self.impulse.fill(0.0)
        self.separation.fill(1.0)
        self.vortex_age.fill(0.0)
        self.vortex_strength.fill(0.0)
        self.alpha_prev[:] = alpha
        self.initialized = True
        self.time_s = 0.0

    def step(self, *, alpha_rad, pitch_rate_rad_s, speed_mps, dt_s: float) -> dict[str, Any]:
        import numpy as np

        n = len(self.params)
        alpha = np.asarray(alpha_rad, dtype=float)
        pitch = np.broadcast_to(np.asarray(pitch_rate_rad_s, dtype=float), (n,))
        speed = np.broadcast_to(np.asarray(speed_mps, dtype=float), (n,))
        if alpha.shape != (n,):
            raise ValueError("alpha_rad shape mismatch")
        if np.any(speed <= 1e-6) or dt_s <= 0.0:
            raise ValueError("positive speed and dt are required")
        if not self.initialized:
            self.reset(alpha)

        chord = np.asarray([p.chord_m for p in self.params])
        cl_alpha = np.asarray([p.cl_alpha_per_rad for p in self.params])
        alpha0 = np.asarray([p.alpha_zero_lift_rad for p in self.params])
        alpha_stall = np.asarray([p.alpha_static_stall_rad for p in self.params])
        clmax = np.asarray([p.cl_max_static for p in self.params])
        cd0 = np.asarray([p.cd0 for p in self.params])
        cm0 = np.asarray([p.cm0 for p in self.params])
        width = np.asarray([p.separation_width_rad for p in self.params])
        t_sep = np.asarray([p.t_sep for p in self.params])
        tvd = np.asarray([p.t_vortex_decay for p in self.params])
        tvl = np.asarray([p.t_vortex_life for p in self.params])
        timp = np.asarray([p.t_impulse for p in self.params])
        a1 = np.asarray([p.a1 for p in self.params])
        a2 = np.asarray([p.a2 for p in self.params])
        b1 = np.asarray([p.b1 for p in self.params])
        b2 = np.asarray([p.b2 for p in self.params])

        ds = np.maximum(2.0 * speed * float(dt_s) / chord, 1e-9)
        da = alpha - self.alpha_prev
        self.x = self.x * np.exp(-b1 * ds) + a1 * da * np.exp(-0.5 * b1 * ds)
        self.y = self.y * np.exp(-b2 * ds) + a2 * da * np.exp(-0.5 * b2 * ds)
        reduced_pitch = pitch * chord / (2.0 * speed)
        alpha_eff = alpha - self.x - self.y + reduced_pitch

        self.impulse = self.impulse * np.exp(-ds / timp) + (da / ds) * np.exp(-0.5 * ds / timp)
        cn_attached = cl_alpha * (alpha_eff - alpha0) + 4.0 * self.impulse
        excess = np.abs(alpha_eff) - alpha_stall
        f_target = np.where(
            excess <= 0.0,
            1.0 - 0.30 * np.exp(excess / width),
            0.04 + 0.66 * np.exp(-excess / width),
        )
        f_target = np.clip(f_target, 0.02, 1.0)
        self.separation += (f_target - self.separation) * (1.0 - np.exp(-ds / t_sep))
        self.separation = np.clip(self.separation, 0.02, 1.0)
        cn_separated = cl_alpha * (alpha_eff - alpha0) * ((1.0 + np.sqrt(self.separation)) / 2.0) ** 2

        trigger = np.abs(cn_attached) > np.abs(clmax) * 1.05
        newly = trigger & (self.vortex_age <= 0.0)
        self.vortex_strength = np.where(newly, 0.55 * (cn_attached - cn_separated), self.vortex_strength)
        active = trigger & (self.vortex_age < tvl)
        self.vortex_age = np.where(active, self.vortex_age + ds, np.where(trigger, self.vortex_age, 0.0))
        self.vortex_strength = np.where(trigger, self.vortex_strength, 0.0)
        cn_vortex = np.where(active, self.vortex_strength * np.exp(-self.vortex_age / tvd), 0.0)

        cn = cn_separated + cn_vortex
        cl = cn * np.cos(alpha_eff)
        cd = cd0 + np.abs(cn * np.sin(alpha_eff)) * 0.60
        cm = cm0 - 0.22 * (cn - cn_attached) - 0.12 * cn_vortex
        self.alpha_prev[:] = alpha
        self.time_s += float(dt_s)
        return {
            "cl": cl,
            "cd": cd,
            "cm": cm,
            "cn": cn,
            "alpha_effective_rad": alpha_eff,
            "separation": self.separation.copy(),
            "vortex_active": active.copy(),
            "reduced_pitch_rate": reduced_pitch,
        }
