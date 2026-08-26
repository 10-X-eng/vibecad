# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic internal 6-DOF reference dynamics for VibeCADAero.

The existing upstream JSBSim export remains the preferred production flight-
dynamics path.  This module supplies an inspectable reference integrator for
unit tests, coupling experiments, forced maneuvers and cross-checking JSBSim.
It does not relabel a longitudinal-only aerodynamic provider as a full 6-DOF
aerodynamic model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Callable, Protocol

import numpy as np

from AeroStripTheory import StripWing


@dataclass
class SixDOFState:
    # NED position (north, east, down) [m]
    position_ned_m: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    # Body velocity (+X forward, +Y right, +Z down) [m/s]
    velocity_body_mps: np.ndarray = field(default_factory=lambda: np.array([15.0, 0.0, 0.0], dtype=float))
    # Quaternion body -> NED, scalar first.
    quaternion_bn: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0], dtype=float))
    rates_body_rad_s: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    time_s: float = 0.0

    def copy(self) -> "SixDOFState":
        return SixDOFState(
            position_ned_m=self.position_ned_m.copy(),
            velocity_body_mps=self.velocity_body_mps.copy(),
            quaternion_bn=self.quaternion_bn.copy(),
            rates_body_rad_s=self.rates_body_rad_s.copy(),
            time_s=float(self.time_s),
        )


@dataclass(frozen=True)
class RigidBodyProperties:
    mass_kg: float
    ix_kg_m2: float
    iy_kg_m2: float
    iz_kg_m2: float
    ixz_kg_m2: float = 0.0

    def inertia_matrix(self) -> np.ndarray:
        if min(self.mass_kg, self.ix_kg_m2, self.iy_kg_m2, self.iz_kg_m2) <= 0.0:
            raise ValueError("mass and principal inertias must be positive")
        matrix = np.array(
            [
                [self.ix_kg_m2, 0.0, -self.ixz_kg_m2],
                [0.0, self.iy_kg_m2, 0.0],
                [-self.ixz_kg_m2, 0.0, self.iz_kg_m2],
            ],
            dtype=float,
        )
        if np.linalg.det(matrix) <= 0.0:
            raise ValueError("inertia tensor is not positive definite")
        return matrix


@dataclass(frozen=True)
class ForceMomentBody:
    force_n: np.ndarray
    moment_nm: np.ndarray
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def zero(cls) -> "ForceMomentBody":
        return cls(np.zeros(3, dtype=float), np.zeros(3, dtype=float))


@dataclass
class ControlInputs:
    aileron_rad: float = 0.0
    elevator_rad: float = 0.0
    rudder_rad: float = 0.0
    throttle: float = 0.0


class ForceProvider(Protocol):
    def evaluate(self, state: SixDOFState, controls: ControlInputs, dt_s: float) -> ForceMomentBody: ...


def quat_normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm = float(np.linalg.norm(q))
    if norm <= 1e-15:
        raise ValueError("zero quaternion")
    return q / norm


