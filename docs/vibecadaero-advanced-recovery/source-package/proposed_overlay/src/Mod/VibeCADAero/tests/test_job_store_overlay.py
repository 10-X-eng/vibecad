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
