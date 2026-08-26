# Canonical Code Reference — Reconciliation Pass 03 Correction 01

Generated from the cleaned `proposed_overlay/` source tree. Files: **57**.

This is a consolidated reference/handoff, not a claim that these files are installed upstream. The host-runtime reference modules are design evidence; upstream integration still requires a fresh freeze and authorized implementation.

## `README.md`

````markdown
# VibeCADAero Reconciliation Pass 03 Correction 01 — Proposed Overlay

This tree is a **reference implementation/handoff**, not a drop-in replacement for the live upstream tree.

It contains executable reference semantics for:

- CFD case/result/artifact/frame contracts;
- host-aligned evidence and artifact taxonomy;
- geometry-readiness states distinct from exactness;
- Native revision/result attachment and repair-transition checks;
- detached solver-input hashing/stale-result attachment invariants;
- solver-neutral Aero job-domain records;
- local and Kaggle compute providers;
- FluidX3D and CfdOF/OpenFOAM adapters;
- mesh and field correspondence;
- deterministic/explainable routing;
- versioned solver qualification;
- dynamic/unsteady/6-DOF reference models;
- one-time informational third-party notice.

`AeroJobStore.py` is now explicitly **TRANSITIONAL / REFERENCE ONLY**. Correction 01 requires one host-owned VibeCAD Analysis Runtime extracted from the existing Native Background + detached FEM paths. FEM must prove parity first; Aero then consumes that host service. Do not promote this overlay store into production scheduling/persistence authority.

The first-use notice checkbox text is exactly **“I understand.”** It is informational only and does not classify VibeCAD/Aero use or control solver eligibility.

## Test

From this `proposed_overlay/` directory:

```bash
python -m compileall -q .
pytest -q
```

The package includes `tests/conftest.py`, so no caller-side `PYTHONPATH` is required. Correction-01 validation: **45 tests passed**.


## Reference host-runtime proof

`reference_host_runtime/VibeCADAnalysisJobState.py` is a FreeCAD-independent proof model for the atomic cancellation/publication gate identified during Correction-01 deepening. `reference_host_runtime/VibeCADAnalysisPublication.py` models inert durable publication descriptors/currentness and proves that source/currentness/fresh-host-authorization are distinct prerequisites. It is **not** claimed to be installed or integrated upstream. Production implementation must follow the compatibility, document-lifecycle, persistence and process-control gates in the package root.
````

## `fluidx3d_bridge/README.md`

```markdown
# VibeCAD FluidX3D Bridge Reference

This directory contains the VibeCAD-owned `setup_vibecad.cpp` reference bridge intended to be built against the pinned vendored FluidX3D source. It uses verified source-level FluidX3D APIs rather than the unverified Python API assumed in early discussion drafts.

The normal target product uses the packaged vendored bridge; `AeroLBM` also accepts an explicitly configured external bridge override. This is ordinary configuration, not a purpose/use profile.

The bridge contract uses environment/job files for SI physical inputs, geometry scale, domain/resolution, transient/sample controls and result location. VibeCAD computes final aerodynamic coefficients from canonical reference quantities after the bridge returns dimensional body-axis force/moment evidence.

FluidX3D's authoritative third-party license remains readable with its vendored source. Aero's single first-use notice is informational and uses **“I understand.”**
```

## `fluidx3d_bridge/setup_vibecad.cpp`

```cpp
// VibeCADAero reconciliation pass 03 -- FluidX3D setup template
// SPDX-License-Identifier: LGPL-2.1-or-later for this adapter file only.
//
// IMPORTANT: FluidX3D itself has a separate custom license. This VibeCAD-owned
// bridge does not change that license. The canonical VibeCAD distribution
// vendors a pinned FluidX3D tree while preserving the upstream license/origin.
// The bundled documentation states the third-party usage and redistribution
// requirements; VibeCAD does not classify user purpose or auto-disable this bridge.
//
// Pin used while designing this adapter:
//   ProjectPhysX/FluidX3D @ 8986874e626e0aebd317ab16c420b39e30dfa273
// Verified public APIs at that pin include LBM::run(), voxelize_stl(),
// update_force_field(), object_force(), object_center_of_mass(), object_torque(),
// and Units::{set_m_kg_s,u,nu,si_F,si_M,si_t}.
//
// This is intended to be used as the contents of the vendored FluidX3D's
// src/setup.cpp (or mechanically included from it) for the packaged vendored
// bridge binary. The same adapter can also be built against an explicitly configured
// external FluidX3D installation. It uses
// FluidX3D's normal main_setup() entry point rather than inventing an argv parser.
// VibeCAD passes the job through environment variables.
//
// Required defines.hpp extensions for this baseline:
//   #define FORCE_FIELD
//   #define EQUILIBRIUM_BOUNDARIES
// Optional after validation:
//   #define SUBGRID
//   #define MOVING_BOUNDARIES

#include "lbm.hpp"
#include "units.hpp"

#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <stdexcept>
#include <string>

#ifndef VIBECAD_FLUIDX3D_COMMIT
#define VIBECAD_FLUIDX3D_COMMIT "unknown"
#endif

static std::string env_required(const char* key) {
    const char* value = std::getenv(key);
    if(value == nullptr || *value == '\0') {
        throw std::runtime_error(std::string("Missing environment variable: ") + key);
    }
    return std::string(value);
}

static float env_float(const char* key, const float fallback) {
    const char* value = std::getenv(key);
    return value == nullptr || *value == '\0' ? fallback : std::stof(value);
}

static uint env_uint(const char* key, const uint fallback) {
    const char* value = std::getenv(key);
    return value == nullptr || *value == '\0' ? fallback : (uint)std::stoul(value);
}

static std::string json_escape(const std::string& input) {
    std::string out;
    out.reserve(input.size());
    for(const char c : input) {
        switch(c) {
            case '\\': out += "\\\\"; break;
            case '"':  out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += c; break;
        }
    }
    return out;
}

void main_setup() {
    const std::string case_id = env_required("VIBECAD_FX3D_CASE_ID");
    const std::string stl_path = env_required("VIBECAD_FX3D_STL");
    const std::string result_path = env_required("VIBECAD_FX3D_RESULT");

    // SI flow state in VibeCAD body axes (+X forward, +Y right, +Z down).
    const float ux_si = env_float("VIBECAD_FX3D_UX", 10.0f);
    const float uy_si = env_float("VIBECAD_FX3D_UY", 0.0f);
    const float uz_si = env_float("VIBECAD_FX3D_UZ", 0.0f);
    const float speed_si = sqrt(ux_si*ux_si + uy_si*uy_si + uz_si*uz_si);
    const float rho_si = env_float("VIBECAD_FX3D_RHO", 1.225f);
    const float mu_si = env_float("VIBECAD_FX3D_MU", 1.81e-5f);
    if(speed_si <= 0.0f || rho_si <= 0.0f || mu_si <= 0.0f) {
        throw std::runtime_error("Velocity, density and viscosity must be positive/non-zero.");
    }

    // ``geometry_size_m`` is the physical size corresponding to the STL's
    // maximum dimension.  Requiring it explicitly prevents the old design bug
    // where an arbitrary reference chord was silently used to scale a whole UAV.
    const float geometry_size_m = env_float("VIBECAD_FX3D_GEOMETRY_SIZE_M", -1.0f);
    const float geometry_size_lu = env_float("VIBECAD_FX3D_GEOMETRY_SIZE_LU", 128.0f);
    const float lbm_speed = env_float("VIBECAD_FX3D_LBM_SPEED", 0.08f);
    if(geometry_size_m <= 0.0f || geometry_size_lu <= 0.0f || lbm_speed <= 0.0f) {
        throw std::runtime_error("Geometry scale and lattice speed must be positive.");
    }

    const uint Nx = env_uint("VIBECAD_FX3D_NX", 512u);
    const uint Ny = env_uint("VIBECAD_FX3D_NY", 256u);
    const uint Nz = env_uint("VIBECAD_FX3D_NZ", 256u);
    const uint transient_steps = max(1u, env_uint("VIBECAD_FX3D_TRANSIENT_STEPS", 2000u));
    const uint sample_every = max(1u, env_uint("VIBECAD_FX3D_SAMPLE_EVERY", 100u));
    const uint sample_count = max(1u, env_uint("VIBECAD_FX3D_SAMPLE_COUNT", 50u));

    // Establish one physically meaningful SI<->lattice conversion.  The mesh's
    // maximum physical dimension maps to geometry_size_lu cells; the SI speed
    // magnitude maps to lbm_speed.  Vector components are converted consistently.
    units.set_m_kg_s(
        geometry_size_lu,
        lbm_speed,
        1.0f,
        geometry_size_m,
        speed_si,
        rho_si
    );
    const float nu_si = mu_si / rho_si;
    const float nu_lbm = units.nu(nu_si);
    const float ux = units.u(ux_si);
    const float uy = units.u(uy_si);
    const float uz = units.u(uz_si);

    LBM lbm(Nx, Ny, Nz, nu_lbm);

    // FluidX3D's STL voxelizer preserves relative shape proportions and places
    // the mesh at the lattice center.  TYPE_X marks this specific solid for force
    // reduction without conflating it with domain boundary cells.
    lbm.voxelize_stl(
        stl_path,
        lbm.center(),
        float3x3(1.0f),
        geometry_size_lu,
        TYPE_S | TYPE_X
    );

    // Baseline external-flow initialization.  All fluid cells start at the
    // freestream velocity.  Outer cells use equilibrium boundaries.  This is a
    // robust *baseline*, not yet a validated high-Re wind-tunnel boundary model;
    // the reconciliation paper requires benchmark validation and domain studies.
    parallel_for(lbm.get_N(), [&](ulong n) {
        uint x=0u, y=0u, z=0u;
        lbm.coordinates(n, x, y, z);
        const bool solid = (lbm.flags[n] & TYPE_S) != 0u;
        if(!solid) {
            lbm.u.x[n] = ux;
            lbm.u.y[n] = uy;
            lbm.u.z[n] = uz;
            if(x==0u || x==Nx-1u || y==0u || y==Ny-1u || z==0u || z==Nz-1u) {
                lbm.flags[n] = TYPE_E;
            }
        }
    });

    lbm.run(transient_steps);
    const float averaging_start_s = units.si_t(lbm.get_t());

    float3 force_sum = float3(0.0f);
    float3 torque_sum = float3(0.0f);
    for(uint sample=0u; sample<sample_count; ++sample) {
        lbm.run(sample_every);
        // Explicit update documents the force-field dependency; object_force()
        // then performs the reduction for TYPE_S|TYPE_X cells.
        lbm.update_force_field();
        const float3 force = lbm.object_force(TYPE_S | TYPE_X);
        const float3 center = lbm.object_center_of_mass(TYPE_S | TYPE_X);
        const float3 torque = lbm.object_torque(center, TYPE_S | TYPE_X);
        force_sum += force;
        torque_sum += torque;
    }

    const float inv_samples = 1.0f / (float)sample_count;
    const float3 force_avg = force_sum * inv_samples;
    const float3 torque_avg = torque_sum * inv_samples;
    const float3 force_si(
        units.si_F(force_avg.x),
        units.si_F(force_avg.y),
        units.si_F(force_avg.z)
    );
    const float3 torque_si(
        units.si_M(torque_avg.x),
        units.si_M(torque_avg.y),
        units.si_M(torque_avg.z)
    );
    const float averaging_end_s = units.si_t(lbm.get_t());

    std::ofstream out(result_path);
    if(!out) throw std::runtime_error("Unable to open result output file.");
    out << std::setprecision(10);
    out << "{\n";
    out << "  \"schema_version\": \"vibecad.fluidx3d.bridge/1\",\n";
    out << "  \"bridge_version\": \"pass01-1\",\n";
    out << "  \"fluidx3d_commit\": \"" << json_escape(VIBECAD_FLUIDX3D_COMMIT) << "\",\n";
    out << "  \"case_id\": \"" << json_escape(case_id) << "\",\n";
    out << "  \"force_body_n\": [" << force_si.x << ", " << force_si.y << ", " << force_si.z << "],\n";
    out << "  \"moment_body_nm\": [" << torque_si.x << ", " << torque_si.y << ", " << torque_si.z << "],\n";
    out << "  \"moment_reference\": \"object_center_of_mass\",\n";
    out << "  \"sample_count\": " << sample_count << ",\n";
    out << "  \"averaging_start_s\": " << averaging_start_s << ",\n";
    out << "  \"averaging_end_s\": " << averaging_end_s << ",\n";
    out << "  \"simulated_time_s\": " << averaging_end_s << ",\n";
    out << "  \"iterations\": " << lbm.get_t() << ",\n";
    out << "  \"converged\": null,\n";
    out << "  \"lattice\": {\"Nx\": " << Nx << ", \"Ny\": " << Ny << ", \"Nz\": " << Nz << "},\n";
    out << "  \"warnings\": [\"Baseline equilibrium outer boundaries; validate domain and high-Re behavior before qualification.\"]\n";
    out << "}\n";
    out.close();
}
```

## `reference_host_runtime/README.md`

```markdown
# Reference host runtime

This directory is **design evidence only**. It is not claimed to be installed or integrated in upstream VibeCAD.

`VibeCADAnalysisJobState.py` demonstrates the corrected lifecycle invariant: cancellation and publication ownership are linearized under one lock so an accepted cancellation can never be followed by CAD publication.

`VibeCADAnalysisPublication.py` demonstrates the second invariant: durable job provenance is inert. Missing source waits, stale dependencies remain stale, current results without fresh host publication authorization wait, and an existing receipt makes replay idempotent. It deliberately contains no FreeCAD mutation code.

Production integration must be reconciled against a fresh upstream SHA and must use VibeCAD's actual main-thread dispatch, Native mutation boundary, FEM adapter, persistence, and provider implementations.
```

## `reference_host_runtime/VibeCADAnalysisJobState.py`

```python
"""Reference-only host Analysis Runtime lifecycle model.

This module is intentionally FreeCAD-independent and is NOT wired into upstream
VibeCAD. It proves the atomic cancellation-versus-publication gate required by
Pass 03 Correction 01. Its internal status/phase vocabulary is a proof model,
not a replacement for the current public NativeBackground phase surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

Status = Literal[
    "queued", "running", "cancelling", "waiting_to_commit",
    "succeeded", "failed", "cancelled",
]
Phase = Literal[
    "queued", "running_solver", "finalizing", "waiting_to_commit",
    "committing", "completed",
]

_TERMINAL = {"succeeded", "failed", "cancelled"}
_CANCELABLE_PHASES = {"queued", "running_solver", "waiting_to_commit"}


@dataclass
class AnalysisJobState:
    job_id: str
    status: Status = "queued"
    phase: Phase = "queued"
    cancellation_requested: bool = False
    terminal_reason: str | None = None
    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def start(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL or self.cancellation_requested:
                return False
            if self.phase != "queued":
                return False
            self.status = "running"
            self.phase = "running_solver"
            return True

    def provider_completed(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.cancellation_requested:
                self._finish_cancelled("cancelled_before_publication")
                return False
            if self.phase != "running_solver":
                return False
            self.status = "waiting_to_commit"
            self.phase = "waiting_to_commit"
            return True

    def request_cancel(self) -> bool:
        """Linearizable cancellation request.

        True means cancellation won before publication ownership. False means
        the job is terminal or publication has become non-cancellable.
        """
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.phase not in _CANCELABLE_PHASES:
                return False
            self.cancellation_requested = True
            self.status = "cancelling"
            if self.phase in {"queued", "waiting_to_commit"}:
                self._finish_cancelled("cancelled_before_publication")
            return True

    def acknowledge_running_cancel(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "cancelled"
            if not self.cancellation_requested:
                return False
            self._finish_cancelled("provider_cancelled")
            return True

    def try_begin_publication(self) -> bool:
        """Atomic cancellation-vs-publication ownership gate."""
        with self._lock:
            if self.status in _TERMINAL:
                return False
            if self.phase != "waiting_to_commit":
                return False
            if self.cancellation_requested:
                self._finish_cancelled("cancelled_before_publication")
                return False
            self.status = "running"
            self.phase = "committing"
            return True

    # Backward-compatible reference name used by earlier Correction-01 tests.
    def try_begin_commit(self) -> bool:
        return self.try_begin_publication()

    def succeed(self) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "succeeded"
            if self.phase != "committing":
                return False
            self.status = "succeeded"
            self.phase = "completed"
            self.terminal_reason = "published"
            return True

    def fail(self, reason: str) -> bool:
        with self._lock:
            if self.status in _TERMINAL:
                return self.status == "failed"
            self.status = "failed"
            self.phase = "completed"
            self.terminal_reason = reason
            return True

    def _finish_cancelled(self, reason: str) -> None:
        self.status = "cancelled"
        self.phase = "completed"
        self.terminal_reason = reason
```

