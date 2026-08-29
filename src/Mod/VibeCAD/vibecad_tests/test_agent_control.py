# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the local agent-control channel."""

from __future__ import annotations

from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any
from urllib import error, request

import pytest

import VibeCADAgentCli as cli
import VibeCADAgentControl as control
import VibeCADAuth as auth


class _Document:
    def __init__(self, name: str, path: str = "", objects: list | None = None) -> None:
        self.Name = name
        self.Label = name
        self.FileName = path
        self.Objects = list(objects or [])
        self.recomputed = False
        self.Modified = False
        self.Partial = False
        self.content_revision = 0
        self.saved = 0

    @property
    def Content(self) -> str:  # noqa: N802 - FreeCAD API spelling
        return json.dumps(
            {
                "name": self.Name,
                "path": self.FileName,
                "content_revision": self.content_revision,
            },
            sort_keys=True,
        )

    def recompute(self) -> None:
        self.recomputed = True

    def save(self) -> bool:
        if not self.FileName:
            return False
        self.saved += 1
        Path(self.FileName).write_bytes(f"saved-{self.saved}".encode("ascii"))
        self.Modified = False
        return True

    def saveAs(self, path: str) -> bool:  # noqa: N802 - FreeCAD API spelling
        self.FileName = path
        return self.save()

    def isSaved(self) -> bool:  # noqa: N802 - FreeCAD API spelling
        # Native FreeCAD uses this as a "has a file name" query; the GUI
        # document owns the persisted dirty flag.
        return bool(self.FileName)


class _App:
    def __init__(self) -> None:
        self.documents: dict[str, _Document] = {}
        self.ActiveDocument: _Document | None = None
        self.GuiUp = False
        self.restoring = False
        self.opened: list[str] = []

    def isRestoring(self) -> bool:  # noqa: N802 - FreeCAD API spelling
        return self.restoring

    def listDocuments(self) -> dict[str, _Document]:
        return dict(self.documents)

    def setActiveDocument(self, name: str) -> None:
        self.ActiveDocument = self.documents[name]

    def openDocument(self, path: str) -> _Document:
        self.opened.append(path)
        document = _Document(Path(path).stem, path)
        self.documents[document.Name] = document
        self.ActiveDocument = document
        return document

    def closeDocument(self, name: str) -> None:
        document = self.documents.pop(name)
        if self.ActiveDocument is document:
            self.ActiveDocument = next(iter(self.documents.values()), None)


def _install_app(monkeypatch, app: _App) -> None:
    import FreeCAD

    monkeypatch.setattr(FreeCAD, "GuiUp", app.GuiUp, raising=False)
    monkeypatch.setattr(FreeCAD, "isRestoring", app.isRestoring, raising=False)
    monkeypatch.setattr(FreeCAD, "listDocuments", app.listDocuments, raising=False)
    monkeypatch.setattr(FreeCAD, "setActiveDocument", app.setActiveDocument, raising=False)
    monkeypatch.setattr(FreeCAD, "openDocument", app.openDocument, raising=False)
    monkeypatch.setattr(FreeCAD, "closeDocument", app.closeDocument, raising=False)
    monkeypatch.setattr(FreeCAD, "ActiveDocument", app.ActiveDocument, raising=False)

    def _refresh_active(*_args, **_kwargs):
        FreeCAD.ActiveDocument = app.ActiveDocument
        return None

    original_set = app.setActiveDocument

    def set_active(name: str) -> None:
        original_set(name)
        FreeCAD.ActiveDocument = app.ActiveDocument

    original_open = app.openDocument

    def open_document(path: str) -> _Document:
        document = original_open(path)
        FreeCAD.ActiveDocument = app.ActiveDocument
        return document

    original_close = app.closeDocument

    def close_document(name: str) -> None:
        original_close(name)
        FreeCAD.ActiveDocument = app.ActiveDocument

    monkeypatch.setattr(FreeCAD, "setActiveDocument", set_active)
    monkeypatch.setattr(FreeCAD, "openDocument", open_document)
    monkeypatch.setattr(FreeCAD, "closeDocument", close_document)
    monkeypatch.setattr(app, "setActiveDocument", set_active)
    monkeypatch.setattr(app, "openDocument", open_document)
    monkeypatch.setattr(app, "closeDocument", close_document)

    class _GuiDocumentAdapter:
        def __init__(self, document: _Document) -> None:
            self.document = document

        @property
        def Modified(self) -> bool:  # noqa: N802 - FreeCAD API spelling
            return bool(self.document.Modified)

        @Modified.setter
        def Modified(self, value: bool) -> None:  # noqa: N802
            self.document.Modified = bool(value)

    def get_gui_document(name: str):
        document = app.documents.get(name)
        return _GuiDocumentAdapter(document) if document is not None else None

    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getDocument=get_gui_document),
    )


@pytest.fixture(autouse=True)
def _explicit_test_document_dispatch(monkeypatch):
    """Every direct test opts into a synchronous test-only dispatcher."""

    import FreeCAD

    monkeypatch.setattr(FreeCAD, "GuiUp", False, raising=False)
    monkeypatch.setattr(FreeCAD, "isRestoring", lambda: False, raising=False)
    monkeypatch.setattr(
        control,
        "_document_thread_dispatch",
        lambda operation: operation(),
    )


def test_token_and_endpoint_stay_in_agent_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    token = control.load_or_create_token()
    assert len(token) >= 40
    assert control.load_token() == token
    path = control.write_endpoint(host="127.0.0.1", port=8766)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["base_url"] == "http://127.0.0.1:8766"
    assert payload["assistant_disabled_by_this_channel"] is False
    assert "token" not in payload
    assert payload["token_path"] == str(control.token_path())


