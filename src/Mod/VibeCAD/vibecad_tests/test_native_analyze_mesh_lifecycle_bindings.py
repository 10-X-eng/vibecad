# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import VibeCADNativeAnalyzeMeshLifecycleBindings as bindings
import VibeCADNativeAnalyzeMeshRuntime as mesh_runtime
from VibeCADNativeAnalyzeMeshGenerationState import PreparedMeshGenerationTarget
from VibeCADNativeAnalyzeMeshRuntime import NativeAnalyzeMeshRuntime
from VibeCADNativeAnalyzeTargets import PreparedFemMeshDefinitionTarget


def test_focused_gmsh_edit_retargets_an_existing_mesh_definition(monkeypatch) -> None:
    runtime = object.__new__(NativeAnalyzeMeshRuntime)
    requests: list[dict] = []
    runtime.execute = lambda request, *, ticket: requests.append(request) or {
        "updated": True
    }
    mesh_state = {
        "object_name": "Mesh",
        "state_sha256": "a" * 64,
        "mesher": "gmsh",
        "settings": {
            "maximum_size_mm": 12.0,
            "minimum_size_mm": 2.0,
            "element_order": "second",
        },
    }
    monkeypatch.setattr(
        bindings,
        "current_state",
        lambda _runtime, name, _reader: (SimpleNamespace(Name=name), mesh_state),
    )
    monkeypatch.setattr(
        bindings,
        "current_target",
        lambda _runtime, name, _reader: {
            "object_name": name,
            "expected_state_sha256": "b" * 64,
        },
    )

    result = bindings._edit_values(
        runtime,
        {"mesh_name": "Mesh", "source_name": "TwoBodyDomain"},
        ticket=SimpleNamespace(),
    )

    assert requests == [
        {
            "operation": "update_gmsh",
            "target": {
                "object_name": "Mesh",
                "expected_state_sha256": "a" * 64,
            },
            "source": {
                "object_name": "TwoBodyDomain",
                "expected_state_sha256": "b" * 64,
            },
        }
    ]
    assert result == {"updated": True, "mesh_name": "Mesh"}


def test_mesh_generation_job_uses_the_exact_owner_study_scope(monkeypatch) -> None:
    runtime = object.__new__(NativeAnalyzeMeshRuntime)
    mesh = SimpleNamespace(Name="StudyBMesh")
    analysis = SimpleNamespace(
        Name="StudyB",
        Group=(mesh,),
        isDerivedFrom=lambda type_name: type_name == "Fem::FemAnalysis",
    )
    document = SimpleNamespace(Objects=(analysis,))
    mesh.Document = document
    request = SimpleNamespace(
        target=PreparedMeshGenerationTarget(
            PreparedFemMeshDefinitionTarget(mesh, "gmsh", "a" * 64),
            (),
            (),
        )
    )
    submitted: dict[str, object] = {}

    class Manager:
        def submit(self, **values):
            submitted.update(values)
            return SimpleNamespace(
                job_id="a" * 32,
                capability_name="analyze.mesh.generate_gmsh",
                phase="prepared",
                progress_percent=0,
                progress_message="Queued",
                terminal=False,
            )

    runtime._context = SimpleNamespace(
        background_manager=Manager(),
        document_thread_dispatch=lambda callback: callback(),
        document=document,
        document_uid="doc-128",
        guard=lambda: None,
        state=SimpleNamespace(cancel_mutation=lambda _ticket: None),
    )
    monkeypatch.setattr(
        mesh_runtime,
        "prepare_gmsh_generation_request",
        lambda *_args, **_kwargs: request,
    )

    runtime._start_generation(
        "gmsh",
        {"target": {}, "timeout_seconds": 30},
        SimpleNamespace(),
    )

    assert submitted["resource_scope"] == "analyze:StudyB"
