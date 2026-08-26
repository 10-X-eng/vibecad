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