def test_status_reports_grok_without_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    import VibeCADGrokAuth as grok

    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    grok.store_tokens(
        grok.GrokTokens(
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
            expires_at=9_999_999_999,
            account=grok.GrokAccount(email="user@x.ai", name="User"),
        )
    )

    class _Settings:
        provider = "grok"
        active_model = "grok-4.6"
        active_base_url = grok.DEFAULT_XAI_API_BASE
        use_online_provider = True
        mcp_enabled = False

    monkeypatch.setattr(control, "_safe_settings", lambda: _Settings())
    payload = control.dispatch("status")
    assert payload["ok"] is True
    assert payload["provider"] == "grok"
    assert payload["assistant_available"] is True
    assert payload["mcp_enabled"] is False
    assert payload["grok"]["signed_in"] is True
    assert payload["grok"]["email"] == "user@x.ai"
    dumped = json.dumps(payload)
    assert "secret-access-token" not in dumped
    assert "secret-refresh-token" not in dumped
    assert "xAI OAuth" in payload["oauth_note"]


def test_open_and_run_python_against_active_document(tmp_path, monkeypatch) -> None:
    app = _App()
    _install_app(monkeypatch, app)
    document_path = tmp_path / "part.FCStd"
    document_path.write_bytes(b"fcstd")
    script_path = tmp_path / "edit.py"
    script_path.write_text(
        "result = App.ActiveDocument.Name\nprint('ran')\n",
        encoding="utf-8",
    )

    opened = control.dispatch("open", {"path": str(document_path)})
    assert opened["ok"] is True
    assert opened["already_open"] is False
    assert opened["opened"]["path"] == str(document_path.resolve())

    again = control.dispatch("open", {"path": str(document_path)})
    assert again["already_open"] is True

    ran = control.dispatch(
        "run",
        {"script": str(script_path), "recompute": True},
    )
    assert ran["ok"] is True
    assert ran["result"] == "part"
    assert "ran" in ran["stdout"]
    assert app.ActiveDocument is not None
    assert app.ActiveDocument.recomputed is True


def test_run_reports_script_errors(monkeypatch) -> None:
    app = _App()
    _install_app(monkeypatch, app)
    payload = control.dispatch("run", {"python": "raise RuntimeError('boom')"})
    assert payload["ok"] is False
    assert payload["failure_code"] == "SCRIPT_FAILED"
    assert "boom" in payload["error"]


def test_open_requires_absolute_existing_path(tmp_path) -> None:
    missing = control.dispatch("open", {"path": str(tmp_path / "missing.FCStd")})
    assert missing["ok"] is False
    assert missing["failure_code"] == "DOCUMENT_NOT_FOUND"
    relative = control.dispatch("open", {"path": "part.FCStd"})
    assert relative["failure_code"] == "DOCUMENT_PATH_NOT_ABSOLUTE"


def test_save_close_and_reopen_document_round_trip(tmp_path, monkeypatch) -> None:
    app = _App()
    _install_app(monkeypatch, app)
    document_path = (tmp_path / "round-trip.FCStd").resolve()
    document_path.write_bytes(b"original")

    opened = control.dispatch("open", {"path": str(document_path)})
    assert opened["ok"] is True
    assert app.ActiveDocument is not None
    app.ActiveDocument.Modified = True

    saved = control.dispatch("save")
    assert saved["ok"] is True
    assert saved["saved"]["path"] == str(document_path)
    assert saved["saved"]["modified"] is False
    assert document_path.read_bytes() == b"saved-1"

    closed = control.dispatch("close", {"document": "round-trip"})
    assert closed["ok"] is True
    assert closed["closed"] == "round-trip"
    assert app.ActiveDocument is None

    reopened = control.dispatch("open", {"path": str(document_path)})
    assert reopened["ok"] is True
    assert reopened["already_open"] is False
    assert reopened["opened"]["path"] == str(document_path)


def test_native_gui_modified_state_guards_persisted_app_and_view_changes(
    monkeypatch,
) -> None:
    document = _Document("Saved", "C:/tmp/saved.FCStd")
    gui_document = SimpleNamespace(Modified=False)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(getDocument=lambda _name: gui_document),
    )

    assert document.isSaved() is True
    assert control._document_modified(document) is False

    # App-model edits and persisted view-provider edits both set this native
    # flag. App-level content equality must never override it.
    document.content_revision += 1
    gui_document.Modified = True
    assert control._document_modified(document) is True


def test_missing_native_gui_dirty_state_fails_closed(monkeypatch) -> None:
    document = _Document("Unknown", "C:/tmp/unknown.FCStd")
    document.Modified = False
    monkeypatch.setattr(control, "_gui", lambda: None)
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(GuiUp=True))

    assert control._document_modified(document) is True


def test_headless_generic_dirty_state_fails_closed_without_gui_flag(monkeypatch) -> None:
    document = _Document("Headless", "C:/tmp/headless.FCStd")
    del document.Modified
    monkeypatch.setattr(control, "_gui", lambda: None)
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(GuiUp=False))

    assert control._document_modified(document) is True


def test_open_preserves_native_restore_time_modified_state(
    tmp_path, monkeypatch
) -> None:
    document_path = (tmp_path / "restore-dirty.FCStd").resolve()
    document_path.write_bytes(b"native-document")
    app = _App()
    _install_app(monkeypatch, app)
    gui_documents: dict[str, SimpleNamespace] = {}

    def get_gui_document(name: str):
        return gui_documents.setdefault(name, SimpleNamespace(Modified=True))

    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(getDocument=get_gui_document),
    )

    opened = control.dispatch("open", {"path": str(document_path)})
    assert opened["ok"] is True
    assert opened["opened"]["modified"] is True

    refused = control.dispatch("close", {"document": "restore-dirty"})
    assert refused["failure_code"] == "DOCUMENT_MODIFIED"
    assert "restore-dirty" in app.documents


