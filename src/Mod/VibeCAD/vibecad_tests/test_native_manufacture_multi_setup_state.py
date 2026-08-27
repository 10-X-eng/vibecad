# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state boundaries for documents containing multiple CAM setups."""

from __future__ import annotations

from types import SimpleNamespace

import VibeCADNativeManufactureState as manufacture_state


def _document(*objects):
    by_name = {item.Name: item for item in objects}
    document = SimpleNamespace(Objects=list(objects))
    document.getObject = by_name.get
    for item in objects:
        item.Document = document
    return document


def test_other_setup_snapshot_excludes_owned_jobs_and_detects_semantic_change(
    monkeypatch,
):
    first = SimpleNamespace(Name="SetupOne", state_sha256="a" * 64)
    second = SimpleNamespace(Name="SetupTwo", state_sha256="b" * 64)
    model = SimpleNamespace(Name="Model", state_sha256="c" * 64)
    document = _document(first, model, second)

    monkeypatch.setattr(
        manufacture_state,
        "is_job",
        lambda item: item is first or item is second,
    )
    monkeypatch.setattr(
        manufacture_state,
        "job_state",
        lambda item: {"state_sha256": item.state_sha256},
    )

    frozen = manufacture_state.capture_other_job_states(document, (first,))

    assert frozen == ((second, "b" * 64),)
    assert manufacture_state.other_job_states_are_current(document, frozen)

    second.state_sha256 = "d" * 64
    assert not manufacture_state.other_job_states_are_current(document, frozen)


def test_other_setup_snapshot_detects_replaced_job_identity(monkeypatch):
    first = SimpleNamespace(Name="SetupOne", state_sha256="a" * 64)
    document = _document(first)
    monkeypatch.setattr(manufacture_state, "is_job", lambda _item: True)
    monkeypatch.setattr(
        manufacture_state,
        "job_state",
        lambda item: {"state_sha256": item.state_sha256},
    )
    frozen = manufacture_state.capture_other_job_states(document, ())

    replacement = SimpleNamespace(
        Name="SetupOne",
        state_sha256="a" * 64,
        Document=document,
    )
    document.Objects[:] = [replacement]
    document.getObject = lambda name: replacement if name == "SetupOne" else None

    assert not manufacture_state.other_job_states_are_current(document, frozen)
