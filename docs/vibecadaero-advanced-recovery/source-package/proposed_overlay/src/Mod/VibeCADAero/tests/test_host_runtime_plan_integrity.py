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