def test_close_refuses_gui_dirty_change_when_is_saved_only_means_has_file(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    document = _Document(
        "NativeDirty", str((tmp_path / "native-dirty.FCStd").resolve())
    )
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)
    gui_document = SimpleNamespace(Modified=False)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(getDocument=lambda _name: gui_document),
    )

    document.content_revision += 1
    gui_document.Modified = True
    assert document.isSaved() is True

    refused = control.dispatch("close", {"document": document.Name})
    assert refused["failure_code"] == "DOCUMENT_MODIFIED"
    assert document.Name in app.documents


def test_verified_agent_save_clears_only_the_current_native_gui_dirty_state(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    document_path = (tmp_path / "agent-save.FCStd").resolve()
    document = _Document("AgentSave", str(document_path))
    document.Modified = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)
    gui_document = SimpleNamespace(Modified=True)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(getDocument=lambda _name: gui_document),
    )

    saved = control.dispatch("save", {"document": document.Name})
    assert saved["ok"] is True
    assert saved["saved"]["modified"] is False
    assert gui_document.Modified is False

    # A later persisted GUI-only change must become dirty again.
    gui_document.Modified = True
    refused = control.dispatch("close", {"document": document.Name})
    assert refused["failure_code"] == "DOCUMENT_MODIFIED"


def test_partial_document_save_is_rejected_without_touching_existing_file(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    document_path = (tmp_path / "partial.FCStd").resolve()
    document_path.write_bytes(b"stale-source-bytes")
    document = _Document("Partial", str(document_path))
    document.Partial = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)
    gui_document = SimpleNamespace(Modified=True)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getDocument=lambda _name: gui_document),
    )

    refused = control.dispatch("save", {"document": document.Name})

    assert refused["failure_code"] == "DOCUMENT_PARTIAL"
    assert document.saved == 0
    assert document_path.read_bytes() == b"stale-source-bytes"
    assert gui_document.Modified is True


def test_partial_document_overwrite_save_as_is_rejected_before_path_change(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    source = (tmp_path / "partial-source.FCStd").resolve()
    source.write_bytes(b"source-bytes")
    target = (tmp_path / "existing-target.FCStd").resolve()
    target.write_bytes(b"stale-target-bytes")
    document = _Document("PartialSaveAs", str(source))
    document.Partial = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)
    gui_document = SimpleNamespace(Modified=True)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getDocument=lambda _name: gui_document),
    )

    refused = control.dispatch(
        "save_as",
        {
            "document": document.Name,
            "path": str(target),
            "overwrite": True,
        },
    )

    assert refused["failure_code"] == "DOCUMENT_PARTIAL"
    assert document.saved == 0
    assert document.FileName == str(source)
    assert target.read_bytes() == b"stale-target-bytes"
    assert gui_document.Modified is True


def test_unknown_partial_state_fails_closed_before_save(tmp_path, monkeypatch) -> None:
    app = _App()
    document_path = (tmp_path / "unknown-partial.FCStd").resolve()
    document_path.write_bytes(b"existing-bytes")
    document = _Document("UnknownPartial", str(document_path))
    del document.Partial
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)

    refused = control.dispatch("save", {"document": document.Name})

    assert refused["failure_code"] == "DOCUMENT_PARTIAL_STATE_UNKNOWN"
    assert document.saved == 0
    assert document_path.read_bytes() == b"existing-bytes"


def test_native_file_menu_save_state_needs_no_agent_owned_baseline(monkeypatch) -> None:
    document = _Document("ManualSave", "C:/tmp/manual-save.FCStd")
    gui_document = SimpleNamespace(Modified=True)
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(getDocument=lambda _name: gui_document),
    )

    document.content_revision += 1
    assert control._document_modified(document) is True

    # Native File -> Save persists App and GUI state and clears this flag.
    gui_document.Modified = False
    assert control._document_modified(document) is False


def test_fail_closed_status_document_snapshot_uses_the_document_thread_dispatch(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def on_document_thread(operation):
        calls.append("document-thread")
        return operation()

    monkeypatch.setattr(control, "_document_thread_dispatch", on_document_thread)
    monkeypatch.setattr(control, "report_status", lambda: {"ok": True})

    assert control.dispatch("status", fail_closed=True) == {"ok": True}
    assert calls == ["document-thread"]


def test_existing_dispatch_default_preserves_direct_execution_without_dispatcher(
    monkeypatch,
) -> None:
    """The pre-existing public dispatch default remains behaviorally compatible."""

    touched: list[str] = []
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("status") or {"ok": True, "mode": "legacy"},
    )

    assert control.dispatch("status") == {"ok": True, "mode": "legacy"}
    assert touched == ["status"]


def test_existing_documents_default_executes_directly_without_dispatcher(
    monkeypatch,
) -> None:
    touched: list[str] = []
    expected = {"ok": True, "documents": ["legacy"]}
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(
        control,
        "list_documents",
        lambda: touched.append("documents") or expected,
    )

    assert control.dispatch("documents") == expected
    assert touched == ["documents"]


def test_existing_documents_default_uses_dispatcher_without_opt_in_busy_gate(
    monkeypatch,
) -> None:
    """Only the development tester opts existing commands into fail-busy."""

    dispatched: list[str] = []
    expected = {"ok": True, "documents": ["legacy"]}
    monkeypatch.setattr(
        control,
        "_document_thread_dispatch",
        lambda operation: dispatched.append("document-thread") or operation(),
    )
    monkeypatch.setattr(control, "list_documents", lambda: expected)
    assert control._document_operation_gate.acquire(blocking=False)
    try:
        assert control.dispatch("documents") == expected
    finally:
        control._document_operation_gate.release()
    assert dispatched == ["document-thread"]


