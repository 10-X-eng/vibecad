from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from VibeCADAssemblySolverPolicy import set_joint_connectors_without_auto_solve


class _Preferences:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def GetBool(self, _name: str, _default: bool) -> bool:
        return self.enabled

    def SetBool(self, _name: str, enabled: bool) -> None:
        self.enabled = enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_joint_connector_batch_policy_suppresses_and_restores_autosolve(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    preferences = _Preferences(enabled)
    monkeypatch.setitem(
        sys.modules,
        "Preferences",
        SimpleNamespace(preferences=lambda: preferences),
    )
    calls: list[tuple[object, list[object], bool]] = []
    joint = SimpleNamespace()
    joint.Proxy = SimpleNamespace(
        setJointConnectors=lambda obj, refs: calls.append(
            (obj, refs, preferences.enabled)
        )
    )
    references = [object(), object()]

    set_joint_connectors_without_auto_solve(joint, references)

    assert calls == [(joint, references, False)]
    assert preferences.enabled is enabled


def test_joint_connector_batch_policy_restores_autosolve_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferences = _Preferences(True)
    monkeypatch.setitem(
        sys.modules,
        "Preferences",
        SimpleNamespace(preferences=lambda: preferences),
    )
    joint = SimpleNamespace()

    def fail(_obj: object, _refs: list[object]) -> None:
        assert preferences.enabled is False
        raise RuntimeError("connector failure")

    joint.Proxy = SimpleNamespace(setJointConnectors=fail)

    with pytest.raises(RuntimeError, match="connector failure"):
        set_joint_connectors_without_auto_solve(joint, [object(), object()])

    assert preferences.enabled is True


def test_joint_connector_batch_policy_preserves_authored_component_placements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preferences = _Preferences(True)
    monkeypatch.setitem(
        sys.modules,
        "Preferences",
        SimpleNamespace(preferences=lambda: preferences),
    )
    first = SimpleNamespace(Placement="first-authored")
    second = SimpleNamespace(Placement="second-authored")
    updates: list[object] = []
    joint = SimpleNamespace()

    def configure(_obj: object, _refs: list[object]) -> None:
        first.Placement = "first-pre-solved"
        second.Placement = "second-pre-solved"

    joint.Proxy = SimpleNamespace(
        setJointConnectors=configure,
        updateJCSPlacements=lambda obj: updates.append(obj),
    )

    set_joint_connectors_without_auto_solve(
        joint,
        [object(), object()],
        preserve_placements=[first, second],
    )

    assert first.Placement == "first-authored"
    assert second.Placement == "second-authored"
    assert updates == [joint]
    assert preferences.enabled is True