## `reference_host_runtime/VibeCADAnalysisPublication.py`

```python
"""Reference-only durable publication descriptor/currentness semantics.

This file deliberately contains no FreeCAD mutation code.  It demonstrates the
architectural rule that persisted job provenance is inert and that publication
requires a separately supplied fresh host authorization after currentness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

PublicationState = Literal[
    "UNVALIDATED",
    "AWAITING_SOURCE",
    "AWAITING_PUBLICATION",
    "CURRENT",
    "STALE",
    "QUARANTINED",
    "PUBLISHED",
]


@dataclass(frozen=True, slots=True)
class PublicationDescriptor:
    publication_id: str
    job_id: str
    analysis_id: str
    submission_id: str
    domain_id: str
    adapter_id: str
    adapter_version: str
    source_document_uid: str
    frozen_dependency_snapshot_id: str
    output_manifest_id: str
    result_identity: str


@dataclass(frozen=True, slots=True)
class CurrentnessReport:
    current: bool
    source_resolved: bool
    changed_dependencies: tuple[str, ...] = ()
    ambiguous_dependencies: tuple[str, ...] = ()

    @property
    def disposition(self) -> PublicationState:
        if not self.source_resolved:
            return "AWAITING_SOURCE"
        if self.ambiguous_dependencies or not self.current:
            return "STALE"
        return "CURRENT"


def publication_disposition(
    descriptor: PublicationDescriptor,
    report: CurrentnessReport,
    *,
    fresh_host_authorization: bool,
    existing_receipt: Mapping[str, object] | None = None,
) -> PublicationState:
    """Pure decision helper; it cannot and does not mutate CAD."""
    if existing_receipt is not None:
        return "PUBLISHED"
    disposition = report.disposition
    if disposition != "CURRENT":
        return disposition
    if not fresh_host_authorization:
        return "AWAITING_PUBLICATION"
    # A real host now enters NativeMutationRunner/transaction/postcondition flow.
    return "CURRENT"
```

## `reference_host_runtime/__init__.py`

```python

```

## `reference_host_runtime/test_analysis_job_state.py`

```python
from reference_host_runtime.VibeCADAnalysisJobState import AnalysisJobState


def test_cancel_before_start_wins_and_prevents_execution() -> None:
    job = AnalysisJobState("job-1")
    assert job.request_cancel() is True
    assert job.status == "cancelled"
    assert job.start() is False


def test_cancel_after_provider_completion_before_publication_prevents_publication() -> None:
    job = AnalysisJobState("job-2")
    assert job.start() is True
    assert job.provider_completed() is True
    assert job.phase == "waiting_to_commit"
    assert job.request_cancel() is True
    assert job.status == "cancelled"
    assert job.try_begin_publication() is False


def test_publication_gate_wins_atomically_and_late_cancel_is_rejected() -> None:
    job = AnalysisJobState("job-3")
    assert job.start() is True
    assert job.provider_completed() is True
    assert job.try_begin_publication() is True
    assert job.phase == "committing"
    assert job.request_cancel() is False
    assert job.succeed() is True
    assert job.status == "succeeded"


def test_terminal_success_is_idempotent_and_not_reopened() -> None:
    job = AnalysisJobState("job-4")
    assert job.start() is True
    assert job.provider_completed() is True
    assert job.try_begin_commit() is True
    assert job.succeed() is True
    assert job.succeed() is True
    assert job.provider_completed() is False
    assert job.request_cancel() is False
    assert job.status == "succeeded"
```

## `reference_host_runtime/test_analysis_publication.py`

```python
from reference_host_runtime.VibeCADAnalysisPublication import (
    CurrentnessReport,
    PublicationDescriptor,
    publication_disposition,
)


def _descriptor() -> PublicationDescriptor:
    return PublicationDescriptor(
        publication_id="pub-1",
        job_id="job-1",
        analysis_id="analysis-1",
        submission_id="sub-1",
        domain_id="aero",
        adapter_id="openfoam",
        adapter_version="1",
        source_document_uid="doc-uid",
        frozen_dependency_snapshot_id="deps-1",
        output_manifest_id="out-1",
        result_identity="result-1",
    )


def test_missing_source_waits_instead_of_guessing_document() -> None:
    report = CurrentnessReport(current=False, source_resolved=False)
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=False
    ) == "AWAITING_SOURCE"


def test_current_result_without_fresh_host_authorization_waits() -> None:
    report = CurrentnessReport(current=True, source_resolved=True)
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=False
    ) == "AWAITING_PUBLICATION"


def test_relevant_dependency_drift_is_stale_even_with_host_authorization() -> None:
    report = CurrentnessReport(
        current=False,
        source_resolved=True,
        changed_dependencies=("geometry_sha256",),
    )
    assert publication_disposition(
        _descriptor(), report, fresh_host_authorization=True
    ) == "STALE"


def test_existing_receipt_makes_replay_idempotently_published() -> None:
    report = CurrentnessReport(current=True, source_resolved=True)
    assert publication_disposition(
        _descriptor(),
        report,
        fresh_host_authorization=True,
        existing_receipt={"publication_id": "pub-1"},
    ) == "PUBLISHED"
```

## `schema/aero-cfd-result.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "vibecad.aero.cfd.result.v1",
  "title": "VibeCADAero CFD Result",
  "type": "object",
  "required": ["schema_version", "case_id", "solver_backend", "compute_provider", "state", "method"],
  "properties": {
    "schema_version": {"const": "vibecad.aero.cfd/1"},
    "case_id": {"type": "string", "minLength": 1},
    "solver_backend": {"type": "string", "minLength": 1},
    "solver_version": {"type": ["string", "null"]},
    "compute_provider": {"type": "string", "minLength": 1},
    "state": {"enum": ["prepared", "running", "succeeded", "failed", "cancelled"]},
    "method": {"type": "string", "minLength": 1},
    "evidence_state": {"type": "string"},
    "claim_ceiling": {"type": "string"},
    "error": {"type": ["string", "null"]},
    "force_moment": {
      "type": ["object", "null"],
      "properties": {
        "force_body_n": {"$ref": "#/$defs/vector3"},
        "moment_body_nm": {"$ref": "#/$defs/vector3"},
        "sample_count": {"type": ["integer", "null"], "minimum": 0}
      }
    },
    "coefficients": {
      "type": ["object", "null"],
      "properties": {
        "cd": {"type": "number"},
        "cl": {"type": "number"},
        "cs": {"type": "number"},
        "cm_pitch": {"type": ["number", "null"]},
        "cl_roll": {"type": ["number", "null"]},
        "cn_yaw": {"type": ["number", "null"]}
      },
      "required": ["cd", "cl", "cs"]
    },
    "artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "sha256", "media_type", "size_bytes", "role"],
        "properties": {
          "path": {"type": "string"},
          "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
          "media_type": {"type": "string"},
          "size_bytes": {"type": "integer", "minimum": 0},
          "role": {"type": "string"}
        }
      }
    }
  },
  "$defs": {
    "vector3": {
      "type": "object",
      "required": ["x", "y", "z"],
      "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"}
      },
      "additionalProperties": false
    }
  }
}
```

## `src/Mod/VibeCADAero/AeroAcknowledgement.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Persistence contract for VibeCADAero's single first-use acknowledgement.