def quat_to_dcm_bn(q: np.ndarray) -> np.ndarray:
    """Direction cosine matrix body -> NED."""

    w, x, y, z = quat_normalize(q)
    return np.array(
        [
            [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
            [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
            [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
        ],
        dtype=float,
    )


def quat_derivative_bn(q: np.ndarray, rates_body_rad_s: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    p, qrate, r = rates_body_rad_s
    return 0.5 * np.array(
        [
            -(x*p + y*qrate + z*r),
            w*p + y*r - z*qrate,
            w*qrate - x*r + z*p,
            w*r + x*qrate - y*p,
        ],
        dtype=float,
    )


def euler_from_quat_bn(q: np.ndarray) -> tuple[float, float, float]:
    w, x, y, z = quat_normalize(q)
    roll = math.atan2(2 * (w*x + y*z), 1 - 2 * (x*x + y*y))
    pitch = math.asin(float(np.clip(2 * (w*y - z*x), -1.0, 1.0)))
    yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
    return roll, pitch, yaw


class SixDOFReferenceSimulator:
    def __init__(
        self,
        properties: RigidBodyProperties,
        provider: ForceProvider,
        *,
        gravity_mps2: float = 9.80665,
    ) -> None:
        self.properties = properties
        self.inertia = properties.inertia_matrix()
        self.inv_inertia = np.linalg.inv(self.inertia)
        self.provider = provider
        self.gravity = float(gravity_mps2)
        self.state = SixDOFState()
        self.controls = ControlInputs()
        self.history: list[dict[str, object]] = []

    def reset(self, state: SixDOFState | None = None) -> None:
        self.state = (state or SixDOFState()).copy()
        self.state.quaternion_bn = quat_normalize(self.state.quaternion_bn)
        self.history.clear()

    def step(self, dt_s: float) -> dict[str, object]:
        if dt_s <= 0.0:
            raise ValueError("dt_s must be positive")
        s = self.state
        p = self.properties
        fm = self.provider.evaluate(s, self.controls, dt_s)
        force = np.asarray(fm.force_n, dtype=float).reshape(3).copy()
        moment = np.asarray(fm.moment_nm, dtype=float).reshape(3)

        # Gravity in NED is +down; rotate the weight into body axes.
        dcm_bn = quat_to_dcm_bn(s.quaternion_bn)
        weight_ned = np.array([0.0, 0.0, p.mass_kg * self.gravity])
        force += dcm_bn.T @ weight_ned

        omega = s.rates_body_rad_s
        velocity = s.velocity_body_mps
        acceleration_body = force / p.mass_kg - np.cross(omega, velocity)
        angular_accel = self.inv_inertia @ (moment - np.cross(omega, self.inertia @ omega))

        # Semi-implicit Euler is intentional for stateful aero: the aerodynamic
        # model is evaluated once per physical step, avoiding fictitious repeated
        # dynamic-stall state updates that a naïve RK4 coupling would create.
        s.velocity_body_mps = velocity + acceleration_body * dt_s
        s.rates_body_rad_s = omega + angular_accel * dt_s
        s.quaternion_bn = quat_normalize(
            s.quaternion_bn + quat_derivative_bn(s.quaternion_bn, s.rates_body_rad_s) * dt_s
        )
        s.position_ned_m = s.position_ned_m + quat_to_dcm_bn(s.quaternion_bn) @ s.velocity_body_mps * dt_s
        s.time_s += dt_s

        roll, pitch, yaw = euler_from_quat_bn(s.quaternion_bn)
        speed = float(np.linalg.norm(s.velocity_body_mps))
        record: dict[str, object] = {
            "time_s": s.time_s,
            "position_ned_m": s.position_ned_m.copy(),
            "velocity_body_mps": s.velocity_body_mps.copy(),
            "rates_body_rad_s": s.rates_body_rad_s.copy(),
            "quaternion_bn": s.quaternion_bn.copy(),
            "euler_rad": np.array([roll, pitch, yaw]),
            "speed_mps": speed,
            "force_body_n": force,
            "moment_body_nm": moment,
            "provider": dict(fm.metadata),
        }
        self.history.append(record)
        return record

    def run(
        self,
        *,
        duration_s: float,
        dt_s: float,
        control_fn: Callable[[float, SixDOFState, ControlInputs], None] | None = None,
        reset: bool = False,
    ) -> list[dict[str, object]]:
        if reset:
            self.reset()
        steps = int(math.ceil(duration_s / dt_s))
        for _ in range(steps):
            if control_fn is not None:
                control_fn(self.state.time_s, self.state, self.controls)
            self.step(dt_s)
        return list(self.history)


class LongitudinalStripForceProvider:
    """Longitudinal strip-aero + thrust adapter for 6-DOF dynamics.

    Lateral force, rolling/yawing aerodynamics and control derivatives are
    intentionally absent and explicitly declared in metadata.  This corrects the
    prior draft that called a zero-lateral model a "full 6-DOF UAV".
    """

    def __init__(self, wing: StripWing, *, max_thrust_n: float = 0.0) -> None:
        self.wing = wing
        self.max_thrust_n = max(0.0, float(max_thrust_n))

    def evaluate(self, state: SixDOFState, controls: ControlInputs, dt_s: float) -> ForceMomentBody:
        u, _v, w = state.velocity_body_mps
        speed = float(np.linalg.norm(state.velocity_body_mps))
        if speed <= 1e-6:
            return ForceMomentBody.zero()
        alpha = math.atan2(float(w), float(u))
        pitch_rate = float(state.rates_body_rad_s[1])
        aero = self.wing.step(
            alpha_root_deg=math.degrees(alpha),
            pitch_rate_deg_s=math.degrees(pitch_rate),
            speed_mps=speed,
            dt_s=dt_s,
        )
        ca, sa = math.cos(alpha), math.sin(alpha)
        force = np.array(
            [
                -aero.drag_n * ca + aero.lift_n * sa,
                0.0,
                -aero.drag_n * sa - aero.lift_n * ca,
            ],
            dtype=float,
        )
        force[0] += float(np.clip(controls.throttle, 0.0, 1.0)) * self.max_thrust_n
        moment = np.array([0.0, aero.pitch_moment_nm, 0.0], dtype=float)
        return ForceMomentBody(
            force_n=force,
            moment_nm=moment,
            metadata={
                "aero_model": aero.model_id,
                "cl": aero.cl,
                "cd": aero.cd,
                "cm": aero.cm,
                "alpha_rad": alpha,
                "lateral_aero_implemented": False,
                "aileron_effect_implemented": False,
                "rudder_effect_implemented": False,
                "elevator_effect_implemented": False,
            },
        )
