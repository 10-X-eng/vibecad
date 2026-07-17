# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contract tests for the VibeScript engine module (no FreeCAD required).

VibeCADVibeScript exposes the same runner API surface as the build123d and
OpenSCAD engines (prepare_execution, execute_prepared, record_failed_attempt,
cleanup_prepared, inspect_model, delete_model, model_summaries, editor
staging). Source execution is isolated from the GUI process; candidate import,
publication, and asynchronous native recompute are separate host stages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import VibeCADReferenceContracts as reference_contracts
import VibeCADVibeScript as vibescript

MODEL_ID = "b" * 32

SOURCE_OK = (
    'body = doc.addObject("PartDesign::Body", "Body")\nresult = {"Body": body}\n'
)


@pytest.fixture(autouse=True)
def _bounded_sidecar_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent from a running FreeCAD preference service."""

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
    monkeypatch.setattr(vibescript, "_freecad_home_path", lambda: str(Path(sys.executable).parent))


# ---------------------------------------------------------------------------
# Stub document objects
# ---------------------------------------------------------------------------


class _StubBoundBox:
    XMin = 0.0
    YMin = 0.0
    ZMin = 0.0
    XMax = 10.0
    YMax = 20.0
    ZMax = 30.0
    XLength = 10.0
    YLength = 20.0
    ZLength = 30.0


class _StubShape:
    def __init__(self, *, solids: int = 1, valid: bool = True) -> None:
        self.Solids = [object() for _ in range(solids)]
        self.Faces = [object() for _ in range(6)]
        self.Edges = [object() for _ in range(12)]
        self.Vertexes = [object() for _ in range(8)]
        self.Volume = 6000.0
        self.Area = 2200.0
        self.BoundBox = _StubBoundBox()
        self._valid = valid

    def isValid(self) -> bool:
        return self._valid


class _StubObject:
    def __init__(self, name: str, type_id: str) -> None:
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.PropertiesList: list[str] = []
        self.Group: list[Any] = []
        self.OutListRecursive: list[Any] = []
        if type_id != "App::Part":
            self.Shape = _StubShape()

    def addProperty(self, _type: str, name: str, _group: str = "") -> None:
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
            setattr(self, name, "")

    def removeProperty(self, name: str) -> bool:
        if name not in self.PropertiesList:
            return False
        self.PropertiesList.remove(name)
        delattr(self, name)
        return True

    def addObject(self, obj: Any) -> None:
        self.Group.append(obj)
        self.OutListRecursive.append(obj)

    def isValid(self) -> bool:
        return True


class _StubDocument:
    """Document stub with FreeCAD transaction semantics and abort rollback."""

    def __init__(self, name: str = "Doc") -> None:
        self.Name = name
        self.Objects: list[Any] = []
        self.transaction_log: list[str] = []
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
        obj = _StubObject(f"{name}{self._sequence:03d}", type_id)
        self.Objects.append(obj)
        return obj

    def getObject(self, name: str) -> Any | None:
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def findObjects(self, *, Type: str) -> list[Any]:
        return [obj for obj in self.Objects if obj.TypeId == Type]

    def removeObject(self, name: str) -> None:
        obj = self.getObject(name)
        if obj is not None:
            self.Objects.remove(obj)
            for owner in self.Objects:
                owner.Group = [item for item in owner.Group if item is not obj]
                owner.OutListRecursive = [
                    item for item in owner.OutListRecursive if item is not obj
                ]

    def recompute(self) -> None:
        pass


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


def _create_arguments(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "model_name": "Test Model",
        "source": SOURCE_OK,
        "parameters": {"width": 10.0},
        "expected_outputs": ["Body"],
    }
    arguments.update(overrides)
    return arguments


def _prepare_create(
    tmp_path: Path, doc: _StubDocument | None = None, **overrides: Any
) -> dict[str, Any]:
    doc = doc if doc is not None else _StubDocument()
    service = _stub_service(doc, tmp_path)
    return vibescript.prepare_execution(
        service, "vibescript.create_model", _create_arguments(**overrides)
    )


# ---------------------------------------------------------------------------
# Source policy
# ---------------------------------------------------------------------------


class TestVibeScriptSourcePolicy:
    def test_valid_source_passes(self) -> None:
        vibescript.validate_source(
            "import math\n"
            "from vibescript_api import SketchBuilder\n"
            "result = {'Body': doc.addObject('PartDesign::Body', 'Body')}\n"
        )

    def test_freecad_imports_allowed(self) -> None:
        vibescript.validate_source(
            "import FreeCAD\nimport Part\nimport Sketcher\nimport PartDesign\n"
            "result = {'Body': None}\n"
        )

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("")
        assert excinfo.value.payload["failure_code"] == "SOURCE_REQUIRED"

    def test_oversized_source_rejected(self) -> None:
        big = "# pad\n" * (vibescript.MAX_SOURCE_BYTES // 6 + 2)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source(big)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_TOO_LARGE"
        assert payload["observed"]["source_bytes"] > vibescript.MAX_SOURCE_BYTES

    def test_syntax_error_reports_location(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("def broken(:\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_SYNTAX_ERROR"
        assert payload["observed"]["line"] == 1

    def test_disallowed_import_rejected_with_allowed_list(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("import os\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        violations = payload["observed"]["violations"]
        assert violations and "os" in violations[0]["reason"]
        # Actionable: the message names what *is* allowed.
        assert "vibescript_api" in violations[0]["reason"]
        assert payload["retry"]["required_changes"]

    def test_disallowed_import_from_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("from subprocess import run\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "subprocess" in item["reason"] for item in payload["observed"]["violations"]
        )

    def test_relative_import_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("from . import secrets\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_POLICY_VIOLATION"

    def test_disallowed_call_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("data = open('/etc/passwd').read()\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "open" in item["reason"] for item in payload["observed"]["violations"]
        )

    def test_dunder_access_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("x = (1).__class__\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "__class__" in item["reason"] for item in payload["observed"]["violations"]
        )

    @pytest.mark.parametrize(
        "source, method",
        (
            ("import FreeCAD\nFreeCAD.newDocument('Other')\n", "newDocument"),
            ("import FreeCAD\nFreeCAD.openDocument('/tmp/other.FCStd')\n", "openDocument"),
            ("doc.saveAs('/tmp/output.FCStd')\n", "saveAs"),
        ),
    )
    def test_document_lifecycle_calls_rejected(
        self, source: str, method: str
    ) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source(source)
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            method in item["reason"] for item in payload["observed"]["violations"]
        )

    def test_builtins_name_read_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("x = __builtins__\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "__builtins__" in item["reason"]
            for item in payload["observed"]["violations"]
        )

    def test_shiboken_private_import_name_rejected(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("module = __orig_import__('os')\n")
        payload = excinfo.value.payload
        assert payload["failure_code"] == "SOURCE_POLICY_VIOLATION"
        assert any(
            "__orig_import__" in item["reason"]
            for item in payload["observed"]["violations"]
        )

    def test_violation_line_numbers_reported(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("import math\nimport socket\n")
        assert excinfo.value.payload["observed"]["violations"][0]["line"] == 2


# ---------------------------------------------------------------------------
# Failure payload parity with the other engines
# ---------------------------------------------------------------------------


class TestFailurePayloadContract:
    REQUIRED_KEYS = (
        "ok",
        "tool",
        "failure_code",
        "failure_stage",
        "error",
        "requested",
        "normalized",
        "observed",
        "candidates",
        "allowed_values",
        "state_change",
        "native_diagnostics",
        "retry",
    )

    def test_failure_payloads_carry_shared_contract_keys(self) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.validate_source("import os\n")
        payload = excinfo.value.payload
        for key in self.REQUIRED_KEYS:
            assert key in payload, key
        assert payload["tool"] == "vibescript"
        assert payload["ok"] is False
        assert payload["engine_stage"] == "source_validation"


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


class TestSourceRevision:
    def test_revision_is_deterministic(self) -> None:
        first = vibescript.source_revision(SOURCE_OK, {"a": 1.0}, ["Body"])
        second = vibescript.source_revision(SOURCE_OK, {"a": 1.0}, ["Body"])
        assert first == second
        assert len(first) == 64

    def test_revision_changes_with_any_component(self) -> None:
        base = vibescript.source_revision(SOURCE_OK, {"a": 1.0}, ["Body"])
        assert vibescript.source_revision(SOURCE_OK + "#", {"a": 1.0}, ["Body"]) != base
        assert vibescript.source_revision(SOURCE_OK, {"a": 2.0}, ["Body"]) != base
        assert vibescript.source_revision(SOURCE_OK, {"a": 1.0}, ["Other"]) != base


# ---------------------------------------------------------------------------
# prepare_execution
# ---------------------------------------------------------------------------


class TestPrepareExecution:
    def test_create_prepares_and_persists_working_candidate(
        self, tmp_path: Path
    ) -> None:
        prepared = _prepare_create(tmp_path)
        assert prepared["engine"] == "vibescript"
        assert prepared["creating"] is True
        assert prepared["expected_outputs"] == ["Body"]
        directory = Path(prepared["artifacts"]["artifact_directory"])
        assert (directory / "model.py").read_text(encoding="utf-8") == SOURCE_OK
        assert (directory / "manifest.json").is_file()
        assert (directory / "parameters.json").is_file()

    def test_invalid_model_name_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            _prepare_create(tmp_path, model_name="9bad")
        assert excinfo.value.payload["failure_code"] == "INVALID_MODEL_NAME"

    def test_invalid_parameters_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            _prepare_create(tmp_path, parameters={"width": "wide"})
        assert excinfo.value.payload["failure_code"] == "INVALID_PARAMETERS"

    def test_missing_outputs_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            _prepare_create(tmp_path, expected_outputs=[])
        assert excinfo.value.payload["failure_code"] == "OUTPUTS_REQUIRED"

    def test_policy_violating_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            _prepare_create(tmp_path, source="import os\nresult = {}\n")
        assert excinfo.value.payload["failure_code"] == "SOURCE_POLICY_VIOLATION"

    def test_duplicate_model_label_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        first = _prepare_create(tmp_path, doc=doc)
        assert first["model_id"]
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            _prepare_create(tmp_path, doc=doc)
        assert excinfo.value.payload["failure_code"] == "MODEL_NAME_EXISTS"

    def test_edit_unknown_model_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.edit_source",
                {"model_id": MODEL_ID, "expected_revision": "x", "edits": []},
            )
        assert excinfo.value.payload["failure_code"] == "MODEL_NOT_FOUND"

    def test_edit_stale_revision_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        prepared = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.edit_source",
                {
                    "model_id": prepared["model_id"],
                    "expected_revision": "stale",
                    "edits": [{"old_text": "Body", "new_text": "Plate"}],
                },
            )
        assert excinfo.value.payload["failure_code"] == "STALE_MODEL_REVISION"

    def test_edit_source_applies_unique_replacement(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        prepared = vibescript.prepare_execution(
            service,
            "vibescript.edit_source",
            {
                "model_id": created["model_id"],
                "expected_revision": created["revision"],
                "edits": [
                    {
                        "old_text": 'doc.addObject("PartDesign::Body", "Body")',
                        "new_text": 'doc.addObject("PartDesign::Body", "Plate")',
                    }
                ],
            },
        )
        assert '"Plate"' in prepared["source"]
        assert prepared["revision"] != created["revision"]

    def test_edit_source_ambiguous_replacement_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.edit_source",
                {
                    "model_id": created["model_id"],
                    "expected_revision": created["revision"],
                    "edits": [{"old_text": "Body", "new_text": "Plate"}],
                },
            )
        assert excinfo.value.payload["failure_code"] == "SOURCE_EDIT_NOT_UNIQUE"

    def test_noop_edit_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.set_parameters",
                {
                    "model_id": created["model_id"],
                    "expected_revision": created["revision"],
                    "patch": {"width": 10.0},
                },
            )
        assert excinfo.value.payload["failure_code"] == "NO_MODEL_CHANGE"

    def test_set_parameters_merges_patch(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        prepared = vibescript.prepare_execution(
            service,
            "vibescript.set_parameters",
            {
                "model_id": created["model_id"],
                "expected_revision": created["revision"],
                "patch": {"width": 12.5, "height": 3.0},
            },
        )
        assert prepared["parameters"] == {"width": 12.5, "height": 3.0}

    def test_edit_source_parameter_patch_adds_and_removes_in_one_call(
        self, tmp_path: Path
    ) -> None:
        """Schema+source evolution lands atomically in one prepared candidate.

        The source edit switches which parameter the program reads while the
        patch supplies the new value and null-removes the obsolete key, so no
        intermediate revision ever reads a missing parameter.
        """
        doc = _StubDocument()
        created = _prepare_create(
            tmp_path,
            doc=doc,
            source=(
                'angle = params["old_angle"]\n'
                'body = doc.addObject("PartDesign::Body", "Body")\n'
                'result = {"Body": body}\n'
            ),
            parameters={"width": 10.0, "old_angle": 15.0},
        )
        service = _stub_service(doc, tmp_path)
        prepared = vibescript.prepare_execution(
            service,
            "vibescript.edit_source",
            {
                "model_id": created["model_id"],
                "expected_revision": created["revision"],
                "edits": [
                    {
                        "old_text": 'params["old_angle"]',
                        "new_text": 'params["splitter_count"]',
                    }
                ],
                "parameter_patch": {"splitter_count": 4.0, "old_angle": None},
            },
        )
        assert prepared["parameters"] == {"width": 10.0, "splitter_count": 4.0}
        assert 'params["splitter_count"]' in prepared["source"]
        assert "old_angle" not in prepared["source"]
        assert prepared["revision"] != created["revision"]

    def test_edit_source_without_patch_leaves_parameters_untouched(
        self, tmp_path: Path
    ) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        prepared = vibescript.prepare_execution(
            service,
            "vibescript.edit_source",
            {
                "model_id": created["model_id"],
                "expected_revision": created["revision"],
                "edits": [
                    {
                        "old_text": '"PartDesign::Body", "Body"',
                        "new_text": '"PartDesign::Body", "Plate"',
                    }
                ],
            },
        )
        assert prepared["parameters"] == {"width": 10.0}

    def test_edit_source_empty_parameter_patch_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.edit_source",
                {
                    "model_id": created["model_id"],
                    "expected_revision": created["revision"],
                    "edits": [
                        {
                            "old_text": '"PartDesign::Body", "Body"',
                            "new_text": '"PartDesign::Body", "Plate"',
                        }
                    ],
                    "parameter_patch": {},
                },
            )
        assert excinfo.value.payload["failure_code"] == "EMPTY_PARAMETER_PATCH"
        assert "parameter_patch" in excinfo.value.payload["error"]

    def test_edit_source_non_numeric_patch_value_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.edit_source",
                {
                    "model_id": created["model_id"],
                    "expected_revision": created["revision"],
                    "edits": [
                        {
                            "old_text": '"PartDesign::Body", "Body"',
                            "new_text": '"PartDesign::Body", "Plate"',
                        }
                    ],
                    "parameter_patch": {"material": "steel"},
                },
            )
        assert excinfo.value.payload["failure_code"] == "INVALID_PARAMETERS"

    def test_unsupported_tool_rejected(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        created = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        with pytest.raises(vibescript.VibeScriptFailure) as excinfo:
            vibescript.prepare_execution(
                service,
                "vibescript.transmogrify",
                {
                    "model_id": created["model_id"],
                    "expected_revision": created["revision"],
                },
            )
        assert excinfo.value.payload["failure_code"] == "UNSUPPORTED_VIBESCRIPT_TOOL"


# ---------------------------------------------------------------------------
# execute_prepared: isolated worker lifecycle
# ---------------------------------------------------------------------------


def _worker_process_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "started": True,
        "cancelled": False,
        "memory_exceeded": False,
        "timed_out": False,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "elapsed_seconds": 0.125,
        "observed_memory_bytes": 32 * 1024 * 1024,
    }
    result.update(overrides)
    return result


class TestExecutePrepared:
    def test_success_returns_detached_worker_report_without_document_mutation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADScriptedProcess as process_module

        doc = _StubDocument()
        prepared = _prepare_create(tmp_path, doc=doc)
        captured: dict[str, Any] = {}

        def run_process(command: list[str], **kwargs: Any) -> dict[str, Any]:
            captured["command"] = list(command)
            captured.update(kwargs)
            report = {
                "ok": True,
                "outputs": [{"key": "Body", "brep_path": "Body.brep"}],
                "interfaces": {},
                "stdout": "worker output\n",
            }
            (Path(prepared["staging"]) / "result.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            return _worker_process_result()

        monkeypatch.setattr(process_module, "run_process", run_process)
        payload = vibescript.execute_prepared(prepared)

        assert payload["ok"] is True
        assert payload["stdout"] == "worker output\n"
        assert payload["elapsed_seconds"] == pytest.approx(0.125)
        assert captured["command"][1] == "--safe-mode"
        assert captured["cwd"] == Path(prepared["staging"])
        assert captured["memory_limit_bytes"] == 512 * 1024 * 1024
        assert doc.Objects == []
        assert doc.transaction_log == []

    def test_worker_contract_failure_is_structured_and_non_mutating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADScriptedProcess as process_module

        doc = _StubDocument()
        prepared = _prepare_create(tmp_path, doc=doc)

        def run_process(_command: list[str], **_kwargs: Any) -> dict[str, Any]:
            report = {
                "ok": False,
                "exception_kind": "contract_violation",
                "exception_type": "ContractViolation",
                "error": "result must contain Body",
                "stdout": "diagnostic\n",
                "failure_location": {"line": 4},
            }
            (Path(prepared["staging"]) / "result.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            return _worker_process_result(returncode=1)

        monkeypatch.setattr(process_module, "run_process", run_process)
        payload = vibescript.execute_prepared(prepared)

        assert payload["failure_code"] == "VIBESCRIPT_CONTRACT_VIOLATION"
        assert payload["failure_stage"] == "postcondition"
        assert payload["engine_stage"] == "contract"
        assert payload["observed"]["stdout"] == "diagnostic\n"
        assert payload["observed"]["failure_location"] == {"line": 4}
        assert doc.Objects == []
        assert doc.transaction_log == []

    def test_cancellation_terminates_before_publication(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADScriptedProcess as process_module

        doc = _StubDocument()
        prepared = _prepare_create(tmp_path, doc=doc)
        monkeypatch.setattr(
            process_module,
            "run_process",
            lambda *_args, **_kwargs: _worker_process_result(
                cancelled=True, returncode=-15
            ),
        )

        payload = vibescript.execute_prepared(prepared)
        assert payload["failure_code"] == "RUN_CANCELLED"
        assert payload["cancelled"] is True
        assert doc.Objects == []
        assert doc.transaction_log == []

    def test_missing_worker_result_is_not_guessed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import VibeCADScriptedProcess as process_module

        prepared = _prepare_create(tmp_path)
        monkeypatch.setattr(
            process_module,
            "run_process",
            lambda *_args, **_kwargs: _worker_process_result(returncode=7),
        )

        payload = vibescript.execute_prepared(prepared)
        assert payload["failure_code"] == "RUNNER_NO_RESULT"
        assert payload["observed"]["exit_code"] == 7


# ---------------------------------------------------------------------------
# Failed attempts, cleanup, and working-source artifacts
# ---------------------------------------------------------------------------


class TestWorkingArtifacts:
    def test_failed_attempt_persists_failure_artifacts(self, tmp_path: Path) -> None:
        prepared = _prepare_create(tmp_path, source='raise RuntimeError("boom")\n')
        failure = {
            "ok": False,
            "failure_code": "VIBESCRIPT_EXECUTION_FAILED",
            "failure_stage": "execution",
            "error": "boom",
        }
        candidate = vibescript.record_failed_attempt(prepared, failure)
        assert candidate["state"] == "draft_failed"
        attempt = Path(candidate["attempt_directory"])
        assert (attempt / "failure.json").is_file()
        assert '"status": "failed"' in (
            attempt / "manifest.json"
        ).read_text(encoding="utf-8")

    def test_cleanup_prepared_removes_only_worker_staging(self, tmp_path: Path) -> None:
        prepared = _prepare_create(tmp_path)
        artifact_directory = Path(prepared["artifacts"]["artifact_directory"])
        staging = Path(prepared["staging"])

        vibescript.cleanup_prepared(prepared)
        vibescript.cleanup_prepared(prepared)

        assert not staging.exists()
        assert artifact_directory.exists()
        assert "service" not in prepared

    def test_editor_stages_a_new_working_revision(self, tmp_path: Path) -> None:
        doc = _StubDocument()
        prepared = _prepare_create(tmp_path, doc=doc)
        service = _stub_service(doc, tmp_path)
        new_source = SOURCE_OK + "# edited\n"

        staged = vibescript.stage_editor_source(
            service, prepared["model_id"], prepared["revision"], new_source
        )

        assert staged["ok"] is True
        assert staged["changed"] is True
        assert staged["working_revision"] != prepared["revision"]
        directory = vibescript._model_directory(tmp_path, prepared["model_id"])
        assert (directory / "model.py").read_text(encoding="utf-8") == new_source

    def test_unknown_model_inspection_lists_no_models(self, tmp_path: Path) -> None:
        service = _stub_service(_StubDocument(), tmp_path)
        payload = vibescript.inspect_model(service, MODEL_ID)
        assert payload["failure_code"] == "MODEL_NOT_FOUND"
        assert payload["observed"]["available_models"] == []


# ---------------------------------------------------------------------------
# Published-reference resolution
# ---------------------------------------------------------------------------


def test_published_object_dereferences_native_link_before_forwarded_role() -> None:
    publication = SimpleNamespace(
        Name="PublishedBody",
        VibeCADScriptedRole="publication",
    )
    occurrence = SimpleNamespace(
        Name="PublishedBody001",
        LinkedObject=publication,
        # App::Link forwards this property from LinkedObject in FreeCAD.
        VibeCADScriptedRole="publication",
    )

    assert reference_contracts.published_object(occurrence) is publication
    assert reference_contracts.published_object(publication) is publication


def test_rollback_comparison_does_not_boolean_infinite_origin_geometry() -> None:
    import vibescript_executor

    class _InfiniteShape:
        def isNull(self):
            raise AssertionError("infinite construction geometry must not be sampled")

    public = {
        "name": "X_Axis",
        "type": "App::Line",
        "label": "X-axis",
        "state": ["Up-to-date"],
        "placement": [1.0, 0.0, 0.0, 0.0] * 3,
    }
    before = {**public, "_shape_restore": _InfiniteShape()}
    after = {**public, "_shape_restore": _InfiniteShape()}

    assert vibescript_executor._rollback_records_equal(before, after) is True


# ---------------------------------------------------------------------------

# Runner API surface parity
# ---------------------------------------------------------------------------


class TestRunnerApiSurface:
    def test_engine_exposes_full_runner_api(self) -> None:
        for name in (
            "prepare_execution",
            "execute_prepared",
            "import_validated_outputs",
            "commit_outputs",
            "continue_commit",
            "resolve_commit_rebind",
            "finish_commit_rebind",
            "validate_commit",
            "finish_commit_validation",
            "cancel_commit",
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
        ):
            assert callable(getattr(vibescript, name)), name

    def test_no_freecad_import_at_module_scope(self) -> None:
        import ast as ast_module
        import inspect

        source = inspect.getsource(vibescript)
        tree = ast_module.parse(source)
        top_level_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast_module.Import):
                top_level_imports.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast_module.ImportFrom):
                top_level_imports.add(str(node.module or "").split(".")[0])
        assert not top_level_imports & {"FreeCAD", "FreeCADGui", "Part", "Sketcher"}

    def test_vibescript_never_calls_synchronous_document_recompute(self) -> None:
        import ast as ast_module
        import inspect

        tree = ast_module.parse(inspect.getsource(vibescript))
        synchronous_calls = [
            node
            for node in ast_module.walk(tree)
            if isinstance(node, ast_module.Call)
            and isinstance(node.func, ast_module.Attribute)
            and node.func.attr == "recompute"
        ]

        assert synchronous_calls == []