This module intentionally contains no license-purpose classification, solver-specific
checks, telemetry, version counter, expiry, or repeated acceptance mechanism.
The GUI/native entry surface owns presentation of the one checkbox and calls
``acknowledge()`` once the user dismisses the first-use notice.
"""

from __future__ import annotations

from typing import Any

PREFERENCE_GROUP = "User parameter:BaseApp/Preferences/Mod/VibeCADAero"
ACKNOWLEDGEMENT_KEY = "ThirdPartyNoticesAcknowledged"
ACKNOWLEDGEMENT_TEXT = "I understand."
PRODUCT_LICENSE_NOTICE = (
    "VibeCAD Aero can use third-party software with license terms separate from VibeCAD. "
    "Those terms apply only to the third-party components they govern and do not change "
    "the VibeCAD/VibeCADAero license or ownership of CAD designs created in VibeCAD. "
    "See Third-Party Notices for details."
)


def _preferences(store: Any | None = None) -> Any:
    if store is not None:
        return store
    import FreeCAD  # type: ignore
    return FreeCAD.ParamGet(PREFERENCE_GROUP)


def is_acknowledged(store: Any | None = None) -> bool:
    """Return the one persistent Aero acknowledgement bit."""
    return bool(_preferences(store).GetBool(ACKNOWLEDGEMENT_KEY, False))


def acknowledge(store: Any | None = None) -> None:
    """Persist that the informational notice was acknowledged. There is deliberately no version/expiry value."""
    _preferences(store).SetBool(ACKNOWLEDGEMENT_KEY, True)


def first_use_state(store: Any | None = None) -> dict[str, Any]:
    """Small UI/native contract; does not classify purpose or solver eligibility."""
    accepted = is_acknowledged(store)
    return {
        "show_notice": not accepted,
        "acknowledged": accepted,
        "product_license_notice": PRODUCT_LICENSE_NOTICE,
        "checkbox_text": ACKNOWLEDGEMENT_TEXT,
        "preference_key": ACKNOWLEDGEMENT_KEY,
        "versioned": False,
    }
```

## `src/Mod/VibeCADAero/AeroCFD.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""High-fidelity solver orchestration for the VibeCADAero overlay.

The core design rule is separation of *solver* and *compute provider*:
FluidX3D or OpenFOAM defines what physics is solved; local execution or Kaggle
defines where a prepared job is run.  This prevents cloud-provider policy from
leaking into aerodynamic semantics and keeps future HPC providers possible.

This module intentionally does not write FreeCAD documents.  The public
``VibeCADAero.py`` authority should call this layer and then persist a summary
through the existing ``AeroResults`` / ``AeroStamp`` path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Protocol

from AeroCFDContracts import (
    AeroCase,
    CFDResult,
    ExecutionReceipt,
    JobState,
    PreparedJob,
)


class BackendError(RuntimeError):
    pass


class SolverBackend(Protocol):
    name: str

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob: ...
    def parse(self, case: AeroCase, job: PreparedJob, receipt: ExecutionReceipt) -> CFDResult: ...


class ComputeProvider(Protocol):
    name: str

    def execute(self, job: PreparedJob) -> ExecutionReceipt: ...


class Registry:
    def __init__(self) -> None:
        self.solvers: dict[str, SolverBackend] = {}
        self.providers: dict[str, ComputeProvider] = {}

    def add_solver(self, backend: SolverBackend) -> None:
        key = str(backend.name).strip().lower()
        if not key:
            raise ValueError("solver backend requires a name")
        self.solvers[key] = backend

    def add_provider(self, provider: ComputeProvider) -> None:
        key = str(provider.name).strip().lower()
        if not key:
            raise ValueError("compute provider requires a name")
        self.providers[key] = provider

    def solver(self, name: str) -> SolverBackend:
        try:
            return self.solvers[str(name).lower()]
        except KeyError as exc:
            raise BackendError(f"CFD solver backend is not registered: {name}") from exc

    def provider(self, name: str) -> ComputeProvider:
        try:
            return self.providers[str(name).lower()]
        except KeyError as exc:
            raise BackendError(f"CFD compute provider is not registered: {name}") from exc


def run_case(
    case: AeroCase,
    *,
    registry: Registry,
    workspace_root: str | Path | None = None,
) -> CFDResult:
    """Prepare, execute and parse exactly one immutable CFD case."""

    case.validate()
    solver = registry.solver(case.solver.backend)
    provider = registry.provider(case.compute.provider)

    root = Path(workspace_root) if workspace_root else Path(tempfile.mkdtemp(prefix="vibecad_cfd_"))
    workdir = root / case.case_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        job = solver.prepare(case, workdir)
        receipt = provider.execute(job)
        result = solver.parse(case, job, receipt)
        result.validate()
        return result
    except Exception as exc:
        return CFDResult(
            case_id=case.case_id,
            solver_backend=case.solver.backend,
            solver_version=case.solver.backend_version,
            compute_provider=case.compute.provider,
            state=JobState.FAILED,
            evidence_state="failed",
            claim_ceiling="not_airworthy",
            method=f"cfd:{case.solver.backend}",
            error=str(exc),
            metadata={"workspace": str(workdir)},
        )


def report_patch(result: CFDResult) -> dict[str, object]:
    """Map a CFD result into fields that can extend the existing AeroReport.

    Existing CL/CD/CM names are intentionally preserved.  New provenance fields
    are namespaced so the upstream report can grow without breaking existing
    consumers or assistant context.
    """

    patch: dict[str, object] = {
        "CFDBackend": result.solver_backend,
        "CFDBackendVersion": result.solver_version or "",
        "CFDComputeProvider": result.compute_provider,
        "CFDState": result.state.value,
        "CFDMethod": result.method,
        "CFDEvidenceState": result.evidence_state,
        "CFDClaimCeiling": result.claim_ceiling,
        "CFDError": result.error or "",
        "CFDArtifacts": [
            {
                "path": a.path,
                "sha256": a.sha256,
                "media_type": a.media_type,
                "role": a.role,
                "size_bytes": a.size_bytes,
            }
            for a in result.artifacts
        ],
    }
    if result.coefficients is not None:
        patch.update(
            {
                "CL": result.coefficients.cl,
                "CD": result.coefficients.cd,
                "CM": result.coefficients.cm_pitch if result.coefficients.cm_pitch is not None else 0.0,
                "CFDSideCoefficient": result.coefficients.cs,
                "CFDRollCoefficient": result.coefficients.cl_roll,
                "CFDYawCoefficient": result.coefficients.cn_yaw,
                "source": f"CFD:{result.solver_backend}",
            }
        )
    if result.force_moment is not None:
        patch["CFDForceBodyN"] = list(result.force_moment.force_body_n.as_tuple())
        patch["CFDMomentBodyNm"] = list(result.force_moment.moment_body_nm.as_tuple())
    patch["CFDConverged"] = result.diagnostics.converged
    patch["CFDResiduals"] = dict(result.diagnostics.residuals)
    return patch
```

## `src/Mod/VibeCADAero/AeroCFDContracts.py`

```python
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
```

## `src/Mod/VibeCADAero/AeroCFDUpstreamAdapter.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Adapter from existing upstream AeroConfig dictionaries into canonical CFD cases."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    ComputeSpec,
    FlowConditions,
    GeometryArtifact,
    ReferenceQuantities,
    SolverSpec,
    Vector3,
)


def _case_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "aero-" + hashlib.sha256(raw).hexdigest()[:20]


def case_from_upstream_config(
    cfg: dict[str, Any],
    geometry: GeometryArtifact,
    *,
    speed_mps: float,
    alpha_deg: float | None = None,
    beta_deg: float = 0.0,
    density_kg_m3: float = 1.225,
    dynamic_viscosity_pa_s: float = 1.81e-5,
    solver_backend: str,
    solver_model: str,
    solver_settings: dict[str, Any] | None = None,
    compute_provider: str = "local",
    compute_settings: dict[str, Any] | None = None,
    moment_reference_body_m: Vector3 = Vector3(),
) -> AeroCase:
    """Build a solver case from the current ``AeroConfig.resolve_geometry`` shape.

    ``geometry`` must already be expressed in the canonical body frame.  This reference
    refuses to guess the transform from raw CAD axes because the live upstream
    has an explicit ``CadAeroFrame`` mapping that must be reused during later
    integration.  This is an intentional safety seam, not missing bookkeeping.
    """

    if speed_mps <= 0.0:
        raise ValueError("speed_mps must be positive")
    alpha = math.radians(float(cfg.get("alpha_deg", 0.0) if alpha_deg is None else alpha_deg))
    beta = math.radians(float(beta_deg))
    # Aircraft velocity relative to the air in canonical body axes: +X forward,
    # +Y right, +Z down.  Positive alpha therefore has positive body-Z velocity.
    velocity = Vector3(
        speed_mps * math.cos(alpha) * math.cos(beta),
        speed_mps * math.sin(beta),
        speed_mps * math.sin(alpha) * math.cos(beta),
    )

    area = float(cfg.get("reference_area_m2") or 0.0)
    chord = float(cfg.get("chord_m") or 0.0)
    span = float(cfg.get("span_m") or 0.0)
    if area <= 0.0 or chord <= 0.0 or span <= 0.0:
        raise ValueError("upstream AeroConfig must provide positive reference area/chord/span")

    identity = {
        "geometry": geometry.artifact.sha256,
        "revision": geometry.geometry_revision,
        "speed_mps": speed_mps,
        "alpha_deg": math.degrees(alpha),
        "beta_deg": beta_deg,
        "solver": solver_backend,
        "model": solver_model,
        "solver_settings": solver_settings or {},
        "compute": compute_provider,
        "compute_settings": compute_settings or {},
    }
    case = AeroCase(
        case_id=_case_id(identity),
        geometry=geometry,
        flow=FlowConditions(
            freestream_body_mps=velocity,
            density_kg_m3=float(density_kg_m3),
            dynamic_viscosity_pa_s=float(dynamic_viscosity_pa_s),
        ),
        references=ReferenceQuantities(
            area_m2=area,
            length_m=chord,
            span_m=span,
            moment_reference_body_m=moment_reference_body_m,
            area_definition=str(cfg.get("reference_area_definition") or "upstream_AeroConfig"),
        ),
        solver=SolverSpec(
            backend=solver_backend,
            model=solver_model,
            settings=dict(solver_settings or {}),
        ),
        compute=ComputeSpec(provider=compute_provider, settings=dict(compute_settings or {})),
        metadata={
            "vehicle_type": cfg.get("vehicle_type"),
            "airfoil": cfg.get("airfoil"),
            "config_source": cfg.get("source"),
            "source_span_m": span,
            "source_chord_m": chord,
        },
    )
    case.validate()
    return case
```

## `src/Mod/VibeCADAero/AeroDetachedExecution.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Solver-neutral detached execution invariants distilled from live VibeCAD FEM.

Pass 03 Correction 01 treats this module as a **transitional reference only**.
The canonical target is to extract these physics-neutral invariants into one
host-owned VibeCAD Analysis Runtime, prove that runtime first with existing FEM,
and then make Aero a client without changing Aero case/result contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FrozenInput:
    root: str
    sha256: str
    file_count: int
    total_bytes: int


def freeze_directory(root: str | Path, *, max_files: int = 4096, max_bytes: int = 4 * 1024**3) -> FrozenInput:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValueError("detached input contains symlink")
        if not path.is_file():
            continue
        count += 1
        total += path.stat().st_size
        if count > max_files or total > max_bytes:
            raise ValueError("detached input exceeds configured bounds")
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    if count == 0:
        raise ValueError("detached input contains no files")
    return FrozenInput(str(base), digest.hexdigest(), count, total)


@dataclass(frozen=True)
class AttachmentGuard:
    input_sha256: str
    native_revision: int
    geometry_revision: str
    case_sha256: str


def can_attach(guard: AttachmentGuard, *, input_sha256: str, native_revision: int, geometry_revision: str, case_sha256: str) -> bool:
    return (
        str(input_sha256) == guard.input_sha256
        and int(native_revision) == guard.native_revision
        and str(geometry_revision) == guard.geometry_revision
        and str(case_sha256) == guard.case_sha256
    )
```

## `src/Mod/VibeCADAero/AeroDynamicStall.py`

```python
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
```

## `src/Mod/VibeCADAero/AeroFieldResults.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Surface/volume field result contracts and CAD-face aggregation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SurfaceSample:
    triangle_index: int
    source_face_index: int
    area_m2: float
    pressure_pa: float | None = None
    cp: float | None = None
    wall_shear_pa: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SurfaceField:
    samples: tuple[SurfaceSample, ...]
    geometry_sha256: str
    mapping_version: str = "triangle_to_cad_face/1"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        seen: set[int] = set()
        for sample in self.samples:
            if sample.triangle_index in seen:
                raise ValueError(f"duplicate triangle sample: {sample.triangle_index}")
            seen.add(sample.triangle_index)
            if sample.source_face_index < 0 or sample.area_m2 <= 0.0:
                raise ValueError("invalid source face mapping/area")

    def aggregate(self, field_name: str) -> dict[int, float]:
        """Area-weight one scalar field back to original CAD face indices."""
        if field_name not in {"pressure_pa", "cp"}:
            raise ValueError("field_name must be pressure_pa or cp")
        self.validate()
        weighted: dict[int, float] = {}
        areas: dict[int, float] = {}
        for sample in self.samples:
            value = getattr(sample, field_name)
            if value is None or not math.isfinite(float(value)):
                continue
            face = sample.source_face_index
            weighted[face] = weighted.get(face, 0.0) + float(value) * sample.area_m2
            areas[face] = areas.get(face, 0.0) + sample.area_m2
        return {face: weighted[face] / areas[face] for face in weighted if areas[face] > 0.0}


def linear_diverging_color(value: float, low: float, high: float) -> tuple[float, float, float, float]:
    """Small dependency-free blue/white/red scalar map for FreeCAD face display."""
    if not math.isfinite(value):
        return (0.5, 0.5, 0.5, 1.0)
    if high <= low:
        return (1.0, 1.0, 1.0, 1.0)
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    if t <= 0.5:
        r = g = 2.0 * t
        b = 1.0
    else:
        r = 1.0
        g = b = 2.0 * (1.0 - t)
    return (r, g, b, 1.0)


def apply_face_scalar_colors(obj, face_values: Mapping[int, float]) -> None:
    """Apply one color per CAD face through FreeCAD's DiffuseColor property.

    Face indices in ``face_values`` are zero-based contract indices; FreeCAD's
    ``Shape.Faces`` list is also addressed here by zero-based Python position.
    Missing faces retain their current/default shape color.
    """
    if obj is None or not hasattr(obj, "Shape") or not hasattr(obj, "ViewObject"):
        raise TypeError("FreeCAD shape object with ViewObject required")
    face_count = len(obj.Shape.Faces)
    if not face_values:
        return
    values = [float(v) for v in face_values.values() if math.isfinite(float(v))]
    if not values:
        return
    low, high = min(values), max(values)
    default = getattr(obj.ViewObject, "ShapeColor", (0.8, 0.8, 0.8))
    if len(default) == 3:
        base = (float(default[0]), float(default[1]), float(default[2]), 1.0)
    else:
        base = tuple(default)
    colors = [base] * face_count
    for index, value in face_values.items():
        if index < 0 or index >= face_count:
            raise IndexError(f"source face index {index} outside 0..{face_count-1}")
        colors[index] = linear_diverging_color(float(value), low, high)
    obj.ViewObject.DiffuseColor = colors
```

## `src/Mod/VibeCADAero/AeroGeometryReadiness.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry-readiness vocabulary separate from artifact provenance.

An exact B-rep can still be unsuitable for CFD.  Conversely a derived mesh can
be a valid solver input.  This module prevents `exact` from being misread as
`watertight`, `mesh-ready`, `manufacturable`, or `airworthy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class GeometryReadiness(IntEnum):
    UNKNOWN = 0
    BREP_ACCEPTED = 10
    SURFACE_CLOSED = 20
    SURFACE_WATERTIGHT = 30
    FLUID_DOMAIN_READY = 40
    MESH_READY = 50
    SOLVER_INPUT_FROZEN = 60


@dataclass(frozen=True)
class ReadinessEvidence:
    state: GeometryReadiness
    checks: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.failures

    def require(self, minimum: GeometryReadiness) -> None:
        if self.state < minimum or self.failures:
            detail = ", ".join(self.failures) or self.state.name.lower()
            raise ValueError(f"geometry is not {minimum.name.lower()}: {detail}")


def assessed(state: GeometryReadiness, *, checks: Iterable[str] = (), failures: Iterable[str] = ()) -> ReadinessEvidence:
    return ReadinessEvidence(state, tuple(checks), tuple(failures))
```

## `src/Mod/VibeCADAero/AeroHostEvidence.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared host-aligned evidence/artifact semantics for VibeCADAero reference code.

Pass 03 adopts VibeCAD's current evidence distinctions instead of inventing an
Aero-only vocabulary:
- preparing an analysis is not solving it;
- a finished numerical solve is not automatically a qualified model;
- exact/derived/presentation describe artifact provenance, not aerodynamic truth;
- airworthiness is never inferred from any of these states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ArtifactClass(str, Enum):
    EXACT = "exact"
    DERIVED = "derived"
    PRESENTATION = "presentation"


class EvidenceState(str, Enum):
    WAITING = "evidence_waiting"
    UNAVAILABLE = "capability_unavailable"
    FAILED = "failed"
    MODEL_UNQUALIFIED = "model_unqualified"
    MODEL_QUALIFIED = "model_qualified"
    MEASURED = "measured"


@dataclass(frozen=True)
class EvidenceStamp:
    evidence_state: EvidenceState
    claim_ceiling: str
    method: str
    solver_finished: bool = False
    model_qualified: bool = False
    not_airworthy: bool = True
    metadata: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evidence_state": self.evidence_state.value,
            "claim_ceiling": self.claim_ceiling,
            "method": self.method,
            "solver_finished": self.solver_finished,
            "model_qualified": self.model_qualified,
            "not_airworthy": self.not_airworthy,
        }
        if self.metadata:
            payload.update(dict(self.metadata))
        return payload


def prepared_case(method: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.WAITING, "not_solved", str(method))


def solver_finished(method: str, *, qualified: bool = False, metadata: Mapping[str, Any] | None = None) -> EvidenceStamp:
    """Stamp a completed solver run without equating completion with qualification."""
    if qualified:
        return EvidenceStamp(
            EvidenceState.MODEL_QUALIFIED,
            "not_airworthy",
            str(method),
            solver_finished=True,
            model_qualified=True,
            metadata=metadata,
        )
    return EvidenceStamp(
        EvidenceState.MODEL_UNQUALIFIED,
        "model_unqualified",
        str(method),
        solver_finished=True,
        model_qualified=False,
        metadata=metadata,
    )


def failed(method: str, error: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.FAILED, "not_solved", str(method), metadata={"error": str(error)})


def unavailable(method: str, reason: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.UNAVAILABLE, "not_solved", str(method), metadata={"reason": str(reason)})


def artifact_metadata(kind: str, *, source_sha256: str | None = None) -> dict[str, Any]:
    """Map common Aero artifacts onto the current host provenance vocabulary."""
    normalized = str(kind).strip().lower()
    exact = {"brep", "step", "iges", "native_brep"}
    derived = {"stl", "obj", "surface_mesh", "volume_mesh", "voxel_grid", "cfd_field", "solver_result", "vtk", "vtu"}
    presentation = {"screenshot", "render", "preview_image", "animation_frame"}
    if normalized in exact:
        cls = ArtifactClass.EXACT
    elif normalized in derived:
        cls = ArtifactClass.DERIVED
    elif normalized in presentation:
        cls = ArtifactClass.PRESENTATION
    else:
        raise ValueError(f"unknown Aero artifact kind: {kind}")
    payload = {
        "artifact_class": cls.value,
        "exact": cls is ArtifactClass.EXACT,
        "derived": cls is ArtifactClass.DERIVED,
        "presentation_only": cls is ArtifactClass.PRESENTATION,
    }
    if source_sha256:
        payload["source_sha256"] = str(source_sha256)
        if cls is ArtifactClass.DERIVED:
            payload["derived_from_exact"] = True
    return payload
```

## `src/Mod/VibeCADAero/AeroJobStore.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Persistent, solver-neutral lifecycle records for long-running Aero jobs.

Pass 03 Correction 01 retains this implementation only as **TRANSITIONAL / REFERENCE ONLY** lifecycle/domain-payload semantics.  It is NOT the target production job
authority.  The canonical target is one host-owned VibeCAD Analysis Runtime
extracted non-destructively from Native Background + detached FEM execution,
with FEM as the first parity-proven client and Aero as the second.

Native previews remain short-lived CAD mutation authorization. CFD/remote jobs
are evidence-producing work that may outlive Native sessions. This reference
performs no CAD mutation and no license/purpose enforcement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

JOB_STORE_SCHEMA = "vibecad.aero.jobs/1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LifecycleState(str, Enum):
    PREPARED = "prepared"
    QUEUED = "queued"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    RUNNING = "running"
    DOWNLOADING = "downloading"
    PARSING = "parsing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"


TERMINAL_STATES = frozenset(
    {LifecycleState.SUCCEEDED, LifecycleState.FAILED, LifecycleState.CANCELLED}
)

_ALLOWED: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.PREPARED: frozenset({LifecycleState.QUEUED, LifecycleState.UPLOADING, LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.QUEUED: frozenset({LifecycleState.UPLOADING, LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.UPLOADING: frozenset({LifecycleState.SUBMITTED, LifecycleState.CANCELLED, LifecycleState.FAILED}),
    LifecycleState.SUBMITTED: frozenset({LifecycleState.RUNNING, LifecycleState.DOWNLOADING, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.RUNNING: frozenset({LifecycleState.DOWNLOADING, LifecycleState.PARSING, LifecycleState.SUCCEEDED, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.DOWNLOADING: frozenset({LifecycleState.PARSING, LifecycleState.SUCCEEDED, LifecycleState.CANCELLED, LifecycleState.FAILED, LifecycleState.ORPHANED}),
    LifecycleState.PARSING: frozenset({LifecycleState.SUCCEEDED, LifecycleState.FAILED}),
    LifecycleState.ORPHANED: frozenset({LifecycleState.SUBMITTED, LifecycleState.RUNNING, LifecycleState.DOWNLOADING, LifecycleState.FAILED, LifecycleState.CANCELLED}),
    LifecycleState.SUCCEEDED: frozenset(),
    LifecycleState.FAILED: frozenset(),
    LifecycleState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class AeroJobRecord:
    job_id: str
    case_id: str
    solver_backend: str
    compute_provider: str
    document_uid: str
    captured_native_revision: int
    geometry_revision: str
    state: LifecycleState = LifecycleState.PREPARED
    provider_job_id: str | None = None
    workdir: str | None = None
    result_path: str | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    attempt: int = 1
    progress: float | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for label, value in (
            ("job_id", self.job_id),
            ("case_id", self.case_id),
            ("solver_backend", self.solver_backend),
            ("compute_provider", self.compute_provider),
            ("document_uid", self.document_uid),
            ("geometry_revision", self.geometry_revision),
        ):
            if not str(value).strip():
                raise ValueError(f"{label} is required")
        if type(self.captured_native_revision) is not int or self.captured_native_revision < 0:
            raise ValueError("captured_native_revision must be non-negative")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.progress is not None and not 0.0 <= float(self.progress) <= 1.0:
            raise ValueError("progress must be between 0 and 1")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def stale_against(self, *, native_revision: int, geometry_revision: str) -> bool:
        return (
            int(native_revision) != self.captured_native_revision
            or str(geometry_revision) != self.geometry_revision
        )


class AeroJobStore:
    """Atomic JSON persistence for bounded-size job metadata (not solver fields)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": JOB_STORE_SCHEMA, "jobs": []}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("schema") != JOB_STORE_SCHEMA:
            raise ValueError("Aero job store schema is invalid")
        if not isinstance(loaded.get("jobs"), list):
            raise ValueError("Aero job store jobs must be a list")
        return loaded

    @staticmethod
    def _from_dict(raw: Mapping[str, Any]) -> AeroJobRecord:
        data = dict(raw)
        data["state"] = LifecycleState(str(data.get("state") or LifecycleState.PREPARED.value))
        record = AeroJobRecord(**data)
        record.validate()
        return record

    def list(self) -> list[AeroJobRecord]:
        return [self._from_dict(raw) for raw in self._load_payload()["jobs"]]

    def get(self, job_id: str) -> AeroJobRecord | None:
        for record in self.list():
            if record.job_id == job_id:
                return record
        return None

    def save(self, records: Iterable[AeroJobRecord]) -> None:
        items = list(records)
        seen: set[str] = set()
        serialized: list[dict[str, Any]] = []
        for record in items:
            record.validate()
            if record.job_id in seen:
                raise ValueError(f"duplicate job_id: {record.job_id}")
            seen.add(record.job_id)
            raw = asdict(record)
            raw["state"] = record.state.value
            raw["metadata"] = dict(record.metadata)
            serialized.append(raw)
        payload = {"schema": JOB_STORE_SCHEMA, "jobs": serialized}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    def put(self, record: AeroJobRecord) -> AeroJobRecord:
        records = self.list()
        replaced = False
        for index, existing in enumerate(records):
            if existing.job_id == record.job_id:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self.save(records)
        return record

    def transition(
        self,
        job_id: str,
        state: LifecycleState | str,
        *,
        provider_job_id: str | None = None,
        progress: float | None = None,
        result_path: str | None = None,
        error: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AeroJobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        target = LifecycleState(state)
        if target != current.state and target not in _ALLOWED[current.state]:
            raise ValueError(f"illegal Aero job transition: {current.state.value} -> {target.value}")
        merged_metadata = dict(current.metadata)
        merged_metadata.update(dict(metadata or {}))
        data = asdict(current)
        data.update(
            state=target,
            provider_job_id=provider_job_id if provider_job_id is not None else current.provider_job_id,
            progress=progress if progress is not None else current.progress,
            result_path=result_path if result_path is not None else current.result_path,
            error=error,
            metadata=merged_metadata,
            updated_at=_utc_now(),
        )
        data["state"] = target
        updated = AeroJobRecord(**data)
        return self.put(updated)
```

## `src/Mod/VibeCADAero/AeroKaggle.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Kaggle compute-provider integration for VibeCADAero CFD jobs.

The current Kaggle CLI is the authority for notebook lifecycle.  This module
queries the installed CLI rather than hard-coding the old discussion's
"30 hours/week" assumption.  Current CLI releases provide ``kaggle quota`` for
weekly accelerator quota and ``kernels push/status/output`` for job lifecycle.

Kaggle is a *compute provider*, not a CFD solver.  A solver adapter must prepare
a Kaggle-runnable kernel directory and put its path in
``PreparedJob.metadata['kaggle_kernel_dir']``.  This prevents a local absolute
FluidX3D/OpenFOAM executable path from being accidentally treated as remotely
runnable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from AeroCFDContracts import ExecutionReceipt, JobState, PreparedJob


class KaggleError(RuntimeError):
    pass


class KaggleCLI:
    def __init__(self, executable: str = "kaggle") -> None:
        self.executable = executable

    def _run(self, args: list[str], *, cwd: str | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [self.executable, *args]
        try:
            return subprocess.run(
                cmd,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise KaggleError("Kaggle CLI is not installed or not on PATH.") from exc

    def version(self) -> str:
        proc = self._run(["--version"], timeout=15)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Unable to query Kaggle CLI version")
        return proc.stdout.strip()

    def quota_raw(self) -> str:
        """Return the live CLI quota report without assuming its numeric schema."""
        proc = self._run(["quota"], timeout=30)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Unable to query Kaggle accelerator quota")
        return proc.stdout.strip()

    def push(self, kernel_dir: Path, *, accelerator: str, timeout_s: int) -> str:
        proc = self._run(
            ["kernels", "push", "-p", str(kernel_dir), "--accelerator", accelerator, "-t", str(int(timeout_s))],
            timeout=max(60, timeout_s + 60),
        )
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or proc.stdout.strip() or "Kaggle kernel push failed")
        return (proc.stdout + "\n" + proc.stderr).strip()

    def status(self, kernel_ref: str) -> str:
        proc = self._run(["kernels", "status", kernel_ref], timeout=30)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Kaggle kernel status failed")
        return (proc.stdout + "\n" + proc.stderr).strip()

    def output(self, kernel_ref: str, output_dir: Path) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        proc = self._run(["kernels", "output", kernel_ref, "-p", str(output_dir), "-o"], timeout=600)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Kaggle kernel output download failed")
        return (proc.stdout + "\n" + proc.stderr).strip()


def write_kernel_metadata(
    kernel_dir: str | Path,
    *,
    owner: str,
    slug: str,
    code_file: str,
    title: str | None = None,
    accelerator: str = "NvidiaTeslaT4",
    enable_internet: bool = False,
) -> str:
    """Write current documented Kaggle kernel metadata for a private script."""

    directory = Path(kernel_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / code_file).is_file():
        raise KaggleError(f"Kaggle code_file does not exist: {directory / code_file}")
    payload = {
        "id": f"{owner}/{slug}",
        "title": title or slug.replace("-", " ").title(),
        "code_file": code_file,
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": bool(enable_internet),
        "machine_shape": accelerator,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }
    path = directory / "kernel-metadata.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _classify_status(text: str) -> JobState:
    value = text.lower()
    # Different CLI versions have varied wording. Match failure before success.
    if any(token in value for token in ("error", "failed", "failure", "cancelled", "canceled")):
        return JobState.FAILED
    if any(token in value for token in ("complete", "completed", "success", "succeeded")):
        return JobState.SUCCEEDED
    return JobState.RUNNING


class KaggleComputeProvider:
    name = "kaggle"

    def __init__(
        self,
        *,
        accelerator: str = "NvidiaTeslaT4",
        poll_interval_s: float = 15.0,
        timeout_s: int = 9 * 60 * 60,
        cli: KaggleCLI | None = None,
    ) -> None:
        self.accelerator = accelerator
        self.poll_interval_s = max(1.0, float(poll_interval_s))
        self.timeout_s = int(timeout_s)
        self.cli = cli or KaggleCLI()

    def quota(self) -> str:
        return self.cli.quota_raw()

    def execute(self, job: PreparedJob) -> ExecutionReceipt:
        kernel_dir_value = job.metadata.get("kaggle_kernel_dir")
        if not kernel_dir_value:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                error=(
                    "Solver job has no kaggle_kernel_dir. Remote execution must be explicitly "
                    "prepared by the solver adapter; local executables are not portable to Kaggle."
                ),
            )
        kernel_dir = Path(str(kernel_dir_value)).expanduser().resolve()
        metadata_path = kernel_dir / "kernel-metadata.json"
        if not metadata_path.is_file():
            return ExecutionReceipt(state=JobState.FAILED, returncode=None, error="kernel-metadata.json is missing")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        kernel_ref = str(metadata.get("id") or "")
        if "/" not in kernel_ref:
            return ExecutionReceipt(state=JobState.FAILED, returncode=None, error="Kaggle kernel metadata has no valid id")

        started = time.monotonic()
        provider_log = Path(job.workdir) / "kaggle_provider.log"
        try:
            # Capture the live quota report for provenance.  Failure to retrieve
            # quota should block automatic submission rather than invent a value.
            quota = self.cli.quota_raw()
            push_output = self.cli.push(kernel_dir, accelerator=self.accelerator, timeout_s=self.timeout_s)
            log_lines = ["# quota", quota, "# push", push_output]

            while True:
                elapsed = time.monotonic() - started
                if elapsed > self.timeout_s:
                    provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
                    return ExecutionReceipt(
                        state=JobState.FAILED,
                        returncode=None,
                        provider_job_id=kernel_ref,
                        wall_time_s=elapsed,
                        metadata={"quota_report": quota},
                        error="Kaggle kernel exceeded provider timeout",
                    )
                status_text = self.cli.status(kernel_ref)
                log_lines.extend(("# status", status_text))
                state = _classify_status(status_text)
                if state == JobState.SUCCEEDED:
                    break
                if state == JobState.FAILED:
                    provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
                    return ExecutionReceipt(
                        state=JobState.FAILED,
                        returncode=None,
                        provider_job_id=kernel_ref,
                        wall_time_s=elapsed,
                        metadata={"quota_report": quota, "status": status_text},
                        error="Kaggle kernel failed",
                    )
                time.sleep(self.poll_interval_s)

            output_dir = Path(job.workdir) / "kaggle_output"
            output_text = self.cli.output(kernel_ref, output_dir)
            log_lines.extend(("# output", output_text))
            expected_remote = output_dir / Path(job.expected_result).name
            expected_local = Path(job.workdir) / job.expected_result
            if expected_remote.is_file():
                expected_local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(expected_remote, expected_local)
            provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return ExecutionReceipt(
                state=JobState.SUCCEEDED,
                returncode=0,
                provider_job_id=kernel_ref,
                wall_time_s=time.monotonic() - started,
                stdout_path=str(provider_log),
                metadata={"quota_report": quota, "accelerator": self.accelerator, "output_dir": str(output_dir)},
            )
        except Exception as exc:
            try:
                provider_log.write_text(str(exc) + "\n", encoding="utf-8")
            except Exception:
                pass
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                provider_job_id=kernel_ref,
                wall_time_s=time.monotonic() - started,
                stdout_path=str(provider_log),
                error=str(exc),
            )