def test_document_operation_gate_rejects_concurrent_worker_before_qt_queue(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    first_result: list[dict[str, Any]] = []

    def blocking_dispatch(operation):
        entered.set()
        assert release.wait(timeout=2.0)
        return operation()

    monkeypatch.setattr(control, "_document_thread_dispatch", blocking_dispatch)
    monkeypatch.setattr(control, "report_status", lambda: {"ok": True})
    first = threading.Thread(
        target=lambda: first_result.append(
            control.dispatch("status", fail_closed=True)
        ),
        daemon=True,
    )
    first.start()
    assert entered.wait(timeout=2.0)
    try:
        busy = control.dispatch("status", fail_closed=True)
        assert busy["failure_code"] == "DOCUMENT_OPERATION_BUSY"
        assert first_result == []
    finally:
        release.set()
        first.join(timeout=2.0)

    assert not first.is_alive()
    assert first_result == [{"ok": True}]


def test_document_operation_refuses_native_restore_reentry_before_state_access(
    monkeypatch,
) -> None:
    app = _App()
    app.restoring = True
    touched: list[str] = []
    monkeypatch.setattr(control, "_app", lambda: app)
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("document-state") or {"ok": True},
    )

    refused = control.dispatch("status", fail_closed=True)

    assert refused["failure_code"] == "DOCUMENT_RESTORE_IN_PROGRESS"
    assert touched == []


def test_unknown_native_restore_state_fails_closed(monkeypatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(GuiUp=False))
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("document-state") or {"ok": True},
    )

    refused = control.dispatch("status", fail_closed=True)

    assert refused["failure_code"] == "DOCUMENT_RESTORE_STATE_UNAVAILABLE"
    assert touched == []


@pytest.mark.parametrize("dispatcher", [None, object()])
def test_gui_dispatch_fails_closed_when_document_thread_is_unavailable_or_invalid(
    monkeypatch,
    dispatcher,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(control, "_document_thread_dispatch", dispatcher)
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("document-state") or {"ok": True},
    )

    refused = control.dispatch("status", fail_closed=True)

    assert refused["failure_code"] == "DOCUMENT_THREAD_UNAVAILABLE"
    assert touched == []


def test_explicit_headless_local_adapter_can_run_without_gui_dispatcher(
    monkeypatch,
) -> None:
    app = SimpleNamespace(GuiUp=False, isRestoring=lambda: False)
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(control, "_app", lambda: app)
    monkeypatch.setattr(control, "report_status", lambda: {"ok": True})

    assert control.dispatch(
        "status",
        allow_headless_direct=True,
        fail_closed=True,
    ) == {"ok": True}


def test_explicit_headless_local_adapter_can_save_and_save_as(
    tmp_path, monkeypatch
) -> None:
    class HeadlessDocument:
        """Minimal DocumentPy-shaped fake with no synthetic Modified field."""

        def __init__(self) -> None:
            self.Name = "Headless"
            self.Label = "Headless"
            self.FileName = ""
            self.Objects: list[Any] = []
            self.Partial = False
            self.saved = 0

        def isSaved(self) -> bool:  # noqa: N802 - FreeCAD API spelling
            return bool(self.FileName)

        def save(self) -> bool:
            if not self.FileName:
                return False
            self.saved += 1
            Path(self.FileName).write_bytes(f"saved-{self.saved}".encode("ascii"))
            return True

        def saveAs(self, path: str) -> bool:  # noqa: N802 - FreeCAD API spelling
            self.FileName = path
            return self.save()

    app = _App()
    document = HeadlessDocument()
    app.documents[document.Name] = document
    app.ActiveDocument = document
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(control, "_app", lambda: app)
    monkeypatch.setattr(control, "_gui", lambda: None)

    target = (tmp_path / "headless.FCStd").resolve()
    saved_as = control.dispatch(
        "save_as",
        {"path": str(target)},
        allow_headless_direct=True,
        fail_closed=True,
    )
    assert saved_as["ok"] is True
    assert saved_as["saved_as"]["modified"] is False
    assert target.read_bytes() == b"saved-1"

    saved = control.dispatch(
        "save",
        allow_headless_direct=True,
        fail_closed=True,
    )
    assert saved["ok"] is True
    assert saved["saved"]["modified"] is False
    assert target.read_bytes() == b"saved-2"


def test_headless_adapter_refuses_direct_execution_when_app_gui_is_up(
    monkeypatch,
) -> None:
    app = SimpleNamespace(GuiUp=True, isRestoring=lambda: False)
    touched: list[str] = []
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(control, "_app", lambda: app)
    monkeypatch.setattr(control, "_gui", lambda: None)
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("document-state") or {"ok": True},
    )

    refused = control.dispatch(
        "status",
        allow_headless_direct=True,
        fail_closed=True,
    )

    assert refused["failure_code"] == "DOCUMENT_THREAD_UNAVAILABLE"
    assert touched == []


def test_save_as_requires_explicit_absolute_fcstd_and_protects_existing_target(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    document = _Document("Unsaved")
    document.Modified = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)

    relative = control.dispatch("save_as", {"path": "relative.FCStd"})
    assert relative["failure_code"] == "SAVE_PATH_NOT_ABSOLUTE"
    wrong_extension = control.dispatch(
        "save_as", {"path": str((tmp_path / "part.step").resolve())}
    )
    assert wrong_extension["failure_code"] == "SAVE_EXTENSION_UNSUPPORTED"

    existing = (tmp_path / "existing.FCStd").resolve()
    existing.write_bytes(b"keep")
    protected = control.dispatch("save_as", {"path": str(existing)})
    assert protected["failure_code"] == "SAVE_TARGET_EXISTS"
    assert existing.read_bytes() == b"keep"

    target = (tmp_path / "created.FCStd").resolve()
    saved = control.dispatch("save_as", {"path": str(target)})
    assert saved["ok"] is True
    assert saved["saved_as"]["path"] == str(target)
    assert target.read_bytes() == b"saved-1"


