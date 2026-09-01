# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached FEM solver input, execution, and exact result publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, TypeVar

from VibeCADNativeAnalyzeAssignments import validate_assignments
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecutionProcess import run_solver_processes
from VibeCADNativeAnalyzeSolverState import (
    PreparedSolverTarget,
    prepare_solver_target,
    solver_state,
    solver_still_exact,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity

MAX_INPUT_FILES = 4096
MAX_INPUT_BYTES = 4 * 1024 * 1024 * 1024
_T = TypeVar("_T")
_FEM_PREFERENCES = "User parameter:BaseApp/Preferences/Mod/Fem"
_SOLVER_RUNTIME_PREFERENCE_SPECS = (
    (_FEM_PREFERENCES + "/General", "KeepResultsOnReRun", "bool", False, None),
    (_FEM_PREFERENCES + "/Ccx", "ccxBinaryPath", "string", "", "calculix"),
    (_FEM_PREFERENCES + "/Ccx", "AnalysisNumCPUs", "int", 1, "calculix"),
    (_FEM_PREFERENCES + "/Ccx", "BinaryOutput", "bool", False, "calculix"),
    (_FEM_PREFERENCES + "/Elmer", "gridBinaryPath", "string", "", "elmer"),
    (_FEM_PREFERENCES + "/Elmer", "elmerBinaryPath", "string", "", "elmer"),
    (_FEM_PREFERENCES + "/Elmer", "mpiBinaryPath", "string", "", "elmer"),
    (_FEM_PREFERENCES + "/Elmer", "NumberOfTasks", "int", 1, "elmer"),
    (_FEM_PREFERENCES + "/Elmer", "ThreadsPerTask", "int", 1, "elmer"),
    (_FEM_PREFERENCES + "/Elmer", "MaxOutputLevel", "int", 10, "elmer"),
    (_FEM_PREFERENCES + "/Mystran", "mystranBinaryPath", "string", "", "mystran"),
    (_FEM_PREFERENCES + "/Z88", "z88BinaryPath", "string", "", "z88"),
    (
        _FEM_PREFERENCES + "/OpenFOAM",
        "EnvironmentFile",
        "string",
        "",
        "openfoam",
    ),
    ("User parameter:BaseApp/Preferences/Units", "UserSchema", "int", None, None),
)


@dataclass(frozen=True, slots=True)
class SolverExecutionRequest:
    target: PreparedSolverTarget
    implementation: str
    history_operations: tuple[Any, ...]
    working_directory: str
    commands: tuple[tuple[str, tuple[str, ...]], ...]
    environment: Mapping[str, str]
    timeout_seconds: int
    input_sha256: str
    input_file_count: int
    keep_results: bool
    importer_state: Mapping[str, Any]
    runtime_preferences: tuple[tuple[str, str, str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CapturedSolverExecutionRequest:
    """Exact live-document guards captured before detached case generation."""

    target: PreparedSolverTarget
    history_operations: tuple[Any, ...]
    timeout_seconds: int
    keep_results: bool
    runtime_preferences: tuple[tuple[str, str, str, Any], ...]


@dataclass(frozen=True, slots=True)
class PreparedSolverExecution:
    request: SolverExecutionRequest
    stages: tuple[dict[str, Any], ...]


def _timeout(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 86400:
        raise NativeAnalyzeError("timeout_seconds must be an integer from 1 to 86400.")
    return value


def _require_history_root(document: Any, solver: Any) -> tuple[Any, ...]:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        solver not in operations
        or str(getattr(solver, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(solver, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM solver is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    return operations


def _input_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise NativeAnalyzeError(
                "A detached FEM input contains an unsafe symbolic link."
            )
        if not path.is_file():
            continue
        count += 1
        size += path.stat().st_size
        if count > MAX_INPUT_FILES or size > MAX_INPUT_BYTES:
            raise NativeAnalyzeError(
                "The detached FEM input exceeds 4096 files or 4 GiB.",
                error_code="NATIVE_ANALYZE_SOLVER_INPUT_LIMIT",
            )
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    if count == 0:
        raise NativeAnalyzeError("The FEM solver input writer produced no artifacts.")
    return digest.hexdigest(), count


def _executable(program: str, label: str) -> str:
    resolved = Path(str(program)).resolve()
    if not resolved.is_file():
        raise NativeAnalyzeError(
            f"The human-configured {label} executable is unavailable.",
            error_code="NATIVE_ANALYZE_SOLVER_UNAVAILABLE",
        )
    return str(resolved)


def _prepare_calculix(tool: Any) -> None:
    try:
        tool.prepare()
    except RuntimeError as exc:
        detail = " ".join(str(exc).split())
        prefix = "CalculiX prerequisites failed:"
        if detail.startswith(prefix):
            detail = detail.removeprefix(prefix).strip()
        message = "CalculiX prerequisites are incomplete"
        if detail:
            message += ": " + detail
        raise NativeAnalyzeError(
            message + "." if not message.endswith(".") else message,
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        ) from exc


def _run_with_progress_heartbeat(
    work: Callable[[], _T],
    *,
    progress: Callable[[int, str], None] | None,
    percent: int,
    message: str,
    interval_seconds: float = 5.0,
) -> _T:
    """Run one opaque library call while publishing honest elapsed-time liveness."""

    if progress is None:
        return work()
    progress(percent, message)
    stopped = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stopped.wait(interval_seconds):
            elapsed = max(1, int(time.monotonic() - started))
            progress(percent, f"{message} ({elapsed}s elapsed)")

    thread = threading.Thread(
        target=heartbeat,
        name="VibeCAD-CalculiX-input-progress",
        daemon=True,
    )
    thread.start()
    try:
        return work()
    finally:
        stopped.set()
        thread.join(timeout=max(0.1, min(interval_seconds, 1.0)))


def _calculix_request(
    solver: Any,
    root: Path,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[Any, tuple, dict, dict]:
    from femsolver import settings
    from femsolver.calculix.calculixtools import CalculiXTools

    tool = CalculiXTools(solver, detached=True, working_directory=str(root))
    _run_with_progress_heartbeat(
        lambda: _prepare_calculix(tool),
        progress=progress,
        percent=4,
        message="Generating CalculiX input deck",
    )
    if progress is not None:
        progress(6, "CalculiX input deck generated")
    program = _executable(settings.require_binary("Calculix"), "CalculiX")
    arguments = ("-i", str(root / tool.input_deck))
    environment = dict(os.environ)
    threads = tool.fem_param.GetGroup("Ccx").GetInt("AnalysisNumCPUs", 1)
    environment["OMP_NUM_THREADS"] = str(max(1, threads))
    environment["PASTIX_MIXED_PRECISION"] = "1" if solver.PastixMixedPrecision else "0"
    return tool, ((program, arguments),), environment, {"input_deck": tool.input_deck}


def _ccx_tools_request(
    solver: Any,
    root: Path,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[Any, tuple, dict, dict]:
    import FreeCAD

    from femsolver import settings
    from femtools.ccxtools import CcxTools

    tool = CcxTools(solver)
    tool.update_objects()
    tool.setup_working_dir(str(root))
    readiness = str(tool.check_prerequisites() or "").strip()
    if readiness:
        raise NativeAnalyzeError(
            "CalculiX input prerequisites are incomplete: " + readiness,
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
        )
    _run_with_progress_heartbeat(
        tool.write_inp_file,
        progress=progress,
        percent=4,
        message="Generating CalculiX input deck",
    )
    if progress is not None:
        progress(6, "CalculiX input deck generated")
    input_path = Path(str(tool.inp_file_name)).resolve()
    if input_path.parent != root.resolve() or not input_path.is_file():
        raise NativeAnalyzeError(
            "The CalculiX input writer escaped its detached working directory."
        )
    program = _executable(settings.require_binary("Calculix"), "CalculiX")
    environment = dict(os.environ)
    threads = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Ccx").GetInt(
        "AnalysisNumCPUs", 1
    )
    environment["OMP_NUM_THREADS"] = str(max(1, threads))
    environment["PASTIX_MIXED_PRECISION"] = (
        "1" if bool(getattr(solver, "PastixMixedPrecision", False)) else "0"
    )
    return (
        tool,
        ((program, ("-i", input_path.stem)),),
        environment,
        {"input_file": str(input_path)},
    )


def _elmer_request(solver: Any, root: Path) -> tuple[Any, tuple, dict, dict]:
    from femsolver import settings
    from femsolver.elmer.elmertools import ElmerTools

    tool = ElmerTools(solver, detached=True, working_directory=str(root))
    tool.prepare(run_grid=False)
    _require_no_ignored_elmer_constraints(tool)
    commands = [
        (_executable(program, "ElmerGrid"), tuple(arguments))
        for program, arguments in tool.grid_commands
    ]
    task_count = tool.fem_param.GetGroup("Elmer").GetInt("NumberOfTasks", 1)
    solver_program = _executable(settings.require_binary("ElmerSolver"), "ElmerSolver")
    if task_count > 1:
        mpi = _executable(settings.require_binary("MPIElmer"), "MPI Elmer")
        commands.append((mpi, ("-n", str(task_count), solver_program)))
        result_format = ".pvtu"
    else:
        commands.append((solver_program, ()))
        result_format = ".vtu"
    if str(solver.SimulationType) == "Transient":
        result_format = ".pvd"
    environment = dict(os.environ)
    threads = tool.fem_param.GetGroup("Elmer").GetInt("ThreadsPerTask", 1)
    environment["OMP_NUM_THREADS"] = str(max(1, threads))
    return tool, tuple(commands), environment, {"result_format": result_format}


def _require_no_ignored_elmer_constraints(tool: Any) -> None:
    ignored = tuple(getattr(tool, "ignored_constraints", ()) or ())
    if not ignored:
        return
    labels = ", ".join(
        str(getattr(value, "Label", "") or value.Name) for value in ignored
    )
    raise NativeAnalyzeError(
        "Elmer has no active equation for these study conditions: " + labels + ".",
        error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
    )


def _z88_request(solver: Any, root: Path) -> tuple[Any, tuple, dict, dict]:
    from femsolver import settings
    from femsolver.z88.z88tools import Z88Tools

    tool = Z88Tools(solver, detached=True, working_directory=str(root))
    tool.prepare()
    program = _executable(settings.require_binary("Z88"), "Z88")
    test = (program, ("-t", "-" + str(solver.SolverType)))
    commands = (
        (test,)
        if str(solver.AnalysisType) == "test"
        else (
            test,
            (program, ("-c", "-" + str(solver.SolverType))),
        )
    )
    return tool, commands, dict(os.environ), {}


def _mystran_request(solver: Any, root: Path) -> tuple[Any, tuple, dict, dict]:
    from femsolver import settings
    from femsolver.mystran import tasks
    from femsolver.mystran import writer
    from femsolver.mystran.mystrantools import MystranTools

    if not hasattr(writer, "BDF"):
        raise NativeAnalyzeError(
            "Mystran input generation requires the pyNastran Python package.",
            error_code="NATIVE_ANALYZE_SOLVER_UNAVAILABLE",
        )
    if tasks.result_reading is not True:
        raise NativeAnalyzeError(
            "Mystran result import requires the hfcMystranNeuIn module.",
            error_code="NATIVE_ANALYZE_SOLVER_UNAVAILABLE",
        )
    tool = MystranTools(solver, working_directory=str(root))
    tool.prepare()
    program = _executable(settings.require_binary("Mystran"), "Mystran")
    input_name = tool.input_deck + ".bdf"
    return (
        tool,
        ((program, (input_name,)),),
        dict(os.environ),
        {"input_deck": tool.input_deck},
    )


def _openfoam_request(solver: Any, root: Path) -> tuple[Any, tuple, dict, dict]:
    from VibeCADNativeAnalyzeOpenFOAMExecution import prepare_openfoam_request

    return prepare_openfoam_request(solver, root)


def capture_solver_execution_request(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    timeout_seconds: Any,
) -> CapturedSolverExecutionRequest:
    """Capture exact solver guards without generating files or running tools."""

    prepared = prepare_solver_target(document, document_uid, target)
    state = solver_state(prepared.solver)
    if state["suppressed"]:
        raise NativeAnalyzeError("A suppressed FEM solver cannot be run.")
    analysis_name = str(state.get("analysis") or "")
    analysis = document.getObject(analysis_name) if analysis_name else None
    validation = validate_assignments(analysis) if analysis is not None else None
    if analysis_name and (
        not isinstance(validation, Mapping) or validation.get("valid") is not True
    ):
        issues = list(validation.get("issues") or ()) if isinstance(validation, Mapping) else []
        first = str(issues[0].get("message") or "") if issues else ""
        raise NativeAnalyzeError(
            "The FEM study has invalid assignment or mesh coverage"
            + (": " + first if first else "."),
            error_code="NATIVE_ANALYZE_SOLVER_NOT_READY",
            repair={
                "analysis": analysis_name,
                "issue_count": int(validation.get("issue_count", 0) or 0)
                if isinstance(validation, Mapping)
                else 0,
            },
        )
    history = _require_history_root(document, prepared.solver)
    runtime_preferences = _current_solver_runtime_preferences(prepared.kind)
    return CapturedSolverExecutionRequest(
        prepared,
        history,
        _timeout(timeout_seconds),
        _keep_results_from_runtime_preferences(runtime_preferences),
        runtime_preferences,
    )


def solver_resource_scope(captured: CapturedSolverExecutionRequest) -> str:
    """Return the exact study scope that owns one captured solver request."""

    if not isinstance(captured, CapturedSolverExecutionRequest):
        raise TypeError("captured must be a CapturedSolverExecutionRequest")
    analysis_name = str(solver_state(captured.target.solver).get("analysis") or "")
    if not analysis_name:
        raise NativeAnalyzeError("The captured FEM solver has no exact study owner.")
    return f"analyze:{analysis_name}"


def validate_captured_solver_execution(
    document: Any,
    captured: CapturedSolverExecutionRequest,
) -> None:
    """Reject a captured request when its exact live commit guards changed."""

    if not isinstance(captured, CapturedSolverExecutionRequest):
        raise TypeError("captured must be CapturedSolverExecutionRequest")
    solver = captured.target.solver
    if getattr(solver, "Document", None) is not document or not solver_still_exact(
        solver,
        captured.target.expected_state_sha256,
    ):
        raise NativeAnalyzeError(
            "The exact FEM solver changed while execution was being prepared.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if _require_history_root(document, solver) != captured.history_operations:
        raise NativeAnalyzeError(
            "Document History changed while FEM execution was being prepared.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    current_preferences = _current_solver_runtime_preferences(captured.target.kind)
    if (
        _keep_results_from_runtime_preferences(current_preferences)
        is not captured.keep_results
    ):
        raise NativeAnalyzeError(
            "The FEM result-retention preference changed while execution was being prepared.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if current_preferences != captured.runtime_preferences:
        raise NativeAnalyzeError(
            "The FEM solver runtime preferences changed while execution was being prepared.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def prepare_solver_execution_request(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    timeout_seconds: Any,
    working_directory: str | Path | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> SolverExecutionRequest:
    """Generate one solver case synchronously for compatibility callers.

    GUI and provider entry points capture exact guards first and generate this
    case in an isolated child process. The optional working directory lets that
    child keep every artifact inside its parent-owned private workspace.
    """

    captured = capture_solver_execution_request(
        document,
        document_uid,
        target=target,
        timeout_seconds=timeout_seconds,
    )
    prepared = captured.target
    state = solver_state(prepared.solver)
    implementation = str(state["implementation"])
    if working_directory is None:
        root = Path(tempfile.mkdtemp(prefix=f"vibecad-native-fem-{prepared.kind}-"))
    else:
        root = Path(working_directory).resolve()
        try:
            root.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as exc:
            raise NativeAnalyzeError(
                "The private FEM case directory could not be created.",
                error_code="NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
            ) from exc
    try:
        maker = {
            "calculix": (
                _ccx_tools_request
                if implementation == "ccx_tools"
                else _calculix_request
            ),
            "elmer": _elmer_request,
            "mystran": _mystran_request,
            "openfoam": _openfoam_request,
            "z88": _z88_request,
        }[prepared.kind]
        if prepared.kind == "calculix":
            _tool, commands, environment, importer_state = maker(
                prepared.solver,
                root,
                progress=progress,
            )
        else:
            _tool, commands, environment, importer_state = maker(prepared.solver, root)
        digest, count = _input_digest(root)
        return SolverExecutionRequest(
            prepared,
            implementation,
            captured.history_operations,
            str(root),
            tuple(commands),
            environment,
            captured.timeout_seconds,
            digest,
            count,
            captured.keep_results,
            importer_state,
            captured.runtime_preferences,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def discard_solver_execution_request(request: SolverExecutionRequest) -> None:
    shutil.rmtree(request.working_directory, ignore_errors=True)


def run_solver_execution(
    request: SolverExecutionRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedSolverExecution:
    try:
        progress(7, "FEM solver input frozen")
        stages = run_solver_processes(
            request.commands,
            working_directory=request.working_directory,
            environment=request.environment,
            timeout_seconds=request.timeout_seconds,
            cancelled=cancelled,
            progress=progress,
            backend=request.target.kind.title(),
        )
        return PreparedSolverExecution(request, stages)
    except Exception:
        discard_solver_execution_request(request)
        raise


def _current_keep_results() -> bool:
    import FreeCAD as App

    return bool(
        App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General").GetBool(
            "KeepResultsOnReRun", False
        )
    )


def _current_solver_runtime_preferences(
    solver_kind: str,
) -> tuple[tuple[str, str, str, Any], ...]:
    """Capture only preferences that affect detached input, execution, or import."""

    import FreeCAD as App

    kind = str(solver_kind)
    if kind not in {"calculix", "elmer", "mystran", "openfoam", "z88"}:
        raise ValueError(f"Unsupported FEM solver kind: {kind!r}")
    values = []
    for path, name, value_kind, default, backend in _SOLVER_RUNTIME_PREFERENCE_SPECS:
        if backend is not None and backend != kind:
            continue
        if default is None:
            default = int(App.Units.Scheme.Internal)
        group = App.ParamGet(path)
        if value_kind == "bool":
            value = bool(group.GetBool(name, default))
        elif value_kind == "int":
            value = int(group.GetInt(name, default))
        else:
            value = str(group.GetString(name, default))
        values.append((path, name, value_kind, value))
    return tuple(values)


def _keep_results_from_runtime_preferences(
    preferences: tuple[tuple[str, str, str, Any], ...],
) -> bool:
    matches = [
        value
        for path, name, value_kind, value in preferences
        if path == _FEM_PREFERENCES + "/General"
        and name == "KeepResultsOnReRun"
        and value_kind == "bool"
    ]
    if len(matches) != 1 or type(matches[0]) is not bool:
        raise NativeAnalyzeError(
            "The FEM result-retention preference could not be captured exactly.",
            error_code="NATIVE_ANALYZE_SOLVER_INPUT_INVALID",
        )
    return matches[0]


def _import_tool(request: SolverExecutionRequest) -> Any:
    solver = request.target.solver
    root = request.working_directory
    if request.target.kind == "calculix":
        if request.implementation == "ccx_tools":
            from femtools.ccxtools import CcxTools

            tool = CcxTools(solver)
            tool.working_dir = root
            tool.inp_file_name = str(request.importer_state["input_file"])
            return tool
        from femsolver.calculix.calculixtools import CalculiXTools

        tool = CalculiXTools(solver, detached=True, working_directory=root)
        tool.input_deck = str(request.importer_state["input_deck"])
        return tool
    if request.target.kind == "elmer":
        from femsolver.elmer.elmertools import ElmerTools

        tool = ElmerTools(solver, detached=True, working_directory=root)
        tool._result_format = str(request.importer_state["result_format"])
        return tool
    if request.target.kind == "mystran":
        from femsolver.mystran.mystrantools import MystranTools

        tool = MystranTools(solver, working_directory=root)
        tool.input_deck = str(request.importer_state["input_deck"])
        return tool
    if request.target.kind == "openfoam":
        from femsolver.openfoam.openfoamtools import OpenFOAMTools

        return OpenFOAMTools(
            solver,
            root,
            result_glob=str(request.importer_state["result_glob"]),
            solver_log=str(request.importer_state["solver_log"]),
            summary_context=request.importer_state.get("summary_context"),
        )
    from femsolver.z88.z88tools import Z88Tools

    return Z88Tools(solver, detached=True, working_directory=root)


def _unpack_result_graph(value: Any) -> tuple[Any, tuple[Any, ...], bool, Any]:
    if not isinstance(value, tuple) or len(value) not in {2, 3, 4}:
        raise NativeAnalyzeError(
            "The FEM result importer returned no exact result graph."
        )
    root = value[0]
    resources = tuple(value[1])
    root_is_new = bool(value[2]) if len(value) >= 3 else True
    reconciliation = value[3] if len(value) == 4 else None
    return root, resources, root_is_new, reconciliation


def _select_final_result_frame(root: Any) -> None:
    try:
        frames = tuple(root.getFrameValues())
    except (AttributeError, RuntimeError, TypeError):
        return
    if frames:
        root.Frame = len(frames) - 1


def commit_solver_execution(
    document: Any,
    prepared: PreparedSolverExecution,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSolverExecution):
        raise TypeError("prepared must be PreparedSolverExecution")
    request = prepared.request
    solver = request.target.solver
    if not solver_still_exact(solver, request.target.expected_state_sha256):
        raise NativeAnalyzeError(
            "The exact FEM solver changed while execution was running; results were not applied.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    timeline = getattr(document, "VibeCADTimeline", None)
    if tuple(getattr(timeline, "Operations", ()) or ()) != request.history_operations:
        raise NativeAnalyzeError(
            "Document History changed while the FEM solver was running; results were not applied.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if _current_keep_results() is not request.keep_results:
        raise NativeAnalyzeError(
            "The FEM result-retention preference changed while the solver was running.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    from femtools.objecttools import _ensure_exact_retained_result_graph
    from femcommands.manager import _finalize_timeline_result_graph

    _ensure_exact_retained_result_graph(solver)
    graph = _unpack_result_graph(_import_tool(request).update_properties())
    root, resources, root_is_new, reconciliation = graph
    _finalize_timeline_result_graph(
        solver,
        root,
        resources,
        root_is_new=root_is_new,
        reconciliation=reconciliation,
    )
    _select_final_result_frame(root)
    return NativeMutationDraft(
        value={"prepared": prepared, "root": root, "resources": resources},
        recompute_targets=(root, solver),
        created=(
            *((object_identity(root),) if root_is_new else ()),
            *(object_identity(resource) for resource in resources),
        ),
        changed=(object_identity(solver),),
    )


def _result_summary(
    root: Any, resources: tuple[Any, ...], solver: Any
) -> dict[str, Any]:
    return {
        "object_name": str(root.Name),
        "object_id": int(root.ID),
        "label": str(root.Label),
        "type_id": str(root.TypeId),
        "solver": str(solver.Name),
        "resource_count": len(resources),
        "resources": [
            {
                "object_name": str(value.Name),
                "object_id": int(value.ID),
                "type_id": str(value.TypeId),
            }
            for value in resources
        ],
    }


def _require_meaningful_result_state(state: Mapping[str, Any]) -> None:
    if int(state.get("field_count", 0) or 0) > 0:
        return
    raise NativeAnalyzeError(
        "The FEM solver produced no result fields.",
        error_code="NATIVE_ANALYZE_RESULT_DATA_MISSING",
    )


def verify_solver_execution(
    document: Any, draft: NativeMutationDraft
) -> dict[str, Any]:
    from femcommands.manager import (
        _canonical_timeline_resource_order,
        _result_solver_matches,
        _timeline_root,
    )

    prepared = draft.value["prepared"]
    request = prepared.request
    solver = request.target.solver
    root = draft.value["root"]
    resources = draft.value["resources"]
    from VibeCADNativeAnalyzeResultState import result_state

    _require_meaningful_result_state(result_state(root))
    timeline = tuple(getattr(document.VibeCADTimeline, "Operations", ()) or ())
    result_list = tuple(getattr(solver, "Results", ()) or ())
    solver_resources = tuple(
        candidate
        for candidate in timeline
        if candidate is not solver and _timeline_root(candidate, document) is solver
    )
    solver_index = timeline.index(solver) if solver in timeline else -1
    checks = {
        "live result root": is_live(document, root),
        "solver result link": root in result_list,
        "result source": _result_solver_matches(root, solver),
        "result History role": str(getattr(root, "VibeCADTimelineRole", "") or "")
        == "resource",
        "resource ownership": all(
            is_live(document, value)
            and getattr(value, "VibeCADTimelineOwner", None) is root
            for value in resources
        ),
        "canonical History block": solver_index >= len(solver_resources)
        and tuple(timeline[solver_index - len(solver_resources) : solver_index])
        == solver_resources
        and tuple(
            _canonical_timeline_resource_order(
                solver,
                solver_resources,
            )
        )
        == solver_resources,
        "native validity": bool(root.isValid()) and bool(solver.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "FEM solver result publication failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "solver": solver_state(solver),
        "result": _result_summary(root, resources, solver),
        "execution": {
            "backend": request.target.kind,
            "implementation": request.implementation,
            "input_sha256": request.input_sha256,
            "input_file_count": request.input_file_count,
            "stages": list(prepared.stages),
        },
    }
