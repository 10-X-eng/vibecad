# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached Gmsh FEM generation with an exact main-thread commit."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshGenerationProcess import run_mesh_process
from VibeCADNativeAnalyzeMeshGenerationState import (
    PreparedMeshGenerationTarget,
    generation_target_summary,
    mesh_generation_resources_still_exact,
    mesh_generation_target_still_exact,
    prepare_mesh_generation_target,
)
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GmshGenerationRequest:
    target: PreparedMeshGenerationTarget
    working_directory: str
    executable: str
    model_file: str
    result_file: str
    log_verbosity: str
    timeout_seconds: int
    group_indices: dict[str, int]
    group_elements: dict[str, int]
    analysis_group_meshing: bool


@dataclass(frozen=True, slots=True)
class PreparedGmshGeneration:
    request: GmshGenerationRequest
    artifact: bytes
    artifact_sha256: str
    suffix: str


def _timeout(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 86400:
        raise NativeAnalyzeError("timeout_seconds must be an integer from 1 to 86400.")
    return value


def _inside(root: Path, value: str) -> Path:
    path = Path(value).resolve()
    if path.parent != root.resolve():
        raise NativeAnalyzeError("The FEM mesher produced an unsafe detached path.")
    return path


def prepare_gmsh_generation_request(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    timeout_seconds: Any,
) -> GmshGenerationRequest:
    prepared = prepare_mesh_generation_target(
        document,
        document_uid,
        target,
        backend="gmsh",
    )
    root = Path(tempfile.mkdtemp(prefix="vibecad-native-fem-gmsh-"))
    try:
        from femmesh.gmshtools import GmshTools

        tool = GmshTools(prepared.mesh, detached=True)
        tool.prepare(str(root))
        executable = Path(str(tool.gmsh_bin)).resolve()
        if not executable.is_file():
            raise NativeAnalyzeError(
                "The human-configured Gmsh executable is unavailable.",
                error_code="NATIVE_ANALYZE_GMSH_UNAVAILABLE",
            )
        model_file = _inside(root, tool.model_file)
        result_file = _inside(root, tool.temp_file_mesh)
        if not model_file.is_file():
            raise NativeAnalyzeError("Gmsh input preparation did not create its project file.")
        import FreeCAD as App

        log_verbosity = str(
            App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").GetString(
                "LogVerbosity", "3"
            )
        )
        analysis_group_meshing = bool(
            App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General").GetBool(
                "AnalysisGroupMeshing", False
            )
        )
        return GmshGenerationRequest(
            prepared,
            str(root),
            str(executable),
            str(model_file),
            str(result_file),
            log_verbosity,
            _timeout(timeout_seconds),
            {str(name): int(index) for name, index in tool.group_indices.items()},
            {str(name): int(index) for name, index in tool.group_elements.items()},
            analysis_group_meshing,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def discard_gmsh_generation_request(request: GmshGenerationRequest) -> None:
    shutil.rmtree(request.working_directory, ignore_errors=True)


def run_gmsh_generation(
    request: GmshGenerationRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedGmshGeneration:
    root = Path(request.working_directory)
    try:
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()
        progress(8, "Gmsh input frozen")
        run_mesh_process(
            (
                request.executable,
                "-v",
                request.log_verbosity,
                "-",
                request.model_file,
            ),
            log_path=root / "gmsh.log",
            timeout_seconds=request.timeout_seconds,
            cancelled=cancelled,
            progress=progress,
            backend="Gmsh",
        )
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()
        progress(82, "Reading detached Gmsh artifact")
        output = Path(request.result_file)
        if not output.is_file():
            raise NativeAnalyzeError(
                "Gmsh completed without creating its requested mesh artifact.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_MISSING",
            )
        size = output.stat().st_size
        if size < 1 or size > _MAX_ARTIFACT_BYTES:
            raise NativeAnalyzeError(
                "The Gmsh mesh artifact is empty or exceeds the 16 GiB safety bound.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_INVALID",
            )
        artifact = output.read_bytes()
        digest = hashlib.sha256(artifact).hexdigest()
        progress(89, "Gmsh artifact verified")
        return PreparedGmshGeneration(request, artifact, digest, output.suffix.lower())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _analysis_group_meshing() -> bool:
    import FreeCAD as App

    return bool(
        App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General").GetBool(
            "AnalysisGroupMeshing", False
        )
    )


def commit_gmsh_generation(
    document: Any,
    prepared: PreparedGmshGeneration,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedGmshGeneration):
        raise TypeError("prepared must be PreparedGmshGeneration")
    request = prepared.request
    if not mesh_generation_target_still_exact(request.target):
        raise NativeAnalyzeError(
            "The exact Gmsh definition or refinement graph changed while Gmsh was running; "
            "the stale result was not applied.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if _analysis_group_meshing() is not request.analysis_group_meshing:
        raise NativeAnalyzeError(
            "The FEM analysis-group meshing preference changed while Gmsh was running.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    root = Path(tempfile.mkdtemp(prefix="vibecad-native-fem-gmsh-import-"))
    try:
        output = root / ("mesh" + prepared.suffix)
        output.write_bytes(prepared.artifact)
        import Fem

        fem_mesh = Fem.FemMesh()
        if prepared.suffix == ".vtk" and request.group_elements:
            fem_mesh.read(str(output), vtk_cell_group_array="CellEntityIds")
        else:
            fem_mesh.read(str(output))
        if int(fem_mesh.NodeCount) < 1:
            raise NativeAnalyzeError(
                "Gmsh produced a readable but empty FEM mesh.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_EMPTY",
            )
        mesh = request.target.mesh
        mesh.FemMesh = fem_mesh
        from femmesh.gmshtools import GmshTools

        tool = GmshTools(mesh, detached=True)
        tool.group_indices = dict(request.group_indices)
        tool.group_elements = dict(request.group_elements)
        tool.rename_groups()
        tool.postprocess_groups()
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            "The detached Gmsh artifact could not be imported as a FEM mesh.",
            error_code="NATIVE_ANALYZE_MESH_RESULT_INVALID",
        ) from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(request.target.mesh,),
        changed=(object_identity(request.target.mesh),),
    )


def verify_gmsh_generation(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    target = prepared.request.target
    state = fem_mesh_definition_state(target.mesh)
    checks = {
        "backend": state["mesher"] == "gmsh",
        "generated": state["generated"],
        "nonempty topology": state["topology"]["nodes"] > 0,
        "content hash": bool(state.get("mesh_content_sha256")),
        "resource graph": mesh_generation_resources_still_exact(target),
        "native validity": bool(target.mesh.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "Gmsh generation failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "generated_mesh_definition": state,
        "generation": {
            **generation_target_summary(target),
            "artifact_sha256": prepared.artifact_sha256,
        },
    }
