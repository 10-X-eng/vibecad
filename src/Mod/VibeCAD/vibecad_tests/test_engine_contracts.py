# SPDX-License-Identifier: LGPL-2.1-or-later

"""Engine contract tests for the build123d, OpenSCAD, and VibeScript engines.

These tests exercise the pure-Python contract layer of the scripted engines
without requiring a running FreeCAD, an OpenSCAD binary, or the isolated
build123d runtime. Subprocess-facing paths are exercised with stub executables
so the real process-management and failure-payload code runs end to end;
VibeScript worker generation is likewise isolated from the stub live document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import VibeCADBuild123d as build123d
import VibeCADOpenSCAD as openscad
import VibeCADVibeScript as vibescript
import vibescript_executor

MODEL_ID = "a" * 32


# ---------------------------------------------------------------------------
# GUI transcript: stage-aware failure rendering
# ---------------------------------------------------------------------------


class TestStageAwareFailureRendering:
    """Transcript lines state whether a failed call was rejected pre-execution
    or executed and rolled back, based on the payload's failure_stage."""

    @staticmethod
    def _gui():
        import VibeCADGui

        return VibeCADGui

    def test_pre_execution_stages_render_as_rejected(self) -> None:
        gui = self._gui()
        for stage in ("schema", "surface", "edit_state", "precondition"):
            text = gui._format_progress_event(
                {
                    "event": "tool_call_completed",
                    "ok": False,
                    "tool_name": "vibescript.create_model",
                    "result": {"error": "bad input", "failure_stage": stage},
                }
            )
            assert "rejected before execution" in text
            assert stage in text
            assert "rolled back" not in text

    def test_rolled_back_stages_render_as_executed_and_rolled_back(self) -> None:
        gui = self._gui()
        for stage in ("native_call", "native_recompute", "postcondition"):
            text = gui._format_progress_event(
                {
                    "event": "tool_call_completed",
                    "ok": False,
                    "tool_name": "vibescript.create_model",
                    "result": {"error": "recompute failed", "failure_stage": stage},
                }
            )
            assert "failed during execution, rolled back" in text
            assert stage in text
            assert "rejected" not in text

    def test_external_process_stage_renders_document_unchanged(self) -> None:
        gui = self._gui()
        text = gui._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "result": {"error": "worker died", "failure_stage": "external_process"},
            }
        )
        assert "external process" in text
        assert "document unchanged" in text

    def test_missing_stage_degrades_to_blocked(self) -> None:
        gui = self._gui()
        for result in ({"error": "no stage"}, {}, None, "not-a-dict"):
            text = gui._format_progress_event(
                {
                    "event": "tool_call_completed",
                    "ok": False,
                    "tool_name": "vibescript.create_model",
                    "result": result,
                }
            )
            assert "blocked" in text

    def test_unknown_stage_degrades_to_blocked(self) -> None:
        gui = self._gui()
        text = gui._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": False,
                "result": {"error": "x", "failure_stage": "weird_future_stage"},
            }
        )
        assert "blocked" in text

    def test_successful_call_still_renders_ok(self) -> None:
        gui = self._gui()
        text = gui._format_progress_event(
            {
                "event": "tool_call_completed",
                "ok": True,
                "result": {"title": "Created Body"},
            }
        )
        assert "ok" in text
        assert "blocked" not in text

    def test_provider_tool_result_sent_is_stage_aware(self) -> None:
        gui = self._gui()
        rejected = gui._format_progress_event(
            {
                "event": "provider_tool_result_sent",
                "ok": False,
                "tool_name": "vibescript.create_model",
                "error": "schema mismatch",
                "failure_stage": "schema",
            }
        )
        assert "rejected before execution" in rejected
        rolled_back = gui._format_progress_event(
            {
                "event": "provider_tool_result_sent",
                "ok": False,
                "tool_name": "vibescript.create_model",
                "error": "boolean failed",
                "failure_stage": "native_recompute",
            }
        )
        assert "failed during execution, rolled back" in rolled_back
        missing = gui._format_progress_event(
            {
                "event": "provider_tool_result_sent",
                "ok": False,
                "tool_name": "vibescript.create_model",
                "error": "anything",
            }
        )
        assert "blocked" in missing

    def test_every_declared_failure_stage_has_specific_rendering(self) -> None:
        """New stages added to VibeCADTools.FAILURE_STAGES must not silently
        degrade to the generic 'blocked' rendering."""
        import VibeCADTools

        gui = self._gui()
        covered = (
            gui._PRE_EXECUTION_FAILURE_STAGES
            | gui._ROLLED_BACK_FAILURE_STAGES
            | {"external_process"}
        )
        assert covered == VibeCADTools.FAILURE_STAGES


# ---------------------------------------------------------------------------
# build123d: source policy
# ---------------------------------------------------------------------------


