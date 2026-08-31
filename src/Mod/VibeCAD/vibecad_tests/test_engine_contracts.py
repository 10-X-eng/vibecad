# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for VibeScript authoring and project-selection migration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[4]


def test_release_paths_purge_retired_authoring_artifacts() -> None:
    purge_name = "purge_vibecad_retired_authoring_artifacts.sh"
    purge = (
        ROOT / "package/rattler-build/scripts" / purge_name
    ).read_text(encoding="utf-8")
    for retired_path in (
        "VibeCADBuild123d.py",
        "VibeCADOpenSCAD.py",
        "build123d_runtime",
        "openscad_runtime",
        '$(dirname "${module_directory}")/OpenSCAD',
    ):
        assert retired_path in purge
    assert "Retired VibeCAD authoring artifact remains" in purge

    release_scripts = (
        "package/rattler-build/scripts/build_vibecad_local_release.sh",
        "package/rattler-build/linux/create_bundle.sh",
        "package/rattler-build/osx/create_bundle.sh",
        "package/rattler-build/windows/create_bundle.sh",
    )
    for relative_path in release_scripts:
        assert purge_name in (ROOT / relative_path).read_text(encoding="utf-8")

    linux_bundle = (
        ROOT / "package/rattler-build/linux/create_bundle.sh"
    ).read_text(encoding="utf-8")
    assert 'rm -rf -- "AppDir"' in linux_bundle


def test_linux_bundle_smokes_python_dependencies_independently() -> None:
    linux_bundle = (
        ROOT / "package/rattler-build/linux/create_bundle.sh"
    ).read_text(encoding="utf-8")

    assert "for dependency in" in linux_bundle
    for dependency in (
        "anthropic",
        "keyring",
        "jsonschema",
        "mcp",
        "mcp_types",
        "openai",
        "secretstorage",
        "keyring.backends.SecretService",
    ):
        assert dependency in linux_bundle
    assert "importlib.import_module('${dependency}')" in linux_bundle
    assert "importlib.util.find_spec('agents') is None" in linux_bundle


def test_macos_runtime_validator_imports_importlib_util_explicitly() -> None:
    validator = (
        ROOT / "package/rattler-build/scripts/validate_vibecad_macos_runtime.py"
    ).read_text(encoding="utf-8")

    assert "import importlib.util" in validator
    assert "importlib.util.find_spec(module_name)" in validator


class TestStageAwareFailureRendering:
    @staticmethod
    def _gui():
        import VibeCADGui

        return VibeCADGui

    @pytest.mark.parametrize(
        "stage",
        ("schema", "surface", "edit_state", "precondition"),
    )
    def test_pre_execution_stages_render_as_rejected(self, stage: str) -> None:
        text = self._gui()._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "tool_name": "vibescript.edit_source",
                "result": {"error": "bad input", "failure_stage": stage},
            }
        )
        assert "rejected before execution" in text
        assert stage in text
        assert "rolled back" not in text

    @pytest.mark.parametrize(
        "stage",
        ("native_call", "native_recompute", "postcondition"),
    )
    def test_execution_stages_render_as_rolled_back(self, stage: str) -> None:
        text = self._gui()._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "tool_name": "vibescript.edit_source",
                "result": {"error": "build failed", "failure_stage": stage},
            }
        )
        assert "failed during execution, rolled back" in text
        assert stage in text
        assert "rejected" not in text

    def test_external_process_stage_reports_unchanged_document(self) -> None:
        text = self._gui()._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "result": {
                    "error": "worker exited",
                    "failure_stage": "external_process",
                },
            }
        )
        assert "external process" in text
        assert "document unchanged" in text

    @pytest.mark.parametrize("result", ({"error": "no stage"}, {}, None, "bad"))
    def test_missing_stage_degrades_to_blocked(self, result) -> None:
        text = self._gui()._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "tool_name": "vibescript.edit_source",
                "result": result,
            }
        )
        assert "blocked" in text

    def test_successful_call_still_renders_ok(self) -> None:
        text = self._gui()._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": True,
                "result": {"title": "Updated model"},
            }
        )
        assert "ok" in text
        assert "blocked" not in text

    def test_every_declared_failure_stage_has_specific_rendering(self) -> None:
        import VibeCADTools

        gui = self._gui()
        covered = (
            gui._PRE_EXECUTION_FAILURE_STAGES
            | gui._ROLLED_BACK_FAILURE_STAGES
            | {"external_process"}
        )
        assert covered == VibeCADTools.FAILURE_STAGES


