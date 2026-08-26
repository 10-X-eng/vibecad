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