def test_close_refuses_modified_document_without_explicit_discard(
    tmp_path, monkeypatch
) -> None:
    app = _App()
    document = _Document("Dirty", str((tmp_path / "dirty.FCStd").resolve()))
    document.Modified = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)

    refused = control.dispatch("close", {"document": "Dirty"})
    assert refused["failure_code"] == "DOCUMENT_MODIFIED"
    assert "Dirty" in app.documents

    discarded = control.dispatch(
        "close", {"document": "Dirty", "discard_unsaved": True}
    )
    assert discarded["ok"] is True
    assert "Dirty" not in app.documents


def test_destructive_file_flags_require_literal_json_true(tmp_path, monkeypatch) -> None:
    app = _App()
    document = _Document("Dirty", str((tmp_path / "dirty.FCStd").resolve()))
    document.Modified = True
    app.documents[document.Name] = document
    app.ActiveDocument = document
    _install_app(monkeypatch, app)

    existing = (tmp_path / "existing.FCStd").resolve()
    existing.write_bytes(b"keep")
    protected = control.dispatch(
        "save_as",
        {"path": str(existing), "overwrite": "false"},
    )
    assert protected["failure_code"] == "SAVE_TARGET_EXISTS"
    assert existing.read_bytes() == b"keep"

    refused = control.dispatch(
        "close",
        {"document": "Dirty", "discard_unsaved": "false"},
    )
    assert refused["failure_code"] == "DOCUMENT_MODIFIED"
    assert "Dirty" in app.documents


def test_ui_ribbon_reports_live_semantic_screen_geometry(monkeypatch) -> None:
    class Point:
        def __init__(self, x: int, y: int) -> None:
            self._x = x
            self._y = y

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

    class Rect:
        def __init__(self, x: int, y: int, width: int, height: int) -> None:
            self._x = x
            self._y = y
            self._width = width
            self._height = height

        def topLeft(self) -> Point:  # noqa: N802 - Qt API spelling
            return Point(self._x, self._y)

        def center(self) -> Point:
            return Point(self._x + self._width // 2, self._y + self._height // 2)

        def width(self) -> int:
            return self._width

        def height(self) -> int:
            return self._height

    class Tabs:
        def count(self) -> int:
            return 2

        def tabText(self, index: int) -> str:  # noqa: N802 - Qt API spelling
            return ("&Model", "&Aero")[index]

        def tabData(self, index: int):  # noqa: N802 - Qt API spelling
            return ("PartDesignWorkbench", "VibeCADAeroWorkbench")[index]

        def tabRect(self, index: int) -> Rect:  # noqa: N802 - Qt API spelling
            return Rect(index * 100, 0, 100, 32)

        def mapToGlobal(self, point: Point) -> Point:  # noqa: N802 - Qt API spelling
            return Point(point.x() + 40, point.y() + 120)

        def isTabEnabled(self, _index: int) -> bool:  # noqa: N802
            return True

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def currentIndex(self) -> int:  # noqa: N802
            return 1

        def objectName(self) -> str:  # noqa: N802
            return "VibeCADRibbonTabs"

    tabs = Tabs()
    window = SimpleNamespace(
        findChild=lambda _kind, name: tabs if name == "VibeCADRibbonTabs" else None,
        windowTitle=lambda: "VibeCAD DEV CONTROLLED",
        winId=lambda: 4242,
    )
    qt_widgets = SimpleNamespace(QTabBar=object)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtWidgets=qt_widgets))
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getMainWindow=lambda: window),
    )

    payload = control.dispatch("ui_ribbon")
    assert payload["ok"] is True
    assert payload["object_name"] == "VibeCADRibbonTabs"
    assert payload["window_handle"] == 4242
    assert payload["selected_text"] == "Aero"
    assert payload["tabs"][1] == {
        "index": 1,
        "text": "Aero",
        "workbench": "VibeCADAeroWorkbench",
        "enabled": True,
        "selected": True,
        "screen_rect": {
            "left": 140,
            "top": 120,
            "width": 100,
            "height": 32,
            "center_x": 190,
            "center_y": 136,
        },
    }


@pytest.mark.parametrize(
    ("cursor_positions", "expected_after", "expected_unchanged"),
    (
        (((911, 733), (911, 733)), {"x": 911, "y": 733}, True),
        (((911, 733), (1042, 688)), {"x": 1042, "y": 688}, False),
    ),
    ids=("stationary-operator", "operator-moves-during-click"),
)
def test_ui_click_uses_in_process_qt_mouse_without_controlling_os_cursor(
    monkeypatch,
    cursor_positions,
    expected_after,
    expected_unchanged,
) -> None:
    class Point:
        def __init__(self, x: int, y: int) -> None:
            self._x = x
            self._y = y

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

    class Rect:
        def center(self) -> Point:
            return Point(75, 16)

    class Tabs:
        current = 0

        def count(self) -> int:
            return 2

        def tabText(self, index: int) -> str:  # noqa: N802
            return ("Model", "Aero")[index]

        def tabData(self, index: int):  # noqa: N802
            return ("PartDesignWorkbench", "VibeCADAeroWorkbench")[index]

        def tabRect(self, _index: int) -> Rect:  # noqa: N802
            return Rect()

        def isTabEnabled(self, _index: int) -> bool:  # noqa: N802
            return True

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def currentIndex(self) -> int:  # noqa: N802
            return self.current

        def objectName(self) -> str:  # noqa: N802
            return "VibeCADRibbonTabs"

    tabs = Tabs()
    window = SimpleNamespace(
        findChild=lambda _kind, name: tabs if name == "VibeCADRibbonTabs" else None,
        menuBar=lambda: None,
    )
    cursor_samples = iter(Point(x, y) for x, y in cursor_positions)
    qt_core = SimpleNamespace(
        Qt=SimpleNamespace(LeftButton="left", NoModifier="none")
    )
    qt_gui = SimpleNamespace(QCursor=SimpleNamespace(pos=lambda: next(cursor_samples)))
    qt_widgets = SimpleNamespace(
        QTabBar=object,
        QApplication=SimpleNamespace(processEvents=lambda: None),
    )
    clicks: list[tuple[object, object, object, object]] = []

    class QTest:
        @staticmethod
        def mouseClick(widget, button, modifiers, point) -> None:  # noqa: N802
            clicks.append((widget, button, modifiers, point))
            widget.current = 1

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets),
    )
    monkeypatch.setitem(
        sys.modules,
        "PySide6",
        SimpleNamespace(QtTest=SimpleNamespace(QTest=QTest)),
    )
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getMainWindow=lambda: window),
    )

    payload = control.dispatch(
        "ui_click",
        {"kind": "ribbon", "text": "Aero", "expected_index": 1},
    )
    assert payload["ok"] is True
    assert payload["input_method"] == "qt_in_process_mouse_click"
    assert payload["physical_cursor_control"] == "none"
    assert payload["physical_cursor_before"] == {"x": 911, "y": 733}
    assert payload["physical_cursor_after"] == expected_after
    assert payload["physical_cursor_unchanged"] is expected_unchanged
    assert payload["selected_before"] == "Model"
    assert payload["selected_after"] == "Aero"
    assert len(clicks) == 1