class TestAnalyzeContextStatusRendering:
    @staticmethod
    def _gui():
        import VibeCADGui

        return VibeCADGui

    def test_analyze_capture_progress_reports_phase_count_and_percent(self) -> None:
        gui = self._gui()
        event = {
            "event": "analyze_context_progress",
            "percent": 45,
            "message": "Analyzing objects 24 of 80",
        }

        assert gui._format_progress_event(event) == "Analyzing objects 24 of 80"
        assert gui._progress_event_should_update_status(event) is True

    def test_analyze_cache_hit_is_visible_in_the_status_line(self) -> None:
        gui = self._gui()
        event = {
            "event": "analyze_context_cache_hit",
            "structural_revision": 12,
        }

        assert gui._format_progress_event(event) == (
            "Analyze context is ready for document revision 12."
        )
        assert gui._progress_event_should_update_status(event) is True

    def test_drawing_catalog_progress_is_visible_in_the_status_line(self) -> None:
        gui = self._gui()
        progress = {
            "event": "drawing_context_progress",
            "percent": 45,
            "message": "Reading Drawing sources 24 of 80",
        }
        ready = {
            "event": "drawing_context_ready",
            "structural_revision": 12,
        }

        assert gui._format_progress_event(progress) == (
            "Reading Drawing sources 24 of 80"
        )
        assert gui._format_progress_event(ready) == (
            "Drawing context is ready for document revision 12."
        )
        assert gui._progress_event_should_update_status(progress) is True
        assert gui._progress_event_should_update_status(ready) is True

    def test_analyze_progress_reaches_the_application_status_bar(
        self,
        monkeypatch,
    ) -> None:
        gui = self._gui()
        shown: list[tuple[str, int]] = []
        status_bar = SimpleNamespace(
            showMessage=lambda text, timeout=0: shown.append((text, timeout))
        )
        main_window = SimpleNamespace(statusBar=lambda: status_bar)
        monkeypatch.setattr(
            gui.Gui,
            "getMainWindow",
            lambda: main_window,
            raising=False,
        )

        gui._show_analyze_context_application_status(
            {
                "event": "analyze_context_progress",
                "percent": 45,
                "message": "Analyzing objects 24 of 80",
            },
            "Analyzing objects 24 of 80",
        )
        gui._show_analyze_context_application_status(
            {
                "event": "analyze_context_ready",
                "structural_revision": 12,
            },
            "Analyze context is ready for document revision 12.",
        )

        assert shown == [
            ("Analyzing objects 24 of 80", 0),
            ("Analyze context is ready for document revision 12.", 5000),
        ]


def test_private_vibescript_carriers_are_not_provider_document_objects() -> None:
    from VibeCADCore import VibeCADService

    for role in ("implementation", "publication_target", "parameters"):
        assert VibeCADService._is_private_scripted_object(
            SimpleNamespace(VibeCADScriptedRole=role)
        )
    for role in ("model", "publication", ""):
        assert not VibeCADService._is_private_scripted_object(
            SimpleNamespace(VibeCADScriptedRole=role)
        )

    native = SimpleNamespace(Name="Native", Label="Native", TypeId="Part::Box")
    published = SimpleNamespace(
        Name="Published",
        Label="Published",
        TypeId="App::Link",
        VibeCADScriptedRole="publication",
        VibeCADScriptedEngine="vibescript",
        VibeCADScriptedModelId="a" * 32,
        VibeCADScriptedOutputKey="Housing",
    )
    private_target = SimpleNamespace(
        Name="PrivateTarget",
        Label="Private Target",
        TypeId="Part::Feature",
        VibeCADScriptedRole="publication_target",
    )
    service = object.__new__(VibeCADService)
    service._active_document = lambda: SimpleNamespace(
        Name="ContextDoc",
        Objects=[native, published, private_target],
    )

    summary = service.provider_part_summary()

    assert [item["name"] for item in summary["objects"]] == ["Native", "Published"]
    assert summary["objects"][1]["published_output_key"] == "Housing"


