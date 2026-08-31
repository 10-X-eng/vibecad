# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the local agent-control channel."""

from __future__ import annotations

from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import hashlib
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
    for name in control.DEVELOPMENT_IDENTITY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(control, "_active_runtime_identity", None)
    monkeypatch.setattr(control, "_server_started_at_utc", None)
    control._reset_tracked_operations()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return _sha256(path)


def _development_attestations(tmp_path, monkeypatch):
    repo = (tmp_path / "checkout").resolve()
    source_root = repo / "src" / "Mod" / "VibeCAD"
    runtime_root = (
        repo
        / "package"
        / "rattler-build"
        / ".pixi"
        / "envs"
        / "default"
        / "Library"
    )
    installed_root = runtime_root / "Mod" / "VibeCAD"
    executable = runtime_root / "bin" / "VibeCAD.exe"
    environment_root = runtime_root.parent
    python_executable = environment_root / "python.exe"
    qt_dll_directory = runtime_root / "bin"
    qt_platforms_directory = runtime_root / "lib" / "qt6" / "plugins" / "platforms"
    qwindows = qt_platforms_directory / "qwindows.dll"
    attestation_root = repo / ".vibecad-dev" / "attestations"
    executable.parent.mkdir(parents=True, exist_ok=True)
    installed_root.mkdir(parents=True, exist_ok=True)
    attestation_root.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"exact-vibecad-executable")
    python_executable.write_bytes(b"exact-repo-python")
    qwindows.parent.mkdir(parents=True, exist_ok=True)
    qwindows.write_bytes(b"exact-qt6-windows-platform")
    qt_dlls = []
    for name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"):
        path = qt_dll_directory / name
        path.write_bytes(f"exact-{name}".encode("ascii"))
        qt_dlls.append(
            {"name": name, "path": str(path.resolve()), "sha256": _sha256(path)}
        )

    qt_runtime = {
        "qt_major": 6,
        "plugin_root": str(qt_platforms_directory.parent.resolve()),
        "platforms_directory": str(qt_platforms_directory.resolve()),
        "qwindows_path": str(qwindows.resolve()),
        "qwindows_sha256": _sha256(qwindows),
        "dll_directory": str(qt_dll_directory.resolve()),
        "dlls": qt_dlls,
    }
    qt_platform_probe = {
        "complete": True,
        "checked_at_utc": "2026-08-29T19:59:59.000000Z",
        "platform": "windows",
        "python_executable": str(python_executable.resolve()),
        "python_sha256": _sha256(python_executable),
        "loaded_qwindows_path": str(qwindows.resolve()),
        "loaded_qwindows_sha256": _sha256(qwindows),
    }
    release_evidence = {
        "asserted": True,
        "clean_checkout": True,
        "submodule_dirt_checked": True,
        "git_status_mode": (
            "--porcelain=v2 --untracked-files=all --ignore-submodules=none"
        ),
        "cold_build_asserted": True,
        "pre_build_environment_present": False,
        "pre_build_runtime_complete": False,
        "environment_absent_before_install": True,
        "pre_build_checked_at_utc": "2026-08-29T19:59:57.000000Z",
        "environment_cleaned_at_utc": "2026-08-29T19:59:58.000000Z",
        "build_cache_cleaned_at_utc": "2026-08-29T19:59:58.500000Z",
        "pre_receipt_checked_at_utc": "2026-08-29T20:00:00.500000Z",
    }

    module_specs = (
        ("InitGui.py", source_root / "InitGui.py", installed_root / "InitGui.py"),
        (
            "VibeCADAgentControl.py",
            source_root / "VibeCADAgentControl.py",
            installed_root / "VibeCADAgentControl.py",
        ),
        (
            "VibeCADAgentCli.py",
            source_root / "VibeCADAgentCli.py",
            installed_root / "VibeCADAgentCli.py",
        ),
        (
            "VibeCADGui.py",
            source_root / "VibeCADGui.py",
            installed_root / "VibeCADGui.py",
        ),
        (
            "Invoke-VibeCAD-VisibleTour.ps1",
            repo / "Invoke-VibeCAD-VisibleTour.ps1",
            None,
        ),
        ("Launch-VibeCAD-Dev.ps1", repo / "Launch-VibeCAD-Dev.ps1", None),
    )
    modules = []
    runtime_paths = {}
    for index, (name, source_path, installed_path) in enumerate(module_specs):
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_content = f"module-{index}\n"
        source_path.write_text(source_content, encoding="utf-8")
        if installed_path is not None:
            installed_path.write_text(source_content, encoding="utf-8")
            runtime_paths[name] = installed_path.resolve()
        modules.append(
            {
                "name": name,
                "source_path": str(source_path.resolve()),
                "source_sha256": _sha256(source_path),
                "installed_path": (
                    str(installed_path.resolve()) if installed_path is not None else None
                ),
                "installed_sha256": (
                    _sha256(installed_path) if installed_path is not None else None
                ),
            }
        )

    commit = "a" * 40
    tree = "b" * 40
    build_path = attestation_root / "build-test.json"
    build_payload = {
        "schema": control.BUILD_ATTESTATION_SCHEMA,
        "attestation_path": str(build_path.resolve()),
        "created_at_utc": "2026-08-29T20:00:00.000000Z",
        "repository_root": str(repo),
        "commit": commit,
        "tree": tree,
        "executable_path": str(executable.resolve()),
        "executable_sha256": _sha256(executable),
        "qt_runtime": qt_runtime,
        "qt_platform_probe": qt_platform_probe,
        "release_evidence": release_evidence,
        "modules": modules,
    }
    build_sha = _write_json(build_path, build_payload)

    launch_path = attestation_root / "launch-test.json"
    launch_payload = {
        "schema": control.LAUNCH_ATTESTATION_SCHEMA,
        "attestation_path": str(launch_path.resolve()),
        "created_at_utc": "2026-08-29T20:00:01.000000Z",
        "launch_id": "test-launch-id",
        "repository_root": str(repo),
        "commit": commit,
        "tree": tree,
        "executable_path": str(executable.resolve()),
        "executable_sha256": _sha256(executable),
        "build_attestation_path": str(build_path.resolve()),
        "build_attestation_sha256": build_sha,
        "qt_runtime": qt_runtime,
        "qt_platform_probe": qt_platform_probe,
        "release_evidence": release_evidence,
        "modules": modules,
    }
    launch_sha = _write_json(launch_path, launch_payload)

    values = {
        control.DEV_MODE_ENV: "1",
        control.DEV_ATTESTATION_REQUIRED_ENV: "1",
        control.DEV_SOURCE_SHA_ENV: commit,
        control.DEV_SOURCE_TREE_ENV: tree,
        control.DEV_SOURCE_ROOT_ENV: str(repo),
        control.DEV_BUILD_ATTESTATION_ENV: str(build_path.resolve()),
        control.DEV_BUILD_ATTESTATION_SHA256_ENV: build_sha,
        control.DEV_LAUNCH_ATTESTATION_ENV: str(launch_path.resolve()),
        control.DEV_LAUNCH_ATTESTATION_SHA256_ENV: launch_sha,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(control, "_current_executable_path", lambda: executable.resolve())
    monkeypatch.setattr(
        control,
        "_git_checkout_identity",
        lambda repository_root: (repository_root.resolve(), commit, tree),
    )
    monkeypatch.setattr(
        control,
        "_actual_runtime_module_paths",
        lambda: dict(runtime_paths),
    )
    monkeypatch.setattr(
        control,
        "_current_qwindows_module_path",
        lambda: qwindows.resolve(),
        raising=False,
    )
    return SimpleNamespace(
        repo=repo,
        executable=executable,
        source_root=source_root,
        installed_root=installed_root,
        build_path=build_path,
        launch_path=launch_path,
        qwindows=qwindows,
        qt_runtime=qt_runtime,
        qt_platform_probe=qt_platform_probe,
        release_evidence=release_evidence,
        modules=modules,
        commit=commit,
        tree=tree,
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


def test_development_runtime_identity_binds_actual_files(tmp_path, monkeypatch) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)

    identity = control.development_runtime_identity()

    assert identity is not None
    assert identity["schema"] == "vibecad.dev-runtime-identity.v1"
    assert identity["repository_root"] == str(attested.repo)
    assert identity["commit"] == attested.commit
    assert identity["tree"] == attested.tree
    assert identity["executable_path"] == str(attested.executable.resolve())
    assert identity["executable_sha256"] == _sha256(attested.executable)
    assert identity["build_attestation_path"] == str(attested.build_path.resolve())
    assert identity["launch_attestation_path"] == str(attested.launch_path.resolve())
    assert identity["qt_runtime"] == attested.qt_runtime
    assert identity["qt_platform_probe"] == attested.qt_platform_probe
    assert identity["release_evidence"] == attested.release_evidence
    assert identity["qt_process"] == {
        "platform": "windows",
        "loaded_qwindows_path": str(attested.qwindows.resolve()),
        "loaded_qwindows_sha256": _sha256(attested.qwindows),
    }
    assert {module["name"] for module in identity["modules"]} == {
        "InitGui.py",
        "VibeCADAgentControl.py",
        "VibeCADAgentCli.py",
        "VibeCADGui.py",
        "Invoke-VibeCAD-VisibleTour.ps1",
        "Launch-VibeCAD-Dev.ps1",
    }


def test_development_runtime_identity_rejects_partial_qt_receipt(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    build_payload = json.loads(attested.build_path.read_text(encoding="utf-8"))
    build_payload.pop("qt_platform_probe")
    build_sha = _write_json(attested.build_path, build_payload)
    launch_payload = json.loads(attested.launch_path.read_text(encoding="utf-8"))
    launch_payload["build_attestation_sha256"] = build_sha
    launch_sha = _write_json(attested.launch_path, launch_payload)
    monkeypatch.setenv(control.DEV_BUILD_ATTESTATION_SHA256_ENV, build_sha)
    monkeypatch.setenv(control.DEV_LAUNCH_ATTESTATION_SHA256_ENV, launch_sha)

    with pytest.raises(RuntimeError, match="qt_platform_probe"):
        control.development_runtime_identity()


def test_development_runtime_identity_rejects_release_evidence_mismatch(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    launch_payload = json.loads(attested.launch_path.read_text(encoding="utf-8"))
    launch_payload["release_evidence"]["clean_checkout"] = False
    launch_sha = _write_json(attested.launch_path, launch_payload)
    monkeypatch.setenv(control.DEV_LAUNCH_ATTESTATION_SHA256_ENV, launch_sha)

    with pytest.raises(RuntimeError, match="release_evidence"):
        control.development_runtime_identity()


def test_development_runtime_identity_rejects_other_process_qwindows(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    other = attested.qwindows.with_name("other-qwindows.dll")
    other.write_bytes(b"other-platform-plugin")
    monkeypatch.setattr(control, "_current_qwindows_module_path", lambda: other.resolve())

    with pytest.raises(RuntimeError, match="current VibeCAD process"):
        control.development_runtime_identity()


def test_development_runtime_identity_rejects_modified_installed_module(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    (attested.installed_root / "VibeCADAgentControl.py").write_text(
        "modified after attestation\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="installed SHA-256"):
        control.development_runtime_identity()


def test_development_runtime_identity_rejects_actual_checkout_identity_mismatch(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    monkeypatch.setattr(
        control,
        "_git_checkout_identity",
        lambda repository_root: (
            repository_root.resolve(),
            "c" * 40,
            attested.tree,
        ),
    )

    with pytest.raises(RuntimeError, match="actual HEAD"):
        control.development_runtime_identity()


def test_development_runtime_identity_rejects_stale_install_after_source_change(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    (attested.source_root / "VibeCADGui.py").write_text(
        "new source not represented by installed module\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="source SHA-256"):
        control.development_runtime_identity()


@pytest.mark.parametrize("failure", ("missing", "hash-mismatch"))
def test_development_runtime_identity_rejects_missing_or_mismatched_attestation(
    tmp_path, monkeypatch, failure
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    if failure == "missing":
        attested.launch_path.unlink()
    else:
        monkeypatch.setenv(control.DEV_LAUNCH_ATTESTATION_SHA256_ENV, "0" * 64)

    with pytest.raises(RuntimeError, match="launch attestation"):
        control.development_runtime_identity()


def test_endpoint_and_status_publish_matching_process_runtime_identity(
    tmp_path, monkeypatch
) -> None:
    attested = _development_attestations(tmp_path, monkeypatch)
    identity = control.development_runtime_identity()
    started_at = "2026-08-29T20:00:02.123456Z"
    monkeypatch.setenv(
        control.AGENT_HOME_ENV,
        str(attested.repo / ".vibecad-dev" / "agent"),
    )
    monkeypatch.setattr(control, "_active_runtime_identity", identity)
    monkeypatch.setattr(control, "_server_started_at_utc", started_at)
    monkeypatch.setattr(
        control,
        "_enforce_windows_current_user_only_acl",
        lambda _path: None,
    )
    monkeypatch.setattr(control, "_safe_settings", lambda: None)
    monkeypatch.setattr(control, "_gui", lambda: SimpleNamespace(GuiUp=True))
    monkeypatch.setattr(control, "_all_documents", list)
    monkeypatch.setattr(control, "_grok_account_snapshot", lambda: {"signed_in": False})
    monkeypatch.setattr(control, "_aero_status_snapshot", lambda: {"available": False})

    endpoint_path = control.write_endpoint(host="127.0.0.1", port=8766)
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    status = control.report_status()

    assert endpoint["server_instance_id"] == status["server_instance_id"]
    assert len(endpoint["server_instance_id"]) >= 43
    assert endpoint["process_id"] == status["process_id"] == control.os.getpid()
    assert endpoint["server_started_at_utc"] == status["server_started_at_utc"] == started_at
    assert endpoint["runtime_identity"] == status["runtime_identity"] == identity
    assert endpoint["fail_closed"] is status["fail_closed"] is False
    assert endpoint["token_path"] == str(control.token_path().resolve())


def test_status_advertises_additive_operation_tracking_contract(
    monkeypatch,
) -> None:
    monkeypatch.setattr(control, "_safe_settings", lambda: None)
    monkeypatch.setattr(control, "_gui", lambda: SimpleNamespace(GuiUp=True))
    monkeypatch.setattr(control, "_all_documents", list)
    monkeypatch.setattr(control, "_grok_account_snapshot", lambda: {"signed_in": False})
    monkeypatch.setattr(control, "_aero_status_snapshot", lambda: {"available": False})

    status = control.report_status()

    assert status["operation_tracking"] == {
        "schema": "vibecad.dev-operation-tracking.v1",
        "request_operation_id_field": "operation_id",
        "status_route_template": "/v1/operations/{operation_id}",
        "completed_state": "completed",
    }


def test_tracked_http_operation_can_be_proven_after_client_response(
    monkeypatch,
) -> None:
    operation_id = "62d7301a-d80c-4b77-b697-d74976f666ef"
    control._reset_tracked_operations()
    monkeypatch.setattr(
        control,
        "dispatch",
        lambda command, arguments=None, **_kwargs: {
            "ok": True,
            "result": {"completed": True, "value": 42},
        },
    )

    status, response = control.handle_http_request(
        "POST",
        "/v1/run",
        {"operation_id": operation_id, "python": "result = 42"},
        fail_closed=True,
    )
    probe_status, probe = control.handle_http_request(
        "GET",
        f"/v1/operations/{operation_id}",
        fail_closed=True,
    )

    assert status == probe_status == 200
    assert response["ok"] is True
    assert response["operation_id"] == operation_id
    operation = probe["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["server_instance_id"] == control._server_instance_id
    assert operation["command"] == "run"
    assert operation["state"] == "completed"
    assert operation["result"] == {"completed": True, "value": 42}
    assert operation["response"]["operation_id"] == operation_id
    assert operation["started_at_utc"].endswith("Z")
    assert operation["completed_at_utc"].endswith("Z")


def test_operation_status_route_bypasses_document_dispatch_and_reports_running(
    monkeypatch,
) -> None:
    operation_id = "a5ea0ae8-6ea1-49b3-b4a4-65f5c0b204c6"
    entered = threading.Event()
    release = threading.Event()
    control._reset_tracked_operations()

    def blocking_dispatch(command, arguments=None, **_kwargs):
        assert command == "run"
        entered.set()
        assert release.wait(timeout=5.0)
        return {"ok": True, "result": {"completed": True}}

    monkeypatch.setattr(control, "dispatch", blocking_dispatch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            control.handle_http_request,
            "POST",
            "/v1/run",
            {"operation_id": operation_id, "python": "pass"},
            fail_closed=True,
        )
        assert entered.wait(timeout=2.0)
        probe_status, probe = control.handle_http_request(
            "GET",
            f"/v1/operations/{operation_id}",
            fail_closed=True,
        )
        assert probe_status == 200
        assert probe["operation"]["state"] == "running"
        assert probe["operation"]["completed_at_utc"] is None
        release.set()
        assert pending.result(timeout=5.0)[1]["ok"] is True


def test_operation_ids_are_canonical_unique_and_bounded(monkeypatch) -> None:
    operation_id = "2368f751-befa-40e0-b13c-489f25f62c31"
    control._reset_tracked_operations()
    monkeypatch.setattr(
        control,
        "dispatch",
        lambda *_args, **_kwargs: {"ok": True, "result": {"completed": True}},
    )

    _, first = control.handle_http_request(
        "POST", "/v1/run", {"operation_id": operation_id, "python": "pass"}
    )
    _, duplicate = control.handle_http_request(
        "POST", "/v1/run", {"operation_id": operation_id, "python": "pass"}
    )
    _, invalid = control.handle_http_request(
        "POST", "/v1/run", {"operation_id": "not-a-guid", "python": "pass"}
    )

    assert first["ok"] is True
    assert duplicate["failure_code"] == "OPERATION_ID_CONFLICT"
    assert invalid["failure_code"] == "OPERATION_ID_INVALID"


def test_operation_registry_evicts_completed_but_never_running(monkeypatch) -> None:
    first = "7a6f578a-bbe2-45ec-b63f-1b128ea4758f"
    second = "de82babc-4330-4336-bf91-1736561af84d"
    third = "60c67af9-a358-4e60-87dc-28c54687364e"
    control._reset_tracked_operations()
    monkeypatch.setattr(control, "MAX_TRACKED_OPERATIONS", 2)

    assert control._begin_tracked_operation(first, command="run") is None
    control._complete_tracked_operation(first, {"ok": True, "result": {"value": 1}})
    assert control._begin_tracked_operation(second, command="run") is None
    assert control._begin_tracked_operation(third, command="run") is None
    assert control._tracked_operation_snapshot(first) is None
    assert control._tracked_operation_snapshot(second)["state"] == "running"
    assert control._tracked_operation_snapshot(third)["state"] == "running"

    fourth = "6a288844-0d9a-4b39-a4c5-d8abc20e8764"
    full = control._begin_tracked_operation(fourth, command="run")
    assert full is not None
    assert full["failure_code"] == "OPERATION_REGISTRY_FULL"


def test_development_token_acl_is_current_user_only_and_exact_path(
    tmp_path, monkeypatch
) -> None:
    repo = (tmp_path / "checkout").resolve()
    home = (repo / ".vibecad-dev" / "agent").resolve()
    secured = []
    monkeypatch.setenv(control.DEV_MODE_ENV, "1")
    monkeypatch.setenv(control.DEV_SOURCE_ROOT_ENV, str(repo))
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(home))
    monkeypatch.setattr(control.sys, "platform", "win32")
    monkeypatch.setattr(
        control,
        "_enforce_windows_current_user_only_acl",
        lambda path: secured.append(path.resolve()),
    )

    control.load_or_create_token()

    assert secured == [home, (home / control.TOKEN_FILENAME).resolve()]


def test_development_token_acl_failure_is_fail_closed(tmp_path, monkeypatch) -> None:
    repo = (tmp_path / "checkout").resolve()
    monkeypatch.setenv(control.DEV_MODE_ENV, "1")
    monkeypatch.setenv(control.DEV_SOURCE_ROOT_ENV, str(repo))
    monkeypatch.setenv(
        control.AGENT_HOME_ENV,
        str(repo / ".vibecad-dev" / "agent"),
    )
    monkeypatch.setattr(control.sys, "platform", "win32")

    def refuse(_path):
        raise OSError("ACL unavailable")

    monkeypatch.setattr(control, "_enforce_windows_current_user_only_acl", refuse)

    with pytest.raises(RuntimeError, match="current-user-only ACL"):
        control.load_or_create_token()


def test_development_acl_refuses_agent_home_outside_checkout_scope(
    tmp_path, monkeypatch
) -> None:
    repo = (tmp_path / "checkout").resolve()
    outside = (tmp_path / "shared-directory").resolve()
    monkeypatch.setenv(control.DEV_MODE_ENV, "1")
    monkeypatch.setenv(control.DEV_SOURCE_ROOT_ENV, str(repo))
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(outside))
    monkeypatch.setattr(control.sys, "platform", "win32")

    with pytest.raises(RuntimeError, match="checkout-scoped"):
        control.load_or_create_token()
    assert not outside.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_windows_acl_implementation_applies_and_verifies_current_user_only(
    tmp_path,
) -> None:
    private_home = tmp_path / "private-agent-home"
    private_home.mkdir()
    token = private_home / "token"
    token.write_text("secret\n", encoding="utf-8")

    control._enforce_windows_current_user_only_acl(private_home)
    control._enforce_windows_current_user_only_acl(token)


def test_normal_compatibility_startup_needs_no_development_attestation(
    tmp_path, monkeypatch
) -> None:
    secured = []
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(control.sys, "platform", "win32")
    monkeypatch.setattr(
        control,
        "_enforce_windows_current_user_only_acl",
        lambda path: secured.append(path.resolve()),
    )

    assert control.development_runtime_identity() is None
    token = control.load_or_create_token()

    assert control.load_token() == token
    assert secured == []


def test_attested_server_missing_receipt_fails_before_token_or_listener(
    tmp_path, monkeypatch
) -> None:
    touched = []
    monkeypatch.setenv(control.DEV_MODE_ENV, "1")
    monkeypatch.setenv(control.DEV_ATTESTATION_REQUIRED_ENV, "1")
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(
        control,
        "_bind_listener",
        lambda *_args, **_kwargs: touched.append("listener"),
    )
    control.shutdown_server(wait=True)

    with pytest.raises(RuntimeError, match="receipt values are missing"):
        control.ensure_fail_closed_server_started(
            document_thread_dispatch=lambda operation: operation(),
            port=0,
        )

    assert touched == []
    assert not control.token_path().exists()


def test_fail_closed_server_refuses_non_loopback_host_without_binding(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    touched: list[tuple[str, int]] = []
    monkeypatch.setattr(
        control,
        "_bind_listener",
        lambda host, port: touched.append((host, port)),
    )

    with pytest.raises(RuntimeError, match="only to 127.0.0.1"):
        control.ensure_fail_closed_server_started(
            document_thread_dispatch=lambda operation: operation(),
            host="0.0.0.0",
            port=0,
        )

    assert control.server_snapshot()["running"] is False
    assert touched == []


def test_legacy_server_starter_preserves_explicit_host_compatibility(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    control.shutdown_server(wait=True)
    touched: list[tuple[str, int]] = []

    def refuse_after_policy_boundary(host: str, port: int):
        touched.append((host, port))
        raise OSError("intentional test stop")

    monkeypatch.setattr(control, "_bind_listener", refuse_after_policy_boundary)

    with pytest.raises(RuntimeError, match="could not bind"):
        control.ensure_server_started(host="192.0.2.10", port=48766)

    assert touched == [("192.0.2.10", 48766)]
    assert control.server_snapshot()["running"] is False


def test_only_fail_closed_server_rejects_non_loopback_clients() -> None:
    strict_server = SimpleNamespace(vibecad_fail_closed=True)
    legacy_server = SimpleNamespace(vibecad_fail_closed=False)

    assert control._server_accepts_client(strict_server, "127.0.0.1") is True
    assert control._server_accepts_client(strict_server, "192.0.2.20") is False
    assert control._server_accepts_client(legacy_server, "192.0.2.20") is True


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
    application = SimpleNamespace(focus=None, active_window=window, popup=None)

    class FocusWidget:
        def setFocus(self, _reason=None) -> None:  # noqa: N802
            application.focus = self

    focus_widget = FocusWidget()
    application.focus = focus_widget
    cursor_samples = iter(Point(x, y) for x, y in cursor_positions)
    qt_core = SimpleNamespace(
        Qt=SimpleNamespace(
            LeftButton="left",
            NoModifier="none",
            OtherFocusReason="other",
        )
    )
    qt_gui = SimpleNamespace(QCursor=SimpleNamespace(pos=lambda: next(cursor_samples)))
    qt_widgets = SimpleNamespace(
        QTabBar=object,
        QApplication=SimpleNamespace(
            processEvents=lambda: None,
            focusWidget=lambda: application.focus,
            activeWindow=lambda: application.active_window,
            activePopupWidget=lambda: application.popup,
        ),
    )
    clicks: list[tuple[object, object, object, object]] = []

    class QTest:
        @staticmethod
        def mouseClick(widget, button, modifiers, point) -> None:  # noqa: N802
            clicks.append((widget, button, modifiers, point))
            widget.current = 1
            application.focus = widget

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
    assert payload["focus_restored"] is True
    assert payload["active_window_unchanged"] is True
    assert payload["popup_restored"] is True
    assert payload["interaction_restored"] is True
    assert application.focus is focus_widget
    assert len(clicks) == 1


def test_ui_menu_click_closes_popup_and_restores_focus(monkeypatch) -> None:
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
            application.popup = None

        def popup(self, _point: Point) -> None:
            self.visible = True
            application.popup = self
            application.focus = self

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
    previous_action = object()

    class MenuBar:
        active_action = previous_action

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def actions(self) -> list[Action]:
            return [action]

        def actionGeometry(self, _action: Action) -> Rect:  # noqa: N802
            return Rect()

        def mapToGlobal(self, point: Point) -> Point:  # noqa: N802
            return point

        def activeAction(self):  # noqa: N802
            return self.active_action

        def setActiveAction(self, selected) -> None:  # noqa: N802
            self.active_action = selected

    menu_bar = MenuBar()
    window = SimpleNamespace(menuBar=lambda: menu_bar)

    class FocusWidget:
        def setFocus(self, _reason=None) -> None:  # noqa: N802
            application.focus = self

    focus_widget = FocusWidget()
    application = SimpleNamespace(
        focus=focus_widget,
        active_window=window,
        popup=None,
    )
    qt_core = SimpleNamespace(
        Qt=SimpleNamespace(
            LeftButton="left",
            NoModifier="none",
            OtherFocusReason="other",
        ),
        QPoint=Point,
    )
    qt_gui = SimpleNamespace(QCursor=SimpleNamespace(pos=lambda: Point(700, 500)))
    qt_widgets = SimpleNamespace(
        QTabBar=object,
        QApplication=SimpleNamespace(
            processEvents=lambda: None,
            focusWidget=lambda: application.focus,
            activeWindow=lambda: application.active_window,
            activePopupWidget=lambda: application.popup,
        ),
    )

    preview_waits: list[int] = []

    class QTest:
        @staticmethod
        def mouseClick(_widget, _button, _modifiers, _point) -> None:  # noqa: N802
            menu.visible = True

        @staticmethod
        def qWait(milliseconds: int) -> None:  # noqa: N802
            preview_waits.append(milliseconds)

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

    preexisting_popup = object()
    application.popup = preexisting_popup
    busy = control.dispatch("ui_click", {"kind": "menu", "text": "File"})
    assert busy["failure_code"] == "UI_INTERACTION_BUSY"
    assert application.popup is preexisting_popup
    assert application.focus is focus_widget
    assert menu.visible is False
    assert menu_bar.active_action is previous_action
    assert preview_waits == []
    application.popup = None

    payload = control.dispatch("ui_click", {"kind": "menu", "text": "File"})
    assert payload["ok"] is True
    assert payload["click_queued"] is False
    assert payload["semantic_verified"] is True
    assert payload["input_method"] == "qt_in_process_menu_popup"
    assert payload["menu_visible"] is True
    assert payload["menu_open_after"] is False
    assert payload["focus_restored"] is True
    assert payload["active_window_unchanged"] is True
    assert payload["popup_restored"] is True
    assert payload["interaction_restored"] is True
    assert payload["preview_duration_milliseconds"] == 240
    assert preview_waits == [240]
    assert menu.visible is False
    assert menu_bar.active_action is previous_action
    assert application.focus is focus_widget
    assert application.popup is None


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


def test_preferences_route_tracks_requested_operation_id(monkeypatch) -> None:
    operation_id = "a26df2bf-6f00-4f2f-8382-da206589c286"
    calls = []

    def fake_dispatch(command, arguments=None, **_kwargs):
        calls.append((command, dict(arguments or {})))
        return {"ok": True, "opened": "VibeCAD"}

    monkeypatch.setattr(control, "dispatch", fake_dispatch)
    status, payload = control.handle_http_request(
        "POST",
        "/v1/preferences",
        {"operation_id": operation_id},
    )

    assert status == 200
    assert payload["operation_id"] == operation_id
    assert calls == [("preferences", {})]
    operation = control._tracked_operation_snapshot(operation_id)
    assert operation is not None
    assert operation["state"] == "completed"


def test_status_response_uses_immutable_listener_identity(monkeypatch) -> None:
    old_identity = {
        "server_instance_id": "old-listener-instance",
        "process_id": 1234,
        "server_started_at_utc": "2026-08-29T20:00:00.000000Z",
        "runtime_identity": {"commit": "old"},
    }
    monkeypatch.setattr(control, "_server_instance_id", "new-listener-instance")
    monkeypatch.setattr(control, "_server_started_at_utc", "2026-08-29T21:00:00.000000Z")
    monkeypatch.setattr(control, "_active_runtime_identity", {"commit": "new"})
    monkeypatch.setattr(control, "report_status", lambda: {"ok": True})

    status, payload = control.handle_http_request(
        "GET",
        "/v1/status",
        server_instance_id=old_identity["server_instance_id"],
        server_identity=old_identity,
    )

    assert status == 200
    assert {
        name: payload[name]
        for name in (
            "server_instance_id",
            "process_id",
            "server_started_at_utc",
            "runtime_identity",
        )
    } == old_identity


@pytest.mark.parametrize(
    "disconnect_error",
    (
        BrokenPipeError(32, "client closed the pipe"),
        ConnectionResetError(10054, "client reset the connection"),
        ConnectionAbortedError(10053, "client aborted the connection"),
    ),
)
def test_http_response_treats_client_disconnect_as_normal_completion(
    disconnect_error,
) -> None:
    handler = object.__new__(control._AgentRequestHandler)
    handler.close_connection = False
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    class _DisconnectedWriter:
        def write(self, _raw):
            raise disconnect_error

    handler.wfile = _DisconnectedWriter()

    handler._write_json(200, {"ok": True})

    assert handler.close_connection is True


def test_http_response_does_not_hide_unrelated_write_failures() -> None:
    handler = object.__new__(control._AgentRequestHandler)
    handler.close_connection = False
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    class _FailingWriter:
        def write(self, _raw):
            raise OSError(5, "unexpected response write failure")

    handler.wfile = _FailingWriter()

    with pytest.raises(OSError, match="unexpected response write failure"):
        handler._write_json(200, {"ok": True})

    assert handler.close_connection is False


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


def test_server_restart_rotates_instance_and_clears_operation_evidence(
    tmp_path, monkeypatch
) -> None:
    operation_id = "4854d42e-e928-4aaa-9b84-4d0e9e89d105"
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(control, "_document_thread_dispatch", None)
    control.shutdown_server(wait=True)

    control.ensure_server_started(port=0)
    try:
        first_endpoint = json.loads(control.endpoint_path().read_text(encoding="utf-8"))
        assert control._begin_tracked_operation(operation_id, command="run") is None
        control._complete_tracked_operation(
            operation_id,
            {"ok": True, "result": {"completed": True}},
        )
        assert control._tracked_operation_snapshot(operation_id) is not None
    finally:
        control.shutdown_server(wait=True)

    control.ensure_server_started(port=0)
    try:
        second_endpoint = json.loads(control.endpoint_path().read_text(encoding="utf-8"))
        assert second_endpoint["server_instance_id"] != first_endpoint["server_instance_id"]
        status, payload = control.handle_http_request(
            "GET",
            f"/v1/operations/{operation_id}",
        )
        assert status == 404
        assert payload["failure_code"] == "OPERATION_NOT_FOUND"
    finally:
        control.shutdown_server(wait=True)


def test_stale_completion_cannot_overwrite_reused_id_after_server_restart() -> None:
    operation_id = "e910de5a-fb3e-42a2-b2c0-18b36fe41edc"
    old_instance = "old-server-instance"
    new_instance = "new-server-instance"
    control._reset_tracked_operations()

    assert (
        control._begin_tracked_operation(
            operation_id,
            command="run",
            server_instance_id=old_instance,
        )
        is None
    )
    control._reset_tracked_operations()
    assert (
        control._begin_tracked_operation(
            operation_id,
            command="run",
            server_instance_id=new_instance,
        )
        is None
    )

    control._complete_tracked_operation(
        operation_id,
        {"ok": True, "result": {"source": "stale"}},
        server_instance_id=old_instance,
    )

    operation = control._tracked_operation_snapshot(operation_id)
    assert operation is not None
    assert operation["server_instance_id"] == new_instance
    assert operation["state"] == "running"
    assert operation["response"] is None


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
            assert payload["fail_closed"] is True

            operation_id = "20158312-5c69-4822-a720-d87630f97d0a"
            monkeypatch.setattr(
                control,
                "run_script",
                lambda **_kwargs: {"ok": True, "result": {"completed": True}},
            )
            run_request = request.Request(
                f"http://127.0.0.1:{port}/v1/run",
                data=json.dumps(
                    {"operation_id": operation_id, "python": "result = True"}
                ).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with request.urlopen(run_request, timeout=2) as response:
                run_payload = json.loads(response.read().decode("utf-8"))
            assert run_payload["operation_id"] == operation_id

            operation_request = request.Request(
                f"http://127.0.0.1:{port}/v1/operations/{operation_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            with request.urlopen(operation_request, timeout=2) as response:
                operation_payload = json.loads(response.read().decode("utf-8"))
            assert operation_payload["operation"]["state"] == "completed"
            assert operation_payload["operation"]["result"] == {"completed": True}
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


def test_cli_recovers_timed_out_mutation_by_operation_id_without_redispatch(
    monkeypatch,
) -> None:
    operation_id = "f2c628b5-68c4-4d45-b4c2-313edcf5498d"
    requests: list[request.Request] = []

    class Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def close(self) -> None:
            return None

    def fake_urlopen(http_request, timeout):
        requests.append(http_request)
        if len(requests) == 1:
            raise TimeoutError("response deadline expired")
        assert http_request.full_url.endswith(f"/v1/operations/{operation_id}")
        return Response(
            {
                "ok": True,
                "operation": {
                    "operation_id": operation_id,
                    "server_instance_id": "server-a",
                    "state": "completed",
                    "response": {
                        "ok": True,
                        "operation_id": operation_id,
                        "result": {"saved": True},
                    },
                },
            }
        )

    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://127.0.0.1:8766", "secret-token", "server-a", True),
    )
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: cli.uuid.UUID(operation_id))
    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        cli,
        "call_local",
        lambda *_args, **_kwargs: pytest.fail("ambiguous mutation was redispatched"),
    )

    payload = cli.call_http(
        "save",
        {"document": "Part"},
        timeout_seconds=0.01,
    )

    assert payload == {
        "ok": True,
        "operation_id": operation_id,
        "result": {"saved": True},
    }
    posted = json.loads(requests[0].data.decode("utf-8"))
    assert posted == {"document": "Part", "operation_id": operation_id}
    assert requests[1].get_header("Authorization") == "Bearer secret-token"


def test_cli_never_redispatches_ambiguous_mutation_without_completion_proof(
    monkeypatch,
) -> None:
    operation_id = "26ad2b3f-2fe6-46a4-86de-bb2623ad5cb5"
    local_calls: list[str] = []

    def fake_urlopen(http_request, timeout):
        if http_request.get_method() == "POST":
            raise ConnectionResetError("peer reset after request transmission")
        return SimpleNamespace(
            read=lambda: json.dumps(
                {
                    "ok": True,
                    "operation": {
                        "operation_id": operation_id,
                        "server_instance_id": "server-a",
                        "state": "running",
                        "response": None,
                    },
                }
            ).encode("utf-8"),
            close=lambda: None,
        )

    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://127.0.0.1:8766", "secret-token", "server-a", True),
    )
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: cli.uuid.UUID(operation_id))
    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        cli,
        "call_local",
        lambda command, _arguments: local_calls.append(command) or {"ok": True},
    )
    args = cli.build_parser().parse_args(["save", "--document", "Part"])

    payload = cli.execute(args)

    assert payload["ok"] is False
    assert payload["failure_code"] == "REMOTE_OUTCOME_UNRESOLVED"
    assert payload["operation_id"] == operation_id
    assert payload["operation_state"] == "running"
    assert local_calls == []


def test_cli_keeps_local_fallback_for_proven_connection_refusal(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://127.0.0.1:8766", "secret-token", "server-a", True),
    )
    monkeypatch.setattr(
        cli.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            error.URLError(ConnectionRefusedError(10061, "connection refused"))
        ),
    )
    local_calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        cli,
        "call_local",
        lambda command, arguments: local_calls.append((command, arguments))
        or {"ok": True, "via": "local"},
    )
    args = cli.build_parser().parse_args(["save", "--document", "Part"])

    payload = cli.execute(args)

    assert payload == {"ok": True, "via": "local"}
    assert local_calls == [("save", {"document": "Part"})]


def test_cli_refuses_non_loopback_endpoint_before_attaching_token(monkeypatch) -> None:
    opened: list[request.Request] = []
    monkeypatch.setattr(
        cli,
        "_control_module",
        lambda: SimpleNamespace(
            AGENT_HOST="127.0.0.1",
            load_endpoint=lambda: {
                "host": "attacker.example",
                "port": 443,
                "base_url": "https://attacker.example",
                "fail_closed": True,
                "server_instance_id": "forged-server",
            },
            configured_port=lambda: 8766,
            load_token=lambda: "secret-token",
            load_or_create_token=lambda: "secret-token",
        ),
    )
    monkeypatch.setattr(
        cli.request,
        "urlopen",
        lambda http_request, **_kwargs: opened.append(http_request),
    )

    payload = cli.call_http("status", {})

    assert payload is not None
    assert payload["ok"] is False
    assert payload["failure_code"] == "ENDPOINT_NOT_LOOPBACK"
    assert opened == []


def test_cli_preserves_explicit_host_compatibility_endpoint(monkeypatch) -> None:
    opened: list[request.Request] = []
    monkeypatch.setattr(
        cli,
        "_control_module",
        lambda: SimpleNamespace(
            AGENT_HOST="127.0.0.1",
            load_endpoint=lambda: {
                "host": "192.0.2.10",
                "port": 8766,
                "base_url": "http://192.0.2.10:8766",
                "fail_closed": False,
                "server_instance_id": "compatibility-server",
            },
            configured_port=lambda: 8766,
            load_token=lambda: "secret-token",
            load_or_create_token=lambda: "secret-token",
        ),
    )

    def fake_urlopen(http_request, **_kwargs):
        opened.append(http_request)
        return SimpleNamespace(
            read=lambda: b'{"ok":true}',
            close=lambda: None,
        )

    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)

    assert cli.call_http("status", {}) == {"ok": True}
    assert len(opened) == 1
    assert opened[0].full_url == "http://192.0.2.10:8766/v1/status"
    assert opened[0].get_header("Authorization") == "Bearer secret-token"


def test_cli_preserves_legacy_compatibility_base_url(monkeypatch) -> None:
    opened: list[request.Request] = []
    monkeypatch.setattr(
        cli,
        "_control_module",
        lambda: SimpleNamespace(
            AGENT_HOST="127.0.0.1",
            load_endpoint=lambda: {
                "host": "gateway.example",
                "port": 443,
                "base_url": "https://gateway.example/vibecad-control",
                "fail_closed": False,
            },
            configured_port=lambda: 8766,
            load_token=lambda: "secret-token",
            load_or_create_token=lambda: "secret-token",
        ),
    )
    monkeypatch.setattr(
        cli.request,
        "urlopen",
        lambda http_request, **_kwargs: opened.append(http_request)
        or SimpleNamespace(read=lambda: b'{"ok":true}', close=lambda: None),
    )

    assert cli.call_http("status", {}) == {"ok": True}
    assert opened[0].full_url == (
        "https://gateway.example/vibecad-control/v1/status"
    )


def test_cli_compatibility_timeout_preserves_local_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://192.0.2.10:8766", "secret-token", "", False),
    )
    monkeypatch.setattr(
        cli.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("compatibility endpoint timed out")
        ),
    )

    assert cli.call_http("save", {"document": "Part"}) is None


def test_cli_compatibility_post_preserves_original_payload(monkeypatch) -> None:
    opened: list[request.Request] = []
    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://192.0.2.10:8766", "secret-token", "", False),
    )

    def fake_urlopen(http_request, **_kwargs):
        opened.append(http_request)
        return SimpleNamespace(read=lambda: b'{"ok":true}', close=lambda: None)

    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)

    arguments = {"document": "Part", "path": "C:/models/part.FCStd"}
    assert cli.call_http("save-as", arguments) == {"ok": True}
    assert len(opened) == 1
    assert opened[0].get_header("Content-type") == "application/json"
    assert json.loads(opened[0].data.decode("utf-8")) == arguments
    assert "operation_id" not in json.loads(opened[0].data.decode("utf-8"))


