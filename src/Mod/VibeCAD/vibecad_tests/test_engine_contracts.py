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
        "secretstorage",
        "keyring.backends.SecretService",
    ):
        assert dependency in linux_bundle
    assert "importlib.import_module('${dependency}')" in linux_bundle
    assert "importlib.util.find_spec('openai') is None" in linux_bundle
    assert "importlib.util.find_spec('agents') is None" in linux_bundle


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


def test_private_vibescript_carriers_are_not_provider_document_objects() -> None:
    from VibeCADCore import VibeCADService
    from VibeCADWorkbenchTools import get_tool_pack

    for role in ("implementation", "publication_target", "parameters"):
        assert VibeCADService._is_private_scripted_object(
            SimpleNamespace(VibeCADScriptedRole=role)
        )
    for role in ("model", "publication", ""):
        assert not VibeCADService._is_private_scripted_object(
            SimpleNamespace(VibeCADScriptedRole=role)
        )

    part_pack = get_tool_pack("PartWorkbench")
    assert part_pack is not None
    assert VibeCADService._object_matches_pack(
        SimpleNamespace(VibeCADScriptedRole="publication", TypeId="App::Link"),
        part_pack,
    )
    assert not VibeCADService._object_matches_pack(
        SimpleNamespace(
            VibeCADScriptedRole="publication_target",
            TypeId="Part::Feature",
        ),
        part_pack,
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


class TestVibeScriptDefaults:
    _SCOPE = {"project_id": "f" * 32, "title": "Default Test", "document": {}}

    def test_settings_have_no_vibescript_availability_toggle(self) -> None:
        import VibeCADPreferences as prefs

        assert not hasattr(prefs.VibeCADSettings(), "vibescript_enabled")

    def test_removed_vibescript_toggle_is_not_written_or_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADPreferences as prefs

        stored = _RecordingPreferences()
        monkeypatch.setattr(prefs, "preferences", lambda: stored)
        prefs.save_settings(prefs.VibeCADSettings())
        assert "VibeScriptEnabled" not in stored.values
        prefs.reset_settings()
        assert "VibeScriptEnabled" not in stored.values

    def test_default_engine_is_vibescript(self) -> None:
        from VibeCADProject import DEFAULT_MODELING_ENGINE, MODELING_ENGINES

        assert MODELING_ENGINES == {"vibescript"}
        assert DEFAULT_MODELING_ENGINE == "vibescript"

    def test_fresh_manifest_seeds_vibescript_engine(self, tmp_path: Path) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        manifest = store._default_manifest(dict(self._SCOPE))
        assert manifest["modeling_engine"] == "vibescript"
        assert "partdesign_engine" not in manifest

    def test_merge_preserves_vibescript_engine(self, tmp_path: Path) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        merged = store._merge_manifest_defaults(
            {"modeling_engine": "vibescript"},
            dict(self._SCOPE),
        )
        assert merged["modeling_engine"] == "vibescript"

    @pytest.mark.parametrize(
        ("legacy_field", "legacy_engine"),
        (
            ("modeling_engine", "native"),
            ("partdesign_engine", "build123d"),
            ("partdesign_engine", "openscad"),
        ),
    )
    def test_retired_selection_migrates_one_way_to_vibescript(
        self, tmp_path: Path, legacy_field: str, legacy_engine: str
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        merged = store._merge_manifest_defaults(
            {legacy_field: legacy_engine},
            dict(self._SCOPE),
        )
        assert merged["modeling_engine"] == "vibescript"
        assert "partdesign_engine" not in merged

    def test_unknown_engine_has_actionable_error(self, tmp_path: Path) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        with pytest.raises(RuntimeError, match="unsupported modeling engine"):
            store._merge_manifest_defaults(
                {"modeling_engine": "typo"},
                dict(self._SCOPE),
            )

    def test_context_persists_retired_selection_migration(
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
                    "title": "Migrated",
                    "partdesign_engine": "openscad",
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
        assert context["modeling_engine"] == "vibescript"
        assert persisted["modeling_engine"] == "vibescript"
        assert "partdesign_engine" not in persisted

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