def test_ui_menu_click_uses_nonblocking_qt_popup(monkeypatch) -> None:
    class Point:
        def __init__(self, x: int, y: int) -> None:
            self._x = x
            self._y = y

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

    class Rect:
        def center(self) -> Point:
            return Point(24, 12)

        def left(self) -> int:
            return 2

        def bottom(self) -> int:
            return 24

    class Menu:
        visible = False

        def isVisible(self) -> bool:  # noqa: N802
            return self.visible

        def close(self) -> None:
            self.visible = False

        def popup(self, _point: Point) -> None:
            self.visible = True

    menu = Menu()

    class Action:
        def text(self) -> str:
            return "&File"

        def isEnabled(self) -> bool:  # noqa: N802
            return True

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def menu(self) -> Menu:
            return menu

    action = Action()

    class MenuBar:
        def isVisible(self) -> bool:  # noqa: N802
            return True

        def actions(self) -> list[Action]:
            return [action]

        def actionGeometry(self, _action: Action) -> Rect:  # noqa: N802
            return Rect()

        def mapToGlobal(self, point: Point) -> Point:  # noqa: N802
            return point

        def setActiveAction(self, _action: Action) -> None:  # noqa: N802
            return None

    menu_bar = MenuBar()
    window = SimpleNamespace(menuBar=lambda: menu_bar)
    qt_core = SimpleNamespace(
        Qt=SimpleNamespace(LeftButton="left", NoModifier="none"),
        QPoint=Point,
    )
    qt_gui = SimpleNamespace(QCursor=SimpleNamespace(pos=lambda: Point(700, 500)))
    qt_widgets = SimpleNamespace(
        QTabBar=object,
        QApplication=SimpleNamespace(processEvents=lambda: None),
    )

    class QTest:
        @staticmethod
        def mouseClick(_widget, _button, _modifiers, _point) -> None:  # noqa: N802
            menu.visible = True

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets),
    )
    monkeypatch.setitem(
        sys.modules,
        "PySide6",
        SimpleNamespace(QtTest=SimpleNamespace(QTest=QTest)),
    )
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getMainWindow=lambda: window),
    )

    payload = control.dispatch("ui_click", {"kind": "menu", "text": "File"})
    assert payload["ok"] is True
    assert payload["click_queued"] is False
    assert payload["semantic_verified"] is True
    assert payload["input_method"] == "qt_in_process_menu_popup"
    assert menu.visible is True


def test_screenshot_captures_the_visible_vibecad_window(tmp_path, monkeypatch) -> None:
    target = tmp_path / "visible-vibecad.png"

    class Pixmap:
        def width(self) -> int:
            return 1440

        def height(self) -> int:
            return 900

        def save(self, path: str, image_format: str) -> bool:
            assert image_format == "PNG"
            Path(path).write_bytes(b"fake-visible-vibecad-png")
            return True

    window = SimpleNamespace(
        grab=lambda: Pixmap(),
        windowTitle=lambda: "VibeCAD DEV test",
        winId=lambda: 12345,
    )
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getMainWindow=lambda: window),
    )

    payload = control.dispatch("screenshot", {"path": str(target)})

    assert payload["ok"] is True
    assert payload["capture"]["path"] == str(target.resolve())
    assert payload["capture"]["size"] == target.stat().st_size
    assert len(payload["capture"]["sha256"]) == 64
    assert payload["capture"]["width"] == 1440
    assert payload["capture"]["height"] == 900
    assert payload["capture"]["window_title"] == "VibeCAD DEV test"
    assert payload["capture"]["window_handle"] == 12345


def test_screenshot_path_and_overwrite_are_fail_closed(tmp_path, monkeypatch) -> None:
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep")

    relative = control.dispatch("screenshot", {"path": "relative.png"})
    assert relative["failure_code"] == "SCREENSHOT_PATH_NOT_ABSOLUTE"

    wrong_extension = control.dispatch(
        "screenshot", {"path": str(tmp_path / "capture.jpg")}
    )
    assert wrong_extension["failure_code"] == "SCREENSHOT_EXTENSION_UNSUPPORTED"

    protected = control.dispatch("screenshot", {"path": str(existing)})
    assert protected["failure_code"] == "SCREENSHOT_TARGET_EXISTS"

    string_true_is_not_authority = control.dispatch(
        "screenshot", {"path": str(existing), "overwrite": "true"}
    )
    assert string_true_is_not_authority["failure_code"] == "SCREENSHOT_TARGET_EXISTS"
    assert existing.read_bytes() == b"keep"


