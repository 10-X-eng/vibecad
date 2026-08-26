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
