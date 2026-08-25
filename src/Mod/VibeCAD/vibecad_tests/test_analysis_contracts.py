# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADAnalysisContracts import (
    AnalysisCommand,
    AnalysisContractError,
    CanonicalJson,
    DependencyRecord,
    DependencySnapshot,
    ExecutionSpec,
    PreparedAnalysis,
    PreparedInputManifest,
    environment_sha256,
)
from VibeCADAnalysisProviders import AnalysisProvider, ProviderCapabilities
import VibeCADNativeAnalyzeSolverExecution as legacy
import VibeCADNativeAnalyzeSolverExecutionAdapter as adapter


def test_contracts_are_immutable_serializable_and_domain_neutral() -> None:
    command = AnalysisCommand("/solver/bin", ("-i", "case.inp"))
    dependencies = DependencySnapshot(
        (
            DependencyRecord(
                key="solver_state",
                kind="fem_solver_state",
                canonical_digest="a" * 64,
                human_summary="Exact FEM solver state",
            ),
        )
    )
    prepared = PreparedAnalysis.create(
        domain="fem",
        adapter_id="vibecad.native.analyze.fem",
        adapter_version="legacy-compat-v1",
        source_document_uid="doc-1",
        source_summary={"solver": "Solver"},
        dependency_snapshot=dependencies,
        input_manifest=PreparedInputManifest(
            storage_reference="/tmp/job",
            sha256="b" * 64,
            file_count=3,
        ),
        execution_spec=ExecutionSpec(
            provider_id="local-process",
            commands=(command,),
            timeout_seconds=30,
            environment_keys=("OMP_NUM_THREADS",),
            environment_sha256="c" * 64,
        ),
        expected_outputs=("fem_result_graph",),
        publication_descriptor={"backend": "calculix"},
        provenance={"implementation": "pipeline"},
    )

    assert prepared.execution_spec.command_tuples() == (
        ("/solver/bin", ("-i", "case.inp")),
    )
    assert (
        prepared.dependency_snapshot.by_key("solver_state").canonical_digest
        == "a" * 64
    )
    assert prepared.publication_descriptor.to_value() == {"backend": "calculix"}
    assert prepared.schema_version == 1
    with pytest.raises(AnalysisContractError):
        CanonicalJson.from_value({"live": object()})


def test_provider_port_exposes_required_future_surface_without_implementation() -> None:
    capabilities = ProviderCapabilities(
        provider_id="local-process",
        location="local",
        reconnect_supported=False,
        cancel_supported=True,
        log_streaming=True,
        execution_environment="host",
    )
    assert capabilities.provider_id == "local-process"
    assert AnalysisProvider is not None


def test_fem_adapter_preserves_legacy_preparation_and_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = SimpleNamespace(
        kind="calculix",
        expected_state_sha256="d" * 64,
        solver=SimpleNamespace(Name="Solver", ID=7, TypeId="Fem::SolverCalculiX"),
    )
    history = (
        SimpleNamespace(Name="Constraint", ID=2, TypeId="Fem::ConstraintForce"),
        target.solver,
    )
    request = legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
        history_operations=history,
        working_directory="/tmp/fem-job",
        commands=(("/solver/ccx", ("-i", "case")),),
        environment={"OMP_NUM_THREADS": "4", "SAFE": "exact"},
        timeout_seconds=120,
        input_sha256="e" * 64,
        input_file_count=5,
        keep_results=False,
        importer_state={"input_deck": "case"},
    )
    monkeypatch.setattr(
        legacy,
        "prepare_solver_execution_request",
        lambda *_args, **_kwargs: request,
    )

    prepared = adapter.prepare_solver_execution_request(
        object(),
        "doc-1",
        target={"object_name": "Solver"},
        timeout_seconds=120,
    )

    assert prepared.legacy_request is request
    assert prepared.target is request.target
    assert prepared.analysis.domain == "fem"
    assert prepared.analysis.input_manifest.sha256 == request.input_sha256
    assert prepared.analysis.input_manifest.file_count == request.input_file_count
    assert prepared.analysis.execution_spec.command_tuples() == request.commands
    assert prepared.analysis.execution_spec.timeout_seconds == request.timeout_seconds
    assert prepared.analysis.execution_spec.environment_keys == tuple(
        sorted(request.environment)
    )
    assert prepared.analysis.execution_spec.environment_sha256 == environment_sha256(
        request.environment
    )
    assert (
        prepared.analysis.dependency_snapshot.by_key("solver_state").canonical_digest
        == "d" * 64
    )

    called: dict[str, object] = {}
    legacy_completed = object()

    def run(request_value, *, cancelled, progress):
        called["run_request"] = request_value
        return legacy_completed

    monkeypatch.setattr(legacy, "run_solver_execution", run)
    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda _percent, _message: None,
    )
    assert called["run_request"] is request
    assert completed.legacy_prepared is legacy_completed
    assert completed.analysis is prepared.analysis

    monkeypatch.setattr(
        legacy,
        "commit_solver_execution",
        lambda document, value: called.update(commit=(document, value)) or "draft",
    )
    document = object()
    assert adapter.commit_solver_execution(document, completed) == "draft"
    assert called["commit"] == (document, legacy_completed)

    monkeypatch.setattr(
        legacy,
        "discard_solver_execution_request",
        lambda value: called.update(discard=value),
    )
    adapter.discard_solver_execution_request(prepared)
    assert called["discard"] is request

    monkeypatch.setattr(
        legacy,
        "verify_solver_execution",
        lambda document, draft: {"document": document, "draft": draft},
    )
    assert adapter.verify_solver_execution("doc", "draft") == {
        "document": "doc",
        "draft": "draft",
    }
