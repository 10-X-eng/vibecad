# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
import VibeCADNativeSketchVirtualSpace as virtual_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchVirtualSpace import (
    PreparedSketchVirtualSpaceConstraints,
    create_sketch_virtual_space_constraints,
    preflight_sketch_virtual_space,
    prepare_sketch_virtual_space,
    set_sketch_virtual_space_view,
    verify_sketch_virtual_space_constraints,
)
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _constraint(index: int, expected: bool) -> dict[str, object]:
    return {
        "constraint_index": index,
        "expected_virtual_space": expected,
        "virtual_space": not expected,
    }


def _values(target: dict[str, object], **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "target": target,
            **updates,
        }
    )


def _view(expected: bool, shown: bool) -> dict[str, object]:
    return {
        "kind": "view",
        "expected_shown_virtual_space": expected,
        "shown_virtual_space": shown,
    }


def _constraints(*targets: dict[str, object]) -> dict[str, object]:
    return {"kind": "constraints", "constraints": list(targets)}


def _install_view_state(monkeypatch, shown: bool) -> dict[str, bool]:
    state = {"shown": shown}
    monkeypatch.setattr(
        virtual_module,
        "read_shown_virtual_space",
        lambda: state["shown"],
    )

    def write(value: bool) -> None:
        state["shown"] = value

    monkeypatch.setattr(virtual_module, "write_shown_virtual_space", write)
    return state


def test_view_sets_exact_state_without_document_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    state = _install_view_state(monkeypatch, False)
    prepared = preflight_sketch_virtual_space(
        context,
        prepare_sketch_virtual_space(document.Uid, _values(_view(False, True))),
    )

    result = set_sketch_virtual_space_view(context, prepared)

    assert state["shown"] is True
    assert result["target_kind"] == "view"
    assert result["changed"] is True
    assert result["previous_shown_virtual_space"] is False
    assert result["shown_virtual_space"] is True
    assert sketch.GeometryCount == 1
    assert sketch.ConstraintCount == 0


def test_view_noop_and_stale_state(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    state = _install_view_state(monkeypatch, True)
    prepared = preflight_sketch_virtual_space(
        context,
        prepare_sketch_virtual_space(document.Uid, _values(_view(True, True))),
    )
    assert set_sketch_virtual_space_view(context, prepared)["changed"] is False

    state["shown"] = False
    with pytest.raises(NativeSketchError, match="edit-view state changed"):
        preflight_sketch_virtual_space(
            context,
            prepare_sketch_virtual_space(
                document.Uid,
                _values(_view(True, False)),
            ),
        )


def test_view_rolls_back_when_exact_sketch_changes(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    state = _install_view_state(monkeypatch, False)
    prepared = preflight_sketch_virtual_space(
        context,
        prepare_sketch_virtual_space(document.Uid, _values(_view(False, True))),
    )
    original_write = virtual_module.write_shown_virtual_space

    def write_and_drift(value: bool) -> None:
        original_write(value)
        if value:
            sketch.GeometryFacadeList[0].Construction = True

    monkeypatch.setattr(virtual_module, "write_shown_virtual_space", write_and_drift)
    with pytest.raises(NativeSketchError, match="active Sketch changed"):
        set_sketch_virtual_space_view(context, prepared)
    assert state["shown"] is False


def test_constraints_switch_mixed_exact_states(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Horizontal", 0))
    sketch.addConstraint(FakeConstraint("Vertical", 0))
    sketch.Constraints[1].InVirtualSpace = True
    sketch.ExpressionEngine = [("Unrelated", "Spreadsheet.Value")]
    values = _values(
        _constraints(_constraint(0, False), _constraint(1, True)),
        expected_constraint_count=2,
    )
    prepared = preflight_sketch_virtual_space(
        context,
        prepare_sketch_virtual_space(document.Uid, values),
    )
    assert isinstance(prepared, PreparedSketchVirtualSpaceConstraints)

    result = verify_sketch_virtual_space_constraints(
        document,
        create_sketch_virtual_space_constraints(document, prepared),
    )

    assert [item.InVirtualSpace for item in sketch.Constraints] == [True, False]
    assert sketch.ExpressionEngine == [("Unrelated", "Spreadsheet.Value")]
    assert result["target_kind"] == "constraints"
    assert result["changed_constraints"] == [
        {
            "constraint_index": 0,
            "constraint_type": "Horizontal",
            "previous_virtual_space": False,
            "virtual_space": True,
        },
        {
            "constraint_index": 1,
            "constraint_type": "Vertical",
            "previous_virtual_space": True,
            "virtual_space": False,
        },
    ]


@pytest.mark.parametrize(
    "target",
    (
        {"kind": "view", "shown_virtual_space": True},
        _constraints(),
        _constraints(*(_constraint(index, False) for index in range(17))),
        _constraints(_constraint(0, False), _constraint(0, False)),
        _constraints(
            {
                "constraint_index": 0,
                "expected_virtual_space": False,
                "virtual_space": False,
            }
        ),
        {"kind": "unknown"},
    ),
)
def test_target_rejects_open_duplicate_unbounded_or_non_switching_values(
    monkeypatch,
    target,
) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_virtual_space(document.Uid, _values(target))


def test_constraints_reject_stale_state_and_any_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Horizontal", 0))
    values = _values(
        _constraints(_constraint(0, False)),
        expected_constraint_count=1,
    )
    prepared = preflight_sketch_virtual_space(
        context,
        prepare_sketch_virtual_space(document.Uid, values),
    )
    sketch.Constraints[0].InVirtualSpace = True
    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_virtual_space_constraints(document, prepared)

    sketch.Constraints[0].InVirtualSpace = False
    draft = create_sketch_virtual_space_constraints(document, prepared)
    sketch.Constraints[0].IsActive = False
    with pytest.raises(NativeSketchError, match="beyond the exact requested"):
        verify_sketch_virtual_space_constraints(document, draft)


def test_runtime_routes_constraint_target_through_one_transaction(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Horizontal", 0))
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchConstraintRuntime(context).mutate_constraint(
        {
            "operation": "set_virtual_space",
            **_values(
                _constraints(_constraint(0, False)),
                expected_constraint_count=1,
            ),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Set Native Sketch Virtual Space"
    assert result["target_kind"] == "constraints"


def test_runtime_routes_view_target_without_document_transaction(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    state = _install_view_state(monkeypatch, False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("view state must not open a document transaction")

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", unexpected)
    result = NativeSketchConstraintRuntime(context).mutate_constraint(
        {
            "operation": "set_virtual_space",
            **_values(_view(False, True)),
        },
        ticket=None,
    )

    assert state["shown"] is True
    assert result["target_kind"] == "view"
