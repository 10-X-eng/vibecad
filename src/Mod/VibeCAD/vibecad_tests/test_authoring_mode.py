# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADAuthoringMode import (
    AuthoringModeScope,
    AuthoringModeStore,
    normalize_authoring_mode,
)


def _scope(identity: str, path: Path, *, saved: bool) -> AuthoringModeScope:
    return AuthoringModeScope(identity, path, saved)


def test_unsaved_choice_is_session_only() -> None:
    store = AuthoringModeStore()
    writes: list[tuple[Path, str]] = []
    scope = _scope("doc-a", Path("/unused/unsaved.json"), saved=False)

    selected = store.select(scope, "native", lambda path, mode: writes.append((path, mode)))

    assert selected.summary() == {"mode": "native", "persistence": "session"}
    assert store.current(scope, lambda _path: "vibescript") == selected
    assert writes == []
    assert store.session_document_count == 1


def test_unsaved_documents_have_independent_choices() -> None:
    store = AuthoringModeStore()
    first = _scope("doc-a", Path("/unused/a.json"), saved=False)
    second = _scope("doc-b", Path("/unused/b.json"), saved=False)
    store.select(first, "native", lambda _path, _mode: None)

    assert store.current(first, lambda _path: "vibescript").mode == "native"
    assert store.current(second, lambda _path: "native").mode == "vibescript"


def test_first_save_promotes_session_choice_to_project() -> None:
    store = AuthoringModeStore()
    values: dict[Path, str] = {}
    unsaved = _scope("doc-a", Path("/unused/a.json"), saved=False)
    saved = _scope("doc-a", Path("/projects/a/project.vibecad.json"), saved=True)

    def reader(path: Path) -> str:
        return values.get(path, "vibescript")

    def writer(path: Path, mode: str) -> None:
        values[path] = mode

    store.select(unsaved, "native", writer)

    assert store.current(saved, reader).summary() == {
        "mode": "native",
        "persistence": "project_pending",
    }
    assert store.persist_after_save(saved, reader, writer).summary() == {
        "mode": "native",
        "persistence": "project",
    }
    assert values[saved.manifest_path] == "native"
    assert store.session_document_count == 0

    reopened = AuthoringModeStore()
    assert reopened.current(saved, reader).summary() == {
        "mode": "native",
        "persistence": "project",
    }


def test_saved_selection_writes_immediately_without_session_shadow() -> None:
    store = AuthoringModeStore()
    writes: list[tuple[Path, str]] = []
    scope = _scope("doc-a", Path("/projects/a/project.vibecad.json"), saved=True)

    selection = store.select(
        scope,
        "native",
        lambda path, mode: writes.append((path, mode)),
    )

    assert selection.summary() == {"mode": "native", "persistence": "project"}
    assert writes == [(scope.manifest_path, "native")]
    assert store.session_document_count == 0


def test_failed_first_save_keeps_session_choice_for_retry() -> None:
    store = AuthoringModeStore()
    unsaved = _scope("doc-a", Path("/unused/a.json"), saved=False)
    saved = _scope("doc-a", Path("/projects/a/project.vibecad.json"), saved=True)
    store.select(unsaved, "native", lambda _path, _mode: None)

    def fail_write(_path: Path, _mode: str) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        store.persist_after_save(saved, lambda _path: "vibescript", fail_write)

    assert store.current(saved, lambda _path: "vibescript").summary() == {
        "mode": "native",
        "persistence": "project_pending",
    }


@pytest.mark.parametrize("value", ("build123d", "openscad", "typo"))
def test_retired_or_unknown_modes_are_not_supported(value: str) -> None:
    with pytest.raises(RuntimeError, match="unsupported authoring mode"):
        normalize_authoring_mode(value)


def test_scope_requires_identity_and_manifest_path() -> None:
    with pytest.raises(RuntimeError, match="no document scope"):
        AuthoringModeScope.from_project_scope({}, session_id="")
