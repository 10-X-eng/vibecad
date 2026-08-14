# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached Netgen FEM generation with an exact main-thread commit."""

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
class NetgenGenerationRequest:
    target: PreparedMeshGenerationTarget
    working_directory: str
    executable: str
    model_file: str
    result_file: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class PreparedNetgenGeneration:
    request: NetgenGenerationRequest
    artifact: bytes
    artifact_sha256: str


def _timeout(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 86400:
        raise NativeAnalyzeError("timeout_seconds must be an integer from 1 to 86400.")
    return value


def _inside(root: Path, value: str) -> Path:
    path = Path(value).resolve()
    if path.parent != root.resolve():
        raise NativeAnalyzeError("The FEM mesher produced an unsafe detached path.")
    return path


def prepare_netgen_generation_request(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    timeout_seconds: Any,
) -> NetgenGenerationRequest:
    prepared = prepare_mesh_generation_target(
        document,
        document_uid,
        target,
        backend="netgen",
    )
    if str(getattr(prepared.mesh, "EndStep", "")) == "AnalyzeGeometry":
        raise NativeAnalyzeError(
            "This Netgen definition stops after geometry analysis and cannot generate a mesh."
        )
    root = Path(tempfile.mkdtemp(prefix="vibecad-native-fem-netgen-"))
    try:
        from femmesh.netgentools import NetgenTools

        tool = NetgenTools(prepared.mesh, detached=True)
        tool.prepare(str(root))
        configured = str(tool._get_python_exe() or "").strip()
        resolved = shutil.which(configured) if configured else None
        if resolved is None:
            candidate = Path(configured).resolve() if configured else None
            resolved = str(candidate) if candidate is not None and candidate.is_file() else None
        if resolved is None:
            raise NativeAnalyzeError(
                "The human-configured Netgen Python executable is unavailable.",
                error_code="NATIVE_ANALYZE_NETGEN_UNAVAILABLE",
            )
        model_file = _inside(root, tool.model_file)
        result_file = _inside(root, tool.result_file)
        if not model_file.is_file():
            raise NativeAnalyzeError("Netgen input preparation did not create its script.")
        return NetgenGenerationRequest(
            prepared,
            str(root),
            str(Path(resolved).resolve()),
            str(model_file),
            str(result_file),
            _timeout(timeout_seconds),
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def discard_netgen_generation_request(request: NetgenGenerationRequest) -> None:
    shutil.rmtree(request.working_directory, ignore_errors=True)


def run_netgen_generation(
    request: NetgenGenerationRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedNetgenGeneration:
    root = Path(request.working_directory)
    try:
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()
        progress(8, "Netgen input frozen")
        run_mesh_process(
            (
                request.executable,
                "-X",
                "utf8",
                "-E",
                request.model_file,
            ),
            log_path=root / "netgen.log",
            timeout_seconds=request.timeout_seconds,
            cancelled=cancelled,
            progress=progress,
            backend="Netgen",
        )
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()
        progress(82, "Reading detached Netgen artifact")
        output = Path(request.result_file)
        if not output.is_file():
            raise NativeAnalyzeError(
                "Netgen completed without creating its requested mesh artifact.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_MISSING",
            )
        size = output.stat().st_size
        if size < 1 or size > _MAX_ARTIFACT_BYTES:
            raise NativeAnalyzeError(
                "The Netgen mesh artifact is empty or exceeds the 16 GiB safety bound.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_INVALID",
            )
        artifact = output.read_bytes()
        digest = hashlib.sha256(artifact).hexdigest()
        progress(89, "Netgen artifact verified")
        return PreparedNetgenGeneration(request, artifact, digest)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def commit_netgen_generation(
    document: Any,
    prepared: PreparedNetgenGeneration,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedNetgenGeneration):
        raise TypeError("prepared must be PreparedNetgenGeneration")
    request = prepared.request
    if not mesh_generation_target_still_exact(request.target):
        raise NativeAnalyzeError(
            "The exact Netgen definition or refinement graph changed while Netgen was "
            "running; the stale result was not applied.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    root = Path(tempfile.mkdtemp(prefix="vibecad-native-fem-netgen-import-"))
    try:
        output = root / "mesh.npy"
        output.write_bytes(prepared.artifact)
        from femmesh.netgentools import NetgenTools

        fem_mesh = NetgenTools.fem_mesh_from_result_file(str(output))
        if int(fem_mesh.NodeCount) < 1:
            raise NativeAnalyzeError(
                "Netgen produced a readable but empty FEM mesh.",
                error_code="NATIVE_ANALYZE_MESH_RESULT_EMPTY",
            )
        request.target.mesh.FemMesh = fem_mesh
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            "The detached Netgen artifact could not be imported as a FEM mesh.",
            error_code="NATIVE_ANALYZE_MESH_RESULT_INVALID",
        ) from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(request.target.mesh,),
        changed=(object_identity(request.target.mesh),),
    )


def verify_netgen_generation(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    target = prepared.request.target
    state = fem_mesh_definition_state(target.mesh)
    checks = {
        "backend": state["mesher"] == "netgen",
        "generated": state["generated"],
        "nonempty topology": state["topology"]["nodes"] > 0,
        "content hash": bool(state.get("mesh_content_sha256")),
        "resource graph": mesh_generation_resources_still_exact(target),
        "native validity": bool(target.mesh.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "Netgen generation failed its exact postcondition: "
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
