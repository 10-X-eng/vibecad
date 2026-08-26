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
