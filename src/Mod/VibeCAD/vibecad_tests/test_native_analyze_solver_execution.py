# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
import runpy
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    _prepare_calculix,
    _require_meaningful_result_state,
    _require_no_ignored_elmer_constraints,
    _run_with_progress_heartbeat,
)
from VibeCADNativeAnalyzeSolverExecutionProcess import run_solver_processes
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    analyze_solver_execution_capability_definition,
)
from VibeCADNativeBackground import NativeBackgroundCancelled


def _program(path: Path, source: str) -> tuple[str, tuple[str, ...]]:
    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    return sys.executable, (str(script),)


def test_solver_execution_schema_is_one_sharp_background_operation() -> None:
    definition = analyze_solver_execution_capability_definition()
    assert definition.name == "analyze.solver_execution"
    assert tuple(variant.operation for variant in definition.variants) == ("run",)
    variant = definition.variants[0]
    assert variant.background_required
    assert variant.transaction_behavior == "background"
    assert set(variant.parameters["properties"]) == {"target", "timeout_seconds"}


def test_solver_resource_scope_is_derived_from_exact_analysis(monkeypatch) -> None:
    import VibeCADNativeAnalyzeSolverExecution as execution

    target = execution.PreparedSolverTarget(
        solver=SimpleNamespace(Name="Solver"),
        kind="calculix",
        expected_state_sha256="a" * 64,
    )
    captured = execution.CapturedSolverExecutionRequest(
        target,
        (),
        3600,
        False,
        (),
    )
    monkeypatch.setattr(
        execution,
        "solver_state",
        lambda _solver: {"analysis": "StudyA"},
    )

    assert execution.solver_resource_scope(captured) == "analyze:StudyA"


def test_process_sequence_is_exact_bounded_and_shell_free(tmp_path: Path) -> None:
    first = _program(
        tmp_path / "first",
        "from pathlib import Path\n"
        'Path("first.out").write_text("first", encoding="utf-8")\n',
    )
    second = _program(
        tmp_path / "second",
        "import os\n"
        "from pathlib import Path\n"
        'assert os.environ["SAFE_VALUE"] == "exact"\n'
        'assert Path("first.out").is_file()\n'
        'Path("second.out").write_text("second", encoding="utf-8")\n',
    )
    progress = []

    result = run_solver_processes(
        (first, second),
        working_directory=str(tmp_path),
        environment={**os.environ, "SAFE_VALUE": "exact"},
        timeout_seconds=15,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
        backend="Test",
    )

    assert [stage["exit_code"] for stage in result] == [0, 0]
    assert (tmp_path / "second.out").read_text(encoding="utf-8") == "second"
    assert progress[-1] == (84, "Test result artifacts ready")


def test_process_sequence_cooperatively_terminates_on_cancel(tmp_path: Path) -> None:
    program = _program(tmp_path / "slow", "import time\ntime.sleep(30)\n")
    cancelled = threading.Event()

    def trigger() -> None:
        time.sleep(0.15)
        cancelled.set()

    threading.Thread(target=trigger, daemon=True).start()
    with pytest.raises(NativeBackgroundCancelled):
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=cancelled.is_set,
            progress=lambda _percent, _message: None,
            backend="Test",
        )


def test_long_input_generation_emits_elapsed_heartbeat_messages() -> None:
    release = threading.Event()
    progress: list[tuple[int, str]] = []

    def work() -> str:
        assert release.wait(1.0)
        return "deck"

    timer = threading.Timer(0.04, release.set)
    timer.start()
    try:
        result = _run_with_progress_heartbeat(
            work,
            progress=lambda percent, message: progress.append((percent, message)),
            percent=4,
            message="Generating CalculiX input deck",
            interval_seconds=0.01,
        )
    finally:
        timer.cancel()

    assert result == "deck"
    assert progress[0] == (4, "Generating CalculiX input deck")
    assert any("elapsed" in message for _percent, message in progress[1:])
    assert all(percent == 4 for percent, _message in progress)


def test_process_failure_returns_only_bounded_tail(tmp_path: Path) -> None:
    program = _program(
        tmp_path / "fail",
        'import sys\nsys.stdout.write("precise failure")\nraise SystemExit(7)\n',
    )

    with pytest.raises(NativeAnalyzeError, match="code 7: precise failure"):
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="Test",
        )