```

## `src/Mod/VibeCADAero/AeroLBM.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""FluidX3D solver adapter with a vendored-default deployment model.

Distribution documentation
--------------------------
The canonical VibeCAD distribution vendors a pinned FluidX3D source tree and a
VibeCAD force-extraction bridge alongside VibeCADAero. The FluidX3D license and
origin notice remain intact and govern that third-party code.

VibeCAD/VibeCADAero remains governed by VibeCAD's own project license. FluidX3D
remains third-party software governed by its own license; FluidX3D-specific
commercial, military, AI-source-training, attribution, publication/source and
related requirements apply to FluidX3D, not to Aero globally. VibeCAD does not
infer, police, or enforce purpose. Aero presents one first-entry acknowledgement
that states this component-specific boundary and persists it locally.

An explicitly configured external FluidX3D bridge always overrides the vendored
bridge. This is a normal configuration capability, not a purpose detector or
policy profile.

The adapter deliberately avoids the unverified ``fluidx3d.Config`` Python API
used in earlier discussion drafts. This reference uses a stable process contract around
APIs verified in ProjectPhysX/FluidX3D source: LBM::run, LBM::object_force,
LBM::object_torque, LBM::voxelize_stl and Units conversion.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    Artifact,
    Coefficients,
    CFDResult,
    Diagnostics,
    ExecutionReceipt,
    ForceMoment,
    JobState,
    PreparedJob,
    Vector3,
    coefficients_from_force,
    write_json,
)


class FluidX3DError(RuntimeError):
    pass


class FluidX3DBackend:
    name = "fluidx3d"

    def __init__(
        self,
        executable: str | None = None,
        *,
        vendor_root: str | Path | None = None,
    ) -> None:
        self.executable = executable or os.environ.get("VIBECAD_FLUIDX3D_BRIDGE", "")
        self.vendor_root = Path(vendor_root).expanduser().resolve() if vendor_root else (
            Path(__file__).resolve().parent / "vendor" / "FluidX3D"
        )

    def _vendored_bridge_candidates(self) -> tuple[Path, ...]:
        names = (
            "VibeCADFluidX3D.exe",
            "VibeCADFluidX3D",
            "FluidX3D.exe",
            "FluidX3D",
        )
        return tuple(self.vendor_root / "bin" / name for name in names)


    def _resolve_executable(self, case: AeroCase) -> Path:
        configured = str(case.solver.settings.get("executable") or self.executable or "").strip()
        if configured:
            path = Path(configured).expanduser().resolve()
            if not path.is_file():
                raise FluidX3DError(f"FluidX3D bridge executable does not exist: {path}")
            return path

        for candidate in self._vendored_bridge_candidates():
            if candidate.is_file():
                return candidate.resolve()
        raise FluidX3DError(
            "VibeCAD expects its vendored FluidX3D bridge under "
            f"{self.vendor_root / 'bin'}, or an explicit external bridge via "
            "solver.settings.executable / VIBECAD_FLUIDX3D_BRIDGE."
        )

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob:
        case.validate()
        executable = self._resolve_executable(case)
        geometry = Path(case.geometry.artifact.path).expanduser().resolve()
        if not geometry.is_file():
            raise FileNotFoundError(str(geometry))
        if case.geometry.artifact.media_type != "model/stl":
            raise FluidX3DError("the current FluidX3D reference adapter requires STL geometry")

        # The bridge consumes SI quantities and owns all lattice-unit conversion.
        result_path = workdir / "result.json"
        job_payload: dict[str, Any] = {
            "schema_version": "vibecad.fluidx3d.bridge/1",
            "case_id": case.case_id,
            "geometry": {
                "stl_path": str(geometry),
                "sha256": case.geometry.artifact.sha256,
                "source_units": case.geometry.source_units,
                "geometry_revision": case.geometry.geometry_revision,
            },
            "flow": {
                "velocity_body_mps": list(case.flow.freestream_body_mps.as_tuple()),
                "density_kg_m3": case.flow.density_kg_m3,
                "dynamic_viscosity_pa_s": case.flow.dynamic_viscosity_pa_s,
            },
            "reference": {
                "area_m2": case.references.area_m2,
                "length_m": case.references.length_m,
                "span_m": case.references.span_m,
                "moment_reference_body_m": list(case.references.moment_reference_body_m.as_tuple()),
            },
            "solver": dict(case.solver.settings),
            "result_path": str(result_path),
        }
        job_json = workdir / "fluidx3d_job.json"
        write_json(job_json, job_payload)

        device_args: tuple[str, ...] = ()
        device_id = case.compute.settings.get("device_id")
        if device_id is not None:
            # FluidX3D's own main argument handling accepts device identifiers.
            device_args = (str(int(device_id)),)
        settings = dict(case.solver.settings)
        geometry_physical_size_m = settings.get("geometry_physical_size_m")
        if geometry_physical_size_m is None or float(geometry_physical_size_m) <= 0.0:
            raise FluidX3DError(
                "FluidX3D requires solver.settings.geometry_physical_size_m: the physical "
                "size corresponding to the STL maximum dimension. This prevents silent scale errors."
            )
        environment = {
            "VIBECAD_FX3D_JOB": str(job_json),
            "VIBECAD_FX3D_STL": str(geometry),
            "VIBECAD_FX3D_RESULT": str(result_path),
            "VIBECAD_FX3D_CASE_ID": case.case_id,
            "VIBECAD_FX3D_UX": str(case.flow.freestream_body_mps.x),
            "VIBECAD_FX3D_UY": str(case.flow.freestream_body_mps.y),
            "VIBECAD_FX3D_UZ": str(case.flow.freestream_body_mps.z),
            "VIBECAD_FX3D_RHO": str(case.flow.density_kg_m3),
            "VIBECAD_FX3D_MU": str(case.flow.dynamic_viscosity_pa_s),
            "VIBECAD_FX3D_GEOMETRY_SIZE_M": str(float(geometry_physical_size_m)),
            "VIBECAD_FX3D_GEOMETRY_SIZE_LU": str(float(settings.get("geometry_size_lu", 128.0))),
            "VIBECAD_FX3D_NX": str(int(settings.get("nx", 512))),
            "VIBECAD_FX3D_NY": str(int(settings.get("ny", 256))),
            "VIBECAD_FX3D_NZ": str(int(settings.get("nz", 256))),
            "VIBECAD_FX3D_LBM_SPEED": str(float(settings.get("lbm_speed", 0.08))),
            "VIBECAD_FX3D_TRANSIENT_STEPS": str(int(settings.get("transient_steps", 2000))),
            "VIBECAD_FX3D_SAMPLE_EVERY": str(int(settings.get("sample_every", 100))),
            "VIBECAD_FX3D_SAMPLE_COUNT": str(int(settings.get("sample_count", 50))),
        }
        return PreparedJob(
            case=case,
            workdir=str(workdir),
            command=(str(executable), *device_args),
            environment=environment,
            expected_result=str(result_path.name),
            metadata={"bridge_job": str(job_json)},
        )

    def parse(self, case: AeroCase, job: PreparedJob, receipt: ExecutionReceipt) -> CFDResult:
        if receipt.state != JobState.SUCCEEDED:
            return CFDResult(
                case_id=case.case_id,
                solver_backend=self.name,
                solver_version=case.solver.backend_version,
                compute_provider=case.compute.provider,
                state=JobState.FAILED,
                evidence_state="failed",
                method="cfd:fluidx3d",
                error=receipt.error or "FluidX3D execution failed",
                metadata={"returncode": receipt.returncode},
            )
        result_path = Path(job.workdir) / job.expected_result
        if not result_path.is_file():
            raise FluidX3DError(f"FluidX3D bridge produced no result: {result_path}")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        if data.get("schema_version") != "vibecad.fluidx3d.bridge/1":
            raise FluidX3DError("FluidX3D bridge result schema mismatch")
        if str(data.get("case_id")) != case.case_id:
            raise FluidX3DError("FluidX3D bridge returned a different case_id")
        force = ForceMoment(
            force_body_n=Vector3.from_any(data.get("force_body_n") or (0.0, 0.0, 0.0)),
            moment_body_nm=Vector3.from_any(data.get("moment_body_nm") or (0.0, 0.0, 0.0)),
            sample_count=int(data["sample_count"]) if data.get("sample_count") is not None else None,
            averaging_start_s=float(data["averaging_start_s"]) if data.get("averaging_start_s") is not None else None,
            averaging_end_s=float(data["averaging_end_s"]) if data.get("averaging_end_s") is not None else None,
        )
        coeffs = coefficients_from_force(case, force)
        # The pass-01 bridge reports torque about the voxelized object's center of mass.
        # Until an explicit CAD-body origin transform is supplied, do not mislabel that
        # torque as a coefficient about AeroConfig.xyz_ref. Force coefficients remain valid.
        if data.get("moment_reference") != "requested_body_reference":
            coeffs = Coefficients(cd=coeffs.cd, cl=coeffs.cl, cs=coeffs.cs)
        artifact = Artifact.from_file(result_path, media_type="application/json", role="solver_result")
        warnings = tuple(str(v) for v in data.get("warnings") or [])
        diagnostics = Diagnostics(
            converged=data.get("converged"),
            iterations=int(data["iterations"]) if data.get("iterations") is not None else None,
            simulated_time_s=float(data["simulated_time_s"]) if data.get("simulated_time_s") is not None else None,
            wall_time_s=receipt.wall_time_s,
            warnings=warnings,
            notes=(
                "FluidX3D result is CFD evidence but remains not-airworthy until independently validated.",
            ),
        )
        return CFDResult(
            case_id=case.case_id,
            solver_backend=self.name,
            solver_version=str(data.get("fluidx3d_commit") or case.solver.backend_version or "") or None,
            compute_provider=case.compute.provider,
            state=JobState.SUCCEEDED,
            force_moment=force,
            coefficients=coeffs,
            diagnostics=diagnostics,
            artifacts=(artifact,),
            evidence_state="model_unqualified",
            claim_ceiling="not_airworthy",
            method="cfd:fluidx3d:lbm",
            metadata={
                "geometry_sha256": case.geometry.artifact.sha256,
                "bridge_version": data.get("bridge_version"),
                "lattice": data.get("lattice"),
                "fluidx3d_runtime_path": str(Path(job.command[0]).resolve()),
                "fluidx3d_vendor_root": str(self.vendor_root),
            },
        )