def test_file_and_ui_routes_are_registered(monkeypatch) -> None:
    captured: list[tuple[str, dict]] = []

    def fake_dispatch(command: str, arguments=None):
        captured.append((command, dict(arguments or {})))
        return {"ok": True, "command": command}

    monkeypatch.setattr(control, "dispatch", fake_dispatch)
    cases = (
        ("POST", "/v1/save", {}, "save"),
        ("POST", "/v1/save-as", {"path": "/tmp/a.FCStd"}, "save_as"),
        ("POST", "/v1/close", {"document": "a"}, "close"),
        ("GET", "/v1/ui/ribbon", {}, "ui_ribbon"),
        ("GET", "/v1/ui/menus", {}, "ui_menus"),
        ("POST", "/v1/ui/click", {"kind": "ribbon", "text": "Aero"}, "ui_click"),
        ("GET", "/v1/screenshot", {}, "screenshot"),
        ("POST", "/v1/screenshot", {"path": "/tmp/a.png"}, "screenshot"),
    )
    for method, route, body, command in cases:
        status, payload = control.handle_http_request(method, route, body)
        assert status == 200
        assert payload == {"ok": True, "command": command}
    assert [item[0] for item in captured] == [item[3] for item in cases]


def test_preferences_require_gui(monkeypatch) -> None:
    import FreeCADGui

    monkeypatch.setattr(FreeCADGui, "showPreferencesByName", None, raising=False)
    monkeypatch.setattr(FreeCADGui, "getMainWindow", None, raising=False)
    monkeypatch.setattr(FreeCADGui, "GuiUp", False, raising=False)
    payload = control.dispatch("preferences")
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_preferences_open_named_page(monkeypatch) -> None:
    import FreeCADGui

    calls: list[tuple[str, str]] = []

    def show(group: str, page: str) -> None:
        calls.append((group, page))

    monkeypatch.setattr(FreeCADGui, "showPreferencesByName", show, raising=False)
    monkeypatch.setattr(FreeCADGui, "GuiUp", True, raising=False)
    payload = control.dispatch("preferences")
    assert payload == {"ok": True, "opened": "VibeCAD"}
    assert calls == [("VibeCAD", "VibeCAD")]