def test_calculix_failure_returns_structured_artifact_diagnostics(
    tmp_path: Path,
) -> None:
    program = _program(
        tmp_path / "ccx-fail",
        "from pathlib import Path\n"
        'print("Determining the structure of the matrix")\n'
        'Path("model.sta").write_text('
        '"*ERROR: a stiffness matrix coefficient is singular\\n"'
        ', encoding="utf-8")\n'
        "raise SystemExit(201)\n",
    )

    with pytest.raises(NativeAnalyzeError) as raised:
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="CalculiX",
        )

    assert raised.value.error_code == "NATIVE_ANALYZE_SOLVER_BACKEND_FAILED"
    assert "stiffness matrix coefficient is singular" in str(raised.value)
    assert raised.value.repair == {
        "backend": "CalculiX",
        "stage": 1,
        "exit_code": 201,
        "diagnostics": [
            {
                "artifact": "model.sta",
                "excerpt": "*ERROR: a stiffness matrix coefficient is singular",
            },
            {
                "artifact": "solver-1.log",
                "excerpt": "Determining the structure of the matrix",
            },
        ],
    }


def test_atomic_progress_replacement_is_a_transient_sample(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import VibeCADNativeAnalyzeSolverExecutionWorker as worker

    (tmp_path / "progress.json").write_text("{}", encoding="utf-8")
    frozen = SimpleNamespace(
        workspace=SimpleNamespace(path=tmp_path),
        request_sha256="a" * 64,
    )
    monkeypatch.setattr(
        worker,
        "_read_regular",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            worker._ArtifactChangedWhileOpening()
        ),
    )
    progress = []
    state = {"percent": 10, "message": "Starting isolated FEM solver worker"}

    worker._read_progress(
        frozen,
        lambda percent, message: progress.append((percent, message)),
        state,
    )

    assert progress == []
    assert state == {
        "percent": 10,
        "message": "Starting isolated FEM solver worker",
    }


def test_non_transient_progress_validation_failure_remains_fatal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import VibeCADNativeAnalyzeSolverExecutionWorker as worker

    (tmp_path / "progress.json").write_text("{}", encoding="utf-8")
    frozen = SimpleNamespace(
        workspace=SimpleNamespace(path=tmp_path),
        request_sha256="a" * 64,
    )
    malformed = NativeAnalyzeError(
        "The isolated FEM progress report is malformed.",
        error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_INVALID",
    )
    monkeypatch.setattr(
        worker,
        "_read_regular",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(malformed),
    )

    with pytest.raises(NativeAnalyzeError) as raised:
        worker._read_progress(
            frozen,
            lambda _percent, _message: None,
            {"percent": 10, "message": "Starting isolated FEM solver worker"},
        )

    assert raised.value is malformed


def test_openfoam_failure_reports_the_fatal_reason_not_the_stack(
    tmp_path: Path,
) -> None:
    program = _program(
        tmp_path / "foam-fail",
        'print("banner")\n'
        'print("--> FOAM FATAL ERROR:")\n'
        'print("No coarse levels created; refine the volume mesh.")\n'
        'print("From function Foam::GAMGSolver")\n'
        'print("stack detail")\n'
        "raise SystemExit(1)\n",
    )

    with pytest.raises(NativeAnalyzeError) as raised:
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="Openfoam",
        )

    message = str(raised.value)
    assert "No coarse levels created; refine the volume mesh." in message
    assert "stack detail" not in message


def test_solver_execution_rejects_ignored_elmer_constraints() -> None:
    tool = type(
        "ElmerTool",
        (),
        {"ignored_constraints": (type("Constraint", (), {"Label": "Fixed end"})(),)},
    )()

    with pytest.raises(NativeAnalyzeError, match="Fixed end"):
        _require_no_ignored_elmer_constraints(tool)


