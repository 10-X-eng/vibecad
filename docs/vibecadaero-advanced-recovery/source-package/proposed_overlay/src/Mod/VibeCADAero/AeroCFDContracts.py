# SPDX-License-Identifier: LGPL-2.1-or-later
"""Canonical data contracts for VibeCADAero high-fidelity solver extensions.

This file is an *overlay/reference implementation* for reconciliation pass 03.
It does not replace any upstream VibeCADAero module.  The contracts are kept
FreeCAD-independent so they can be unit-tested with the normal Python runtime,
serialized into remote-compute bundles, and consumed by local or cloud workers.

Coordinate convention
---------------------
* Body axes: +X forward, +Y right, +Z down (aircraft convention).
* Freestream vector is expressed in body axes in m/s.
* Forces/moments returned by solvers are normalized into body axes.
* Drag/lift/side coefficients are projections onto an explicit aerodynamic
  basis derived from the freestream and configured lift-up vector; no solver is
  allowed to silently assume that "Fx is drag" or "Fz is lift".

Every artifact carries a content hash and every result carries both solver and
compute-provider provenance.  This is deliberate: CFD is evidence, not merely
numbers printed into a dialog.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "vibecad.aero.cfd/1"


class ContractError(ValueError):
    """A solver case or result violates a canonical contract."""


class JobState(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @classmethod
    def from_any(cls, value: Sequence[float] | Mapping[str, Any] | "Vector3") -> "Vector3":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0)))
        if len(value) != 3:
            raise ContractError("Vector3 requires exactly three values.")
        return cls(*(float(v) for v in value))

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def dot(self, other: "Vector3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vector3") -> "Vector3":
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def norm(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> "Vector3":
        n = self.norm()
        if n <= 1e-15:
            raise ContractError("Cannot normalize a zero vector.")
        return Vector3(self.x / n, self.y / n, self.z / n)

    def scaled(self, factor: float) -> "Vector3":
        f = float(factor)
        return Vector3(self.x * f, self.y * f, self.z * f)

    def plus(self, other: "Vector3") -> "Vector3":
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)


@dataclass(frozen=True)
class AeroBasis:
    """Orthogonal coefficient basis expressed in body axes.

    ``drag`` points in the same direction as freestream velocity.  This makes
    positive aerodynamic force *opposite* the basis direction for conventional
    drag; ``project_force`` applies that sign explicitly.  ``lift`` points up
    relative to the configured lift-up vector, and ``side`` completes a
    right-handed basis.
    """

    drag: Vector3
    side: Vector3
    lift: Vector3

    @classmethod
    def from_freestream(
        cls,
        freestream_body_mps: Vector3,
        lift_up_body: Vector3 = Vector3(0.0, 0.0, -1.0),
    ) -> "AeroBasis":
        drag = freestream_body_mps.normalized()
        # Remove any drag-parallel component from the requested up vector.
        up = lift_up_body.plus(drag.scaled(-lift_up_body.dot(drag)))
        lift = up.normalized()
        side = lift.cross(drag).normalized()
        # Re-orthogonalize lift to suppress accumulated roundoff.
        lift = drag.cross(side).normalized()
        return cls(drag=drag, side=side, lift=lift)


@dataclass(frozen=True)
class ReferenceQuantities:
    area_m2: float
    length_m: float
    span_m: float | None = None
    moment_reference_body_m: Vector3 = Vector3()
    area_definition: str = "explicit"

    def validate(self) -> None:
        if self.area_m2 <= 0.0:
            raise ContractError("reference area must be positive")
        if self.length_m <= 0.0:
            raise ContractError("reference length must be positive")
        if self.span_m is not None and self.span_m <= 0.0:
            raise ContractError("reference span must be positive when supplied")


@dataclass(frozen=True)
class FlowConditions:
    freestream_body_mps: Vector3
    density_kg_m3: float = 1.225
    dynamic_viscosity_pa_s: float = 1.81e-5
    temperature_k: float | None = 288.15
    static_pressure_pa: float | None = 101325.0
    turbulence_intensity: float | None = None
    turbulence_length_scale_m: float | None = None

    def validate(self) -> None:
        if self.freestream_body_mps.norm() <= 0.0:
            raise ContractError("freestream velocity must be non-zero")
        if self.density_kg_m3 <= 0.0:
            raise ContractError("density must be positive")
        if self.dynamic_viscosity_pa_s <= 0.0:
            raise ContractError("dynamic viscosity must be positive")

    @property
    def speed_mps(self) -> float:
        return self.freestream_body_mps.norm()

    @property
    def dynamic_pressure_pa(self) -> float:
        return 0.5 * self.density_kg_m3 * self.speed_mps**2

    def reynolds(self, length_m: float) -> float:
        return self.density_kg_m3 * self.speed_mps * float(length_m) / self.dynamic_viscosity_pa_s


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    media_type: str
    size_bytes: int
    role: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path, *, media_type: str, role: str, metadata: Mapping[str, Any] | None = None) -> "Artifact":
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        digest = hashlib.sha256()
        with p.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(
            path=str(p),
            sha256=digest.hexdigest(),
            media_type=str(media_type),
            size_bytes=p.stat().st_size,
            role=str(role),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class GeometryArtifact:
    artifact: Artifact
    geometry_revision: str
    source_object_names: tuple[str, ...] = ()
    source_units: str = "mm"
    solver_units: str = "m"
    triangulation_linear_deflection_mm: float | None = None
    triangulation_angular_deflection_rad: float | None = None


@dataclass(frozen=True)
class SolverSpec:
    backend: str
    model: str
    backend_version: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeSpec:
    provider: str = "local"
    accelerator: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AeroCase:
    case_id: str
    geometry: GeometryArtifact
    flow: FlowConditions
    references: ReferenceQuantities
    solver: SolverSpec
    compute: ComputeSpec = ComputeSpec()
    lift_up_body: Vector3 = Vector3(0.0, 0.0, -1.0)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported schema version: {self.schema_version}")
        if not self.case_id.strip():
            raise ContractError("case_id is required")
        self.flow.validate()
        self.references.validate()
        AeroBasis.from_freestream(self.flow.freestream_body_mps, self.lift_up_body)

    @property
    def basis(self) -> AeroBasis:
        return AeroBasis.from_freestream(self.flow.freestream_body_mps, self.lift_up_body)


@dataclass(frozen=True)
class ForceMoment:
    force_body_n: Vector3 = Vector3()
    moment_body_nm: Vector3 = Vector3()
    sample_count: int | None = None
    averaging_start_s: float | None = None
    averaging_end_s: float | None = None


@dataclass(frozen=True)
class Coefficients:
    cd: float
    cl: float
    cs: float
    cm_pitch: float | None = None
    cl_roll: float | None = None
    cn_yaw: float | None = None


@dataclass(frozen=True)
class Diagnostics:
    converged: bool | None = None
    residuals: Mapping[str, float] = field(default_factory=dict)
    iterations: int | None = None
    simulated_time_s: float | None = None
    wall_time_s: float | None = None
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CFDResult:
    case_id: str
    solver_backend: str
    solver_version: str | None
    compute_provider: str
    state: JobState
    force_moment: ForceMoment | None = None
    coefficients: Coefficients | None = None
    diagnostics: Diagnostics = Diagnostics()
    artifacts: tuple[Artifact, ...] = ()
    evidence_state: str = "model_unqualified"
    claim_ceiling: str = "not_airworthy"
    method: str = "cfd"
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported result schema version: {self.schema_version}")
        if not self.case_id:
            raise ContractError("result case_id is required")
        if self.state == JobState.SUCCEEDED and self.force_moment is None and self.coefficients is None and not self.artifacts:
            raise ContractError("successful CFD result must contain evidence")


@dataclass(frozen=True)
class PreparedJob:
    """A solver-specific job bundle that a compute provider can execute."""

    case: AeroCase
    workdir: str
    command: tuple[str, ...]
    environment: Mapping[str, str] = field(default_factory=dict)
    expected_result: str = "result.json"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionReceipt:
    state: JobState
    returncode: int | None
    stdout_path: str | None = None
    stderr_path: str | None = None
    provider_job_id: str | None = None
    wall_time_s: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None


def coefficients_from_force(case: AeroCase, force_moment: ForceMoment) -> Coefficients:
    """Project a body-axis force/moment into aerodynamic coefficients."""

    case.validate()
    q = case.flow.dynamic_pressure_pa
    s = case.references.area_m2
    basis = case.basis
    f = force_moment.force_body_n
    # Aerodynamic drag is opposite freestream direction; lift and side follow
    # their explicitly configured positive basis directions.
    drag_n = -f.dot(basis.drag)
    side_n = f.dot(basis.side)
    lift_n = f.dot(basis.lift)
    denom = q * s
    if denom <= 0.0:
        raise ContractError("dynamic pressure * reference area must be positive")
    m = force_moment.moment_body_nm
    pitch_denom = denom * case.references.length_m
    span = case.references.span_m or case.references.length_m
    lateral_denom = denom * span
    return Coefficients(
        cd=drag_n / denom,
        cl=lift_n / denom,
        cs=side_n / denom,
        cm_pitch=m.y / pitch_denom,
        cl_roll=m.x / lateral_denom,
        cn_yaw=m.z / lateral_denom,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def to_json(value: Any, *, indent: int = 2) -> str:
    return json.dumps(_jsonable(value), indent=indent, sort_keys=True, ensure_ascii=False)


def write_json(path: str | Path, value: Any) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(value) + "\n", encoding="utf-8")
    return str(p)
