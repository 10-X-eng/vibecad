from __future__ import annotations

import sys
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibeCADAssemblySolverPolicy import set_joint_connectors_without_auto_solve


@pytest.mark.parametrize("prop", ["Offset1", "Offset2", "Distance", "Angle", "Reference1"])
@pytest.mark.parametrize("transacting", [False, True])
def test_joint_replay_does_not_launch_interactive_work(prop, transacting):
    """Execute the production callback; transaction replay owns restored values."""
    source = Path(__file__).resolve().parents[2] / "Assembly" / "JointObject.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    joint_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == "Joint")
    callback = next(node for node in joint_class.body if isinstance(node, ast.FunctionDef)
                    and node.name == "onChanged")
    calls = []
    namespace = {
        "App": SimpleNamespace(isRestoring=lambda: False),
        "_jointInteractionUsable": lambda joint: True,
        "JointUsingPreSolve": [],
        "solveIfAllowed": lambda assembly: calls.append("solve"),
    }
    exec(compile(ast.Module(body=[callback], type_ignores=[]), str(source), "exec"), namespace)
    joint = SimpleNamespace(
        Document=SimpleNamespace(Transacting=transacting),
        Reference1=(object(), [""]), Reference2=(object(), [""]),
        JointType=prop if prop in {"Distance", "Angle"} else "Fixed",
        Angle=0.0, recompute=lambda: calls.append("recompute"),
    )
    proxy = SimpleNamespace(
        getAssembly=lambda joint: SimpleNamespace(Type="Assembly"),
        updateJCSPlacements=lambda joint: calls.append("connectors"),
    )
    namespace["onChanged"](proxy, joint, prop)
    if transacting:
        assert calls == []
    else:
        assert calls


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
