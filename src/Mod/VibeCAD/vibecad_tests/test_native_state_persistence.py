# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import VibeCADCore as core_module
from VibeCADCore import VibeCADService
from VibeCADNativeState import (
    NativeAuthorityConflict,
    NativeDocumentStateStore,
)
import VibeCADNativeStatePersistence as persistence
from VibeCADNativeStatePersistence import (
    NativeStatePersistenceError,
    native_state_path,
    read_native_state,
    write_native_state,
)


def test_state_file_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "project" / "native-state.json"
    payload = {"schema": "test", "revision": 3, "receipts": []}

    assert write_native_state(path, payload) == path
    assert read_native_state(path) == payload
    assert not path.with_name("native-state.json.tmp").exists()
    assert native_state_path({"root": str(tmp_path / "project")}) == path


def test_missing_state_is_distinct_from_invalid_state(tmp_path: Path) -> None:
    path = tmp_path / "native-state.json"
    assert read_native_state(path) is None
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(NativeStatePersistenceError, match="not an object"):
        read_native_state(path)


def test_state_file_size_is_bounded(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(persistence, "MAX_NATIVE_STATE_JSON_BYTES", 20)
    path = tmp_path / "native-state.json"
    with pytest.raises(NativeStatePersistenceError, match="output exceeds"):
        write_native_state(path, {"payload": "larger than twenty bytes"})
    path.write_text("x" * 21, encoding="utf-8")
    with pytest.raises(NativeStatePersistenceError, match="state exceeds"):
        read_native_state(path)


class _ProjectStore:
    def __init__(self, root: Path, *, mode: str = "vibescript", saved: bool = True):
        self.root = root
        self.mode = mode
        self.saved = saved

    def modeling_engine(self) -> str:
        return self.mode

    def select_modeling_engine(self, mode: str) -> dict[str, str]:
        self.mode = mode
        return {
            "mode": mode,
            "persistence": "project" if self.saved else "session",
        }

    def project_scope(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "document_saved": self.saved,
        }


def _service(project: _ProjectStore) -> VibeCADService:
    service = object.__new__(VibeCADService)
    service._project_store = project
    service._native_document_states = NativeDocumentStateStore()
    service._native_state_restores = set()
    service._native_state_restore_errors = {}
    document = SimpleNamespace(Uid="document-a", Meta={})
    service._active_document = lambda: document
    service._active_document_uid = lambda: document.Uid
    return service


def test_service_transition_persists_authority_and_blocks_silent_return(
    tmp_path: Path,
) -> None:
    project = _ProjectStore(tmp_path)
    service = _service(project)

    assert service.select_modeling_engine("native") == {
        "mode": "native",
        "persistence": "project",
    }
    payload = read_native_state(tmp_path / "native-state.json")
    assert payload is not None
    assert payload["authority_baseline_revision"] == 0

    service._native_document_states.note_structural_change("document-a")
    with pytest.raises(NativeAuthorityConflict):
        service.select_modeling_engine("vibescript")
    assert project.mode == "native"


def test_reselecting_current_mode_embeds_the_portable_document_choice(
    tmp_path: Path,
) -> None:
    service = _service(_ProjectStore(tmp_path, mode="native"))
    document = service._active_document()

    assert service.select_modeling_engine("native") == {
        "mode": "native",
        "persistence": "unchanged",
    }
    assert document.Meta[core_module.AUTHORING_MODE_META_KEY] == "native"


def test_unchanged_native_epoch_can_return_to_vibescript(tmp_path: Path) -> None:
    project = _ProjectStore(tmp_path)
    service = _service(project)
    service.select_modeling_engine("native")

    assert service.select_modeling_engine("vibescript") == {
        "mode": "vibescript",
        "persistence": "project",
    }
    assert service.native_document_state()["native_authority"]["active"] is False


def test_saved_native_state_reconstructs_in_a_new_service(tmp_path: Path) -> None:
    first_project = _ProjectStore(tmp_path)
    first = _service(first_project)
    first.select_modeling_engine("native")
    first._native_document_states.note_structural_change("document-a")
    first._persist_active_native_state()
    expected = first.native_document_state()

    reopened = _service(_ProjectStore(tmp_path, mode="native"))
    reopened.ensure_native_document_state("document-a")

    assert reopened.native_document_state() == expected


def test_failed_state_write_rolls_mode_and_authority_back(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = _ProjectStore(tmp_path)
    service = _service(project)
    monkeypatch.setattr(
        core_module,
        "write_native_state",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        service.select_modeling_engine("native")

    assert project.mode == "vibescript"
    assert service.native_document_state()["native_authority"]["active"] is False


def test_persisted_json_contains_no_python_or_diagnostic_repr(tmp_path: Path) -> None:
    project = _ProjectStore(tmp_path)
    service = _service(project)
    service.select_modeling_engine("native")

    raw = (tmp_path / "native-state.json").read_text(encoding="utf-8")

    assert json.loads(raw)["schema"] == "vibecad-native-state-v1"
    assert "Traceback" not in raw
    assert "<" not in raw