```

## `src/Mod/VibeCADAero/AeroLocalCompute.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Local subprocess compute provider for CFD jobs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

from AeroCFDContracts import ExecutionReceipt, JobState, PreparedJob


class LocalComputeProvider:
    name = "local"

    def __init__(self, *, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s

    def execute(self, job: PreparedJob) -> ExecutionReceipt:
        workdir = Path(job.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        stdout_path = workdir / "stdout.log"
        stderr_path = workdir / "stderr.log"
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in job.environment.items()})
        started = time.monotonic()
        try:
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                proc = subprocess.run(
                    list(job.command),
                    cwd=str(workdir),
                    env=env,
                    stdout=out,
                    stderr=err,
                    timeout=self.timeout_s,
                    check=False,
                )
            state = JobState.SUCCEEDED if proc.returncode == 0 else JobState.FAILED
            error = None if proc.returncode == 0 else f"process exited with {proc.returncode}"
            return ExecutionReceipt(
                state=state,
                returncode=proc.returncode,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error=error,
            )
        except subprocess.TimeoutExpired:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error="local CFD job timed out",
            )
        except Exception as exc:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error=str(exc),
            )
```

## `src/Mod/VibeCADAero/AeroMesh.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry/mesh preparation for VibeCADAero CFD backends.

FreeCAD and Gmsh imports are deliberately lazy.  The module can therefore be
imported by unit tests without a FreeCAD installation, while the live runtime
still gets native Part/Mesh/MeshPart behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Sequence

from AeroCFDContracts import Artifact, GeometryArtifact


class MeshError(RuntimeError):
    pass


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _active_selection() -> list[Any]:
    import FreeCADGui as Gui  # type: ignore

    selected = list(Gui.Selection.getSelection())
    if not selected:
        raise MeshError("Select at least one Part/Body/mesh object for CFD geometry export.")
    return selected


def _shape_from_objects(objects: Sequence[Any]) -> Any:
    import Part  # type: ignore

    shapes = [obj.Shape for obj in objects if hasattr(obj, "Shape") and not obj.Shape.isNull()]
    if not shapes:
        raise MeshError("Selection contains no non-null Part shapes.")
    return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)


def export_shape_to_stl(
    shape: Any,
    path: str | Path,
    *,
    linear_deflection_mm: float = 0.08,
    angular_deflection_rad: float = 0.15,
) -> str:
    """Tessellate a Part shape and write an STL through FreeCAD MeshPart.

    No assumption is made here about ASCII versus binary STL; the artifact is
    verified by content hash.  A later packaging pass can pin the exact writer
    mode after it is tested against VibeCAD's FreeCAD version.
    """

    import MeshPart  # type: ignore

    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=float(linear_deflection_mm),
        AngularDeflection=float(angular_deflection_rad),
        Relative=False,
    )
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("FreeCAD tessellation produced an empty mesh.")
    mesh.write(str(p))
    if not p.is_file() or p.stat().st_size <= 84:
        raise MeshError("STL export did not produce a valid non-empty file.")
    return str(p)


def geometry_revision(document: Any, cfg: dict[str, Any] | None = None) -> str:
    """Reuse upstream AeroPreview revision semantics when available."""

    try:
        import AeroPreview  # type: ignore
        import AeroConfig  # type: ignore

        resolved = cfg if cfg is not None else AeroConfig.resolve_geometry(document)
        return str(AeroPreview.geometry_revision(document, resolved))
    except Exception:
        # Deterministic fallback for hosts that lack AeroPreview.  This is not as
        # rich as the upstream document revision and is therefore clearly marked.
        names = sorted(str(getattr(o, "Name", "")) for o in getattr(document, "Objects", []) or [])
        raw = json.dumps(names, separators=(",", ":")).encode("utf-8")
        return "fallback:" + hashlib.sha256(raw).hexdigest()


def prepare_geometry_from_selection(
    output_path: str | Path,
    *,
    document: Any | None = None,
    linear_deflection_mm: float = 0.08,
    angular_deflection_rad: float = 0.15,
) -> GeometryArtifact:
    import FreeCAD as App  # type: ignore

    doc = document or App.ActiveDocument
    if doc is None:
        raise MeshError("No active FreeCAD document.")
    selected = _active_selection()
    shape = _shape_from_objects(selected)
    stl = export_shape_to_stl(
        shape,
        output_path,
        linear_deflection_mm=linear_deflection_mm,
        angular_deflection_rad=angular_deflection_rad,
    )
    artifact = Artifact.from_file(stl, media_type="model/stl", role="solver_geometry")
    return GeometryArtifact(
        artifact=artifact,
        geometry_revision=geometry_revision(doc),
        source_object_names=tuple(str(getattr(o, "Name", "")) for o in selected),
        source_units="mm",
        solver_units="m",
        triangulation_linear_deflection_mm=float(linear_deflection_mm),
        triangulation_angular_deflection_rad=float(angular_deflection_rad),
    )


def import_stl(path: str | Path, *, name: str = "ImportedSTL", document: Any | None = None) -> Any:
    import FreeCAD as App  # type: ignore
    import Mesh  # type: ignore

    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    mesh = Mesh.Mesh(str(p))
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("STL contains no facets.")
    doc = document or App.ActiveDocument or App.newDocument("AeroMesh")
    obj = doc.addObject("Mesh::Feature", name)
    obj.Mesh = mesh
    obj.Label = name
    doc.recompute()
    return obj


def export_mesh_to_stl(mesh_or_object: Any, path: str | Path) -> str:
    """Write a raw ``Mesh.Mesh`` or ``Mesh::Feature`` to STL."""

    mesh = getattr(mesh_or_object, "Mesh", mesh_or_object)
    if int(getattr(mesh, "CountFacets", 0)) <= 0:
        raise MeshError("Mesh is empty.")
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh.write(str(p))
    return str(p)


def subdivide_tri6(points: Sequence[Any]) -> list[list[Any]]:
    """Linearize a Gmsh 6-node quadratic triangle into four TRI3 facets."""

    if len(points) != 6:
        raise MeshError("TRI6 requires exactly six nodes.")
    n0, n1, n2, n3, n4, n5 = points
    return [
        [n0, n3, n5],
        [n3, n1, n4],
        [n5, n4, n2],
        [n3, n4, n5],
    ]


def _chunks(values: Iterable[int], size: int) -> Iterable[tuple[int, ...]]:
    bucket: list[int] = []
    for value in values:
        bucket.append(int(value))
        if len(bucket) == size:
            yield tuple(bucket)
            bucket = []
    if bucket:
        raise MeshError(f"element connectivity length is not divisible by {size}")


def gmsh_current_surface_to_freecad_mesh() -> Any:
    """Convert the current Gmsh surface mesh into ``Mesh.Mesh``.

    Canonical supported element types in this reference:
    * type 2  TRI3: direct
    * type 3  QUAD4: split into two triangles
    * type 9  TRI6: topologically subdivided into four triangles

    Other higher-order elements are rejected rather than silently reducing them
    to corner nodes.  That strict behavior corrects an unsafe fallback from the
    prior discussion.
    """

    import FreeCAD as App  # type: ignore
    import Mesh  # type: ignore
    import gmsh  # type: ignore

    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    coords = list(coordinates)
    if len(coords) != 3 * len(node_tags):
        raise MeshError("Gmsh node coordinate payload is malformed.")
    tag_to_vec = {
        int(tag): App.Vector(float(coords[3 * i]), float(coords[3 * i + 1]), float(coords[3 * i + 2]))
        for i, tag in enumerate(node_tags)
    }

    element_types, _element_tags, element_nodes = gmsh.model.mesh.getElements(dim=2)
    triangles: list[list[Any]] = []
    unsupported: set[int] = set()
    for etype, flat in zip(element_types, element_nodes):
        et = int(etype)
        if et == 2:  # TRI3
            for tags in _chunks(flat, 3):
                triangles.append([tag_to_vec[tag] for tag in tags])
        elif et == 3:  # QUAD4
            for tags in _chunks(flat, 4):
                p = [tag_to_vec[tag] for tag in tags]
                triangles.extend(([p[0], p[1], p[2]], [p[0], p[2], p[3]]))
        elif et == 9:  # TRI6
            for tags in _chunks(flat, 6):
                triangles.extend(subdivide_tri6([tag_to_vec[tag] for tag in tags]))
        else:
            unsupported.add(et)

    if unsupported:
        raise MeshError(
            "Unsupported Gmsh surface element type(s): " + ", ".join(str(v) for v in sorted(unsupported))
        )
    if not triangles:
        raise MeshError("Gmsh conversion produced no surface triangles.")
    return Mesh.Mesh(triangles)


def shape_to_gmsh_surface_mesh(
    shape: Any,
    *,
    max_size_mm: float = 5.0,
    min_size_mm: float = 0.5,
    element_order: int = 1,
) -> Any:
    """Part.Shape -> Gmsh OCC -> FreeCAD surface mesh."""

    import gmsh  # type: ignore

    if int(element_order) not in (1, 2):
        raise MeshError("this reference supports Gmsh element order 1 or 2")
    brep = tempfile.NamedTemporaryFile(suffix=".brep", delete=False)
    brep.close()
    shape.exportBrep(brep.name)
    initialized_here = False
    try:
        if not gmsh.isInitialized():
            gmsh.initialize()
            initialized_here = True
        gmsh.clear()
        gmsh.model.add("vibecad_cfd")
        gmsh.model.occ.importShapes(brep.name)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", float(max_size_mm))
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", float(min_size_mm))
        gmsh.option.setNumber("Mesh.ElementOrder", int(element_order))
        gmsh.model.mesh.generate(2)
        return gmsh_current_surface_to_freecad_mesh()
    finally:
        try:
            os.unlink(brep.name)
        except OSError:
            pass
        if initialized_here:
            gmsh.finalize()


def mesh_to_part_shape(mesh_or_object: Any, *, tolerance_mm: float = 0.1) -> Any:
    """Convert mesh topology to a Part shape; no solid claim is made."""

    import Part  # type: ignore

    mesh = getattr(mesh_or_object, "Mesh", mesh_or_object)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, float(tolerance_mm))
    if shape.isNull():
        raise MeshError("FreeCAD makeShapeFromMesh produced a null shape.")
    return shape


def mesh_to_solid(mesh_or_object: Any, *, tolerance_mm: float = 0.1) -> Any:
    """Attempt to create a Part solid from a closed mesh-derived shell.

    This is intentionally strict.  Open/non-manifold meshes fail instead of
    being mislabeled as solids.
    """

    import Part  # type: ignore

    shape = mesh_to_part_shape(mesh_or_object, tolerance_mm=tolerance_mm)
    shells = list(getattr(shape, "Shells", []) or [])
    if not shells:
        # Some FreeCAD builds return a single shell-shaped object directly.
        if str(getattr(shape, "ShapeType", "")) == "Shell":
            shells = [shape]
        else:
            raise MeshError("Mesh-derived shape contains no shell.")
    if len(shells) != 1:
        raise MeshError(f"Expected one closed shell, found {len(shells)}.")
    shell = shells[0]
    if not bool(shell.isClosed()):
        raise MeshError("Mesh-derived shell is not closed/manifold enough to form a solid.")
    solid = Part.makeSolid(shell)
    if solid.isNull() or not bool(solid.isValid()):
        raise MeshError("Part.makeSolid produced an invalid solid.")
    return solid
```

## `src/Mod/VibeCADAero/AeroNativeBridge.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-independent bridge contract between Aero evidence and Native authority.

Pass 03 retains the Pass-02 conclusion that VibeCADAero against VibeCAD's now-mature host-owned Native
revision/preview store.  This module does *not* duplicate that store.  It only
captures the host structural revision together with Aero's geometry fingerprint
and decides whether a long-running result is still current enough to attach as
active evidence.

