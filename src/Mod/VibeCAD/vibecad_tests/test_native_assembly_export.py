# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyExport as export_module
import VibeCADNativeAssemblyExportRuntime as runtime_module
from VibeCADNativeAssemblyExport import (
    AssemblyAsmtExportSpec,
    NativeAssemblyExportError,
    export_assembly_asmt,
    preflight_assembly_asmt_export,
)
from VibeCADNativeAssemblyExportRuntime import NativeAssemblyExportRuntime
from VibeCADNativeAssemblyExportSchema import (
    ASSEMBLY_EXPORT_CAPABILITY_NAME,
    assembly_export_capability_definition,
)
from VibeCADNativeOutput import (
    NativeOutputRequest,
    authorize_native_output_path,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeDocumentStateStore
from VibeCADNativeTargets import NativeObjectRef
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "native-assembly-export-document"
    Name = "NativeAssemblyExportDocument"
    UndoCount = 0
    HasPendingTransaction = False
    Recomputing = False
    RecomputePending = False

    def __init__(self) -> None:
        self.Objects = []

    def add(self, obj) -> None:
        obj.Document = self
        self.Objects.append(obj)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)

    def getBookedTransactionID(self) -> int:
        return 0


class _Assembly:
    Name = "Assembly"
    Label = "Main Assembly"
    TypeId = "Assembly::AssemblyObject"
    ID = 7

    def __init__(self) -> None:
        self.Document = None

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id in {self.TypeId, "App::Part"}


def _diagnosis(assembly: _Assembly, digest: str = "a" * 64):
    component = object()
    grounded = object()
    joint = object()
    return SimpleNamespace(
        assembly=assembly,
        joint_group=object(),
        components=(component,),
        grounded_joints=(grounded,),
        regular_joints=(joint,),
        state_sha256=digest,
    )


def _context(document: _Document, *, authorizer=None) -> NativeRuntimeContext:
    state = NativeDocumentStateStore()
    state.ensure_document(document.Uid)
    state.begin_native_authority(document.Uid)
    return NativeRuntimeContext(
        service=object(),
        document=document,
        state=state,
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "assemble",
        edit_or_task_active=lambda: False,
        authorize_output=authorizer,
    )


def _spec(document: _Document, assembly: _Assembly) -> AssemblyAsmtExportSpec:
    return AssemblyAsmtExportSpec(
        assembly_ref=NativeObjectRef(document.Uid, assembly.Name),
        expected_state_sha256="a" * 64,
        expected_component_count=1,
        expected_grounded_count=1,
        expected_joint_count=1,
    )


def _prepare(monkeypatch):
    document = _Document()
    assembly = _Assembly()
    document.add(assembly)
    context = _context(document)
    diagnosis = _diagnosis(assembly)
    monkeypatch.setattr(
        export_module,
        "capture_assembly_diagnosis_state",
        lambda _assembly: diagnosis,
    )
    monkeypatch.setattr(export_module, "_gui_modified", lambda _document: False)
    selection = {"document_uid": document.Uid, "selected_count": 0, "items": []}
    prepared = preflight_assembly_asmt_export(
        context,
        _spec(document, assembly),
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    )
    return document, assembly, context, diagnosis, selection, prepared


