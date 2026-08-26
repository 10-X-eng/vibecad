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