Long-running solver jobs are not CAD mutation previews.  They may run for hours
and must be preserved even if CAD changes.  A stale result is historical evidence,
not failed evidence; it simply may not silently replace the current Aero result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AttachmentState(str, Enum):
    CURRENT = "current"
    STALE_NATIVE_REVISION = "stale_native_revision"
    STALE_GEOMETRY = "stale_geometry"
    STALE_BOTH = "stale_native_and_geometry"


@dataclass(frozen=True)
class AuthoritySnapshot:
    document_uid: str
    native_revision: int
    geometry_revision: str

    def __post_init__(self) -> None:
        if not str(self.document_uid).strip():
            raise ValueError("document_uid is required")
        if type(self.native_revision) is not int or self.native_revision < 0:
            raise ValueError("native_revision must be a non-negative integer")
        if not str(self.geometry_revision).strip():
            raise ValueError("geometry_revision is required")


@dataclass(frozen=True)
class AttachmentDecision:
    state: AttachmentState
    current: bool
    captured_native_revision: int
    current_native_revision: int
    captured_geometry_revision: str
    current_geometry_revision: str

    @property
    def preserve_as_history(self) -> bool:
        return not self.current


def capture_authority(
    state_store: Any,
    *,
    document_uid: str,
    geometry_revision: str,
) -> AuthoritySnapshot:
    """Read VibeCAD's host revision without taking ownership of its state store."""
    getter = getattr(state_store, "current_revision", None)
    if not callable(getter):
        raise TypeError("state_store must expose current_revision(document_uid)")
    return AuthoritySnapshot(
        document_uid=str(document_uid),
        native_revision=int(getter(document_uid)),
        geometry_revision=str(geometry_revision),
    )


def decide_attachment(
    snapshot: AuthoritySnapshot,
    *,
    current_native_revision: int,
    current_geometry_revision: str,
) -> AttachmentDecision:
    native_changed = int(current_native_revision) != snapshot.native_revision
    geometry_changed = str(current_geometry_revision) != snapshot.geometry_revision
    if native_changed and geometry_changed:
        state = AttachmentState.STALE_BOTH
    elif native_changed:
        state = AttachmentState.STALE_NATIVE_REVISION
    elif geometry_changed:
        state = AttachmentState.STALE_GEOMETRY
    else:
        state = AttachmentState.CURRENT
    return AttachmentDecision(
        state=state,
        current=state is AttachmentState.CURRENT,
        captured_native_revision=snapshot.native_revision,
        current_native_revision=int(current_native_revision),
        captured_geometry_revision=snapshot.geometry_revision,
        current_geometry_revision=str(current_geometry_revision),
    )
```

## `src/Mod/VibeCADAero/AeroNativeRepairBridge.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Transition contract for moving Aero CAD repairs onto host Native authority.

Current upstream already owns generic preview/apply/reject, structural revisions,
and preservation of user-explicit intent.  Aero should not recreate those
mechanisms.  Until `aero.*` mutations are registered directly on the host Native
surface, this bridge shows the minimum data that `/v1/aero` must thread through
its existing Aero repair preview: host revision + geometry fingerprint + the
user-explicit intent fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def user_explicit_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    selected = []
    for row in rows:
        if str(row.get("kind") or "") != "user_explicit":
            continue
        selected.append({str(k): row[k] for k in sorted(row) if k not in {"updated_at", "last_used_at"}})
    return _stable_hash(selected)


@dataclass(frozen=True)
class RepairAuthoritySnapshot:
    document_uid: str
    native_revision: int
    geometry_revision: str
    user_explicit_sha256: str


def capture(*, document_uid: str, native_revision: int, geometry_revision: str, intent_rows: Iterable[Mapping[str, Any]] = ()) -> RepairAuthoritySnapshot:
    if type(native_revision) is not int or native_revision < 0:
        raise ValueError("native_revision must be a non-negative integer")
    return RepairAuthoritySnapshot(
        str(document_uid), int(native_revision), str(geometry_revision), user_explicit_fingerprint(intent_rows)
    )


def validate_apply(snapshot: RepairAuthoritySnapshot, *, current_native_revision: int, current_geometry_revision: str, current_intent_rows: Iterable[Mapping[str, Any]] = ()) -> None:
    if int(current_native_revision) != snapshot.native_revision:
        raise ValueError("native_revision_stale")
    if str(current_geometry_revision) != snapshot.geometry_revision:
        raise ValueError("geometry_revision_stale")
    if user_explicit_fingerprint(current_intent_rows) != snapshot.user_explicit_sha256:
        raise ValueError("user_explicit_intent_changed")
```

## `src/Mod/VibeCADAero/AeroOpenFOAM.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""OpenFOAM/CfdOF integration boundary for the canonical overlay.

CfdOF is treated as an optional FreeCAD-native case authoring integration;
OpenFOAM is the solver.  The generic CFD orchestration can then execute a case
locally or through another compute provider without duplicating physics setup.

This reference deliberately does not invent automatic external-domain face selection.
A preconfigured CfdOF analysis can already be written through the *verified*
current CfdOF API.  Automatic far-field domain generation is specified in the
builder plan and must be validated on actual FreeCAD topology before it becomes
canonical behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    Artifact,
    CFDResult,
    Coefficients,
    Diagnostics,
    ExecutionReceipt,
    JobState,
    PreparedJob,
)


class OpenFOAMError(RuntimeError):
    pass


def cfdof_available() -> bool:
    try:
        from CfdOF import CfdAnalysis, CfdTools  # noqa: F401
        from CfdOF.Solve.CfdCaseWriterFoam import CfdCaseWriterFoam  # noqa: F401
        return True
    except Exception:
        return False


def create_cfdof_analysis_shell(document: Any, *, name: str = "CfdAnalysis") -> Any:
    """Create the minimum CfdOF analysis object graph using current namespaces.

    This mirrors the API used by CfdOF's own UAV/demo macros.  Geometry, mesh and
    boundary conditions are intentionally not guessed here.
    """

    from CfdOF import CfdAnalysis, CfdTools
    from CfdOF.Solve import CfdFluidMaterial, CfdInitialiseFlowField, CfdPhysicsSelection, CfdSolverFoam

    analysis = CfdAnalysis.makeCfdAnalysis(name)
    CfdTools.setActiveAnalysis(analysis)
    analysis.addObject(CfdPhysicsSelection.makeCfdPhysicsSelection())
    analysis.addObject(CfdFluidMaterial.makeCfdFluidMaterial("FluidProperties"))
    analysis.addObject(CfdInitialiseFlowField.makeCfdInitialFlowField())
    analysis.addObject(CfdSolverFoam.makeCfdSolverFoam())
    return analysis


def attach_cfdof_mesh(analysis: Any, part_object: Any, *, name: str = "VibeCADAeroCFDMesh", utility: str = "cfMesh") -> Any:
    """Attach a CfdOF 3-D mesh object to an analysis.

    ``part_object`` must be the *fluid-region/domain object*, not the aircraft
    solid itself.  This explicit requirement prevents the old discussion from
    conflating STL/LBM surface meshing with finite-volume OpenFOAM volume meshes.
    """

    from CfdOF import CfdTools
    from CfdOF.Mesh import CfdMesh

    CfdMesh.makeCfdMesh(name)
    import FreeCAD  # type: ignore

    mesh = FreeCAD.ActiveDocument.ActiveObject
    mesh.Part = part_object
    mesh.MeshUtility = utility
    mesh.ElementDimension = "3D"
    CfdTools.getActiveAnalysis().addObject(mesh)
    return mesh


def write_cfdof_case(analysis: Any) -> str:
    """Write a fully configured CfdOF analysis using its current case writer."""

    from CfdOF.Solve.CfdCaseWriterFoam import CfdCaseWriterFoam

    writer = CfdCaseWriterFoam(analysis)
    writer.writeCase()
    if not writer.case_folder:
        raise OpenFOAMError("CfdOF case writer did not expose a case folder")
    return str(Path(writer.case_folder).resolve())