def test_view_attachment_is_one_shot_and_identity_guarded() -> None:
    from VibeCADCore import VibeCADService

    service = object.__new__(VibeCADService)
    service._last_view_screenshot = {
        "captured": True,
        "path": "/project/screenshots/current.png",
        "pending_attachment": True,
    }

    pending = service.view_screenshot_summary()
    stale = service.consume_view_screenshot_attachment(
        {"captured": True, "path": "/project/screenshots/older.png"}
    )
    assert stale["consumed"] is False
    assert service.view_screenshot_summary()["captured"] is True

    consumed = service.consume_view_screenshot_attachment(pending)
    assert consumed == {
        "consumed": True,
        "path": "/project/screenshots/current.png",
    }
    assert service.view_screenshot_summary() == {"captured": False, "path": None}


class _UnsetPreferences:
    def GetBool(self, name: str, default: bool = False) -> bool:
        return default

    def GetString(self, name: str, default: str = "") -> str:
        return default

    def GetFloat(self, name: str, default: float = 0.0) -> float:
        return default

    def GetInt(self, name: str, default: int = 0) -> int:
        return default


class _RecordingPreferences(_UnsetPreferences):
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def SetBool(self, name: str, value: bool) -> None:
        self.values[name] = bool(value)

    def SetString(self, name: str, value: str) -> None:
        self.values[name] = str(value)

    def SetFloat(self, name: str, value: float) -> None:
        self.values[name] = float(value)

    def SetInt(self, name: str, value: int) -> None:
        self.values[name] = int(value)

    def RemBool(self, name: str) -> None:
        self.values.pop(name, None)

    def RemString(self, name: str) -> None:
        self.values.pop(name, None)

    def RemFloat(self, name: str) -> None:
        self.values.pop(name, None)

    def RemInt(self, name: str) -> None:
        self.values.pop(name, None)


