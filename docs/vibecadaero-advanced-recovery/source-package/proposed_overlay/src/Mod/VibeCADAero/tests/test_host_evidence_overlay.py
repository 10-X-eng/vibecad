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
