# SPDX-License-Identifier: LGPL-2.1-or-later

"""Crash-safe admission of provider-collected files into immutable host storage."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from tool_impl.analysis_artifacts import (
    AnalysisArtifactError,
    ArtifactDescriptor,
    ArtifactManifest,
    ContentAddressedArtifactStore,
)
from tool_impl.analysis_persistence import (
    AnalysisMetadataStore,
    AnalysisPersistenceError,
)


class AnalysisOutputAdmissionError(RuntimeError):
    """Collected outputs cannot advance without guessing or losing evidence."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = str(reason or "").strip()
        super().__init__(str(message or "").strip())


@dataclass(frozen=True, slots=True)
class AnalysisOutputAdmissionResult:
    analysis_id: str
    attempt: int
    outcome: str
    reason: str
    record: dict[str, Any]
    admitted_sha256: tuple[str, ...]
    publication_authorized: bool = field(default=False, init=False)


class AnalysisOutputAdmissionCoordinator:
    """Verify and admit exact collected files without publication authority."""

    def __init__(
        self,
        store: AnalysisMetadataStore,
        artifact_store: ContentAddressedArtifactStore,
    ) -> None:
        if not isinstance(store, AnalysisMetadataStore):
            raise TypeError("store must be AnalysisMetadataStore")
        if not isinstance(artifact_store, ContentAddressedArtifactStore):
            raise TypeError("artifact_store must be ContentAddressedArtifactStore")
        self.store = store
        self.artifact_store = artifact_store

    def admit_collected(
        self,
        analysis_id: str,
        manifest: ArtifactManifest,
        transport_root: str | Path,
    ) -> AnalysisOutputAdmissionResult:
        if not isinstance(manifest, ArtifactManifest):
            raise AnalysisOutputAdmissionError(
                "collection_manifest_invalid",
                "Collected outputs must use the immutable artifact manifest.",
            )
        record = self.store.load(analysis_id)
        receipt, attempt = self._authorize_manifest(record, manifest)
        provider_id = receipt["provider_id"]
        if any(
            descriptor.job_id != record["analysis_id"]
            or descriptor.provider_id != provider_id
            for descriptor in manifest.artifacts
        ):
            raise AnalysisOutputAdmissionError(
                "collection_receipt_mismatch",
                "Collected descriptors do not bind the authorized provider attempt.",
            )

        admitted: list[str] = []
        for descriptor in manifest.artifacts:
            try:
                source = self._transport_source(
                    transport_root, descriptor.relative_path
                )
                self.artifact_store.admit(source, descriptor)
            except AnalysisArtifactError as exc:
                if exc.reason in {"read_failed", "invalid_manifest"}:
                    raise AnalysisOutputAdmissionError(
                        "transport_unavailable",
                        "Collected output transport or storage is not durably readable.",
                    ) from exc
                return self._integrity_failure(record, attempt, exc.reason)
            try:
                record = self.store.record_artifact(
                    analysis_id,
                    asdict(descriptor),
                    pinned=False,
                    cleanup_eligible=False,
                    expected_state="collecting",
                )
            except AnalysisPersistenceError as exc:
                raise AnalysisOutputAdmissionError(
                    "metadata_unavailable",
                    "Verified output could not be durably recorded.",
                ) from exc
            admitted.append(descriptor.sha256)

        updated = self.store.transition(
            analysis_id,
            "verifying",
            reason="provider_outputs_admitted",
            expected_state="collecting",
        )
        return AnalysisOutputAdmissionResult(
            analysis_id=analysis_id,
            attempt=attempt,
            outcome="verifying",
            reason="provider_outputs_admitted",
            record=deepcopy(updated),
            admitted_sha256=tuple(admitted),
        )

    @staticmethod
    def _authorize_manifest(
        record: Mapping[str, Any],
        manifest: ArtifactManifest,
    ) -> tuple[Mapping[str, Any], int]:
        if record.get("state") != "collecting":
            raise AnalysisOutputAdmissionError(
                "not_collecting",
                "Durable lifecycle state does not authorize output admission.",
            )
        attempts = record.get("attempts")
        receipts = record.get("provider_collection_receipts")
        if not isinstance(attempts, list) or not attempts:
            raise AnalysisOutputAdmissionError(
                "collection_receipt_mismatch", "Collection attempt is missing."
            )
        attempt = attempts[-1]
        attempt_number = attempt.get("attempt")
        if not isinstance(receipts, list) or not receipts:
            raise AnalysisOutputAdmissionError(
                "collection_receipt_mismatch", "Collection receipt is missing."
            )
        receipt = receipts[-1]
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("attempt") != attempt_number
            or receipt.get("provider_id") != attempt.get("provider_id")
            or receipt.get("provider_job_id") != attempt.get("provider_job_id")
            or receipt.get("output_manifest_sha256") != manifest.sha256
        ):
            raise AnalysisOutputAdmissionError(
                "collection_receipt_mismatch",
                "Manifest does not match the latest durable collection receipt.",
            )
        return receipt, int(attempt_number)

    @staticmethod
    def _transport_source(root: str | Path, relative_path: str) -> Path:
        base = Path(root)
        try:
            resolved_base = base.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AnalysisArtifactError(
                "read_failed", "Collected output transport is unavailable."
            ) from exc
        if base.is_symlink() or not resolved_base.is_dir():
            raise AnalysisArtifactError(
                "unsafe_symlink", "Collected output root is not an owned directory."
            )
        candidate = base
        for part in relative_path.split("/"):
            candidate = candidate / part
            if candidate.is_symlink():
                raise AnalysisArtifactError(
                    "unsafe_symlink",
                    "Collected output traverses a symbolic link.",
                    relative_path=relative_path,
                )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_base)
        except FileNotFoundError as exc:
            raise AnalysisArtifactError(
                "read_failed",
                "Collected output is not available yet.",
                relative_path=relative_path,
            ) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise AnalysisArtifactError(
                "unsafe_path",
                "Collected output escaped its transport root.",
                relative_path=relative_path,
            ) from exc
        if not resolved.is_file():
            raise AnalysisArtifactError(
                "read_failed",
                "Collected output is not a regular file.",
                relative_path=relative_path,
            )
        return resolved

    def _integrity_failure(
        self,
        record: Mapping[str, Any],
        attempt: int,
        artifact_reason: str,
    ) -> AnalysisOutputAdmissionResult:
        reason = {
            "hash_mismatch": "provider_output_hash_mismatch",
            "bounds": "provider_output_bounds_exceeded",
            "unsafe_path": "provider_output_unsafe",
            "unsafe_symlink": "provider_output_unsafe",
            "unsafe_archive": "provider_output_unsafe",
        }.get(artifact_reason, "provider_output_invalid")
        attempts = deepcopy(record["attempts"])
        attempts[-1]["terminal_reason"] = reason
        updated = self.store.transition(
            record["analysis_id"],
            "failed",
            reason=reason,
            updates={"attempts": attempts},
            expected_state="collecting",
        )
        return AnalysisOutputAdmissionResult(
            analysis_id=record["analysis_id"],
            attempt=attempt,
            outcome="failed",
            reason=reason,
            record=deepcopy(updated),
            admitted_sha256=tuple(
                item["sha256"] for item in updated.get("artifacts", [])
                if not item.get("tombstoned_at")
            ),
        )