class TestAuthoringModeDefaults:
    _SCOPE = {"project_id": "f" * 32, "title": "Default Test", "document": {}}

    def test_settings_have_no_vibescript_availability_toggle(self) -> None:
        import VibeCADPreferences as prefs

        settings = prefs.VibeCADSettings()
        assert not hasattr(settings, "vibescript_enabled")
        assert settings.new_document_authoring_mode == "ask"

    def test_removed_vibescript_toggle_is_not_written_or_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADPreferences as prefs

        stored = _RecordingPreferences()
        monkeypatch.setattr(prefs, "preferences", lambda: stored)
        prefs.save_settings(prefs.VibeCADSettings())
        assert "VibeScriptEnabled" not in stored.values
        assert stored.values["NewDocumentAuthoringMode"] == "ask"
        prefs.reset_settings()
        assert "VibeScriptEnabled" not in stored.values
        assert "NewDocumentAuthoringMode" not in stored.values

    def test_default_engine_is_vibescript(self) -> None:
        from VibeCADProject import DEFAULT_MODELING_ENGINE, MODELING_ENGINES

        assert MODELING_ENGINES == {"native", "vibescript"}
        assert DEFAULT_MODELING_ENGINE == "vibescript"

    def test_fresh_manifest_seeds_vibescript_engine(self, tmp_path: Path) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        manifest = store._default_manifest(dict(self._SCOPE))
        assert manifest["modeling_engine"] == "vibescript"
        assert "partdesign_engine" not in manifest

    @pytest.mark.parametrize("mode", ("native", "vibescript"))
    def test_merge_preserves_supported_engine(self, tmp_path: Path, mode: str) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        merged = store._merge_manifest_defaults(
            {"modeling_engine": mode},
            dict(self._SCOPE),
        )
        assert merged["modeling_engine"] == mode

    @pytest.mark.parametrize("mode", ("build123d", "openscad", "typo"))
    def test_unsupported_engine_has_actionable_error(
        self, tmp_path: Path, mode: str
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        with pytest.raises(RuntimeError, match="unsupported authoring mode"):
            store._merge_manifest_defaults(
                {"modeling_engine": mode},
                dict(self._SCOPE),
            )

    def test_context_reads_persisted_native_selection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from VibeCADProject import PROJECT_SCHEMA, VibeCADProjectStore

        manifest_path = tmp_path / "project.vibecad.json"
        index_path = tmp_path / "index.db"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": PROJECT_SCHEMA,
                    "version": 2,
                    "project_id": "f" * 32,
                    "title": "Native project",
                    "modeling_engine": "native",
                    "documents": {},
                }
            ),
            encoding="utf-8",
        )
        scope = {
            **self._SCOPE,
            "root": str(tmp_path),
            "manifest_path": str(manifest_path),
            "persistent": True,
            "document_saved": True,
            "index_path": str(index_path),
        }
        store = VibeCADProjectStore("test-session", index_path=index_path)
        monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

        context = store.context()

        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert context["modeling_engine"] == "native"
        assert persisted["modeling_engine"] == "native"

    def test_unsaved_native_selection_is_promoted_on_first_save(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        index_path = tmp_path / "index.db"
        unsaved_root = tmp_path / "unsaved"
        saved_root = tmp_path / "saved"
        scope = {
            **self._SCOPE,
            "root": str(unsaved_root),
            "manifest_path": str(unsaved_root / "project.vibecad.json"),
            "persistent": True,
            "document_saved": False,
            "index_path": str(index_path),
            "document": {
                "document": "Engine",
                "uid": "stable-document-uid",
                "saved": False,
            },
        }
        store = VibeCADProjectStore("test-session", index_path=index_path)
        monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

        assert store.select_modeling_engine("native") == {
            "mode": "native",
            "persistence": "session",
        }
        assert store.context()["modeling_engine"] == "native"
        unsaved_manifest = json.loads(
            Path(scope["manifest_path"]).read_text(encoding="utf-8")
        )
        assert unsaved_manifest["modeling_engine"] == "vibescript"

        scope.update(
            {
                "root": str(saved_root),
                "manifest_path": str(saved_root / "project.vibecad.json"),
                "document_saved": True,
                "document": {
                    "document": "Engine",
                    "uid": "stable-document-uid",
                    "file_path": str(tmp_path / "Engine.FCStd"),
                    "saved": True,
                },
            }
        )
        assert store.modeling_engine() == "native"
        assert store.persist_modeling_engine_after_save() == {
            "mode": "native",
            "persistence": "project",
        }
        saved_manifest = json.loads(
            Path(scope["manifest_path"]).read_text(encoding="utf-8")
        )
        assert saved_manifest["modeling_engine"] == "native"

        reopened = VibeCADProjectStore("new-session", index_path=index_path)
        monkeypatch.setattr(reopened, "project_scope", lambda: dict(scope))
        assert reopened.modeling_engine() == "native"

    def test_newer_manifest_is_read_without_rewrite(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        manifest_path = tmp_path / "project.vibecad.json"
        manifest = {
            "schema": "vibecad-project-v3",
            "version": 3,
            "project_id": "f" * 32,
            "title": "Forward-compatible project",
            "modeling_engine": "vibescript",
            "documents": {"active": {}},
            "newer_extension": {"preserve": True},
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        original = manifest_path.read_bytes()
        index_path = tmp_path / "index.db"
        scope = {
            **self._SCOPE,
            "root": str(tmp_path),
            "manifest_path": str(manifest_path),
            "persistent": True,
            "document_saved": True,
            "index_path": str(index_path),
        }
        store = VibeCADProjectStore("test-session", index_path=index_path)
        monkeypatch.setattr(store, "project_scope", lambda: dict(scope))

        assert store.context()["modeling_engine"] == "vibescript"
        assert manifest_path.read_bytes() == original
