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