class TestBuild123dSourcePolicy:
    def test_valid_source_passes(self) -> None:
        build123d.validate_source(
            "from build123d import Box\nimport math\nresult = Box(1, 2, math.pi)\n"
        )

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("")
        assert excinfo.value.payload["failure_code"] == "SOURCE_REQUIRED"

    def test_oversized_source_rejected(self) -> None:
        big = "# pad\n" * (build123d.MAX_SOURCE_BYTES // 6 + 2)
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source(big)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_TOO_LARGE"
        assert payload["observed"]["source_bytes"] > build123d.MAX_SOURCE_BYTES

    def test_syntax_error_reports_location(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("def broken(:\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_SYNTAX_ERROR"
        assert payload["observed"]["line"] == 1

    def test_disallowed_import_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("import os\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        violations = payload["observed"]["violations"]
        assert violations and "os" in violations[0]["reason"]
        assert payload["required_changes"]

    def test_disallowed_import_from_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("from subprocess import run\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_POLICY_VIOLATION"

    def test_disallowed_call_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("data = open('/etc/passwd').read()\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "open" in item["reason"] for item in payload["observed"]["violations"]
        )

    def test_dunder_access_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("x = (1).__class__\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "__class__" in item["reason"] for item in payload["observed"]["violations"]
        )

    def test_violation_line_numbers_reported(self) -> None:
        source = "import math\nimport socket\n"
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source(source)
        violations = excinfo.value.payload["observed"]["violations"]
        assert violations[0]["line"] == 2


# ---------------------------------------------------------------------------
# build123d: exporter escape hatch
# ---------------------------------------------------------------------------


class TestBuild123dExporterPolicy:
    def test_exporter_symbol_import_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("from build123d import Box, export_step\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        violations = payload["observed"]["violations"]
        assert any(
            "export_step" in item["reason"] and item["line"] == 1 for item in violations
        )

    def test_exporter_call_rejected_with_line(self) -> None:
        source = "from build123d import Box\nexport_step(Box(1, 1, 1), '/tmp/x.step')\n"
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source(source)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        violations = payload["observed"]["violations"]
        assert any(
            "export_step" in item["reason"] and item["line"] == 2 for item in violations
        )

    def test_exporter_method_attribute_rejected(self) -> None:
        source = "shape.export_stl('/tmp/x.stl')\n"
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source(source)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        violations = payload["observed"]["violations"]
        assert any(
            "export_stl" in item["reason"] and item["line"] == 1 for item in violations
        )

    def test_exporter_submodule_import_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("import build123d.exporters3d\n")
        violations = excinfo.value.payload["observed"]["violations"]
        assert any("exporters3d" in item["reason"] for item in violations)

    def test_exporter_submodule_from_import_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("from build123d.exporters3d import export_step\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_POLICY_VIOLATION"

    def test_mesher_import_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.validate_source("from build123d import Mesher\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_POLICY_VIOLATION"

    def test_legitimate_importer_still_allowed(self) -> None:
        build123d.validate_source(
            "from build123d import Box, import_step\nresult = {'Body': Box(2, 3, 5)}\n"
        )

    def test_runtime_smoke_source_still_valid(self) -> None:
        build123d.validate_source(
            "from build123d import *\nresult = {'Runtime Smoke': Box(2, 3, 5)}\n"
        )


class TestBuild123dWorkerExporterHardening:
    @staticmethod
    def _worker() -> Any:
        import build123d_worker

        return build123d_worker

    def test_restricted_import_denies_exporter_fromlist(self) -> None:
        worker = self._worker()
        with pytest.raises(ImportError, match="export_step"):
            worker._restricted_import("build123d", fromlist=("export_step",))

    def test_restricted_import_denies_exporter_submodule(self) -> None:
        worker = self._worker()
        with pytest.raises(ImportError, match="exporters3d"):
            worker._restricted_import("build123d.exporters3d")
        with pytest.raises(ImportError, match="exporters"):
            worker._restricted_import("build123d", fromlist=("exporters3d",))

    def test_restricted_import_still_denies_other_roots(self) -> None:
        worker = self._worker()
        with pytest.raises(ImportError, match="os"):
            worker._restricted_import("os")


    def test_restricted_import_allows_math(self) -> None:
        worker = self._worker()
        module = worker._restricted_import("math")
        assert module.pi > 3

    def test_remove_exporter_symbols_strips_module_and_shape(self) -> None:
        import types

        worker = self._worker()

        class FakeShape:
            def export_stl(self, path: str) -> None:  # pragma: no cover - stripped
                raise AssertionError("should be removed")

            def export_step(self, path: str) -> None:  # pragma: no cover - stripped
                raise AssertionError("should be removed")

        fake = types.ModuleType("build123d")
        fake.Shape = FakeShape
        fake.export_step = lambda *args: None
        fake.export_stl = lambda *args: None
        fake.Mesher = object
        fake.exporters3d = types.ModuleType("build123d.exporters3d")
        fake.import_step = lambda *args: None
        fake.__all__ = [
            "Shape",
            "export_step",
            "export_stl",
            "Mesher",
            "exporters3d",
            "import_step",
        ]
        worker._remove_exporter_symbols(fake)
        assert not hasattr(fake, "export_step")
        assert not hasattr(fake, "export_stl")
        assert not hasattr(fake, "Mesher")
        assert not hasattr(fake, "exporters3d")
        assert not hasattr(FakeShape, "export_stl")
        assert not hasattr(FakeShape, "export_step")
        assert hasattr(fake, "import_step")
        assert fake.__all__ == ["Shape", "import_step"]

    def test_remove_exporter_symbols_tolerates_missing_attributes(self) -> None:
        import types

        worker = self._worker()
        bare = types.ModuleType("build123d")
        worker._remove_exporter_symbols(bare)


class _FakeResourceModule:
    RLIM_INFINITY = -1
    RLIMIT_AS = 1
    RLIMIT_CPU = 2
    RLIMIT_FSIZE = 3
    RLIMIT_NOFILE = 4

    def __init__(self, soft: int, hard: int) -> None:
        self.soft = soft
        self.hard = hard
        self.applied: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, resource_id: int) -> tuple[int, int]:
        return self.soft, self.hard

    def setrlimit(self, resource_id: int, limits: tuple[int, int]) -> None:
        self.applied.append((resource_id, limits))


class TestBuild123dWorkerResourceLimits:
    @staticmethod
    def _worker() -> Any:
        import build123d_worker

        return build123d_worker

    def test_requested_soft_limit_preserves_infinite_hard_limit(self) -> None:
        worker = self._worker()
        resource = _FakeResourceModule(soft=128, hard=-1)

        worker._set_soft_resource_limit(resource, 7, 512, "address-space")

        assert resource.applied == [(7, (512, -1))]

    def test_requested_soft_limit_preserves_finite_hard_limit(self) -> None:
        worker = self._worker()
        resource = _FakeResourceModule(soft=128, hard=1024)

        worker._set_soft_resource_limit(resource, 7, 512, "address-space")

        assert resource.applied == [(7, (512, 1024))]

    def test_requested_soft_limit_is_clamped_to_finite_hard_limit(self) -> None:
        worker = self._worker()
        resource = _FakeResourceModule(soft=128, hard=256)

        worker._set_soft_resource_limit(resource, 7, 512, "address-space")

        assert resource.applied == [(7, (256, 256))]

    def test_zero_hard_limit_is_rejected(self) -> None:
        worker = self._worker()
        resource = _FakeResourceModule(soft=0, hard=0)

        with pytest.raises(RuntimeError, match="hard limit is 0"):
            worker._set_soft_resource_limit(resource, 7, 512, "address-space")

    def test_darwin_uses_parent_memory_watchdog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worker = self._worker()
        resource = _FakeResourceModule(soft=128, hard=1024)
        monkeypatch.setitem(sys.modules, "resource", resource)
        monkeypatch.setattr(worker.sys, "platform", "darwin")

        worker._resource_limits(
            {
                "memory_limit_bytes": 512,
                "cpu_limit_seconds": 60,
                "output_limit_bytes": 256,
            }
        )

        assert [resource_id for resource_id, _limits in resource.applied] == [2, 3, 4]


# ---------------------------------------------------------------------------
# build123d: source edit uniqueness
# ---------------------------------------------------------------------------


class TestBuild123dSourceEdits:
    def test_single_match_replaced(self) -> None:
        result = build123d._apply_source_edits(
            "radius = 4\nheight = 10\n",
            [{"old_text": "radius = 4", "new_text": "radius = 6"}],
        )
        assert result == "radius = 6\nheight = 10\n"

    def test_zero_matches_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._apply_source_edits(
                "radius = 4\n",
                [{"old_text": "diameter = 4", "new_text": "diameter = 6"}],
            )
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_EDIT_NOT_UNIQUE"
        assert payload["observed"]["match_count"] == 0
        assert payload["required_changes"]

    def test_multiple_matches_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._apply_source_edits(
                "x = 1\nx = 1\n", [{"old_text": "x = 1", "new_text": "x = 2"}]
            )
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_EDIT_NOT_UNIQUE"
        assert payload["observed"]["match_count"] == 2

    def test_empty_edit_list_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._apply_source_edits("x = 1\n", [])
        assert excinfo.value.payload["failure_code"] == "SOURCE_EDITS_REQUIRED"

    def test_empty_old_text_rejected(self) -> None:
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._apply_source_edits(
                "x = 1\n", [{"old_text": "", "new_text": "y"}]
            )
        assert excinfo.value.payload["failure_code"] == "INVALID_SOURCE_EDIT"


# ---------------------------------------------------------------------------
# build123d: revision hashing
# ---------------------------------------------------------------------------


class TestBuild123dRevision:
    SOURCE = "from build123d import Box\nresult = Box(1, 2, 3)\n"

    def test_revision_is_stable(self) -> None:
        first = build123d.source_revision(
            self.SOURCE, {"a": 1}, {"base": "Pad"}, ["Body"]
        )
        second = build123d.source_revision(
            self.SOURCE, {"a": 1}, {"base": "Pad"}, ["Body"]
        )
        assert first == second
        assert len(first) == 64

    def test_revision_ignores_parameter_key_order(self) -> None:
        first = build123d.source_revision(self.SOURCE, {"a": 1, "b": 2}, {}, ["Body"])
        second = build123d.source_revision(self.SOURCE, {"b": 2, "a": 1}, {}, ["Body"])
        assert first == second

    def test_revision_changes_with_each_field(self) -> None:
        base = build123d.source_revision(self.SOURCE, {"a": 1}, {"x": "Pad"}, ["Body"])
        assert base != build123d.source_revision(
            self.SOURCE + "# comment\n", {"a": 1}, {"x": "Pad"}, ["Body"]
        )
        assert base != build123d.source_revision(
            self.SOURCE, {"a": 2}, {"x": "Pad"}, ["Body"]
        )
        assert base != build123d.source_revision(
            self.SOURCE, {"a": 1}, {"x": "Pocket"}, ["Body"]
        )
        assert base != build123d.source_revision(
            self.SOURCE, {"a": 1}, {"x": "Pad"}, ["Other"]
        )


# ---------------------------------------------------------------------------
# build123d: persisted artifact contract
# ---------------------------------------------------------------------------


def _write_build123d_artifact(
    project_root: Path,
    *,
    source: str,
    parameters: dict[str, Any],
    working_revision: str,
) -> Path:
    directory = project_root / "build123d" / MODEL_ID
    directory.mkdir(parents=True)
    (directory / "model.py").write_text(source, encoding="utf-8")
    (directory / "parameters.json").write_text(json.dumps(parameters), encoding="utf-8")
    manifest = {
        "schema": build123d.MODEL_SCHEMA,
        "model_id": MODEL_ID,
        "label": "Test Model",
        "inputs": {},
        "outputs": {"Body": ""},
        "output_facts": {},
        "expected_outputs": ["Body"],
        "working_revision": working_revision,
        "state": "accepted",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


class TestBuild123dArtifactContract:
    SOURCE = "from build123d import Box\nresult = Box(1, 2, 3)\n"

    def test_matching_revision_loads(self, tmp_path: Path) -> None:
        revision = build123d.source_revision(self.SOURCE, {"a": 1}, {}, ["Body"])
        _write_build123d_artifact(
            tmp_path, source=self.SOURCE, parameters={"a": 1}, working_revision=revision
        )
        contract = build123d._artifact_contract(tmp_path, MODEL_ID)
        assert contract is not None
        assert contract["working_revision"] == revision
        assert contract["accepted_revision"] == revision
        assert contract["state"] == "accepted"

    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        assert build123d._artifact_contract(tmp_path, MODEL_ID) is None

    def test_revision_mismatch_rejected(self, tmp_path: Path) -> None:
        _write_build123d_artifact(
            tmp_path, source=self.SOURCE, parameters={"a": 1}, working_revision="0" * 64
        )
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._artifact_contract(tmp_path, MODEL_ID)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        assert payload["observed"]["manifest_revision"] == "0" * 64
        assert payload["observed"]["calculated_revision"] != "0" * 64

    def test_tampered_source_rejected(self, tmp_path: Path) -> None:
        revision = build123d.source_revision(self.SOURCE, {}, {}, ["Body"])
        directory = _write_build123d_artifact(
            tmp_path, source=self.SOURCE, parameters={}, working_revision=revision
        )
        (directory / "model.py").write_text(
            self.SOURCE + "# tampered\n", encoding="utf-8"
        )
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._artifact_contract(tmp_path, MODEL_ID)
        assert (
            excinfo.value.payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        )

    def test_incomplete_artifact_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "build123d" / MODEL_ID
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d._artifact_contract(tmp_path, MODEL_ID)
        assert excinfo.value.payload["failure_code"] == "MODEL_ARTIFACT_INCOMPLETE"


# ---------------------------------------------------------------------------
# build123d: execution failure payloads
# ---------------------------------------------------------------------------


def _prepare_stub_runner(tmp_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Build a prepared payload whose runner writes ``result`` as result.json."""
    staging = tmp_path / "staging"
    staging.mkdir()
    runtime_root = tmp_path / "runtime"
    (runtime_root / "site-packages").mkdir(parents=True)
    worker = staging / "build123d_worker.py"
    worker.write_text(
        "import json\n"
        "import sys\n"
        f"payload = {result!r}\n"
        "with open(sys.argv[2], 'w', encoding='utf-8') as handle:\n"
        "    json.dump(payload, handle)\n",
        encoding="utf-8",
    )
    return {
        "staging": str(staging),
        "health": {
            "python_executable": sys.executable,
            "runtime_root": str(runtime_root),
        },
    }


def test_build123d_runner_uses_an_isolated_home(tmp_path: Path) -> None:
    environment = build123d._runner_environment(tmp_path)

    assert environment["HOME"] == str(tmp_path)
    if sys.platform == "win32":
        drive, path = os.path.splitdrive(str(tmp_path))
        assert environment["USERPROFILE"] == str(tmp_path)
        assert environment["HOMEDRIVE"] == drive
        assert environment["HOMEPATH"] == (path or "\\")


FILLET_EVIDENCE = {
    "fillet_diagnostics": {
        "connected_components": [
            {"component_index": 0, "closed_loop": False, "selection_indices": [1, 2]}
        ],
        "separate_component_fillet_possible": True,
        "component_trials": [
            {
                "component_index": 0,
                "succeeded": True,
                "radius_factor": 0.5,
                "radius_mm": 1.5,
            }
        ],
        "individual_edge_trials": [{"selection_index": 2, "succeeded": False}],
        "diagnostic_complete": True,
    }
}


class TestBuild123dExecutionFailures:
    def test_fillet_failure_payload_carries_evidence(self, tmp_path: Path) -> None:
        prepared = _prepare_stub_runner(
            tmp_path,
            {
                "ok": False,
                "error": "fillet operation did not converge",
                "exception_type": "StdFail_NotDone",
                "exception_kind": "kernel_fillet_failure",
                "traceback": "Traceback (most recent call last): ...",
                "exception_evidence": FILLET_EVIDENCE,
            },
        )
        payload = build123d.execute_prepared(prepared, timeout_seconds=60.0)
        assert payload["ok"] is False
        assert payload["failure_code"] == "BUILD123D_FILLET_FAILED"
        evidence = payload["observed"]["exception_evidence"]
        assert (
            evidence["fillet_diagnostics"]["separate_component_fillet_possible"] is True
        )
        change_keys: set[str] = set()
        for change in payload["required_changes"]:
            change_keys.update(change.keys())
        assert "do_not_retry_unchanged_fillet" in change_keys
        assert "repair_open_selected_edge_components" in change_keys
        assert "apply_fillet_per_connected_component" in change_keys
        assert "maximum_tested_working_radius_by_component_mm" in change_keys
        assert "repair_or_exclude_failing_selection_indices" in change_keys
        radius_change = next(
            change
            for change in payload["required_changes"]
            if "maximum_tested_working_radius_by_component_mm" in change
        )
        assert radius_change["maximum_tested_working_radius_by_component_mm"] == {
            "0": 1.5
        }

    def test_generic_failure_payload_shape(self, tmp_path: Path) -> None:
        prepared = _prepare_stub_runner(
            tmp_path,
            {
                "ok": False,
                "error": "name 'Bax' is not defined",
                "exception_type": "NameError",
                "traceback": "Traceback (most recent call last): ...",
            },
        )
        payload = build123d.execute_prepared(prepared, timeout_seconds=60.0)
        assert payload["failure_code"] == "BUILD123D_EXECUTION_FAILED"
        assert payload["observed"]["exception_evidence"] is None
        assert payload["required_changes"] == [
            {"correct_source_parameters_or_inputs_from_traceback": True}
        ]
        for key in (
            "ok",
            "tool",
            "failure_code",
            "failure_stage",
            "build123d_stage",
            "error",
            "requested",
            "observed",
            "required_changes",
            "retry_same_call",
        ):
            assert key in payload

    def test_runner_start_failure_returns_payload(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        prepared = {
            "staging": str(staging),
            "health": {
                "python_executable": str(tmp_path / "missing-python"),
                "runtime_root": str(tmp_path),
            },
        }
        payload = build123d.execute_prepared(prepared, timeout_seconds=5.0)
        assert payload["ok"] is False
        assert payload["failure_code"] == "RUNNER_START_FAILED"

    def test_runner_without_result_reports_failure(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        runtime_root = tmp_path / "runtime"
        (runtime_root / "site-packages").mkdir(parents=True)
        worker = staging / "build123d_worker.py"
        worker.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        prepared = {
            "staging": str(staging),
            "health": {
                "python_executable": sys.executable,
                "runtime_root": str(runtime_root),
            },
        }
        payload = build123d.execute_prepared(prepared, timeout_seconds=60.0)
        assert payload["failure_code"] == "RUNNER_NO_RESULT"
        assert payload["observed"]["exit_code"] == 3


# ---------------------------------------------------------------------------
# build123d: transactional commit and delete
# ---------------------------------------------------------------------------


class _StubObject:
    """Minimal stand-in for a FreeCAD document object."""

    def __init__(
        self, name: str, type_id: str, document: "_StubDocument | None" = None
    ) -> None:
        self.Name = name
        self.TypeId = type_id
        self.Document = document
        self.Label = name
        self.PropertiesList: list[str] = []
        self.Group: list[Any] = []
        self.OutListRecursive: list[Any] = []

    def addProperty(self, _type: str, name: str, _group: str = "") -> None:
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
            setattr(self, name, "")

    def addObject(self, obj: Any) -> None:
        self.Group.append(obj)
        self.OutListRecursive.append(obj)

    def newObject(self, type_id: str, name: str) -> "_StubObject":
        child = _StubObject(name, type_id, self.Document)
        if self.Document is not None:
            self.Document.Objects.append(child)
        self.Group.append(child)
        self.OutListRecursive.append(child)
        return child


class _StubDocument:
    """Stub FreeCAD document recording transaction call ordering.

    ``abortTransaction`` restores the object list captured at
    ``openTransaction`` so tests can assert the document is unchanged after a
    rollback, mirroring real FreeCAD transaction semantics.
    """

    def __init__(self, name: str = "Doc") -> None:
        self.Name = name
        self.Recomputing = False
        self.Objects: list[Any] = []
        self.transaction_log: list[str] = []
        self.fail_remove = False
        self.fail_recompute = False
        self._snapshot: list[Any] | None = None
        self._sequence = 0

    def openTransaction(self, label: str) -> None:
        self.transaction_log.append(f"open:{label}")
        self._snapshot = list(self.Objects)

    def commitTransaction(self) -> None:
        self.transaction_log.append("commit")
        self._snapshot = None

    def abortTransaction(self) -> None:
        self.transaction_log.append("abort")
        if self._snapshot is not None:
            self.Objects = list(self._snapshot)
            self._snapshot = None

    def addObject(self, type_id: str, name: str) -> _StubObject:
        self._sequence += 1
        obj = _StubObject(f"{name}{self._sequence:03d}", type_id, self)
        self.Objects.append(obj)
        return obj

    def getObject(self, name: str) -> Any | None:
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def findObjects(self, *, Type: str) -> list[Any]:
        return [obj for obj in self.Objects if obj.TypeId == Type]

    def removeObject(self, name: str) -> None:
        if self.fail_remove:
            raise RuntimeError("simulated removeObject failure")
        obj = self.getObject(name)
        if obj is not None:
            self.Objects.remove(obj)

    def recompute(self) -> None:
        if self.fail_recompute:
            raise RuntimeError("simulated recompute failure")


def _stub_service(
    doc: _StubDocument,
    project_root: Path,
    diagnostics: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        _active_document=lambda: doc,
        project_context=lambda: {"root": str(project_root)},
        project_scope_snapshot=lambda: {"root": str(project_root)},
        recompute_diagnostics=lambda: {
            "captured": True,
            "diagnostics": list(diagnostics or []),
        },
        structural_document_revision=lambda: "cad-revision-1",
    )


class TestBuild123dTransactionalCommit:
    SOURCE = "from build123d import Box\nresult = Box(1, 2, 3)\n"

    def _prepared(self, tmp_path: Path) -> dict[str, Any]:
        revision = build123d.source_revision(self.SOURCE, {}, {}, ["Body"])
        return {
            "document_name": "Doc",
            "model_id": MODEL_ID,
            "model_name": "Test Model",
            "accepted_revision_before": "",
            "expected_outputs": ["Body"],
            "source": self.SOURCE,
            "parameters": {},
            "revision": revision,
            "input_objects": {},
            "project_root": str(tmp_path),
        }

    @staticmethod
    def _imported() -> list[dict[str, Any]]:
        return [
            {
                "key": "Body",
                "shape": object(),
                "freecad_shape": {"volume_mm3": 6.0},
                "build123d_shape": {"volume_mm3": 6.0},
                "step_transfer": {"volume_delta_pct": 0.0},
            }
        ]

    def test_success_commits_transaction_in_order(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        service = _stub_service(doc, tmp_path)
        payload = build123d.commit_outputs(
            service,
            self._prepared(tmp_path),
            {"elapsed_seconds": 0.1},
            self._imported(),
        )
        assert payload["ok"] is True
        assert doc.transaction_log == ["open:Accept build123d model", "commit"]
        assert (tmp_path / "build123d" / MODEL_ID / "model.py").is_file()

    def test_commit_exception_aborts_and_restores_document(
        self, tmp_path: Path
    ) -> None:
        doc = _StubDocument()
        doc.fail_recompute = True
        service = _stub_service(doc, tmp_path)
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.commit_outputs(
                service, self._prepared(tmp_path), {}, self._imported()
            )
        assert excinfo.value.payload["failure_code"] == "BUILD123D_COMMIT_FAILED"
        assert doc.transaction_log == ["open:Accept build123d model", "abort"]
        assert doc.Objects == []  # no orphan container or bodies
        assert not (tmp_path / "build123d" / MODEL_ID).exists()

    def test_recompute_errors_fail_structurally(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        errors = [
            {
                "severity": "error",
                "code": "RECOMPUTE_FAILED",
                "object": "Body",
                "message": "failed to rebuild",
            }
        ]
        service = _stub_service(doc, tmp_path, diagnostics=errors)
        with pytest.raises(build123d.Build123dFailure) as excinfo:
            build123d.commit_outputs(
                service, self._prepared(tmp_path), {}, self._imported()
            )
        payload = excinfo.value.payload
        assert payload["failure_code"] == "BUILD123D_COMMIT_FAILED"
        assert payload["failure_stage"] == "postcondition"
        assert payload["observed"]["recompute_errors"] == errors
        assert doc.transaction_log == ["open:Accept build123d model", "abort"]
        assert doc.Objects == []
        assert not (tmp_path / "build123d" / MODEL_ID).exists()


class TestBuild123dTransactionalDelete:
    SOURCE = "from build123d import Box\nresult = Box(1, 2, 3)\n"

    def _setup(
        self, tmp_path: Path
    ) -> tuple[_StubDocument, _StubObject, SimpleNamespace, str]:
        revision = build123d.source_revision(self.SOURCE, {}, {}, ["Body"])
        _write_build123d_artifact(
            tmp_path, source=self.SOURCE, parameters={}, working_revision=revision
        )
        doc = _StubDocument()
        container = doc.addObject("App::Part", "Build123dModel")
        for prop in (
            build123d.PROP_MODEL_ID,
            build123d.PROP_SOURCE,
            build123d.PROP_REVISION,
        ):
            container.addProperty("App::PropertyString", prop, "Build123d")
        setattr(container, build123d.PROP_MODEL_ID, MODEL_ID)
        setattr(container, build123d.PROP_REVISION, revision)
        service = _stub_service(doc, tmp_path)
        return doc, container, service, revision

    def test_delete_success_commits_transaction_in_order(self, tmp_path: Path) -> None:
        doc, container, service, revision = self._setup(tmp_path)
        payload = build123d.delete_model(
            service, MODEL_ID, revision, "user requested cleanup"
        )
        assert payload["ok"] is True
        assert doc.transaction_log == ["open:Delete build123d model", "commit"]
        assert doc.getObject(container.Name) is None
        assert not (tmp_path / "build123d" / MODEL_ID).exists()

    def test_delete_failure_rolls_back(self, tmp_path: Path) -> None:
        doc, container, service, revision = self._setup(tmp_path)
        doc.fail_remove = True
        payload = build123d.delete_model(
            service, MODEL_ID, revision, "user requested cleanup"
        )
        assert payload["ok"] is False
        assert payload["failure_code"] == "DELETE_FAILED"
        assert doc.transaction_log == ["open:Delete build123d model", "abort"]
        assert doc.getObject(container.Name) is container
        assert (tmp_path / "build123d" / MODEL_ID).is_dir()


# ---------------------------------------------------------------------------
# Display contract: Shaded mode in GUI sessions, no-op in headless sessions
# ---------------------------------------------------------------------------


class _StubViewObject:
    """Minimal FreeCAD view provider stand-in."""

    def __init__(self, modes: list[str] | None = None) -> None:
        self._modes = list(modes if modes is not None else ["Wireframe", "Shaded"])
        self.DisplayMode = "Wireframe"

    def listDisplayModes(self) -> list[str]:
        return list(self._modes)


class _GuiStubObject(_StubObject):
    """Document object carrying a view provider, as in a GUI session."""

    def __init__(self, name: str, type_id: str) -> None:
        super().__init__(name, type_id)
        self.ViewObject = _StubViewObject()

    def newObject(self, type_id: str, name: str) -> "_GuiStubObject":
        child = _GuiStubObject(name, type_id)
        self.Group.append(child)
        self.OutListRecursive.append(child)
        return child


class _GuiStubDocument(_StubDocument):
    """Stub document whose objects carry view providers."""

    def addObject(self, type_id: str, name: str) -> _GuiStubObject:
        self._sequence += 1
        obj = _GuiStubObject(f"{name}{self._sequence:03d}", type_id)
        self.Objects.append(obj)
        return obj


class TestScriptedDisplayContract:
    SOURCE = "from build123d import Box\nresult = Box(1, 2, 3)\n"

    def _prepared(self, tmp_path: Path) -> dict[str, Any]:
        revision = build123d.source_revision(self.SOURCE, {}, {}, ["Body"])
        return {
            "document_name": "Doc",
            "model_id": MODEL_ID,
            "model_name": "Test Model",
            "accepted_revision_before": "",
            "expected_outputs": ["Body"],
            "source": self.SOURCE,
            "parameters": {},
            "revision": revision,
            "input_objects": {},
            "project_root": str(tmp_path),
        }

    @staticmethod
    def _imported() -> list[dict[str, Any]]:
        return [
            {
                "key": "Body",
                "shape": object(),
                "freecad_shape": {"volume_mm3": 6.0},
                "build123d_shape": {"volume_mm3": 6.0},
                "step_transfer": {"volume_delta_pct": 0.0},
            }
        ]

    def test_build123d_commit_sets_shaded_in_gui_session(self, tmp_path: Path) -> None:
        doc = _GuiStubDocument()
        service = _stub_service(doc, tmp_path)
        payload = build123d.commit_outputs(
            service,
            self._prepared(tmp_path),
            {"elapsed_seconds": 0.1},
            self._imported(),
        )
        assert payload["ok"] is True
        container = doc.Objects[0]
        outputs = build123d._output_objects(container)
        assert set(outputs) == {"Body"}
        body, feature = outputs["Body"]
        assert body.ViewObject.DisplayMode == "Shaded"
        assert feature.ViewObject.DisplayMode == "Shaded"

    def test_build123d_commit_headless_without_view_provider(
        self, tmp_path: Path
    ) -> None:
        doc = _StubDocument()  # objects have no ViewObject attribute
        service = _stub_service(doc, tmp_path)
        payload = build123d.commit_outputs(
            service,
            self._prepared(tmp_path),
            {"elapsed_seconds": 0.1},
            self._imported(),
        )
        assert payload["ok"] is True
        assert doc.transaction_log == ["open:Accept build123d model", "commit"]

    def test_build123d_restore_output_display_modes(self, tmp_path: Path) -> None:
        doc = _GuiStubDocument()
        service = _stub_service(doc, tmp_path)
        build123d.commit_outputs(
            service,
            self._prepared(tmp_path),
            {"elapsed_seconds": 0.1},
            self._imported(),
        )
        container = doc.Objects[0]
        body, feature = build123d._output_objects(container)["Body"]
        body.ViewObject.DisplayMode = "Wireframe"
        feature.ViewObject.DisplayMode = "Wireframe"
        restored = build123d.restore_output_display_modes(doc)
        assert sorted(restored) == sorted([body.Name, feature.Name])
        assert body.ViewObject.DisplayMode == "Shaded"
        assert feature.ViewObject.DisplayMode == "Shaded"

    def test_build123d_restore_is_headless_safe(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        service = _stub_service(doc, tmp_path)
        build123d.commit_outputs(
            service,
            self._prepared(tmp_path),
            {"elapsed_seconds": 0.1},
            self._imported(),
        )
        assert build123d.restore_output_display_modes(doc) == []

    def test_build123d_shaded_unavailable_raises(self) -> None:
        obj = _GuiStubObject("Body001", "PartDesign::Body")
        obj.ViewObject = _StubViewObject(modes=["Wireframe", "Flat Lines"])
        with pytest.raises(RuntimeError, match="cannot use Shaded display mode"):
            build123d._set_shaded_display(obj)

    def test_openscad_set_shaded_display_headless_noop(self) -> None:
        openscad._set_shaded_display(_StubObject("Solid001", "PartDesign::Body"))

    def test_openscad_set_shaded_display_gui_unchanged(self) -> None:
        obj = _GuiStubObject("Solid001", "PartDesign::Body")
        openscad._set_shaded_display(obj)
        assert obj.ViewObject.DisplayMode == "Shaded"
        obj.ViewObject = _StubViewObject(modes=["Wireframe"])
        with pytest.raises(RuntimeError, match="cannot use Shaded display mode"):
            openscad._set_shaded_display(obj)


# ---------------------------------------------------------------------------
# OpenSCAD: source policy
# ---------------------------------------------------------------------------


class TestOpenSCADSourcePolicy:
    def test_valid_source_passes(self) -> None:
        openscad.validate_source("cube([10, 10, 10]);\n")

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad.validate_source("   \n")
        assert excinfo.value.payload["failure_code"] == "EMPTY_SOURCE"

    def test_oversized_source_rejected(self) -> None:
        big = "// pad\n" * (openscad.MAX_SOURCE_BYTES // 7 + 2)
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad.validate_source(big)
        assert excinfo.value.payload["failure_code"] == "SOURCE_TOO_LARGE"

    def test_nul_byte_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad.validate_source("cube(1);\x00\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_CONTAINS_NUL"

    @pytest.mark.parametrize(
        "path",
        [
            "/abs/evil.scad",
            "../escape.scad",
            "not_scad.txt",
            "attempts/shadow.scad",
        ],
    )
    def test_unsafe_project_paths_rejected(self, path: str) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad.clean_source_files({path: "cube(1);\n"}, "cube(2);\n")
        assert excinfo.value.payload["failure_code"] == "INVALID_SOURCE_PATH"

    def test_main_source_mismatch_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad.clean_source_files({"model.scad": "cube(9);\n"}, "cube(2);\n")
        assert excinfo.value.payload["failure_code"] == "MAIN_SOURCE_MISMATCH"


# ---------------------------------------------------------------------------
# OpenSCAD: source edit uniqueness
# ---------------------------------------------------------------------------


class TestOpenSCADSourceEdits:
    def test_single_match_replaced_and_validated(self) -> None:
        result = openscad._apply_source_edits(
            "cube([4, 4, 4]);\n", [{"old_text": "4, 4, 4", "new_text": "6, 6, 6"}]
        )
        assert result == "cube([6, 6, 6]);\n"

    def test_zero_matches_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad._apply_source_edits(
                "cube(4);\n", [{"old_text": "sphere(4)", "new_text": "sphere(6)"}]
            )
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_EDIT_MATCH_COUNT"
        assert payload["observed"]["match_count"] == 0

    def test_multiple_matches_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad._apply_source_edits(
                "cube(4);\ncube(4);\n",
                [{"old_text": "cube(4);", "new_text": "cube(6);"}],
            )
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_EDIT_MATCH_COUNT"
        assert payload["observed"]["match_count"] == 2
        assert payload["retry"]["required_changes"]

    def test_empty_edit_list_rejected(self) -> None:
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad._apply_source_edits("cube(4);\n", [])
        assert excinfo.value.payload["failure_code"] == "EMPTY_SOURCE_EDITS"


# ---------------------------------------------------------------------------
# OpenSCAD: revision hashing
# ---------------------------------------------------------------------------


class TestOpenSCADRevision:
    SOURCE = "cube([w, 10, 10]);\n"

    def test_revision_is_stable(self) -> None:
        first = openscad.source_revision(self.SOURCE, {"w": 4}, None, "exact_brep")
        second = openscad.source_revision(self.SOURCE, {"w": 4}, None, "exact_brep")
        assert first == second
        assert len(first) == 64

    def test_revision_ignores_parameter_key_order(self) -> None:
        first = openscad.source_revision(
            self.SOURCE, {"a": 1, "b": 2}, None, "exact_brep"
        )
        second = openscad.source_revision(
            self.SOURCE, {"b": 2, "a": 1}, None, "exact_brep"
        )
        assert first == second

    def test_revision_changes_with_each_field(self) -> None:
        base = openscad.source_revision(self.SOURCE, {"w": 4}, None, "exact_brep")
        assert base != openscad.source_revision(
            self.SOURCE + "// note\n", {"w": 4}, None, "exact_brep"
        )
        assert base != openscad.source_revision(
            self.SOURCE, {"w": 5}, None, "exact_brep"
        )
        assert base != openscad.source_revision(
            self.SOURCE, {"w": 4}, None, "faceted_brep"
        )
        assert base != openscad.source_revision(
            self.SOURCE, {"w": 4}, {"lib.scad": "module m() {}\n"}, "exact_brep"
        )


# ---------------------------------------------------------------------------
# OpenSCAD: persisted artifact contract
# ---------------------------------------------------------------------------


def _write_openscad_artifact(
    project_root: Path,
    *,
    source: str,
    parameters: dict[str, Any],
    working_revision: str,
) -> Path:
    directory = project_root / "openscad" / MODEL_ID
    directory.mkdir(parents=True)
    (directory / "model.scad").write_text(source, encoding="utf-8")
    (directory / "parameters.json").write_text(json.dumps(parameters), encoding="utf-8")
    manifest = {
        "schema": openscad.MODEL_SCHEMA,
        "model_id": MODEL_ID,
        "label": "Test Model",
        "conversion_mode": "exact_brep",
        "source_files": ["model.scad"],
        "working_revision": working_revision,
        "state": "accepted",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


class TestOpenSCADArtifactContract:
    SOURCE = "cube([10, 10, 10]);\n"

    def test_matching_revision_loads(self, tmp_path: Path) -> None:
        revision = openscad.source_revision(self.SOURCE, {"w": 4}, None, "exact_brep")
        _write_openscad_artifact(
            tmp_path, source=self.SOURCE, parameters={"w": 4}, working_revision=revision
        )
        contract = openscad._artifact_contract(tmp_path, MODEL_ID)
        assert contract is not None
        assert contract["working_revision"] == revision
        assert contract["conversion_mode"] == "exact_brep"

    def test_invalid_model_id_returns_none(self, tmp_path: Path) -> None:
        assert openscad._artifact_contract(tmp_path, "not-a-model-id") is None

    def test_revision_mismatch_rejected(self, tmp_path: Path) -> None:
        _write_openscad_artifact(
            tmp_path, source=self.SOURCE, parameters={}, working_revision="0" * 64
        )
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad._artifact_contract(tmp_path, MODEL_ID)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        assert payload["observed"]["manifest_revision"] == "0" * 64
        assert payload["observed"]["calculated_revision"] != "0" * 64

    def test_tampered_source_rejected(self, tmp_path: Path) -> None:
        revision = openscad.source_revision(self.SOURCE, {}, None, "exact_brep")
        directory = _write_openscad_artifact(
            tmp_path, source=self.SOURCE, parameters={}, working_revision=revision
        )
        (directory / "model.scad").write_text(
            self.SOURCE + "// tampered\n", encoding="utf-8"
        )
        with pytest.raises(openscad.OpenSCADFailure) as excinfo:
            openscad._artifact_contract(tmp_path, MODEL_ID)
        assert (
            excinfo.value.payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        )


# ---------------------------------------------------------------------------
# OpenSCAD: compiler diagnostics
# ---------------------------------------------------------------------------


class TestOpenSCADDiagnostics:
    def test_error_with_location_parsed(self) -> None:
        stderr = "ERROR: Parser error: syntax error in file model.scad, line 3\n"
        diagnostics = openscad._parse_diagnostics(stderr)
        assert diagnostics == [
            {
                "severity": "error",
                "message": "Parser error: syntax error",
                "file": "model.scad",
                "line": 3,
            }
        ]

    def test_warning_without_location_parsed(self) -> None:
        diagnostics = openscad._parse_diagnostics("WARNING: variable w is undefined\n")
        assert diagnostics == [
            {
                "severity": "warning",
                "message": "variable w is undefined",
                "file": "",
                "line": None,
            }
        ]

    def test_unrelated_lines_ignored(self) -> None:
        assert openscad._parse_diagnostics("Compiling design...\nGeometries: 3\n") == []


# ---------------------------------------------------------------------------
# OpenSCAD: execution failure payloads
# ---------------------------------------------------------------------------


def _stub_settings() -> SimpleNamespace:
    return SimpleNamespace(openscad_executable="", openscad_library_paths="")


def _prepare_stub_openscad(tmp_path: Path, script_body: str) -> dict[str, Any]:
    staging = tmp_path / "staging"
    staging.mkdir()
    source = staging / "model.scad"
    source.write_text("cube(1);\n", encoding="utf-8")
    executable = tmp_path / "fake-openscad.sh"
    executable.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    os.chmod(executable, 0o755)
    return {
        "staging": str(staging),
        "project_root": str(tmp_path),
        "model_id": MODEL_ID,
        "conversion_mode": "exact_brep",
        "parameters": {},
        "artifacts": {"source": str(source)},
        "health": {"executable": str(executable)},
    }


def _solid_facts(
    *,
    volume: float,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    size: tuple[float, float, float] = (10.0, 10.0, 10.0),
    faces: int = 6,
    edges: int = 12,
    vertices: int = 8,
) -> dict[str, Any]:
    maximum = [origin[axis] + size[axis] for axis in range(3)]
    return {
        "valid": True,
        "is_null": False,
        "shape_type": "Solid",
        "solids": 1,
        "faces": faces,
        "edges": edges,
        "vertices": vertices,
        "volume_mm3": volume,
        "area_mm2": volume / 2.0,
        "bbox": {
            "min": list(origin),
            "max": maximum,
            "size": list(size),
        },
    }


def _accepted_facts(facts_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        key: {"shape": facts, "fidelity": "exact_brep", "artifacts": {}}
        for key, facts in facts_by_key.items()
    }


class TestOpenSCADOutputIdentity:
    def test_first_import_uses_ordinal_keys(self) -> None:
        new = [
            _solid_facts(volume=100.0),
            _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
        ]
        assert openscad.match_output_keys(new, {}) == ["Solid 001", "Solid 002"]

    def test_untouched_solids_keep_keys_when_one_is_edited(self) -> None:
        untouched_a = _solid_facts(volume=100.0)
        untouched_b = _solid_facts(volume=300.0, origin=(120.0, 0.0, 0.0))
        accepted = _accepted_facts(
            {
                "Solid 001": untouched_a,
                "Solid 002": _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
                "Solid 003": untouched_b,
            }
        )
        edited_middle = _solid_facts(
            volume=250.0, origin=(50.0, 0.0, 0.0), faces=8, edges=18, vertices=12
        )
        # Import order shifts (edited solid sorts differently), but geometry
        # identifies the untouched solids.
        new = [untouched_b, edited_middle, untouched_a]
        assert openscad.match_output_keys(new, accepted) == [
            "Solid 003",
            "Solid 002",
            "Solid 001",
        ]

    def test_brand_new_solid_receives_new_key(self) -> None:
        untouched = _solid_facts(volume=100.0)
        accepted = _accepted_facts({"Solid 001": untouched})
        added = _solid_facts(volume=999.0, origin=(200.0, 0.0, 0.0))
        assert openscad.match_output_keys([untouched, added], accepted) == [
            "Solid 001",
            "Solid 002",
        ]

    def test_removed_solid_key_is_not_reassigned(self) -> None:
        kept = _solid_facts(volume=100.0)
        accepted = _accepted_facts(
            {
                "Solid 001": kept,
                "Solid 002": _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
            }
        )
        keys = openscad.match_output_keys([kept], accepted)
        assert keys == ["Solid 001"]  # Solid 002 absent -> commit removes its body.

    def test_all_edited_falls_back_to_positional_pairing(self) -> None:
        accepted = _accepted_facts(
            {
                "Solid 001": _solid_facts(volume=100.0),
                "Solid 002": _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
            }
        )
        new = [
            _solid_facts(volume=110.0),
            _solid_facts(volume=210.0, origin=(50.0, 0.0, 0.0)),
        ]
        assert openscad.match_output_keys(new, accepted) == ["Solid 001", "Solid 002"]

    def test_edited_solids_pair_positionally_with_remaining_keys(self) -> None:
        accepted = _accepted_facts(
            {
                "Solid 001": _solid_facts(volume=100.0),
                "Solid 002": _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
                "Solid 003": _solid_facts(volume=300.0, origin=(120.0, 0.0, 0.0)),
            }
        )
        kept = _solid_facts(volume=100.0)
        edited_one = _solid_facts(volume=555.0, origin=(400.0, 0.0, 0.0))
        edited_two = _solid_facts(volume=777.0, origin=(600.0, 0.0, 0.0))
        keys = openscad.match_output_keys([kept, edited_one, edited_two], accepted)
        assert keys == ["Solid 001", "Solid 002", "Solid 003"]

    def test_more_solids_than_accepted_gets_fresh_ordinals(self) -> None:
        accepted = _accepted_facts({"Solid 001": _solid_facts(volume=100.0)})
        new = [
            _solid_facts(volume=100.0),
            _solid_facts(volume=200.0, origin=(50.0, 0.0, 0.0)),
            _solid_facts(volume=300.0, origin=(120.0, 0.0, 0.0)),
        ]
        assert openscad.match_output_keys(new, accepted) == [
            "Solid 001",
            "Solid 002",
            "Solid 003",
        ]

    def test_keys_are_unique_for_identical_geometry(self) -> None:
        identical = _solid_facts(volume=100.0)
        accepted = _accepted_facts({"Solid 001": identical})
        keys = openscad.match_output_keys([identical, dict(identical)], accepted)
        assert len(keys) == len(set(keys)) == 2
        assert keys[0] == "Solid 001"

    def test_tolerates_malformed_accepted_facts(self) -> None:
        accepted = {"Solid 001": {"shape": None}, "Solid 002": "garbage"}
        new = [_solid_facts(volume=100.0)]
        assert openscad.match_output_keys(new, accepted) == ["Solid 001"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="stub executable requires a POSIX shell"
)
class TestOpenSCADExecutionFailures:
    def test_compile_failure_payload_carries_diagnostics(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(openscad, "load_settings", _stub_settings)
        prepared = _prepare_stub_openscad(
            tmp_path,
            'echo "ERROR: Parser error: syntax error in file model.scad, line 3" >&2\nexit 1',
        )
        payload = openscad.execute_prepared(prepared, timeout_seconds=60.0)
        assert payload["ok"] is False
        assert payload["failure_code"] == "OPENSCAD_COMPILE_FAILED"
        diagnostics = payload["observed"]["diagnostics"]
        assert diagnostics and diagnostics[0]["severity"] == "error"
        assert diagnostics[0]["file"] == "model.scad"
        assert diagnostics[0]["line"] == 3
        assert payload["retry"]["required_changes"] == [
            {"edit_source_at_diagnostics": True}
        ]

    def test_missing_executable_returns_start_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(openscad, "load_settings", _stub_settings)
        prepared = _prepare_stub_openscad(tmp_path, "exit 0")
        prepared["health"]["executable"] = str(tmp_path / "missing-openscad")
        payload = openscad.execute_prepared(prepared, timeout_seconds=5.0)
        assert payload["ok"] is False
        assert payload["failure_code"] == "OPENSCAD_START_FAILED"


# ---------------------------------------------------------------------------
# Sidecar resource budgets: preference wiring and memory watchdog
# ---------------------------------------------------------------------------


def _budget_settings(timeout_seconds: float, memory_limit_mb: int) -> SimpleNamespace:
    return SimpleNamespace(
        scripted_timeout_seconds=timeout_seconds,
        scripted_memory_limit_mb=memory_limit_mb,
        openscad_executable="",
        openscad_library_paths="",
    )


def _prepare_hungry_build123d_runner(tmp_path: Path) -> dict[str, Any]:
    """Prepared payload whose worker allocates 256 MB and then sleeps."""
    staging = tmp_path / "staging"
    staging.mkdir()
    runtime_root = tmp_path / "runtime"
    (runtime_root / "site-packages").mkdir(parents=True)
    worker = staging / "build123d_worker.py"
    worker.write_text(
        "import time\ndata = bytearray(256 * 1024 * 1024)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    return {
        "staging": str(staging),
        "health": {
            "python_executable": sys.executable,
            "runtime_root": str(runtime_root),
        },
    }


def _prepare_sleeping_build123d_runner(tmp_path: Path) -> dict[str, Any]:
    staging = tmp_path / "staging"
    staging.mkdir()
    runtime_root = tmp_path / "runtime"
    (runtime_root / "site-packages").mkdir(parents=True)
    worker = staging / "build123d_worker.py"
    worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    return {
        "staging": str(staging),
        "health": {
            "python_executable": sys.executable,
            "runtime_root": str(runtime_root),
        },
    }


class TestConfiguredBudgets:
    def test_defaults_unchanged_when_preferences_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _broken() -> SimpleNamespace:
            raise RuntimeError("preferences unavailable")

        monkeypatch.setattr(build123d, "load_settings", _broken)
        monkeypatch.setattr(openscad, "load_settings", _broken)
        assert build123d._configured_budgets() == (300.0, 6 * 1024 * 1024 * 1024)
        assert openscad._configured_budgets() == (300.0, 6 * 1024 * 1024 * 1024)

    def test_defaults_unchanged_for_legacy_settings_without_budget_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build123d, "load_settings", _stub_settings)
        monkeypatch.setattr(openscad, "load_settings", _stub_settings)
        assert build123d._configured_budgets() == (300.0, 6 * 1024 * 1024 * 1024)
        assert openscad._configured_budgets() == (300.0, 6 * 1024 * 1024 * 1024)

    def test_preference_values_honored_by_both_engines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _budget_settings(45.0, 512)
        monkeypatch.setattr(build123d, "load_settings", lambda: settings)
        monkeypatch.setattr(openscad, "load_settings", lambda: settings)
        expected = (45.0, 512 * 1024 * 1024)
        assert build123d._configured_budgets() == expected
        assert openscad._configured_budgets() == expected

    def test_explicit_overrides_bypass_preferences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _must_not_be_called() -> SimpleNamespace:
            raise AssertionError("load_settings must not be consulted")

        monkeypatch.setattr(build123d, "load_settings", _must_not_be_called)
        monkeypatch.setattr(openscad, "load_settings", _must_not_be_called)
        assert build123d._resolved_budgets(60.0, 123) == (60.0, 123)
        assert openscad._resolved_budgets(60.0, 123) == (60.0, 123)

    def test_partial_override_pulls_remaining_budget_from_preferences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _budget_settings(45.0, 512)
        monkeypatch.setattr(build123d, "load_settings", lambda: settings)
        assert build123d._resolved_budgets(60.0, None) == (60.0, 512 * 1024 * 1024)
        assert build123d._resolved_budgets(None, 123) == (45.0, 123)


@pytest.mark.skipif(
    sys.platform not in ("linux", "win32"),
    reason="memory watchdog requires /proc or the Windows psapi",
)
class TestBuild123dMemoryWatchdog:
    def test_hungry_worker_is_terminated_with_observed_usage(
        self, tmp_path: Path
    ) -> None:
        prepared = _prepare_hungry_build123d_runner(tmp_path)
        payload = build123d.execute_prepared(
            prepared,
            timeout_seconds=60.0,
            memory_limit_bytes=64 * 1024 * 1024,
        )
        assert payload["ok"] is False
        assert payload["failure_code"] == "MEMORY_LIMIT_EXCEEDED"
        assert payload["observed"]["memory_limit_bytes"] == 64 * 1024 * 1024
        assert payload["observed"]["observed_memory_bytes"] > 64 * 1024 * 1024
        assert payload["required_changes"] == [
            {"reduce_model_memory_or_increase_memory_budget_preference": True}
        ]

    def test_preference_memory_budget_enforced_without_explicit_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            build123d, "load_settings", lambda: _budget_settings(60.0, 64)
        )
        prepared = _prepare_hungry_build123d_runner(tmp_path)
        payload = build123d.execute_prepared(prepared)
        assert payload["failure_code"] == "MEMORY_LIMIT_EXCEEDED"
        assert payload["observed"]["memory_limit_bytes"] == 64 * 1024 * 1024


class TestBuild123dPreferenceTimeout:
    def test_preference_timeout_enforced_without_explicit_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            build123d, "load_settings", lambda: _budget_settings(1.0, 8192)
        )
        prepared = _prepare_sleeping_build123d_runner(tmp_path)
        payload = build123d.execute_prepared(prepared)
        assert payload["ok"] is False
        assert payload["failure_code"] == "EXECUTION_TIMEOUT"
        assert "1 seconds" in payload["error"]


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="stub executable requires a POSIX shell and /proc for the watchdog",
)
class TestOpenSCADResourceBudgets:
    def test_preference_memory_budget_terminates_hungry_compiler(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            openscad, "load_settings", lambda: _budget_settings(60.0, 64)
        )
        hungry = (
            f'exec "{sys.executable}" -c '
            '"data = bytearray(256 * 1024 * 1024); import time; time.sleep(60)"'
        )
        prepared = _prepare_stub_openscad(tmp_path, hungry)
        payload = openscad.execute_prepared(prepared)
        assert payload["ok"] is False
        assert payload["failure_code"] == "MEMORY_LIMIT_EXCEEDED"
        assert payload["observed"]["memory_limit_bytes"] == 64 * 1024 * 1024
        assert payload["observed"]["observed_memory_bytes"] > 64 * 1024 * 1024
        assert payload["retry"]["required_changes"] == [
            {"reduce_model_memory_or_increase_memory_budget_preference": True}
        ]

    def test_preference_timeout_enforced_without_explicit_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            openscad, "load_settings", lambda: _budget_settings(1.0, 8192)
        )
        prepared = _prepare_stub_openscad(tmp_path, "sleep 30")
        payload = openscad.execute_prepared(prepared)
        assert payload["ok"] is False
        assert payload["failure_code"] == "EXECUTION_TIMEOUT"
        assert "1 seconds" in payload["error"]


# ---------------------------------------------------------------------------
# VibeScript: persisted artifact contract
# ---------------------------------------------------------------------------


VIBESCRIPT_SOURCE = 'result = {"Body": doc.addObject("PartDesign::Body", "Body")}\n'


def _write_vibescript_artifact(
    project_root: Path,
    *,
    source: str,
    parameters: dict[str, Any],
    working_revision: str,
) -> Path:
    directory = project_root / "vibescript" / MODEL_ID
    directory.mkdir(parents=True)
    (directory / "model.py").write_text(source, encoding="utf-8")
    (directory / "parameters.json").write_text(json.dumps(parameters), encoding="utf-8")
    manifest = {
        "schema": vibescript.MODEL_SCHEMA,
        "model_id": MODEL_ID,
        "label": "Test Model",
        "outputs": {"Body": {"object": "Body001"}},
        "output_facts": {},
        "expected_outputs": ["Body"],
        "working_revision": working_revision,
        "accepted_revision": working_revision,
        "state": "accepted",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


class TestVibeScriptArtifactContract:
    def test_matching_revision_loads(self, tmp_path: Path) -> None:
        revision = vibescript.source_revision(VIBESCRIPT_SOURCE, {"a": 1}, ["Body"])
        _write_vibescript_artifact(
            tmp_path,
            source=VIBESCRIPT_SOURCE,
            parameters={"a": 1},
            working_revision=revision,
        )
        contract = vibescript._artifact_contract(tmp_path, MODEL_ID)
        assert contract is not None
        assert contract["working_revision"] == revision
        assert contract["accepted_revision"] == revision
        assert contract["state"] == "accepted"

    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        assert vibescript._artifact_contract(tmp_path, MODEL_ID) is None

    def test_revision_mismatch_rejected(self, tmp_path: Path) -> None:
        _write_vibescript_artifact(
            tmp_path,
            source=VIBESCRIPT_SOURCE,
            parameters={"a": 1},
            working_revision="0" * 64,
        )
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript._artifact_contract(tmp_path, MODEL_ID)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        assert payload["observed"]["manifest_revision"] == "0" * 64
        assert payload["observed"]["calculated_revision"] != "0" * 64

    def test_tampered_source_rejected(self, tmp_path: Path) -> None:
        revision = vibescript.source_revision(VIBESCRIPT_SOURCE, {}, ["Body"])
        directory = _write_vibescript_artifact(
            tmp_path,
            source=VIBESCRIPT_SOURCE,
            parameters={},
            working_revision=revision,
        )
        (directory / "model.py").write_text(
            VIBESCRIPT_SOURCE + "# tampered\n", encoding="utf-8"
        )
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript._artifact_contract(tmp_path, MODEL_ID)
        assert (
            excinfo.value.payload["failure_code"] == "MODEL_ARTIFACT_REVISION_MISMATCH"
        )

    def test_incomplete_artifact_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "vibescript" / MODEL_ID
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript._artifact_contract(tmp_path, MODEL_ID)
        assert excinfo.value.payload["failure_code"] == "MODEL_ARTIFACT_INCOMPLETE"


# ---------------------------------------------------------------------------
# VibeScript: execution budget defaults
# ---------------------------------------------------------------------------


def test_vibescript_engine_timeout_follows_executor_default() -> None:
    # The wall-clock budget includes native FreeCAD recompute time, so the
    # default is 120s; the engine must inherit it rather than pin its own.
    assert vibescript_executor.DEFAULT_MAX_SECONDS == 120.0
    assert vibescript.DEFAULT_TIMEOUT_SECONDS == vibescript_executor.DEFAULT_MAX_SECONDS


# ---------------------------------------------------------------------------
# VibeScript: transactional delete (shares the stub document)
# ---------------------------------------------------------------------------


class TestVibeScriptTransactionalDelete:
    def _setup(
        self, tmp_path: Path
    ) -> tuple[_StubDocument, _StubObject, SimpleNamespace, str]:
        revision = vibescript.source_revision(VIBESCRIPT_SOURCE, {}, ["Body"])
        _write_vibescript_artifact(
            tmp_path,
            source=VIBESCRIPT_SOURCE,
            parameters={},
            working_revision=revision,
        )
        doc = _StubDocument()
        container = doc.addObject("App::Part", "VibeScriptModel")
        for prop in (
            vibescript.PROP_MODEL_ID,
            vibescript.PROP_SOURCE,
            vibescript.PROP_REVISION,
        ):
            container.addProperty("App::PropertyString", prop, "VibeScript")
        setattr(container, vibescript.PROP_MODEL_ID, MODEL_ID)
        setattr(container, vibescript.PROP_REVISION, revision)
        service = _stub_service(doc, tmp_path)
        return doc, container, service, revision

    def test_delete_success_commits_transaction_in_order(self, tmp_path: Path) -> None:
        doc, container, service, revision = self._setup(tmp_path)
        payload = vibescript.delete_model(
            service, MODEL_ID, revision, "user requested cleanup"
        )
        assert payload["ok"] is True
        assert doc.transaction_log == ["open:Delete VibeScript model", "commit"]
        assert doc.getObject(container.Name) is None
        assert not (tmp_path / "vibescript" / MODEL_ID).exists()

    def test_delete_failure_rolls_back(self, tmp_path: Path) -> None:
        doc, container, service, revision = self._setup(tmp_path)
        doc.fail_remove = True
        payload = vibescript.delete_model(
            service, MODEL_ID, revision, "user requested cleanup"
        )
        assert payload["ok"] is False
        assert payload["failure_code"] == "DELETE_FAILED"
        assert doc.transaction_log == ["open:Delete VibeScript model", "abort"]
        assert doc.getObject(container.Name) is container
        assert (tmp_path / "vibescript" / MODEL_ID).is_dir()

    def test_delete_stale_revision_rejected_before_transaction(
        self, tmp_path: Path
    ) -> None:
        doc, container, service, _revision = self._setup(tmp_path)
        payload = vibescript.delete_model(service, MODEL_ID, "stale", "obsolete")
        assert payload["ok"] is False
        assert payload["failure_code"] == "STALE_MODEL_REVISION"
        assert doc.transaction_log == []
        assert doc.getObject(container.Name) is container
        assert (tmp_path / "vibescript" / MODEL_ID).is_dir()


# ---------------------------------------------------------------------------
# VibeScript: static excluded-builtin policy
# ---------------------------------------------------------------------------


class TestVibeScriptStaticBuiltinPolicy:
    """Reads of sandbox-excluded builtins are rejected at validation time.

    The field report hit a NameError 200 lines into geometry because the
    sandbox excluded a builtin the static validator did not check. These
    tests lock the static gate, its shadow handling (no false positives on
    script-defined names), and its agreement with the runtime allowlist.
    """

    def _violations(self, source: str) -> list[dict[str, Any]]:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source(source)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        return payload["observed"]["violations"]

    def test_excluded_builtin_read_rejected_with_line_number(self) -> None:
        violations = self._violations("x = 1\nchecker = callable\n")
        assert any(
            item["line"] == 2 and "callable" in item["reason"] for item in violations
        )

    @pytest.mark.parametrize("name", ["bytes", "id", "memoryview", "hash"])
    def test_excluded_builtin_call_rejected(self, name: str) -> None:
        violations = self._violations(f"value = {name}()\n")
        assert any(name in item["reason"] for item in violations)

    def test_disallowed_call_reported_once(self) -> None:
        # ``eval`` is both a disallowed call and an excluded builtin; one
        # call site must yield exactly one violation, not two.
        violations = self._violations("eval('1')\n")
        assert len(violations) == 1
        assert "eval" in violations[0]["reason"]

    def test_allowed_builtins_and_namespace_names_pass(self) -> None:
        vibescript.validate_source(
            "import math\n"
            "print(hasattr(doc, 'Name'), dir(params), math.pi)\n"
            "result = {'Body': doc.addObject('PartDesign::Body', 'Body')}\n"
        )

    def test_script_bound_shadows_are_not_false_positives(self) -> None:
        vibescript.validate_source(
            "def helper(callable):\n"
            "    return callable\n"
            "bytes = 3\n"
            "print(bytes)\n"
            "for id in range(2):\n"
            "    print(id)\n"
            "values = [hash for hash in range(2)]\n"
            "if (memoryview := 5):\n"
            "    print(memoryview)\n"
            "result = {'Body': doc.addObject('PartDesign::Body', 'Body')}\n"
        )

    def test_static_gate_and_runtime_allowlist_agree(self) -> None:
        # A statically banned name must be exactly a name the sandbox cannot
        # resolve at runtime: no overlap with the allowlist or the injected
        # namespace, so static and runtime policy can never contradict.
        assert not (
            vibescript._EXCLUDED_BUILTIN_NAMES & vibescript._SANDBOX_BUILTIN_NAMES
        )
        assert not (vibescript._EXCLUDED_BUILTIN_NAMES & vibescript._NAMESPACE_NAMES)


# ---------------------------------------------------------------------------
# VibeScript: per-feature failure evidence pass-through
# ---------------------------------------------------------------------------


class TestVibeScriptFeatureReportPassThrough:
    def test_execution_failure_surfaces_feature_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Worker feature evidence crosses the boundary without live mutation."""
        import VibeCADScriptedProcess as process_module

        doc = _StubDocument()
        service = _stub_service(doc, tmp_path)
        monkeypatch.setattr(
            vibescript,
            "_scripted_budgets",
            lambda: (30.0, 512 * 1024 * 1024),
        )
        monkeypatch.setattr(
            vibescript,
            "_freecadcmd_executable",
            lambda _freecad_home: Path(sys.executable),
        )
        monkeypatch.setattr(
            vibescript,
            "_freecad_home_path",
            lambda: str(Path(sys.executable).parent),
        )
        prepared = vibescript.prepare_execution(
            service,
            "vibescript.create_model",
            {
                "model_name": "Report Model",
                "source": (
                    'body = doc.addObject("PartDesign::Body", "Body")\n'
                    'raise RuntimeError("downstream victim")\n'
                ),
                "parameters": {"width": 10.0},
                "expected_outputs": ["Body"],
            },
        )

        def run_process(_command: list[str], **_kwargs: Any) -> dict[str, Any]:
            report = {
                "ok": False,
                "exception_kind": "worker_failure",
                "exception_type": "RuntimeError",
                "error": "downstream victim",
                "feature_report": {
                    "features": [{"object_name": "Body", "type": "PartDesign::Body"}]
                },
            }
            (Path(prepared["staging"]) / "result.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            return {
                "started": True,
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "cancelled": False,
                "timed_out": False,
                "memory_exceeded": False,
                "observed_memory_bytes": 0,
                "elapsed_seconds": 0.1,
            }

        monkeypatch.setattr(process_module, "run_process", run_process)
        payload = vibescript.execute_prepared(prepared)
        assert payload["ok"] is False
        report = payload["observed"]["feature_report"]
        names = [entry["object_name"] for entry in report["features"]]
        assert names == ["Body"]
        assert doc.transaction_log == []
        assert doc.Objects == []


# ---------------------------------------------------------------------------
# Cross-engine runner API parity
# ---------------------------------------------------------------------------


class TestScriptedEngineParity:
    """All three scripted engines honor the same runner API and payload keys."""

    RUNNER_API = (
        "prepare_execution",
        "execute_prepared",
        "record_failed_attempt",
        "cleanup_prepared",
        "inspect_model",
        "delete_model",
        "model_summaries",
        "stage_editor_source",
        "revert_working_to_accepted",
        "restore_output_display_modes",
        "validate_source",
        "source_revision",
    )

    SHARED_FAILURE_KEYS = frozenset(
        {
            "ok",
            "tool",
            "failure_code",
            "failure_stage",
            "error",
            "requested",
            "observed",
        }
    )

    def test_every_engine_exposes_the_full_runner_api(self) -> None:
        for module in (build123d, openscad, vibescript):
            for name in self.RUNNER_API:
                assert callable(getattr(module, name, None)), (
                    f"{module.__name__}.{name} is missing from the runner API"
                )

    def test_source_policy_failures_share_contract_keys(self) -> None:
        cases = (
            (build123d, build123d.Build123dFailure, "import os\n", "build123d"),
            (openscad, openscad.OpenSCADFailure, "   \n", "openscad"),
            (vibescript, vibescript.VibeScriptFailure, "import os\n", "vibescript"),
        )
        for module, failure_type, bad_source, tool in cases:
            with pytest.raises(failure_type) as excinfo:
                module.validate_source(bad_source)
            payload = excinfo.value.payload
            missing = self.SHARED_FAILURE_KEYS - set(payload)
            assert not missing, (
                f"{tool} failure payload missing keys: {sorted(missing)}"
            )
            assert payload["ok"] is False
            assert payload["tool"] == tool


def test_vibescript_preparation_runs_outside_document_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    owner_call_active = False
    phases: list[tuple[str, bool]] = []

    def dispatch(operation):
        nonlocal owner_call_active
        assert owner_call_active is False
        owner_call_active = True
        try:
            return operation()
        finally:
            owner_call_active = False

    monkeypatch.setattr(
        vibescript,
        "capture_execution_state",
        lambda _service, _tool, _args: phases.append(
            ("capture", owner_call_active)
        )
        or {"captured": True},
    )
    monkeypatch.setattr(
        vibescript,
        "prepare_execution_from_state",
        lambda _captured, _tool, _args: phases.append(
            ("prepare", owner_call_active)
        )
        or {
            "document_name": "Doc",
            "model_id": MODEL_ID,
            "model_name": "Thread Contract",
            "revision": "r1",
            "expected_outputs": ["Body"],
        },
    )
    monkeypatch.setattr(
        vibescript,
        "execute_prepared",
        lambda _prepared, cancellation_check=None: {
            "ok": False,
            "failure_code": "EXPECTED_TEST_FAILURE",
            "failure_stage": "execution",
            "error": "stop before publication",
        },
    )
    monkeypatch.setattr(vibescript, "record_failed_attempt", lambda *_args: {})
    monkeypatch.setattr(vibescript, "cleanup_prepared", lambda _prepared: None)

    runner = next(
        item
        for item in session._SCRIPTED_ENGINE_RUNNERS
        if item.engine == "vibescript"
    )
    payload = session._run_scripted_engine_tool(
        runner,
        SimpleNamespace(),
        "vibescript.create_model",
        {},
        document_thread_dispatch=dispatch,
        cancellation_check=None,
        progress_callback=None,
    )

    assert payload["failure_code"] == "EXPECTED_TEST_FAILURE"
    assert phases == [("capture", True), ("prepare", False)]


def test_vibescript_success_lifecycle_keeps_heavy_work_off_document_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADSession as session

    owner_call_active = False
    phases: list[tuple[str, bool]] = []

    def record(name: str, value: Any) -> Any:
        phases.append((name, owner_call_active))
        return value

    def dispatch(operation):
        nonlocal owner_call_active
        assert owner_call_active is False
        owner_call_active = True
        try:
            return operation()
        finally:
            owner_call_active = False

    prepared = {
        "document_name": "Doc",
        "model_id": MODEL_ID,
        "model_name": "Thread Contract",
        "revision": "r1",
        "expected_outputs": ["Body"],
    }
    monkeypatch.setattr(
        vibescript,
        "capture_execution_state",
        lambda _service, _tool, _args: record("capture", {"captured": True}),
    )
    monkeypatch.setattr(
        vibescript,
        "prepare_execution_from_state",
        lambda _captured, _tool, _args: record("prepare", prepared),
    )
    monkeypatch.setattr(
        vibescript,
        "execute_prepared",
        lambda _prepared, cancellation_check=None: record(
            "execute", {"ok": True}
        ),
    )
    monkeypatch.setattr(
        vibescript,
        "import_validated_outputs",
        lambda _prepared, _execution: record("import", [{"key": "Body"}]),
    )

    def commit(_service, _prepared, _execution, _imported):
        return record(
            "commit",
            {
                "ok": True,
                "outputs": [{"key": "Body"}],
                "publication": {"reference_refresh": {}},
                "_vibecad_async_artifact": {"request": True},
                "_vibecad_async_commit": {"phase": 0},
            },
        )

    def continue_commit(_service, payload):
        state = dict(payload.pop("_vibecad_async_commit"))
        record("continue_commit", None)
        if state["phase"] == 0:
            payload["_vibecad_async_rebind"] = {"phase": 0}
        else:
            payload["_vibecad_async_validation"] = {"phase": 1}
        return payload

    def finish_rebind(_service, payload, _resolved):
        record("finish_rebind", None)
        payload.pop("_vibecad_async_rebind")
        payload["_vibecad_async_commit"] = {"phase": 1}
        return payload

    def finish_validation(_service, payload, _validation):
        record("finish_validation", None)
        return payload

    def finish_artifacts(_service, payload, _result):
        record("finish_artifacts", None)
        payload.pop("_vibecad_async_artifact")
        payload.pop("_vibecad_async_validation")
        return payload

    monkeypatch.setattr(vibescript, "commit_outputs", commit)
    monkeypatch.setattr(vibescript, "continue_commit", continue_commit)
    monkeypatch.setattr(
        vibescript,
        "resolve_commit_rebind",
        lambda _payload: record("resolve_rebind", {"ok": True}),
    )
    monkeypatch.setattr(vibescript, "finish_commit_rebind", finish_rebind)
    monkeypatch.setattr(
        vibescript,
        "validate_commit",
        lambda _payload: record("validate", {"ok": True}),
    )
    monkeypatch.setattr(vibescript, "finish_commit_validation", finish_validation)
    monkeypatch.setattr(
        vibescript,
        "persist_commit_artifacts",
        lambda _payload: record("persist_artifacts", {"ok": True}),
    )
    monkeypatch.setattr(vibescript, "finish_commit_artifacts", finish_artifacts)
    monkeypatch.setattr(vibescript, "record_failed_attempt", lambda *_args: {})
    monkeypatch.setattr(
        vibescript,
        "cleanup_prepared",
        lambda _prepared: record("cleanup", None),
    )
    monkeypatch.setattr(
        session,
        "_wait_for_document_idle",
        lambda *_args, **_kwargs: record("wait", {"ok": True}),
    )

    runner = next(
        item
        for item in session._SCRIPTED_ENGINE_RUNNERS
        if item.engine == "vibescript"
    )
    payload = session._run_scripted_engine_tool(
        runner,
        SimpleNamespace(),
        "vibescript.create_model",
        {},
        document_thread_dispatch=dispatch,
        cancellation_check=None,
        progress_callback=None,
    )

    assert payload["ok"] is True
    assert phases == [
        ("capture", True),
        ("prepare", False),
        ("execute", False),
        ("wait", False),
        ("import", False),
        ("commit", True),
        ("wait", False),
        ("continue_commit", True),
        ("resolve_rebind", False),
        ("finish_rebind", True),
        ("wait", False),
        ("continue_commit", True),
        ("validate", False),
        ("finish_validation", True),
        ("persist_artifacts", False),
        ("finish_artifacts", True),
        ("cleanup", False),
    ]


# ---------------------------------------------------------------------------
# VibeScript defaults: enabled by default, default PartDesign engine
# ---------------------------------------------------------------------------


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

    assert [item["name"] for item in summary["objects"]] == [
        "Native",
        "Published",
    ]
    assert summary["objects"][1]["published_output_key"] == "Housing"


class _UnsetPreferences:
    """Stub ParamGet group where every key is unset: each getter echoes the
    fallback default it was called with, exactly like FreeCAD does for keys
    that were never written."""

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

    def GetBool(self, name: str, default: bool = False) -> bool:
        return bool(self.values.get(name, default))

    def GetString(self, name: str, default: str = "") -> str:
        return str(self.values.get(name, default))

    def GetFloat(self, name: str, default: float = 0.0) -> float:
        return float(self.values.get(name, default))

    def GetInt(self, name: str, default: int = 0) -> int:
        return int(self.values.get(name, default))

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
    """Lock the out-of-box defaults: the VibeScript preference is enabled and
    vibescript is the default PartDesign engine. These tests fail if either
    default silently regresses."""

    _SCOPE = {"project_id": "f" * 32, "title": "Default Test", "document": {}}

    def test_settings_dataclass_enables_vibescript_by_default(self) -> None:
        import VibeCADPreferences as prefs

        assert prefs.VibeCADSettings().vibescript_enabled is True
        assert prefs.VibeCADSettings().vibescript_on_bim_enabled is False

    def test_load_settings_with_unset_key_enables_vibescript(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADPreferences as prefs

        monkeypatch.setattr(prefs, "preferences", lambda: _UnsetPreferences())
        settings = prefs.load_settings()
        assert settings.vibescript_enabled is True
        assert settings.vibescript_on_bim_enabled is False

    def test_bim_opt_in_uses_the_normal_preference_round_trip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADPreferences as prefs

        stored = _RecordingPreferences()
        monkeypatch.setattr(prefs, "preferences", lambda: stored)
        prefs.save_settings(prefs.VibeCADSettings(vibescript_on_bim_enabled=True))
        assert stored.values["VibeScriptOnBIMEnabled"] is True
        assert prefs.load_settings().vibescript_on_bim_enabled is True

        prefs.reset_settings()
        assert "VibeScriptOnBIMEnabled" not in stored.values
        assert prefs.load_settings().vibescript_on_bim_enabled is False

    def test_default_engine_constant_is_vibescript_and_valid(self) -> None:
        from VibeCADProject import DEFAULT_PARTDESIGN_ENGINE, PARTDESIGN_ENGINES

        assert DEFAULT_PARTDESIGN_ENGINE == "vibescript"
        assert DEFAULT_PARTDESIGN_ENGINE in PARTDESIGN_ENGINES

    def test_fresh_manifest_seeds_vibescript_engine(self, tmp_path: Path) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        manifest = store._default_manifest(dict(self._SCOPE))
        assert manifest["partdesign_engine"] == "vibescript"

    def test_merge_preserves_explicit_engine_choices(self, tmp_path: Path) -> None:
        from VibeCADProject import PARTDESIGN_ENGINES, VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        for engine in sorted(PARTDESIGN_ENGINES):
            merged = store._merge_manifest_defaults(
                {"partdesign_engine": engine}, dict(self._SCOPE)
            )
            assert merged["partdesign_engine"] == engine

    def test_merge_defaults_missing_or_none_engine_to_vibescript(
        self, tmp_path: Path
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        for manifest in ({}, {"partdesign_engine": None}):
            merged = store._merge_manifest_defaults(dict(manifest), dict(self._SCOPE))
            assert merged["partdesign_engine"] == "vibescript"

    def test_partdesign_engine_accessor_falls_back_to_vibescript(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from VibeCADProject import VibeCADProjectStore

        store = VibeCADProjectStore("test-session", index_path=tmp_path / "index.db")
        monkeypatch.setattr(store, "load_manifest", lambda: {})
        assert store.partdesign_engine() == "vibescript"