def test_existing_server_start_default_preserves_no_dispatcher_compatibility(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    control.shutdown_server(wait=True)

    snapshot = control.ensure_server_started(port=0)
    try:
        assert snapshot["running"] is True
        assert set(snapshot) == {
            "running",
            "host",
            "port",
            "base_url",
            "token_path",
        }
        assert control.server_is_fail_closed() is False
    finally:
        control.shutdown_server(wait=True)


def test_strict_gui_http_server_refuses_startup_without_document_dispatcher(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    control.shutdown_server(wait=True)

    with pytest.raises(RuntimeError, match="document-thread dispatcher"):
        control.ensure_fail_closed_server_started(
            document_thread_dispatch=None,
            port=0,
        )

    assert control.server_snapshot()["running"] is False


def test_fail_closed_http_status_refuses_missing_dispatcher_without_state_access(
    monkeypatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    monkeypatch.setattr(
        control,
        "report_status",
        lambda: touched.append("status") or {"ok": True},
    )

    status, payload = control.handle_http_request(
        "GET",
        "/v1/status",
        {},
        fail_closed=True,
    )

    assert status == 200
    assert payload["failure_code"] == "DOCUMENT_THREAD_UNAVAILABLE"
    assert touched == []


def test_fail_closed_starter_refuses_to_relabel_running_legacy_server(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    legacy = control.ensure_server_started(port=0)
    try:
        assert legacy["running"] is True
        assert control.server_is_fail_closed() is False
        with pytest.raises(RuntimeError, match="compatibility mode"):
            control.ensure_fail_closed_server_started(
                document_thread_dispatch=lambda operation: operation(),
                port=0,
            )
        assert control.server_is_fail_closed() is False
    finally:
        control.shutdown_server(wait=True)


def test_legacy_starter_does_not_downgrade_running_fail_closed_server(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    dispatcher = lambda operation: operation()
    strict = control.ensure_fail_closed_server_started(
        document_thread_dispatch=dispatcher,
        port=0,
    )
    try:
        assert strict["running"] is True
        assert control.server_is_fail_closed() is True
        compatible_view = control.ensure_server_started()
        assert compatible_view["running"] is True
        assert control.server_is_fail_closed() is True
        assert control._document_thread_dispatch is dispatcher
    finally:
        control.shutdown_server(wait=True)
    assert control._document_thread_dispatch is None


def test_legacy_starter_cannot_replace_strict_dispatcher_with_noncallable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    dispatcher = lambda operation: operation()
    strict = control.ensure_fail_closed_server_started(
        document_thread_dispatch=dispatcher,
        port=0,
    )
    try:
        assert strict["running"] is True
        assert control.server_is_fail_closed() is True
        with pytest.raises(RuntimeError, match="callable document-thread dispatcher"):
            control.ensure_server_started(document_thread_dispatch=object())
        assert control.server_is_fail_closed() is True
        assert control._document_thread_dispatch is dispatcher
    finally:
        control.shutdown_server(wait=True)


def test_legacy_starter_cannot_replace_strict_dispatcher_with_different_callable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    calls: list[str] = []

    def dispatcher(operation):
        calls.append("strict-dispatcher")
        return operation()

    control.ensure_fail_closed_server_started(
        document_thread_dispatch=dispatcher,
        port=0,
    )
    try:
        replacement = lambda operation: operation()
        with pytest.raises(RuntimeError, match="cannot replace"):
            control.ensure_server_started(document_thread_dispatch=replacement)
        assert control._document_thread_dispatch is dispatcher
        monkeypatch.setattr(control, "report_status", lambda: {"ok": True})
        assert control.dispatch("status", fail_closed=True) == {"ok": True}
        assert calls == ["strict-dispatcher"]
    finally:
        control.shutdown_server(wait=True)


def test_http_routes_and_bearer_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(
        control,
        "_safe_settings",
        lambda: SimpleNamespace(
            provider="chatgpt",
            active_model="",
            active_base_url=None,
            use_online_provider=True,
            mcp_enabled=False,
        ),
    )
    with ThreadPoolExecutor(max_workers=1) as document_thread:
        def dedicated_dispatch(operation):
            return document_thread.submit(operation).result(timeout=2.0)

        snapshot = control.ensure_fail_closed_server_started(
            document_thread_dispatch=dedicated_dispatch,
            port=0,
        )
        try:
            assert snapshot["running"] is True
            port = snapshot["port"]
            token = control.load_token()
            url = f"http://127.0.0.1:{port}/v1/status"
            with pytest.raises(error.HTTPError) as denied:
                request.urlopen(url, timeout=2)
            assert denied.value.code == 401
            http_request = request.Request(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
            with request.urlopen(http_request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["provider"] == "chatgpt"
            assert payload["assistant_available"] is True
        finally:
            control.shutdown_server(wait=True)


def test_automatic_server_port_candidates_fall_through_and_stay_in_range() -> None:
    assert control._server_port_candidates(8766, explicit=False) == tuple(
        range(8766, 8776)
    )
    assert control._server_port_candidates(65535, explicit=False) == (65535,)
    assert control._server_port_candidates(8766, explicit=True) == (8766,)


def test_cli_parses_freecadcmd_argv_and_prefers_http(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_http(command, arguments, timeout_seconds=30.0):
        captured["command"] = command
        captured["arguments"] = arguments
        captured["timeout"] = timeout_seconds
        return {"ok": True, "via": "http", "command": command}

    monkeypatch.setattr(cli, "call_http", fake_http)
    args = cli.build_parser().parse_args(
        cli._argv_for_parser(
            [
                "FreeCADCmd.exe",
                "C:\\VibeCAD\\Mod\\VibeCAD\\VibeCADAgentCli.py",
                "open",
                "--path",
                "C:\\Models\\part.FCStd",
            ]
        )
    )
    payload = cli.execute(args)
    assert payload == {"ok": True, "via": "http", "command": "open"}
    assert captured["command"] == "open"
    assert captured["arguments"]["path"] == "C:\\Models\\part.FCStd"


def test_cli_local_mode_preserves_existing_and_guards_new_commands(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_dispatch(command, arguments, **kwargs):
        captured.append(
            {"command": command, "arguments": arguments, "kwargs": kwargs}
        )
        return {"ok": True}

    monkeypatch.setattr(
        cli,
        "_control_module",
        lambda: SimpleNamespace(
            dispatch=fake_dispatch,
            UPSTREAM_COMMANDS=control.UPSTREAM_COMMANDS,
        ),
    )

    assert cli.call_local("status", {}) == {"ok": True}
    assert cli.call_local("save", {"document": "Part"}) == {"ok": True}
    assert captured == [
        {"command": "status", "arguments": {}, "kwargs": {}},
        {
            "command": "save",
            "arguments": {"document": "Part"},
            "kwargs": {"allow_headless_direct": True, "fail_closed": True},
        },
    ]


def test_cli_maps_semantic_menu_snapshot_and_independent_ui_click() -> None:
    menus = cli.build_parser().parse_args(["ui-menus"])
    assert cli._http_route(menus.command) == ("GET", "/v1/ui/menus")
    assert cli._control_command(menus.command) == "ui_menus"

    click = cli.build_parser().parse_args(
        [
            "ui-click",
            "--kind",
            "ribbon",
            "--text",
            "Aero",
            "--expected-process-id",
            "1234",
            "--expected-index",
            "7",
        ]
    )
    assert cli._http_route(click.command) == ("POST", "/v1/ui/click")
    assert cli._control_command(click.command) == "ui_click"
    assert cli._command_arguments(click) == {
        "kind": "ribbon",
        "text": "Aero",
        "expected_process_id": 1234,
        "expected_index": 7,
    }

    screenshot = cli.build_parser().parse_args(
        ["screenshot", "--path", "C:\\Evidence\\vibecad.png"]
    )
    assert cli._http_route(screenshot.command) == ("POST", "/v1/screenshot")
    assert cli._control_command(screenshot.command) == "screenshot"
    assert cli._command_arguments(screenshot) == {
        "path": "C:\\Evidence\\vibecad.png",
        "overwrite": False,
    }


def test_cli_gui_only_uses_exit_code_two(monkeypatch) -> None:
    monkeypatch.setattr(cli, "call_http", lambda *args, **kwargs: None)
    args = Namespace(
        command="status",
        local=False,
        gui_only=True,
        timeout=1.0,
        path=None,
        script=None,
        python=None,
        no_recompute=False,
    )
    payload = cli.execute(args)
    assert payload["failure_code"] == "GUI_NOT_RUNNING"
    monkeypatch.setattr(cli, "execute", lambda _args: payload)
    assert cli.main(["--gui-only", "status"]) == cli.EXIT_GUI_UNAVAILABLE


def test_mcp_mode_is_reported_without_being_enabled(monkeypatch) -> None:
    class _Settings:
        provider = "openai"
        active_model = "gpt-5.5"
        active_base_url = None
        use_online_provider = True
        mcp_enabled = True

    monkeypatch.setattr(control, "_safe_settings", lambda: _Settings())
    payload = control.report_status()
    assert payload["mcp_enabled"] is True
    assert payload["assistant_available"] is False


def test_existing_providers_remain_registered() -> None:
    assert set(auth.PROVIDERS) >= {"openai", "anthropic", "chatgpt", "grok"}
    assert control.DEFAULT_AGENT_PORT != 8765
    assert control.DEFAULT_AGENT_PORT == 8766