class OpenFOAMBackend:
    """Run an already-authored OpenFOAM case and consume VibeCAD result JSON.

    Solver settings:
      case_dir: directory produced by CfdOF or another verified case author
      command: list/string command to execute (default: ./Allrun)
      collector_output: JSON path relative to case (default: vibecad_result.json)

    The collector JSON contract decouples OpenFOAM-version-specific function
    object layouts from VibeCAD's stable result schema.
    """

    name = "openfoam"

    def prepare(self, case: AeroCase, workdir: Path) -> PreparedJob:
        case.validate()
        source_dir = case.solver.settings.get("case_dir")
        if not source_dir:
            raise OpenFOAMError(
                "OpenFOAM solver.settings.case_dir is required. Use write_cfdof_case() "
                "after configuring a CfdOF analysis, or provide another verified case."
            )
        src = Path(str(source_dir)).expanduser().resolve()
        if not src.is_dir():
            raise OpenFOAMError(f"OpenFOAM case directory not found: {src}")
        case_dir = workdir / "openfoam_case"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        shutil.copytree(src, case_dir)

        raw_command = case.solver.settings.get("command", ("./Allrun",))
        if isinstance(raw_command, str):
            command = (raw_command,)
        else:
            command = tuple(str(v) for v in raw_command)
        expected = str(case.solver.settings.get("collector_output", "vibecad_result.json"))
        return PreparedJob(
            case=case,
            workdir=str(case_dir),
            command=command,
            expected_result=expected,
            metadata={"source_case": str(src)},
        )

    def parse(self, case: AeroCase, job: PreparedJob, receipt: ExecutionReceipt) -> CFDResult:
        if receipt.state != JobState.SUCCEEDED:
            return CFDResult(
                case_id=case.case_id,
                solver_backend=self.name,
                solver_version=case.solver.backend_version,
                compute_provider=case.compute.provider,
                state=JobState.FAILED,
                evidence_state="failed",
                method="cfd:openfoam",
                error=receipt.error or "OpenFOAM execution failed",
            )
        path = Path(job.workdir) / job.expected_result
        if not path.is_file():
            raise OpenFOAMError(
                f"OpenFOAM run completed but collector result is missing: {path}. "
                "Configure forceCoeffs and the VibeCAD collector before execution."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != "vibecad.openfoam.collector/1":
            raise OpenFOAMError("OpenFOAM collector schema mismatch")
        c = data.get("coefficients") or {}
        coefficients = Coefficients(
            cd=float(c["cd"]),
            cl=float(c["cl"]),
            cs=float(c.get("cs", 0.0)),
            cm_pitch=float(c["cm_pitch"]) if c.get("cm_pitch") is not None else None,
            cl_roll=float(c["cl_roll"]) if c.get("cl_roll") is not None else None,
            cn_yaw=float(c["cn_yaw"]) if c.get("cn_yaw") is not None else None,
        )
        artifact = Artifact.from_file(path, media_type="application/json", role="solver_result")
        return CFDResult(
            case_id=case.case_id,
            solver_backend=self.name,
            solver_version=str(data.get("openfoam_version") or case.solver.backend_version or "") or None,
            compute_provider=case.compute.provider,
            state=JobState.SUCCEEDED,
            coefficients=coefficients,
            diagnostics=Diagnostics(
                converged=data.get("converged"),
                residuals={str(k): float(v) for k, v in (data.get("residuals") or {}).items()},
                iterations=int(data["iterations"]) if data.get("iterations") is not None else None,
                wall_time_s=receipt.wall_time_s,
                warnings=tuple(str(v) for v in data.get("warnings") or []),
            ),
            artifacts=(artifact,),
            evidence_state="model_unqualified",
            claim_ceiling="not_airworthy",
            method="cfd:openfoam",
            metadata={
                "case_source": job.metadata.get("source_case"),
                "force_coefficients": True,
            },
        )
```

## `src/Mod/VibeCADAero/AeroQualification.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned solver qualification evidence contracts for VibeCADAero.

A result being numerically successful does not automatically qualify a solver
for a regime. Qualification is explicit evidence tied to solver build, model,
benchmark source, settings and a bounded applicability envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class QualificationEnvelope:
    reynolds_min: float | None = None
    reynolds_max: float | None = None
    mach_min: float | None = None
    mach_max: float | None = None
    alpha_min_deg: float | None = None
    alpha_max_deg: float | None = None
    geometry_classes: tuple[str, ...] = ()

    def contains(self, *, reynolds: float | None, mach: float | None, alpha_deg: float | None, geometry_class: str | None = None) -> bool:
        for value, low, high in (
            (reynolds, self.reynolds_min, self.reynolds_max),
            (mach, self.mach_min, self.mach_max),
            (alpha_deg, self.alpha_min_deg, self.alpha_max_deg),
        ):
            if value is None:
                if low is not None or high is not None:
                    return False
                continue
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
        if self.geometry_classes and str(geometry_class or "") not in self.geometry_classes:
            return False
        return True


@dataclass(frozen=True)
class BenchmarkResult:
    observable: str
    reference_value: float
    computed_value: float
    tolerance_abs: float | None = None
    tolerance_rel: float | None = None

    @property
    def passed(self) -> bool:
        error = abs(self.computed_value - self.reference_value)
        checks: list[bool] = []
        if self.tolerance_abs is not None:
            checks.append(error <= self.tolerance_abs)
        if self.tolerance_rel is not None:
            denom = max(abs(self.reference_value), 1e-15)
            checks.append(error / denom <= self.tolerance_rel)
        return bool(checks) and all(checks)


@dataclass(frozen=True)
class SolverQualification:
    qualification_id: str
    solver_backend: str
    solver_version: str
    model: str
    benchmark_name: str
    benchmark_source: str
    geometry_sha256: str
    settings_sha256: str
    envelope: QualificationEnvelope
    results: tuple[BenchmarkResult, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def qualified(self) -> bool:
        return bool(self.results) and all(item.passed for item in self.results)


def qualification_applies(qualification: SolverQualification, *, solver_backend: str, solver_version: str, model: str, reynolds: float | None, mach: float | None, alpha_deg: float | None, geometry_class: str | None = None) -> bool:
    """True only when the exact qualified build/model covers the requested case."""
    return (
        qualification.qualified
        and qualification.solver_backend == str(solver_backend)
        and qualification.solver_version == str(solver_version)
        and qualification.model == str(model)
        and qualification.envelope.contains(
            reynolds=reynolds, mach=mach, alpha_deg=alpha_deg, geometry_class=geometry_class
        )
    )
```

## `src/Mod/VibeCADAero/AeroRouting.py`

```python
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic, explainable solver/compute routing contracts.

"Auto" must never mean an opaque preference hidden in a provider adapter.  The
router evaluates already-resolved candidate capabilities and resource estimates.
It does not perform licensing/purpose classification and does not invent quota.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RouteCandidate:
    solver: str
    compute_provider: str
    qualified: bool
    available: bool
    fidelity_rank: int
    estimated_wall_time_s: float | None = None
    estimated_memory_bytes: int | None = None
    quota_fit: bool | None = None
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    selected: RouteCandidate | None
    rejected: tuple[tuple[RouteCandidate, tuple[str, ...]], ...]
    rationale: tuple[str, ...]


def choose_route(candidates: Iterable[RouteCandidate]) -> RoutingDecision:
    """Choose the highest-fidelity qualified available candidate deterministically.

    A known ``quota_fit=False`` makes a remote candidate ineligible. Unknown
    quota remains explicit rather than being guessed. Ties prefer lower known
    wall time, then lexical solver/provider order for reproducibility.
    """
    eligible: list[RouteCandidate] = []
    rejected: list[tuple[RouteCandidate, tuple[str, ...]]] = []
    for candidate in candidates:
        why: list[str] = []
        if not candidate.available:
            why.append("capability_unavailable")
        if not candidate.qualified:
            why.append("model_unqualified_for_requested_case")
        if candidate.quota_fit is False:
            why.append("provider_quota_estimate_does_not_fit")
        if why:
            rejected.append((candidate, tuple(why)))
        else:
            eligible.append(candidate)
    if not eligible:
        return RoutingDecision(None, tuple(rejected), ("no_eligible_route",))

    def key(candidate: RouteCandidate) -> tuple[Any, ...]:
        wall = candidate.estimated_wall_time_s
        return (
            -int(candidate.fidelity_rank),
            float("inf") if wall is None else float(wall),
            candidate.solver,
            candidate.compute_provider,
        )

    selected = sorted(eligible, key=key)[0]
    rationale = [
        f"selected={selected.solver}@{selected.compute_provider}",
        f"fidelity_rank={selected.fidelity_rank}",
    ]
    if selected.estimated_wall_time_s is not None:
        rationale.append(f"estimated_wall_time_s={selected.estimated_wall_time_s:g}")
    if selected.quota_fit is None:
        rationale.append("quota_fit=unknown")
    elif selected.quota_fit:
        rationale.append("quota_fit=true")
    rationale.extend(selected.reasons)
    return RoutingDecision(selected, tuple(rejected), tuple(rationale))
```

## `src/Mod/VibeCADAero/AeroSixDOF.py`

```python
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
```

## `src/Mod/VibeCADAero/AeroStripTheory.py`

```python
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
```

## `src/Mod/VibeCADAero/AeroUnsteady.py`

```python
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
```

## `src/Mod/VibeCADAero/openfoam_collect.py`

```python
#!/usr/bin/env python3
"""Collect OpenFOAM forceCoeffs output into VibeCAD's stable JSON contract.

This script is intentionally independent of FreeCAD.  It can be called at the
end of an OpenFOAM Allrun script.  It parses the *header actually written by the
case* instead of hard-coding one OpenFOAM version's column order.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

SCHEMA = "vibecad.openfoam.collector/1"


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_force_coeffs(path: Path) -> dict[str, float]:
    header: list[str] | None = None
    row: list[float] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            tokens = body.split()
            if tokens and _normalize(tokens[0]) == "time" and len(tokens) >= 4:
                header = tokens
            continue
        try:
            values = [float(v) for v in line.split()]
        except ValueError:
            continue
        row = values
    if row is None:
        raise RuntimeError(f"No numeric forceCoeffs rows found in {path}")
    if header is None:
        # Common forceCoeffs leading columns.  We only use the first 7 and reject
        # shorter files rather than guessing a different layout.
        header = ["Time", "Cd", "Cs", "Cl", "CmRoll", "CmPitch", "CmYaw"]
    if len(row) < min(7, len(header)):
        raise RuntimeError("forceCoeffs row has too few columns")
    values = {header[i]: row[i] for i in range(min(len(header), len(row)))}
    normalized = {_normalize(k): v for k, v in values.items()}

    def required(*names: str) -> float:
        for name in names:
            key = _normalize(name)
            if key in normalized:
                return float(normalized[key])
        raise RuntimeError(f"Missing forceCoeffs column: {names[0]}")

    return {
        "time": required("Time"),
        "cd": required("Cd"),
        "cs": required("Cs"),
        "cl": required("Cl"),
        "cl_roll": required("CmRoll", "ClRoll"),
        "cm_pitch": required("CmPitch", "Cm"),
        "cn_yaw": required("CmYaw", "CnYaw"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="OpenFOAM forceCoeffs coefficient.dat")
    parser.add_argument("--output", default="vibecad_result.json")
    parser.add_argument("--openfoam-version", default="unknown")
    parser.add_argument("--converged", choices=("true", "false", "unknown"), default="unknown")
    args = parser.parse_args()

    values = parse_force_coeffs(Path(args.input))
    converged = None if args.converged == "unknown" else args.converged == "true"
    payload = {
        "schema_version": SCHEMA,
        "openfoam_version": args.openfoam_version,
        "coefficients": {
            "cd": values["cd"],
            "cl": values["cl"],
            "cs": values["cs"],
            "cl_roll": values["cl_roll"],
            "cm_pitch": values["cm_pitch"],
            "cn_yaw": values["cn_yaw"],
        },
        "converged": converged,
        "iterations": None,
        "residuals": {},
        "last_time": values["time"],
        "warnings": [] if converged is True else ["Convergence not independently established by the collector."],
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## `src/Mod/VibeCADAero/tests/conftest.py`

```python
"""Make the reference overlay testable directly with ``pytest -q``.

The reconciliation package is not installed as a Python package.  Tests must
therefore add the reference module directory explicitly rather than depending
on an undocumented caller-side PYTHONPATH.
"""
from pathlib import Path
import sys

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))
```

## `src/Mod/VibeCADAero/tests/test_aero_acknowledgement.py`

```python
import AeroAcknowledgement as ack


class FakePreferences:
    def __init__(self):
        self.values = {}

    def GetBool(self, key, default=False):
        return bool(self.values.get(key, default))

    def SetBool(self, key, value):
        self.values[key] = bool(value)


def test_aero_acknowledgement_is_single_persistent_unversioned_flag():
    store = FakePreferences()
    before = ack.first_use_state(store)
    assert before["show_notice"] is True
    assert before["versioned"] is False
    assert "license terms separate from VibeCAD" in before["product_license_notice"]
    assert "ownership of CAD designs created in VibeCAD" in before["product_license_notice"]
    assert before["checkbox_text"] == "I understand."

    ack.acknowledge(store)
    after = ack.first_use_state(store)
    assert after["show_notice"] is False
    assert after["acknowledged"] is True
    assert store.values == {ack.ACKNOWLEDGEMENT_KEY: True}


def test_acknowledgement_text_is_informational_not_compliance_agreement():
    text = ack.ACKNOWLEDGEMENT_TEXT.lower()
    assert "agree to comply" not in text
    assert "commercial" not in text
    assert "military" not in text
    assert "license eligibility" not in text
    assert text == "i understand."
```

## `src/Mod/VibeCADAero/tests/test_cfd_contracts.py`

```python
from pathlib import Path

from AeroCFDContracts import (
    AeroCase, Artifact, ComputeSpec, FlowConditions, ForceMoment, GeometryArtifact,
    ReferenceQuantities, SolverSpec, Vector3, coefficients_from_force,
)


def _geometry(tmp_path: Path):
    p = tmp_path / "g.stl"
    p.write_bytes(b"solid\nendsolid\n")
    return GeometryArtifact(
        artifact=Artifact.from_file(p, media_type="model/stl", role="solver_geometry"),
        geometry_revision="r1",
    )


def test_force_projection_explicit_body_axes(tmp_path):
    case = AeroCase(
        case_id="c",
        geometry=_geometry(tmp_path),
        flow=FlowConditions(Vector3(10, 0, 0), density_kg_m3=1.0, dynamic_viscosity_pa_s=1e-5),
        references=ReferenceQuantities(area_m2=2.0, length_m=1.0, span_m=2.0),
        solver=SolverSpec("test", "test"),
        compute=ComputeSpec(),
    )
    fm = ForceMoment(force_body_n=Vector3(-100, 0, -50), moment_body_nm=Vector3(0, 10, 0))
    c = coefficients_from_force(case, fm)
    assert abs(c.cd - 1.0) < 1e-12
    assert abs(c.cl - 0.5) < 1e-12
    assert abs(c.cm_pitch - 0.1) < 1e-12
```

## `src/Mod/VibeCADAero/tests/test_detached_execution_overlay.py`

```python
from pathlib import Path
from AeroDetachedExecution import AttachmentGuard, can_attach, freeze_directory


def test_detached_input_hash_is_path_and_content_stable(tmp_path: Path):
    (tmp_path / "case.json").write_text('{"a":1}', encoding="utf-8")
    frozen = freeze_directory(tmp_path)
    assert frozen.file_count == 1
    assert len(frozen.sha256) == 64


def test_detached_result_attachment_requires_exact_frozen_state():
    guard = AttachmentGuard("i", 3, "g", "c")
    assert can_attach(guard, input_sha256="i", native_revision=3, geometry_revision="g", case_sha256="c")
    assert not can_attach(guard, input_sha256="i", native_revision=4, geometry_revision="g", case_sha256="c")
```

## `src/Mod/VibeCADAero/tests/test_dynamic_stall_overlay.py`

```python
import math
import numpy as np

from AeroDynamicStall import AirfoilDynamicParams, DynamicStallEngineeringModel, VectorizedDynamicStallEngineeringModel


def test_scalar_vectorized_same_equations():
    params = [AirfoilDynamicParams(chord_m=0.2), AirfoilDynamicParams(chord_m=0.3)]
    scalar = [DynamicStallEngineeringModel(p) for p in params]
    vector = VectorizedDynamicStallEngineeringModel(params)
    alphas = np.radians(np.array([5.0, 7.0]))
    for m, a in zip(scalar, alphas):
        m.reset(alpha_rad=float(a))
    vector.reset(alphas)
    for step in range(20):
        a = alphas + math.radians(0.1 * step)
        scalar_out = [
            m.step(alpha_rad=float(ai), pitch_rate_rad_s=0.2, speed_mps=15.0, dt_s=0.002)
            for m, ai in zip(scalar, a)
        ]
        vo = vector.step(alpha_rad=a, pitch_rate_rad_s=0.2, speed_mps=15.0, dt_s=0.002)
        assert np.allclose([o.cl for o in scalar_out], vo["cl"], rtol=1e-12, atol=1e-12)
        assert np.allclose([o.cd for o in scalar_out], vo["cd"], rtol=1e-12, atol=1e-12)
        assert np.allclose([o.cm for o in scalar_out], vo["cm"], rtol=1e-12, atol=1e-12)
```

## `src/Mod/VibeCADAero/tests/test_fluidx3d_vendor_policy.py`

```python
from pathlib import Path

from AeroCFDContracts import (
    AeroCase, Artifact, ComputeSpec, FlowConditions, GeometryArtifact,
    ReferenceQuantities, SolverSpec, Vector3,
)
from AeroLBM import FluidX3DBackend


def _case(tmp_path: Path, executable: str | None = None) -> AeroCase:
    stl = tmp_path / "g.stl"
    stl.write_text("solid g\nendsolid g\n", encoding="utf-8")
    settings = {"geometry_physical_size_m": 1.0}
    if executable is not None:
        settings["executable"] = executable
    return AeroCase(
        case_id="vendor-policy",
        geometry=GeometryArtifact(
            artifact=Artifact.from_file(stl, media_type="model/stl", role="solver_geometry"),
            geometry_revision="r1",
        ),
        flow=FlowConditions(Vector3(10, 0, 0), 1.225, 1.81e-5),
        references=ReferenceQuantities(1.0, 1.0, 1.0),
        solver=SolverSpec("fluidx3d", "fluidx3d", settings=settings),
        compute=ComputeSpec(),
    )


def test_default_resolves_vendored_bridge(tmp_path):
    vendor = tmp_path / "vendor" / "FluidX3D"
    bridge = vendor / "bin" / "VibeCADFluidX3D"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("bridge", encoding="utf-8")
    backend = FluidX3DBackend(vendor_root=vendor)
    assert backend._resolve_executable(_case(tmp_path)) == bridge.resolve()


def test_explicit_external_bridge_overrides_vendored_bridge(tmp_path):
    vendor = tmp_path / "vendor" / "FluidX3D"
    vendored = vendor / "bin" / "VibeCADFluidX3D"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("bridge", encoding="utf-8")
    external = tmp_path / "external" / "VibeCADFluidX3D"
    external.parent.mkdir(parents=True)
    external.write_text("bridge", encoding="utf-8")
    backend = FluidX3DBackend(vendor_root=vendor)
    assert backend._resolve_executable(_case(tmp_path, str(external))) == external.resolve()
```

## `src/Mod/VibeCADAero/tests/test_geometry_readiness_overlay.py`

```python
import pytest
from AeroGeometryReadiness import GeometryReadiness, assessed


def test_exact_brep_does_not_imply_cfd_readiness():
    evidence = assessed(GeometryReadiness.BREP_ACCEPTED, checks=["brep_loaded"])
    with pytest.raises(ValueError):
        evidence.require(GeometryReadiness.SURFACE_WATERTIGHT)


def test_solver_input_ready_is_explicit():
    evidence = assessed(GeometryReadiness.SOLVER_INPUT_FROZEN, checks=["watertight", "mesh_valid", "input_hashed"])
    evidence.require(GeometryReadiness.MESH_READY)
```

## `src/Mod/VibeCADAero/tests/test_host_evidence_overlay.py`

```python
from AeroHostEvidence import ArtifactClass, EvidenceState, artifact_metadata, prepared_case, solver_finished


def test_solver_completion_is_not_qualification():
    stamp = solver_finished("fluidx3d", qualified=False).as_dict()
    assert stamp["solver_finished"] is True
    assert stamp["model_qualified"] is False
    assert stamp["evidence_state"] == EvidenceState.MODEL_UNQUALIFIED.value
    assert stamp["claim_ceiling"] == "model_unqualified"


def test_prepared_case_is_not_solved():
    stamp = prepared_case("openfoam").as_dict()
    assert stamp["solver_finished"] is False
    assert stamp["claim_ceiling"] == "not_solved"


def test_artifact_taxonomy_keeps_exact_separate_from_derived():
    assert artifact_metadata("step")["artifact_class"] == ArtifactClass.EXACT.value
    mesh = artifact_metadata("surface_mesh", source_sha256="abc")
    assert mesh["artifact_class"] == ArtifactClass.DERIVED.value
    assert mesh["derived_from_exact"] is True
    assert artifact_metadata("screenshot")["presentation_only"] is True
```

## `src/Mod/VibeCADAero/tests/test_host_runtime_atomic_commit_gate.py`

```python
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load():
    root = Path(__file__).resolve().parents[4]
    path = root / "reference_host_runtime" / "VibeCADAnalysisJobState.py"
    spec = spec_from_file_location("vibecad_analysis_job_state_reference", path)
    assert spec and spec.loader
    mod = module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _waiting_state(mod):
    job = mod.AnalysisJobState("j")
    assert job.start()
    assert job.provider_completed()
    return job


def test_cancel_wins_before_commit_gate_and_commit_cannot_follow():
    mod = _load()
    job = _waiting_state(mod)
    assert job.request_cancel() is True
    assert job.status == "cancelled"
    assert job.try_begin_commit() is False
    assert job.status == "cancelled"


def test_commit_gate_wins_then_cancellation_is_rejected():
    mod = _load()
    job = _waiting_state(mod)
    assert job.try_begin_commit() is True
    assert job.phase == "committing"
    assert job.request_cancel() is False
    assert job.succeed() is True
    assert job.status == "succeeded"


def test_running_cancellation_requires_provider_ack_but_blocks_commit():
    mod = _load()
    job = mod.AnalysisJobState("j")
    assert job.start()
    assert job.request_cancel() is True
    assert job.status == "cancelling"
    assert job.provider_completed() is False
    assert job.status == "cancelled"
    assert job.try_begin_commit() is False


def test_terminal_state_is_idempotent_and_cannot_be_overwritten():
    mod = _load()
    job = _waiting_state(mod)
    assert job.try_begin_commit()
    assert job.succeed()
    assert job.succeed() is True
    assert job.fail("late callback") is False
    assert job.request_cancel() is False
    assert job.status == "succeeded"
```

## `src/Mod/VibeCADAero/tests/test_host_runtime_migration_overlay.py`

```python
from pathlib import Path


def _module(name: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / name).read_text(encoding="utf-8")


def test_aero_job_store_is_explicitly_transitional_not_target_authority() -> None:
    text = _module("AeroJobStore.py")
    assert "TRANSITIONAL" in text
    assert "NOT the target production job" in text
    assert "host-owned VibeCAD Analysis Runtime" in text


def test_detached_execution_reference_targets_host_extraction() -> None:
    text = _module("AeroDetachedExecution.py")
    assert "transitional reference" in text.lower()
    assert "host-owned VibeCAD Analysis Runtime" in text
    assert "prove that runtime first with existing FEM" in text
```

## `src/Mod/VibeCADAero/tests/test_host_runtime_plan_integrity.py`

```python
from pathlib import Path


def _package_root() -> Path:
    # tests -> VibeCADAero -> Mod -> src -> proposed_overlay -> package root
    return Path(__file__).resolve().parents[5]


def _read(name: str) -> str:
    return (_package_root() / name).read_text(encoding="utf-8")


def test_host_runtime_cutover_is_single_authority_not_double_execution() -> None:
    text = _read("HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md")
    assert "MUST NOT both launch the solver" in text
    assert "one execution authority" in text
    assert "Shadow observation" in text
    assert "does not launch a process" in text


def test_document_path_or_label_is_not_attachment_authority() -> None:
    text = _read("HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md")
    assert "document label or file path is never sufficient authority" in text
    assert "AWAITING_SOURCE" in text
    assert "Save As" in text
    assert "Save Copy / document clone" in text


def test_publication_replay_and_restart_are_explicitly_safe() -> None:
    cutover = _read("HOST_ANALYSIS_RUNTIME_CUTOVER_AND_ROLLBACK.md")
    recovery = _read("HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md")
    assert "Publication idempotency" in cutover
    assert "duplicate result graphs" in cutover
    assert "never mark success merely because output files exist after restart" in recovery
    assert "ORPHANED" in recovery


def test_architectural_ownership_does_not_move_fem_semantics_into_host() -> None:
    text = _read("HOST_ANALYSIS_RUNTIME_ARCHITECTURAL_DECISIONS.md")
    assert "Who owns job identity/lifecycle? | VibeCAD host" in text
    assert "Who owns physics/case meaning? | domain adapter" in text
    assert "Is FEM state genericized? | no" in text
    assert "Does Aero own a scheduler? | no" in text


def test_durable_job_does_not_persist_native_mutation_authority() -> None:
    text = _read("HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md")
    assert "provenance" in text
    assert "standing permission to mutate CAD" in text
    assert "NativeRuntimeContext object" in text
    assert "NativeCallTicket as executable authority" in text
    assert "fresh host authorization" in text
    assert "AWAITING_PUBLICATION" in text


def test_fem_initial_migration_preserves_original_ticket_revision_semantics() -> None:
    text = _read("HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md")
    assert "FEM migration phase" in text
    assert "original global expected structural revision" in text
    assert "The initial generic-runtime extraction MUST preserve current FEM behavior exactly" in text
    assert "Optional later FEM refinement" in text


def test_source_verified_public_api_and_preparation_boundary_are_recorded() -> None:
    text = _read("SOURCE_VERIFIED_HOST_RUNTIME_BASELINE.md")
    assert "capability name: `analyze.solver_execution`" in text
    assert "operations: **`status` and `cancel` only**" in text
    assert "before** calling `background_manager.submit" in text
    assert "reads FreeCAD `Document.Uid`" in text
```

## `src/Mod/VibeCADAero/tests/test_job_store_overlay.py`

```python
from AeroJobStore import AeroJobRecord, AeroJobStore, LifecycleState


def _record() -> AeroJobRecord:
    return AeroJobRecord(
        job_id="job-1",
        case_id="case-1",
        solver_backend="fluidx3d",
        compute_provider="local",
        document_uid="doc-1",
        captured_native_revision=3,
        geometry_revision="geom-a",
    )


def test_job_store_persists_transitions_and_staleness(tmp_path) -> None:
    store = AeroJobStore(tmp_path / "jobs.json")
    store.put(_record())
    running = store.transition("job-1", LifecycleState.RUNNING, progress=0.25)
    assert running.state is LifecycleState.RUNNING
    assert AeroJobStore(tmp_path / "jobs.json").get("job-1").progress == 0.25
    done = store.transition("job-1", LifecycleState.SUCCEEDED, progress=1.0, result_path="result.json")
    assert done.terminal
    assert done.stale_against(native_revision=3, geometry_revision="geom-a") is False
    assert done.stale_against(native_revision=4, geometry_revision="geom-a") is True
```

## `src/Mod/VibeCADAero/tests/test_kaggle_overlay.py`

```python
from AeroCFDContracts import JobState
from AeroKaggle import _classify_status


def test_status_classification():
    assert _classify_status("Kernel complete") == JobState.SUCCEEDED
    assert _classify_status("status: ERROR") == JobState.FAILED
    assert _classify_status("running") == JobState.RUNNING
```

## `src/Mod/VibeCADAero/tests/test_mesh_overlay.py`

```python
from AeroMesh import subdivide_tri6


def test_quadratic_triangle_subdivision():
    tris = subdivide_tri6([0, 1, 2, 3, 4, 5])
    assert tris == [[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]]
```

## `src/Mod/VibeCADAero/tests/test_native_bridge_overlay.py`

```python
from AeroNativeBridge import AttachmentState, capture_authority, decide_attachment


class State:
    def __init__(self, revision: int):
        self.revision = revision
    def current_revision(self, _document_uid: str) -> int:
        return self.revision


def test_authority_snapshot_and_stale_decisions() -> None:
    snap = capture_authority(State(7), document_uid="doc-1", geometry_revision="geom-a")
    current = decide_attachment(snap, current_native_revision=7, current_geometry_revision="geom-a")
    assert current.current is True
    assert current.state is AttachmentState.CURRENT
    stale = decide_attachment(snap, current_native_revision=8, current_geometry_revision="geom-b")
    assert stale.current is False
    assert stale.preserve_as_history is True
    assert stale.state is AttachmentState.STALE_BOTH
```

## `src/Mod/VibeCADAero/tests/test_native_repair_bridge_overlay.py`

```python
import pytest
from AeroNativeRepairBridge import capture, validate_apply


def test_repair_apply_rejects_native_revision_change():
    snap = capture(document_uid="doc", native_revision=4, geometry_revision="g", intent_rows=[])
    with pytest.raises(ValueError, match="native_revision_stale"):
        validate_apply(snap, current_native_revision=5, current_geometry_revision="g", current_intent_rows=[])


def test_repair_apply_preserves_user_explicit_intent():
    before = [{"kind":"user_explicit", "key":"target_mass", "value":1.5}]
    snap = capture(document_uid="doc", native_revision=4, geometry_revision="g", intent_rows=before)
    validate_apply(snap, current_native_revision=4, current_geometry_revision="g", current_intent_rows=before)
    after = [{"kind":"user_explicit", "key":"target_mass", "value":2.0}]
    with pytest.raises(ValueError, match="user_explicit_intent_changed"):
        validate_apply(snap, current_native_revision=4, current_geometry_revision="g", current_intent_rows=after)
```

## `src/Mod/VibeCADAero/tests/test_qualification_overlay.py`

```python
from AeroQualification import BenchmarkResult, QualificationEnvelope, SolverQualification


def test_qualification_is_explicit_and_envelope_bounded() -> None:
    q = SolverQualification(
        qualification_id="q1",
        solver_backend="openfoam",
        solver_version="x",
        model="kOmegaSST",
        benchmark_name="example",
        benchmark_source="reference",
        geometry_sha256="a" * 64,
        settings_sha256="b" * 64,
        envelope=QualificationEnvelope(reynolds_min=1e5, reynolds_max=2e6, alpha_min_deg=-5, alpha_max_deg=12),
        results=(BenchmarkResult("Cd", 0.03, 0.031, tolerance_abs=0.002),),
    )
    assert q.qualified
    assert q.envelope.contains(reynolds=1e6, mach=None, alpha_deg=4)
    assert not q.envelope.contains(reynolds=5e6, mach=None, alpha_deg=4)


def test_qualification_requires_exact_solver_build_and_envelope():
    from AeroQualification import BenchmarkResult, QualificationEnvelope, SolverQualification, qualification_applies
    q = SolverQualification(
        qualification_id="q1", solver_backend="fluidx3d", solver_version="abc", model="lbm",
        benchmark_name="bench", benchmark_source="source", geometry_sha256="g", settings_sha256="s",
        envelope=QualificationEnvelope(reynolds_min=1000, reynolds_max=100000),
        results=(BenchmarkResult("Cd", 1.0, 1.01, tolerance_rel=0.02),),
    )
    assert qualification_applies(q, solver_backend="fluidx3d", solver_version="abc", model="lbm", reynolds=5000, mach=None, alpha_deg=None)
    assert not qualification_applies(q, solver_backend="fluidx3d", solver_version="def", model="lbm", reynolds=5000, mach=None, alpha_deg=None)
```

## `src/Mod/VibeCADAero/tests/test_routing_overlay.py`

```python
from AeroRouting import RouteCandidate, choose_route


def test_routing_is_deterministic_and_explainable() -> None:
    decision = choose_route([
        RouteCandidate("openfoam", "local", qualified=True, available=True, fidelity_rank=5, estimated_wall_time_s=1000),
        RouteCandidate("fluidx3d", "kaggle", qualified=True, available=True, fidelity_rank=4, quota_fit=False),
        RouteCandidate("vlm", "local", qualified=True, available=True, fidelity_rank=2, estimated_wall_time_s=1),
    ])
    assert decision.selected.solver == "openfoam"
    assert any("selected=openfoam@local" in item for item in decision.rationale)
    assert decision.rejected[0][1] == ("provider_quota_estimate_does_not_fit",)
```

## `src/Mod/VibeCADAero/tests/test_sixdof_overlay.py`

```python
import numpy as np

from AeroSixDOF import ForceMomentBody, RigidBodyProperties, SixDOFReferenceSimulator


class ZeroProvider:
    def evaluate(self, state, controls, dt_s):
        return ForceMomentBody.zero()


def test_ned_gravity_moves_positive_down():
    sim = SixDOFReferenceSimulator(
        RigidBodyProperties(mass_kg=1.0, ix_kg_m2=1.0, iy_kg_m2=1.0, iz_kg_m2=1.0),
        ZeroProvider(),
    )
    sim.reset()
    rec = sim.step(0.1)
    assert sim.state.velocity_body_mps[2] > 0.0
    assert sim.state.position_ned_m[2] > 0.0
    assert abs(np.linalg.norm(sim.state.quaternion_bn) - 1.0) < 1e-12
```

## `src/Mod/VibeCADAero/tests/test_strip_overlay.py`

```python
from AeroStripTheory import StripWing


def test_semi_span_mirroring_is_explicit():
    wing = StripWing([0.0, 1.0], [1.0, 1.0], mirror=True)
    assert wing.area_m2 == 2.0
    half = StripWing([0.0, 1.0], [1.0, 1.0], mirror=False)
    assert half.area_m2 == 1.0
```

## `vendor/FluidX3D/FLUIDX3D_VENDOR_MANIFEST.json`

```json
{
  "commit": "8986874e626e0aebd317ab16c420b39e30dfa273",
  "first_use_notice": {
    "checkbox_text": "I understand.",
    "controls_solver_eligibility": false,
    "informational_only": true,
    "preference_key": "ThirdPartyNoticesAcknowledged",
    "repeat_after_updates": false,
    "telemetry": false,
    "versioned": false
  },
  "integration": {
    "commercial_product_profile": false,
    "external_bridge_override_supported": true,
    "purpose_classification": false,
    "runtime_download": false,
    "vendored_default": true
  },
  "license_sha256": "1c00d80544659d334c5880ba62ebe9f04307c1cfae856864c1433b63f55403e7",
  "license_source_path": "LICENSE.md",
  "note": "Recorded integration/documentation metadata; not an entitlement or compliance control.",
  "repository": "ProjectPhysX/FluidX3D",
  "schema_version": "vibecad.fluidx3d.vendor/3",
  "target_vendor_path": "src/Mod/VibeCADAero/vendor/FluidX3D"
}
```

## `vendor/FluidX3D/LICENSE.md`

```markdown
Copyright (c) 2022-2026 Dr. Moritz Lehmann

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files, to use this software for public research, education or personal use, and to alter it and redistribute it freely, subject to the following restrictions:

1. The [origin of this software](https://github.com/ProjectPhysX/FluidX3D) must not be misrepresented; you must not claim that you wrote the original software. Altered source versions must be plainly marked as such, and must not be misrepresented as being the original software.
2. Commercial use is not allowed. You may not sell this software, altered source versions, any part thereof or any of the rights granted to you under the license. You may not provide to third parties, for a fee or other consideration (including without limitation fees for hosting or consulting/support services related to the software), a product or service whose value derives from the functionality of this software, altered source versions or any part thereof, unless explicit permission is granted to you by the copyright owner.
3. Military use is not allowed. You may not use this software, altered source versions or any part thereof for military research or any military or defense industry purposes, or within a military institution.
4. You may not train AI models on the source code of this software, altered source versions or any part thereof.
5. If binaries of altered source versions or data or results generated by altered source versions are published, the altered source code must be published as well.
6. If scientific publications arise from this software or altered source versions, the articles [listed here](https://github.com/ProjectPhysX/FluidX3D#references) should be cited.
7. This license notice may not be removed or altered from any source distribution.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

German [Act on Copyright and Related Rights](https://www.gesetze-im-internet.de/englisch_urhg/englisch_urhg.html) (Urheberrechtsgesetz - UrhG) - Copyright Act of 9 September 1965 (Federal Law Gazette I, p. 1273), as last amended by Article 25 of the Act of 23 June 2021 (Federal Law Gazette I, p. 1858) - applies, in particular also [§ 97 (2) UrhG](https://www.gesetze-im-internet.de/englisch_urhg/englisch_urhg.html#p0881). The name "FluidX3D" is protected by German Werktitelschutz, [§ 5 (3) MarkenG](https://www.gesetze-im-internet.de/markeng/__5.html).
```

## `vendor/FluidX3D/VENDOR_POLICY.md`

```markdown
# FluidX3D Vendor Integration — Pass 03

**Pinned source:** `ProjectPhysX/FluidX3D@8986874e626e0aebd317ab16c420b39e30dfa273`

## Target integration

VibeCAD plans to vendor the pinned FluidX3D source tree beneath:

`src/Mod/VibeCADAero/vendor/FluidX3D/`

and build/package the VibeCAD FluidX3D bridge with the Aero capability. An explicitly configured external FluidX3D bridge is also supported as a normal override.

FluidX3D remains third-party software. Keep its authoritative `LICENSE.md` and origin visible with the vendored source. VibeCAD-owned bridge files and any modified FluidX3D source should be clearly identifiable in human-readable documentation/source history.

## No product-wide use profiles

Do not classify VibeCAD or VibeCADAero by FluidX3D-specific use terms. Do not create product-wide purpose profiles, purpose detectors or backend entitlement systems. The current FluidX3D license and `THIRD_PARTY_NOTICES.md` tell users/distributors the applicable FluidX3D terms.

The official FluidX3D repository currently does not publish a standardized commercial agreement/deployment model. If explicit permission is obtained in the future, the actual granted terms control; VibeCAD does not pre-invent deployment terms that are not actually published.

## Runtime behavior

1. Look for an explicitly configured bridge override first when the user supplied one.
2. Otherwise use the packaged vendored bridge.
3. Do not auto-download solver source during a normal run.
4. Do not ask purpose-of-use questions.
5. Do not add per-run or per-solver legal prompts.
6. First Aero entry uses the one product-level informational notice documented elsewhere.

## Re-vendoring engineering checklist

When updating the vendored source:

- freeze the exact new upstream commit;
- read current upstream build/API/license docs and update human-readable notices as needed;
- rebuild the VibeCAD bridge;
- verify the APIs actually used by the bridge;
- rerun scale/unit/force/torque/domain/refinement/field tests;
- rerun platform packaging tests;
- update the recorded source pin.

This checklist is engineering/documentation work, not a purpose-of-use enforcement gate.
```

## `vendor/FluidX3D/VIBECAD_VENDOR.md`

```markdown
# VibeCAD / FluidX3D Integration Note

FluidX3D is planned as the vendored/default VibeCADAero LBM implementation. It remains third-party code governed by its own supplied license. Its terms do not redefine the VibeCAD/VibeCADAero product license and do not change ownership of CAD designs created in VibeCAD.

The external bridge override remains available for development, custom builds or any deployment where the user/distributor chooses a separate FluidX3D build. VibeCAD does not infer why the override is used.

On first Aero entry, the product may show one informational Third-Party Software Notice with checkbox **“I understand.”** After acknowledgement, the local unversioned flag normally prevents the notice from appearing again.
```