def test_calculix_prerequisite_failure_is_provider_actionable() -> None:
    class Tool:
        def prepare(self) -> None:
            raise RuntimeError(
                "CalculiX prerequisites failed:\n"
                "Thermomechanical analysis: No initial temperature defined.\n"
            )

    with pytest.raises(NativeAnalyzeError) as raised:
        _prepare_calculix(Tool())

    assert raised.value.error_code == "NATIVE_ANALYZE_SOLVER_NOT_READY"
    assert str(raised.value) == (
        "CalculiX prerequisites are incomplete: "
        "Thermomechanical analysis: No initial temperature defined."
    )


def test_solver_execution_rejects_empty_result_data() -> None:
    with pytest.raises(NativeAnalyzeError, match="no result fields"):
        _require_meaningful_result_state(
            {"result_kind": "pipeline", "field_count": 0, "data_available": False}
        )

    _require_meaningful_result_state({"result_kind": "result", "field_count": 2})


def test_solver_capture_does_not_generate_case_files(monkeypatch) -> None:
    import VibeCADNativeAnalyzeSolverExecution as execution

    solver = SimpleNamespace(Name="Solver", Document=object())
    target = SimpleNamespace(
        solver=solver,
        kind="openfoam",
        expected_state_sha256="a" * 64,
    )
    history = (solver,)
    monkeypatch.setattr(execution, "prepare_solver_target", lambda *_args: target)
    monkeypatch.setattr(
        execution,
        "solver_state",
        lambda *_args: {"suppressed": False},
    )
    monkeypatch.setattr(execution, "_require_history_root", lambda *_args: history)
    monkeypatch.setattr(execution, "_current_keep_results", lambda: True)
    monkeypatch.setattr(
        execution,
        "_current_solver_runtime_preferences",
        lambda _kind: (
            (
                "User parameter:BaseApp/Preferences/Mod/Fem/General",
                "KeepResultsOnReRun",
                "bool",
                True,
            ),
            ("preferences", "threads", "int", 6),
        ),
    )
    monkeypatch.setattr(
        execution,
        "_openfoam_request",
        lambda *_args: pytest.fail("capture must not generate solver input"),
    )

    captured = execution.capture_solver_execution_request(
        solver.Document,
        "document-a",
        target={
            "object_name": "Solver",
            "expected_state_sha256": "a" * 64,
        },
        timeout_seconds=7200,
    )

    assert captured.target is target
    assert captured.history_operations == history
    assert captured.timeout_seconds == 7200
    assert captured.keep_results is True
    assert captured.runtime_preferences == (
        (
            "User parameter:BaseApp/Preferences/Mod/Fem/General",
            "KeepResultsOnReRun",
            "bool",
            True,
        ),
        ("preferences", "threads", "int", 6),
    )