def test_schema_exposes_no_provider_controlled_path() -> None:
    definition = assembly_export_capability_definition()
    variant = definition.variants[0]
    schema = definition.provider_schema(("asmt",))["parameters"]["oneOf"][0]

    assert definition.name == ASSEMBLY_EXPORT_CAPABILITY_NAME
    assert definition.primary_classification == "export"
    assert variant.action_ids == frozenset({"Assembly_ExportASMT"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "output"
    assert variant.background_required is False
    assert set(schema["required"]) == {
        "operation",
        "assembly",
        "expected_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
    }
    assert not (
        {"path", "file_path", "directory", "destination"} & set(schema["properties"])
    )
    assert schema["additionalProperties"] is False


def test_preflight_freezes_exact_active_assembly_and_output_request(
    monkeypatch,
) -> None:
    document, assembly, _context_value, diagnosis, selection, prepared = _prepare(
        monkeypatch
    )

    assert prepared.state is diagnosis
    assert prepared.active_before is assembly
    assert prepared.selection_before == selection
    assert prepared.document_objects_before == (assembly,)
    assert prepared.undo_count_before == 0
    assert prepared.transaction_id_before == 0
    assert prepared.output_request == NativeOutputRequest(
        purpose="assembly_asmt_export",
        title="Export active Assembly as ASMT",
        suggested_file_name=f"{document.Name}.asmt",
        allowed_suffixes=(".asmt",),
        name_filter="ASMT Files (*.asmt)",
        maximum_bytes=256 * 1024 * 1024,
    )


def test_export_uses_native_serializer_atomically_without_document_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    document, assembly, context, diagnosis, selection, prepared = _prepare(monkeypatch)
    destination = tmp_path / "Machine.asmt"
    authorization = authorize_native_output_path(prepared.output_request, destination)
    ticket = context.state.begin_call(document.Uid, ASSEMBLY_EXPORT_CAPABILITY_NAME)
    calls = []

    def exporter(exact_assembly, path: str) -> None:
        calls.append((exact_assembly, Path(path)))
        Path(path).write_bytes(b"OndselSolver\nAssembly\n\tName\n\t\tOndselAssembly\n")

    result = export_assembly_asmt(
        context,
        prepared,
        authorization,
        ticket,
        exporter=exporter,
        active_reader=lambda _document: assembly,
        selection_reader=lambda _document: selection,
    )

    assert len(calls) == 1
    assert calls[0][0] is assembly
    assert calls[0][1] != destination
    assert calls[0][1].parent == destination.parent
    assert destination.read_bytes().startswith(b"OndselSolver\nAssembly\n")
    assert result["operation"] == "asmt"
    assert result["assembly"] == {
        "object_name": assembly.Name,
        "object_id": assembly.ID,
    }
    assert result["output"]["file_name"] == destination.name
    assert "path" not in result["output"]
    assert result["source_state_sha256"] == diagnosis.state_sha256
    assert document.Objects == [assembly]
    assert document.UndoCount == 0
    assert document.getBookedTransactionID() == 0


def test_stale_source_after_path_choice_never_calls_serializer_or_replaces_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    document, assembly, context, diagnosis, selection, prepared = _prepare(monkeypatch)
    destination = tmp_path / "Machine.asmt"
    destination.write_text("existing", encoding="utf-8")
    authorization = authorize_native_output_path(prepared.output_request, destination)
    changed = dict(vars(diagnosis))
    changed["state_sha256"] = "b" * 64
    monkeypatch.setattr(
        export_module,
        "capture_assembly_diagnosis_state",
        lambda _assembly: SimpleNamespace(**changed),
    )
    ticket = context.state.begin_call(document.Uid, ASSEMBLY_EXPORT_CAPABILITY_NAME)
    calls = []

    with pytest.raises(NativeAssemblyExportError, match="changed during ASMT export"):
        export_assembly_asmt(
            context,
            prepared,
            authorization,
            ticket,
            exporter=lambda _assembly, _path: calls.append(True),
            active_reader=lambda _document: assembly,
            selection_reader=lambda _document: selection,
        )

    assert calls == []
    assert destination.read_text(encoding="utf-8") == "existing"


def test_runtime_requests_human_authorization_and_passes_no_path_from_provider(
    monkeypatch,
) -> None:
    request = NativeOutputRequest(
        purpose="assembly_asmt_export",
        title="Export active Assembly as ASMT",
        suggested_file_name="Assembly.asmt",
        allowed_suffixes=(".asmt",),
        name_filter="ASMT Files (*.asmt)",
        maximum_bytes=1024,
    )
    authorization = object()
    authorized = []
    context = SimpleNamespace(
        document_uid="document-uid",
        authorize_output=lambda value: authorized.append(value) or authorization,
    )
    runtime = object.__new__(NativeAssemblyExportRuntime)
    runtime._context = context
    prepared = SimpleNamespace(output_request=request)
    ticket = NativeCallTicket(
        "document-uid", ASSEMBLY_EXPORT_CAPABILITY_NAME, 0, "token"
    )
    monkeypatch.setattr(
        runtime_module, "require_current_output_ticket", lambda *_args: None
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_assembly_asmt_export",
        lambda _context, _spec: prepared,
    )
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "export_assembly_asmt",
        lambda *args: calls.append(args) or {"operation": "asmt"},
    )

    arguments = {
        "operation": "asmt",
        "assembly": {"object_name": "Assembly"},
        "expected_state_sha256": "a" * 64,
        "expected_component_count": 1,
        "expected_grounded_count": 1,
        "expected_joint_count": 1,
    }
    assert runtime.export(arguments, ticket) == {"operation": "asmt"}
    assert authorized == [request]
    assert calls == [(context, prepared, authorization, ticket)]
    assert not ({"path", "file_path", "destination"} & set(arguments))


def test_runtime_cancelled_authorization_is_a_noop(monkeypatch) -> None:
    context = SimpleNamespace(
        document_uid="document-uid",
        authorize_output=lambda _request: None,
    )
    runtime = object.__new__(NativeAssemblyExportRuntime)
    runtime._context = context
    prepared = SimpleNamespace(output_request=object())
    ticket = NativeCallTicket(
        "document-uid", ASSEMBLY_EXPORT_CAPABILITY_NAME, 0, "token"
    )
    monkeypatch.setattr(
        runtime_module, "require_current_output_ticket", lambda *_args: None
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_assembly_asmt_export",
        lambda _context, _spec: prepared,
    )
    monkeypatch.setattr(
        runtime_module,
        "export_assembly_asmt",
        lambda *_args: pytest.fail("cancelled authorization must not export"),
    )

    with pytest.raises(NativeAssemblyExportError, match="cancelled"):
        runtime.export(
            {
                "operation": "asmt",
                "assembly": {"object_name": "Assembly"},
                "expected_state_sha256": "a" * 64,
                "expected_component_count": 1,
                "expected_grounded_count": 1,
                "expected_joint_count": 1,
            },
            ticket,
        )


def test_production_registry_contains_complete_export_family() -> None:
    registry = build_native_capability_registry()

    assert registry.definition(ASSEMBLY_EXPORT_CAPABILITY_NAME) is not None
    assert registry.implementation(ASSEMBLY_EXPORT_CAPABILITY_NAME) is not None
