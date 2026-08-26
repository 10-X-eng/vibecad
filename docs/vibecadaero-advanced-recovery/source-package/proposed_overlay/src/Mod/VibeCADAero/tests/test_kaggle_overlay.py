from AeroCFDContracts import JobState
from AeroKaggle import _classify_status


def test_status_classification():
    assert _classify_status("Kernel complete") == JobState.SUCCEEDED
    assert _classify_status("status: ERROR") == JobState.FAILED
    assert _classify_status("running") == JobState.RUNNING