@pytest.mark.parametrize(
    "endpoint",
    (
        {
            "host": "127.0.0.1",
            "port": "not-a-port",
            "base_url": "http://127.0.0.1:8766",
            "fail_closed": True,
        },
        {
            "host": "127.0.0.1",
            "port": 8766,
            "base_url": "http://127.0.0.1:8766/credential-relay",
            "fail_closed": True,
        },
        {
            "host": "localhost",
            "port": 8766,
            "base_url": "http://127.0.0.1:8766",
            "fail_closed": True,
        },
        {
            "host": "localhost",
            "port": 8766,
            "base_url": "http://localhost:8766",
            "fail_closed": True,
        },
        {
            "host": "127.0.0.1",
            "port": 8766,
            "base_url": "http://user@127.0.0.1:8766",
            "fail_closed": True,
        },
    ),
)
def test_cli_rejects_malformed_endpoint_before_reading_token(
    endpoint, monkeypatch
) -> None:
    token_reads: list[str] = []
    monkeypatch.setattr(
        cli,
        "_control_module",
        lambda: SimpleNamespace(
            AGENT_HOST="127.0.0.1",
            load_endpoint=lambda: endpoint,
            configured_port=lambda: 8766,
            load_token=lambda: token_reads.append("load") or "secret-token",
            load_or_create_token=lambda: token_reads.append("create")
            or "secret-token",
        ),
    )

    payload = cli.call_http("status", {})

    assert payload is not None
    assert payload["failure_code"] == "ENDPOINT_NOT_LOOPBACK"
    assert token_reads == []


