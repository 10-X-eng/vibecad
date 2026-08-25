# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Native aero.* tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Mapping
import zipfile

_AERO_DIR = Path(__file__).resolve().parent.parent / "VibeCADAero"
if _AERO_DIR.is_dir() and str(_AERO_DIR) not in sys.path:
    sys.path.insert(0, str(_AERO_DIR))

import AeroAirfoil
import AeroConfig
import AeroDetachedAnalysis
import AeroFlightCard
import AeroMass
import AeroPreview
import AeroRepair
import AeroResults
import AeroStamp
import VibeCADAero

from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeOutput import (
    NativeOutputRequest,
    publish_authorized_output,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict
from VibeCADNativeTargets import object_identity

_SOLVE_OPS = frozenset({"analyze", "section", "vlm", "report", "propose_repairs", "apply_repairs"})
_BACKGROUND_SOLVE_OPS = frozenset({"analyze", "section", "vlm"})

_TRANSACTION_NAMES = {
    "analyze": "Aero Analyze",
    "section": "Aero Section",
    "vlm": "Aero 3D Solve",
    "report": "Write Aero Report",
    "propose_repairs": "Propose Aero Repairs",
    "apply_repairs": "Apply Aero Repairs",
}

_BASE_MUTATION_OBJECTS = {
    "analyze": ("AeroConfig", "AeroReport", "AeroAssistantJson"),
    "section": ("AeroConfig", "AeroReport", "AeroAssistantJson"),
    "vlm": ("AeroConfig", "AeroReport", "AeroAssistantJson"),
    "report": (
        "AeroReport",
        "AeroAssistantJson",
        "AeroSpreadsheet",
        "AeroReportMarkdown",
    ),
    "propose_repairs": ("AeroRepairPreview",),
    "apply_repairs": ("AeroRepairPreview", "AeroConfig"),
}

_JSBSIM_MEMBERS = (
    "vibecad_aero/vibecad_aero.xml",
    "engine/electric.xml",
    "engine/direct.xml",
)


class AeroAnalysisRuntimeError(RuntimeError):
    """A prepared Aero solve cannot be published against the exact document."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        current_revision: str | None = None,
    ) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code or "AERO_ANALYSIS_RUNTIME")
        self.current_revision = str(current_revision or "").strip() or None

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": str(self),
        }
        if self.current_revision is not None:
            result["current_revision"] = self.current_revision
        return result


def prepare_document_input(
    document: Any,
    operation: str,
) -> tuple[AeroDetachedAnalysis.PreparedAeroAnalysis, str]:
    """Freeze document-bound Aero inputs before detached execution starts."""

    clean_operation = str(operation or "").strip().lower()
    if clean_operation not in _BACKGROUND_SOLVE_OPS:
        raise AeroAnalysisRuntimeError(
            "Aero background operation must be analyze, section, or vlm.",
            error_code="AERO_OPERATION_INVALID",
        )
    cfg = AeroConfig.resolve_geometry(document)
    revision = AeroPreview.geometry_revision(document, cfg)
    coordinates, airfoil_source = AeroAirfoil.load_airfoil_coordinates(cfg["airfoil"])
    prepared = AeroDetachedAnalysis.PreparedAeroAnalysis.create(
        operation=clean_operation,
        config=cfg,
        coordinates=coordinates,
        airfoil_source=airfoil_source,
    )
    return prepared, revision


def run_detached(
    prepared: AeroDetachedAnalysis.PreparedAeroAnalysis,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> AeroDetachedAnalysis.CompletedAeroAnalysis:
    """Run only the document-free Aero computation."""

    return AeroDetachedAnalysis.execute(
        prepared,
        cancelled=cancelled,
        progress=progress,
    )


def validate_document_input(document: Any, expected_revision: str) -> None:
    """Reject publication when Aero geometry changed after preparation."""

    current_cfg = AeroConfig.resolve_geometry(document)
    current_revision = AeroPreview.geometry_revision(document, current_cfg)
    if current_revision != str(expected_revision or ""):
        raise AeroAnalysisRuntimeError(
            "The Aero geometry changed while analysis was running; stale results were not published.",
            error_code="AERO_ANALYSIS_STALE",
            current_revision=current_revision,
        )


def publish_document_result(
    document: Any,
    completed: AeroDetachedAnalysis.CompletedAeroAnalysis,
) -> dict[str, Any]:
    """Publish detached physics through the existing synchronous result contract."""

    if not isinstance(completed, AeroDetachedAnalysis.CompletedAeroAnalysis):
        raise TypeError("completed must be CompletedAeroAnalysis")
    cfg = completed.prepared.config()
    payload = completed.payload()
    VibeCADAero._ensure_aeroconfig(document, cfg)
    changes: list[dict[str, Any]] = []
    payload["changes"] = changes
    payload["RepairPasses"] = 0
    payload["Corrections"] = []
    payload["user_message"] = AeroRepair.format_user_message(changes, payload, 0)
    mass = AeroMass.measure_document(document, cfg)
    payload["mass"] = mass
    payload["flight_card"] = AeroFlightCard.build_card(cfg, payload, mass)
    payload.update(AeroStamp.analysis_stamp(payload.get("source")))
    AeroResults.write_report(document, payload)
    return {
        "ok": True,
        **payload,
        "changes": changes,
        "user_message": payload["user_message"],
        "jsbsim_path": None,
        "jsbsim_boot_error": "",
    }


def native_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise RuntimeError("Aero wrapper returned a non-object result.")
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Aero operation failed."))
    return {key: value for key, value in result.items() if key != "ok"}


class NativeAeroRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def solve(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation = str(arguments.get("operation") or "")
        if operation not in _SOLVE_OPS:
            raise ValueError(f"Unsupported Aero operation {operation!r}.")
        background = arguments.get("background", False)
        if type(background) is not bool:
            raise ValueError("Aero background must be true or false.")
        if background and operation not in _BACKGROUND_SOLVE_OPS:
            raise ValueError("Aero background execution is only available for analyze, section, or vlm.")
        context = self._context
        context.guard()
        self._require_ticket_identity(ticket, "aero.solve")
        if background:
            self._require_current_ticket(ticket, "aero.solve")
            return self._solve_background(operation, ticket)
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=_TRANSACTION_NAMES[operation],
            mutate=lambda document: _mutate_aero(document, operation),
            verify=_verify_aero_mutation,
        )

    def _solve_background(
        self,
        operation: str,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise RuntimeError(
                "Background Aero analysis is unavailable in this Native session."
            )
        prepared, expected_geometry_revision = prepare_document_input(
            context.document,
            operation,
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_detached(
                prepared,
                cancelled=cancelled,
                progress=progress,
            )

        def validate_before_commit() -> None:
            context.guard()
            self._require_current_ticket(ticket, "aero.solve")
            validate_document_input(
                context.document,
                expected_geometry_revision,
            )

        def commit(completed: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=_TRANSACTION_NAMES[operation],
                mutate=lambda document: _mutate_completed_aero(
                    document,
                    operation,
                    completed,
                ),
                verify=_verify_aero_mutation,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="aero.solve",
                prepare=prepare,
                validate_before_commit=validate_before_commit,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing verified Aero results",
            )
        except NativeBackgroundError as exc:
            raise RuntimeError(str(exc)) from exc
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "phase": str(snapshot.phase),
                "progress_percent": int(snapshot.progress_percent),
                "progress_message": str(snapshot.progress_message),
                "terminal": bool(snapshot.terminal),
            },
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }

    def export(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        if arguments:
            operation = str(arguments.get("operation") or "export_jsbsim")
            if operation != "export_jsbsim":
                raise ValueError(f"Unsupported Aero operation {operation!r}.")
        return self._export_jsbsim(ticket)

    def inspect(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        if arguments:
            operation = str(arguments.get("operation") or "flight_card")
            if operation != "flight_card":
                raise ValueError(f"Unsupported Aero operation {operation!r}.")
        self._context.guard()
        self._require_current_ticket(ticket, "aero.inspect")
        return native_payload(VibeCADAero.flight_card(self._context.document))

    def _require_ticket_identity(
        self,
        ticket: NativeCallTicket,
        capability_name: str,
    ) -> None:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("An Aero operation requires one exact Native call ticket")
        if (
            ticket.document_uid != self._context.document_uid
            or ticket.capability_name != capability_name
        ):
            raise RuntimeError("The Native Aero ticket belongs to another authority.")

    def _require_current_ticket(
        self,
        ticket: NativeCallTicket,
        capability_name: str,
    ) -> None:
        self._require_ticket_identity(ticket, capability_name)
        current = self._context.state.current_revision(self._context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)

    def _export_jsbsim(self, ticket: NativeCallTicket) -> dict[str, Any]:
        context = self._context
        context.guard()
        self._require_current_ticket(ticket, "aero.export")
        authorizer = context.authorize_output
        if authorizer is None:
            raise RuntimeError("Human-authorized Aero output is unavailable in this session.")
        payload = _prepare_jsbsim_payload(context.document)
        source_sha256 = _payload_sha256(payload)
        request = NativeOutputRequest(
            purpose="Export the current Aero report as a JSBSim plant archive.",
            title="Export JSBSim Plant",
            suggested_file_name="vibecad_aero_jsbsim.zip",
            allowed_suffixes=(".zip",),
            name_filter="JSBSim plant archive (*.zip)",
            maximum_bytes=16 * 1024 * 1024,
        )
        authorization = authorizer(request)
        if authorization is None:
            raise RuntimeError("The human cancelled Aero output authorization.")
        self._require_current_ticket(ticket, "aero.export")

        def guard() -> None:
            context.guard()
            self._require_current_ticket(ticket, "aero.export")
            if _payload_sha256(_prepare_jsbsim_payload(context.document)) != source_sha256:
                raise RuntimeError("The Aero report changed after output authorization.")

        artifact = publish_authorized_output(
            request,
            authorization,
            writer=lambda path: _write_jsbsim_archive(path, payload),
            guard=guard,
            validator=_validate_jsbsim_archive,
            temporary_suffix=".zip",
        )
        return {
            "output": artifact.summary(),
            "model": "vibecad_aero",
            "members": list(_JSBSIM_MEMBERS),
            "source_sha256": source_sha256,
            "document_unchanged": True,
        }


def _invoke_aero(document: Any, operation: str) -> dict[str, Any]:
    if operation == "analyze":
        return native_payload(VibeCADAero.run_analyze(document, repair=False))
    if operation == "section":
        return native_payload(VibeCADAero.run_section(document))
    if operation == "vlm":
        return native_payload(VibeCADAero.run_vlm(document))
    if operation == "report":
        return native_payload(VibeCADAero.write_last_report(document))
    if operation == "propose_repairs":
        return native_payload(VibeCADAero.propose_repairs(document))
    if operation == "apply_repairs":
        return native_payload(VibeCADAero.apply_repairs(document, manage_transaction=False))
    raise ValueError(f"Unsupported Aero operation {operation!r}.")


def _mutation_draft_from_result(
    document: Any,
    operation: str,
    before: Mapping[str, Any],
    result: Mapping[str, Any],
) -> NativeMutationDraft:
    names = list(_BASE_MUTATION_OBJECTS[operation])
    if operation == "apply_repairs":
        names.extend(
            str(item.get("part") or "")
            for item in result.get("landed", ())
            if isinstance(item, Mapping) and item.get("part")
        )
    after = _named_objects(document)
    created = []
    changed = []
    recompute = []
    for name in dict.fromkeys(names):
        obj = after.get(name)
        if obj is None:
            continue
        recompute.append(obj)
        identity = _safe_identity(obj)
        if identity is None:
            continue
        if name not in before:
            created.append(identity)
        elif before[name] is obj:
            changed.append(identity)
    return NativeMutationDraft(
        value={"operation": operation, "result": dict(result)},
        recompute_targets=tuple(recompute),
        created=tuple(created),
        changed=tuple(changed),
    )


def _mutate_aero(document: Any, operation: str) -> NativeMutationDraft:
    before = _named_objects(document)
    result = _invoke_aero(document, operation)
    return _mutation_draft_from_result(document, operation, before, result)


def _mutate_completed_aero(
    document: Any,
    operation: str,
    completed: Any,
) -> NativeMutationDraft:
    before = _named_objects(document)
    result = native_payload(publish_document_result(document, completed))
    return _mutation_draft_from_result(document, operation, before, result)


def _verify_aero_mutation(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    if not isinstance(draft.value, Mapping):
        raise RuntimeError("Aero mutation produced no verifiable result.")
    for identity in (*draft.created, *draft.changed):
        getter = getattr(document, "getObject", None)
        obj = getter(identity.object_name) if callable(getter) else None
        if obj is None or _safe_identity(obj) != identity:
            raise RuntimeError("An Aero mutation result is no longer in the document.")
    result = draft.value.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("Aero mutation produced a non-object result.")
    return dict(result)


def _named_objects(document: Any) -> dict[str, Any]:
    return {
        str(getattr(obj, "Name", "") or ""): obj
        for obj in getattr(document, "Objects", ()) or ()
        if str(getattr(obj, "Name", "") or "")
    }


def _safe_identity(obj: Any) -> Any | None:
    try:
        return object_identity(obj)
    except Exception:
        return None


def _prepare_jsbsim_payload(document: Any) -> dict[str, Any]:
    payload = VibeCADAero.prepare_jsbsim_payload(document)
    if payload is None:
        raise RuntimeError("No AeroReport. Run Analyze before exporting JSBSim.")
    return dict(payload)


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_jsbsim_archive(path: str, payload: Mapping[str, Any]) -> None:
    import AeroJSBSim

    with tempfile.TemporaryDirectory(prefix="vibecad-jsbsim-") as temporary:
        root = Path(temporary)
        written = AeroJSBSim.write_plant(dict(payload), output_dir=root)
        sources = (
            Path(str(written["fdm_path"])),
            Path(str(written["engine_path"])),
            Path(str(written["thruster_path"])),
        )
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, member in zip(sources, _JSBSIM_MEMBERS, strict=True):
                archive.write(source, member)


def _validate_jsbsim_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = tuple(archive.namelist())
            if names != _JSBSIM_MEMBERS:
                raise RuntimeError("The JSBSim archive has an unexpected file set.")
            for name in names:
                content = archive.read(name)
                if not content or b"<" not in content:
                    raise RuntimeError("The JSBSim archive contains invalid XML.")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise RuntimeError("The JSBSim archive could not be verified.") from exc