def test_solver_capture_rejects_assignments_outside_the_generated_mesh(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSolverExecution as execution

    analysis = SimpleNamespace(Name="Analysis")
    document = SimpleNamespace(
        getObject=lambda name: analysis if name == "Analysis" else None
    )
    target = SimpleNamespace(
        solver=SimpleNamespace(Name="Solver", Document=document),
        kind="calculix",
        expected_state_sha256="a" * 64,
    )
    monkeypatch.setattr(execution, "prepare_solver_target", lambda *_args: target)
    monkeypatch.setattr(
        execution,
        "solver_state",
        lambda *_args: {"suppressed": False, "analysis": "Analysis"},
    )
    monkeypatch.setattr(
        execution,
        "validate_assignments",
        lambda _analysis: {
            "valid": False,
            "issue_count": 1,
            "issues": [
                {
                    "message": (
                        "Contact references Body043, which is outside every "
                        "generated mesh domain in this analysis."
                    )
                }
            ],
        },
    )

    with pytest.raises(NativeAnalyzeError) as raised:
        execution.capture_solver_execution_request(
            document,
            "document-a",
            target={
                "object_name": "Solver",
                "expected_state_sha256": "a" * 64,
            },
            timeout_seconds=7200,
        )

    assert raised.value.error_code == "NATIVE_ANALYZE_SOLVER_NOT_READY"
    assert "Body043" in str(raised.value)
    assert raised.value.repair == {"analysis": "Analysis", "issue_count": 1}


def test_solver_capture_validation_rejects_runtime_preference_changes(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSolverExecution as execution

    document = object()
    solver = SimpleNamespace(Document=document)
    captured = execution.CapturedSolverExecutionRequest(
        SimpleNamespace(
            solver=solver,
            kind="calculix",
            expected_state_sha256="c" * 64,
        ),
        (solver,),
        3600,
        False,
        (
            (
                "User parameter:BaseApp/Preferences/Mod/Fem/General",
                "KeepResultsOnReRun",
                "bool",
                False,
            ),
            ("preferences", "threads", "int", 6),
        ),
    )
    monkeypatch.setattr(execution, "solver_still_exact", lambda *_args: True)
    monkeypatch.setattr(execution, "_require_history_root", lambda *_args: (solver,))
    monkeypatch.setattr(execution, "_current_keep_results", lambda: False)
    monkeypatch.setattr(
        execution,
        "_current_solver_runtime_preferences",
        lambda _kind: (
            (
                "User parameter:BaseApp/Preferences/Mod/Fem/General",
                "KeepResultsOnReRun",
                "bool",
                False,
            ),
            ("preferences", "threads", "int", 8),
        ),
    )

    with pytest.raises(NativeAnalyzeError, match="runtime preferences changed"):
        execution.validate_captured_solver_execution(document, captured)


def test_solver_runtime_preferences_are_scoped_to_selected_backend(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSolverExecution as execution

    class Group:
        def GetBool(self, _name, default):
            return default

        def GetInt(self, _name, default):
            return default

        def GetString(self, name, default):
            return f"configured-{name}" if name.endswith("BinaryPath") else default

    freecad = SimpleNamespace(
        ParamGet=lambda _path: Group(),
        Units=SimpleNamespace(Scheme=SimpleNamespace(Internal=7)),
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", freecad)

    preferences = execution._current_solver_runtime_preferences("calculix")
    paths = {path for path, _name, _kind, _value in preferences}

    assert "User parameter:BaseApp/Preferences/Mod/Fem/Ccx" in paths
    assert "User parameter:BaseApp/Preferences/Mod/Fem/Elmer" not in paths
    assert "User parameter:BaseApp/Preferences/Mod/Fem/OpenFOAM" not in paths
    assert (
        "User parameter:BaseApp/Preferences/Units",
        "UserSchema",
        "int",
        7,
    ) in preferences


def test_solver_snapshot_is_materialized_only_inside_owned_workspace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import VibeCADNativeAnalyzeSolverExecutionInput as execution_input

    snapshot_calls = []

    class Document:
        def saveCopy(self, path: str) -> bool:
            snapshot_calls.append(path)
            Path(path).write_bytes(b"FCStd snapshot")
            return True

    captured = SimpleNamespace(
        target=SimpleNamespace(
            solver=SimpleNamespace(Name="Solver", ID=17, TypeId="Fem::FemSolverObject"),
            kind="calculix",
            expected_state_sha256="b" * 64,
        ),
        history_operations=(),
        timeout_seconds=3600,
        keep_results=False,
        runtime_preferences=(),
    )
    workspace = execution_input.SolverExecutionWorkspace(
        temporary=SimpleNamespace(cleanup=lambda: None),
        path=tmp_path,
        freecadcmd=SimpleNamespace(path=tmp_path / "FreeCADCmd.exe"),
        child=SimpleNamespace(path=tmp_path / "solver-child.py"),
    )
    workspace.freecadcmd.path.write_bytes(b"exe")
    workspace.child.path.write_bytes(b"child")
    monkeypatch.setattr(
        execution_input,
        "validate_captured_solver_execution",
        lambda *_args: None,
    )

    materialized = execution_input.materialize_solver_execution_snapshot(
        Document(),
        captured,
        workspace,
    )
    frozen = execution_input.freeze_solver_execution_snapshot(materialized)

    assert snapshot_calls == [str(tmp_path / "document.FCStd")]
    assert materialized.snapshot_path == tmp_path / "document.FCStd"
    assert frozen.snapshot.path == tmp_path / "document.FCStd"
    assert frozen.request.path == tmp_path / "request.json"
    assert frozen.solver_name == "Solver"


def test_human_and_ai_solver_entrypoints_do_not_call_synchronous_case_builder() -> None:
    root = Path(__file__).resolve().parents[1]
    human = (root / "VibeCADAnalyzeSolverGui.py").read_text(encoding="utf-8")
    ai = (root / "VibeCADNativeAnalyzeSolverExecutionRuntime.py").read_text(
        encoding="utf-8"
    )

    for source in (human, ai):
        assert "capture_solver_execution_request" in source
        assert "prepare_solver_execution_request" not in source
        assert "execute_frozen_solver_execution" in source


def test_human_solver_progress_is_mirrored_to_the_status_bar(monkeypatch) -> None:
    pyside = ModuleType("PySide")
    pyside.QtCore = SimpleNamespace()
    pyside.QtWidgets = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    import VibeCADAnalyzeSolverGui as solver_gui

    dialog_updates: list[tuple[str, object]] = []
    status_updates: list[str] = []
    runner = object.__new__(solver_gui._SolverRunUi)
    runner.job_id = "solver-job"
    runner.backend = "CalculiX"
    runner.manager = SimpleNamespace(
        snapshot=lambda _job_id: SimpleNamespace(
            progress_percent=37,
            progress_message="Running CalculiX stage 1/1 (65s elapsed)",
            terminal=False,
        )
    )
    runner.dialog = SimpleNamespace(
        setValue=lambda value: dialog_updates.append(("percent", value)),
        setLabelText=lambda value: dialog_updates.append(("message", value)),
    )
    monkeypatch.setattr(
        solver_gui.Gui,
        "getMainWindow",
        lambda: SimpleNamespace(
            statusBar=lambda: SimpleNamespace(
                showMessage=lambda value, *_args: status_updates.append(value)
            )
        ),
        raising=False,
    )

    runner.poll()

    assert dialog_updates == [
        ("percent", 37),
        ("message", "Running CalculiX stage 1/1 (65s elapsed)"),
    ]
    assert status_updates == [
        "CalculiX: Running CalculiX stage 1/1 (65s elapsed)"
    ]


def test_ai_solver_progress_is_mirrored_to_the_status_bar(monkeypatch) -> None:
    pyside = ModuleType("PySide")
    pyside.QtCore = SimpleNamespace()
    pyside.QtWidgets = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "PySide", pyside)
    import VibeCADAnalyzeSolverGui as solver_gui

    status_updates: list[str] = []
    watcher = object.__new__(solver_gui._SolverJobStatusUi)
    watcher.job_id = "ai-solver-job"
    watcher.backend = "CalculiX"
    watcher.manager = SimpleNamespace(
        snapshot=lambda _job_id: SimpleNamespace(
            progress_percent=23,
            progress_message="Generating CalculiX input deck (95s elapsed)",
            terminal=False,
        )
    )
    monkeypatch.setattr(
        solver_gui.Gui,
        "getMainWindow",
        lambda: SimpleNamespace(
            statusBar=lambda: SimpleNamespace(
                showMessage=lambda value, *_args: status_updates.append(value)
            )
        ),
        raising=False,
    )

    watcher.poll()

    assert status_updates == [
        "CalculiX: Generating CalculiX input deck (95s elapsed)"
    ]


def test_analyze_preferences_expose_the_openfoam_environment_contract() -> None:
    repository = Path(__file__).resolve().parents[4]
    gui = repository / "src" / "Mod" / "Fem" / "Gui"
    app_source = (gui / "AppFemGui.cpp").read_text(encoding="utf-8")
    cmake_source = (gui / "CMakeLists.txt").read_text(encoding="utf-8")
    init_gui_source = (
        repository / "src" / "Mod" / "Fem" / "InitGui.py"
    ).read_text(encoding="utf-8")
    runtime_source = (
        repository / "src" / "Mod" / "Fem" / "femsolver" / "runtime.py"
    ).read_text(encoding="utf-8")
    openfoam_ui = (gui / "DlgSettingsFemOpenFOAM.ui").read_text(encoding="utf-8")

    assert '#include "DlgSettingsFemOpenFOAMImp.h"' in app_source
    assert "PrefPageProducer<FemGui::DlgSettingsFemOpenFOAMImp>" in app_source
    assert (
        'setGroupData(\n        "Analyze", "fem"' in app_source
    )
    assert app_source.count('QT_TRANSLATE_NOOP("QObject", "Analyze")') == 7
    assert 'QT_TRANSLATE_NOOP("QObject", "FEM")' not in app_source
    assert (
        'addPreferencePage(fempreferencepages.DlgSettingsNetgen, "Analyze")'
        in init_gui_source
    )
    assert (
        'addPreferencePage(fempreferencepages.DlgSettingsNetgen, "FEM")'
        not in init_gui_source
    )
    for name in (
        "DlgSettingsFemOpenFOAM.ui",
        "DlgSettingsFemOpenFOAMImp.cpp",
        "DlgSettingsFemOpenFOAMImp.h",
    ):
        assert name in cmake_source
    assert "<cstring>EnvironmentFile</cstring>" in openfoam_ui
    assert "<cstring>Mod/Fem/OpenFOAM</cstring>" in openfoam_ui
    assert (
        '_OPENFOAM_PARAMETER_PATH = "User parameter:BaseApp/Preferences/Mod/Fem/OpenFOAM"'
        in runtime_source
    )
    assert '_OPENFOAM_ENVIRONMENT_KEY = "EnvironmentFile"' in runtime_source


def test_analyze_preferences_register_before_workbench_activation(monkeypatch) -> None:
    repository = Path(__file__).resolve().parents[4]
    preference_pages: list[tuple[object, str]] = []
    workbenches: list[object] = []

    class Workbench:
        pass

    freecad = ModuleType("FreeCAD")
    freecad.__cmake__ = ""
    freecad.__unit_test__ = []
    freecad.getResourceDir = lambda: "resources/"

    freecad_gui = ModuleType("FreeCADGui")
    freecad_gui.Workbench = Workbench
    freecad_gui.addWorkbench = workbenches.append
    freecad_gui.addPreferencePage = lambda page, group: preference_pages.append(
        (page, group)
    )

    migrate_gui = ModuleType("femguiutils.migrate_gui")

    class FemMigrateGui:
        pass

    migrate_gui.FemMigrateGui = FemMigrateGui
    femguiutils = ModuleType("femguiutils")
    femguiutils.__path__ = []
    fem = ModuleType("Fem")
    fem_gui = ModuleType("FemGui")
    fem_commands = ModuleType("femcommands")
    fem_commands.__path__ = []
    command_module = ModuleType("femcommands.commands")
    fem_commands.commands = command_module
    preferences = ModuleType("fempreferencepages")
    preferences.DlgSettingsNetgen = type("DlgSettingsNetgen", (), {})

    for name, module in (
        ("FreeCAD", freecad),
        ("FreeCADGui", freecad_gui),
        ("Fem", fem),
        ("FemGui", fem_gui),
        ("femcommands", fem_commands),
        ("femcommands.commands", command_module),
        ("fempreferencepages", preferences),
        ("femguiutils", femguiutils),
        ("femguiutils.migrate_gui", migrate_gui),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    old_meta_path = list(sys.meta_path)
    try:
        runpy.run_path(str(repository / "src" / "Mod" / "Fem" / "InitGui.py"))
        assert preference_pages == [(preferences.DlgSettingsNetgen, "Analyze")]
        assert len(workbenches) == 1

        workbenches[0].Initialize()
        assert preference_pages == [(preferences.DlgSettingsNetgen, "Analyze")]
    finally:
        sys.meta_path[:] = old_meta_path


def test_authenticated_worker_result_reuses_only_parent_commit_guards(
    tmp_path: Path,
) -> None:
    import json

    import VibeCADNativeAnalyzeSolverExecutionWorker as worker
    from VibeCADNativeAnalyzeSolverExecution import CapturedSolverExecutionRequest
    from VibeCADNativeAnalyzeSolverExecutionInput import (
        ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        FrozenSolverExecution,
        SolverExecutionWorkspace,
    )

    case = tmp_path / "case"
    case.mkdir()
    target = SimpleNamespace(
        solver=SimpleNamespace(Name="Solver"),
        kind="calculix",
        expected_state_sha256="d" * 64,
    )
    history = (object(), object())
    runtime_preferences = (
        (
            "User parameter:BaseApp/Preferences/Mod/Fem/General",
            "KeepResultsOnReRun",
            "bool",
            False,
        ),
    )
    captured = CapturedSolverExecutionRequest(
        target,
        history,
        7200,
        False,
        runtime_preferences,
    )
    request_sha256 = "e" * 64
    workspace = SolverExecutionWorkspace(
        temporary=SimpleNamespace(cleanup=lambda: None),
        path=tmp_path,
        freecadcmd=SimpleNamespace(),
        child=SimpleNamespace(),
    )
    frozen = FrozenSolverExecution(
        workspace=workspace,
        captured=captured,
        snapshot=SimpleNamespace(),
        request=SimpleNamespace(),
        request_sha256=request_sha256,
        solver_name="Solver",
    )
    result = {
        "ok": True,
        "protocol": ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        "request_sha256": request_sha256,
        "solver_name": "Solver",
        "solver_kind": "calculix",
        "solver_state_sha256": "d" * 64,
        "implementation": "pipeline",
        "case": "case",
        "input_sha256": "f" * 64,
        "input_file_count": 3,
        "keep_results": False,
        "importer_state": {"input_deck": "model"},
        "stages": [{"stage": 1, "program": "ccx.exe", "exit_code": 0}],
    }
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")

    prepared = worker._read_result(frozen)

    assert prepared.request.target is target
    assert prepared.request.history_operations == history
    assert prepared.request.commands == ()
    assert prepared.request.environment == {}
    assert prepared.request.runtime_preferences == runtime_preferences
    assert prepared.stages == ({"stage": 1, "program": "ccx.exe", "exit_code": 0},)


def test_worker_preserves_structured_backend_failure_diagnostics(
    tmp_path: Path,
) -> None:
    import json

    import VibeCADNativeAnalyzeSolverExecutionWorker as worker
    from VibeCADNativeAnalyzeSolverExecutionInput import (
        ANALYZE_SOLVER_EXECUTION_PROTOCOL,
    )

    frozen = SimpleNamespace(
        workspace=SimpleNamespace(path=tmp_path),
        request_sha256="a" * 64,
    )
    result = {
        "ok": False,
        "protocol": ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        "request_sha256": "a" * 64,
        "error_code": "NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
        "message": "CalculiX exited with code 201.",
        "repair": {
            "backend": "CalculiX",
            "stage": 1,
            "exit_code": 201,
            "diagnostics": [
                {"artifact": "model.sta", "excerpt": "*ERROR: singular matrix"}
            ],
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(NativeAnalyzeError) as raised:
        worker._read_result(frozen)

    assert raised.value.repair == result["repair"]


def test_worker_rejects_backend_implementation_substitution(tmp_path: Path) -> None:
    import json

    import VibeCADNativeAnalyzeSolverExecutionWorker as worker
    from VibeCADNativeAnalyzeSolverExecution import CapturedSolverExecutionRequest
    from VibeCADNativeAnalyzeSolverExecutionInput import (
        ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        FrozenSolverExecution,
        SolverExecutionWorkspace,
    )

    (tmp_path / "case").mkdir()
    target = SimpleNamespace(
        solver=SimpleNamespace(Name="Solver"),
        kind="elmer",
        expected_state_sha256="1" * 64,
    )
    frozen = FrozenSolverExecution(
        workspace=SolverExecutionWorkspace(
            temporary=SimpleNamespace(cleanup=lambda: None),
            path=tmp_path,
            freecadcmd=SimpleNamespace(),
            child=SimpleNamespace(),
        ),
        captured=CapturedSolverExecutionRequest(target, (), 60, False, ()),
        snapshot=SimpleNamespace(),
        request=SimpleNamespace(),
        request_sha256="2" * 64,
        solver_name="Solver",
    )
    result = {
        "ok": True,
        "protocol": ANALYZE_SOLVER_EXECUTION_PROTOCOL,
        "request_sha256": "2" * 64,
        "solver_name": "Solver",
        "solver_kind": "elmer",
        "solver_state_sha256": "1" * 64,
        "implementation": "openfoam",
        "case": "case",
        "input_sha256": "3" * 64,
        "input_file_count": 1,
        "keep_results": False,
        "importer_state": {"result_format": ".vtu"},
        "stages": [{"stage": 1, "program": "ElmerSolver", "exit_code": 0}],
    }
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(NativeAnalyzeError, match="protocol validation"):
        worker._read_result(frozen)