def test_cli_refuses_completed_operation_from_another_server_instance(
    monkeypatch,
) -> None:
    operation_id = "927e2d40-67c6-4d17-965b-fd90d2cde908"
    requests: list[request.Request] = []

    def fake_urlopen(http_request, timeout):
        requests.append(http_request)
        if http_request.get_method() == "POST":
            raise ConnectionResetError("ambiguous response")
        return SimpleNamespace(
            read=lambda: json.dumps(
                {
                    "ok": True,
                    "operation": {
                        "operation_id": operation_id,
                        "server_instance_id": "server-b",
                        "state": "completed",
                        "response": {
                            "ok": True,
                            "operation_id": operation_id,
                        },
                    },
                }
            ).encode("utf-8"),
            close=lambda: None,
        )

    monkeypatch.setattr(
        cli,
        "_endpoint_context",
        lambda: ("http://127.0.0.1:8766", "secret-token", "server-a", True),
    )
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: cli.uuid.UUID(operation_id))
    monkeypatch.setattr(cli.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    payload = cli.call_http("save", {"document": "Part"})

    assert payload is not None
    assert payload["failure_code"] == "REMOTE_OUTCOME_UNRESOLVED"
    assert payload["operation_id"] == operation_id
    assert payload["operation_state"] == "completed"
    assert "server instance changed" in payload["error"]
    assert len(requests) == 1 + cli.OPERATION_POLL_ATTEMPTS


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
