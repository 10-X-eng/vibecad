# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-facing restart reconciliation for durable Analysis attempts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Mapping

from VibeCADAnalysisContracts import AnalysisContractError
from VibeCADAnalysisProviders import ProviderCapabilities
from tool_impl.analysis_artifacts import (
    DEFAULT_MAXIMUM_BYTES,
    DEFAULT_MAXIMUM_FILES,
    AnalysisArtifactError,
    ArtifactDescriptor,
    ArtifactManifest,
)
from tool_impl.analysis_persistence import (
    AnalysisMetadataStore,
    restart_disposition_for_record,
)


_PROVIDER_STATES = frozenset({
    "queued", "running", "completed", "failed", "cancelled",
})
_STATUS_FIELDS = frozenset({
    "provider_job_id", "state", "outputs_available", "failure_code",
})
_RECONNECT_FIELDS = frozenset({"provider_job_id"})
_ARTIFACT_FIELDS = frozenset(
    field_info.name for field_info in fields(ArtifactDescriptor)
)
_REASON_CODE_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)


class AnalysisProviderRecoveryError(RuntimeError):
    """Provider recovery could not proceed without guessing durable state."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = str(reason or "").strip()
        super().__init__(str(message or "").strip())


@dataclass(frozen=True, slots=True)
class AnalysisProviderRecoveryResult:
    analysis_id: str
    attempt: int
    provider_id: str
    provider_job_id: str
    outcome: str
    reason: str
    provider_state: str
    record: dict[str, Any]
    output_manifest: ArtifactManifest | None = None
    publication_authorized: bool = field(default=False, init=False)


class AnalysisProviderRecoveryCoordinator:
    """Reconnect proven remote work without verification/publication authority."""

    def __init__(
        self,
        store: AnalysisMetadataStore,
        *,
        maximum_artifacts: int = DEFAULT_MAXIMUM_FILES,
        maximum_artifact_bytes: int = DEFAULT_MAXIMUM_BYTES,
    ) -> None:
        if not isinstance(store, AnalysisMetadataStore):
            raise TypeError("store must be AnalysisMetadataStore")
        if type(maximum_artifacts) is not int or maximum_artifacts < 1:
            raise ValueError("maximum_artifacts must be a positive integer")
        if type(maximum_artifact_bytes) is not int or maximum_artifact_bytes < 1:
            raise ValueError("maximum_artifact_bytes must be a positive integer")
        self.store = store
        self.maximum_artifacts = min(
            maximum_artifacts, store.maximum_artifacts_per_analysis,
        )
        self.maximum_artifact_bytes = min(
            maximum_artifact_bytes, store.maximum_artifact_bytes_per_analysis,
        )

    def reconcile_remote(
        self,
        analysis_id: str,
        provider: Any,
    ) -> AnalysisProviderRecoveryResult:
        record = self.store.load(analysis_id)
        disposition = restart_disposition_for_record(record)
        if disposition["action"] != "reconnect_remote":
            raise AnalysisProviderRecoveryError(
                "not_reconnectable",
                "Persisted latest-attempt evidence does not authorize reconnect.",
            )
        attempt_number = disposition["attempt"]
        provider_id = disposition["provider_id"]
        provider_job_id = disposition["provider_job_id"]

        capabilities = self._describe_capabilities(provider)
        if (
            capabilities.provider_id != provider_id
            or capabilities.location not in {"remote", "hybrid"}
            or not capabilities.reconnect_supported
            or not capabilities.job_survives_client_exit
        ):
            raise AnalysisProviderRecoveryError(
                "provider_mismatch",
                "The live provider does not match persisted reconnect authority.",
            )

        try:
            reconnect_value = self._provider_method(provider, "reconnect")(
                provider_job_id
            )
        except AnalysisProviderRecoveryError:
            raise
        except Exception as exc:
            raise AnalysisProviderRecoveryError(
                "provider_unavailable",
                "The provider could not be reached for reconnect.",
            ) from exc
        if reconnect_value is None:
            interrupted = self.store.interrupt_missing_provider_job_after_restart(
                analysis_id
            )
            return self._result(
                interrupted,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="interrupted",
                reason="provider_job_not_found",
                provider_state="",
            )
        if (
            not isinstance(reconnect_value, Mapping)
            or set(reconnect_value) != _RECONNECT_FIELDS
            or reconnect_value.get("provider_job_id") != provider_job_id
        ):
            return self._terminal_result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="failed",
                reason="provider_reconnect_invalid",
                provider_state="",
            )

        try:
            status_value = self._provider_method(provider, "status")(provider_job_id)
        except AnalysisProviderRecoveryError:
            raise
        except Exception as exc:
            raise AnalysisProviderRecoveryError(
                "provider_unavailable",
                "The provider could not report remote job status.",
            ) from exc
        try:
            status = self._normalize_status(status_value, provider_job_id)
        except (TypeError, ValueError):
            return self._terminal_result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="failed",
                reason="provider_status_invalid",
                provider_state="",
            )

        provider_state = status["state"]
        if provider_state in {"queued", "running"}:
            return self._result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="running",
                reason="provider_confirms_running",
                provider_state=provider_state,
            )
        if provider_state == "failed":
            reason = f"provider_failed:{status['failure_code']}"
            return self._terminal_result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="failed",
                reason=reason,
                provider_state=provider_state,
            )
        if provider_state == "cancelled":
            return self._terminal_result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="cancelled",
                reason="provider_cancelled",
                provider_state=provider_state,
            )

        try:
            collected_value = self._provider_method(provider, "collect")(
                provider_job_id
            )
        except AnalysisProviderRecoveryError:
            raise
        except Exception as exc:
            raise AnalysisProviderRecoveryError(
                "provider_unavailable",
                "The provider could not collect completed outputs.",
            ) from exc
        try:
            manifest = self._normalize_manifest(
                collected_value,
                analysis_id=record["analysis_id"],
                provider_id=provider_id,
            )
        except (AnalysisArtifactError, TypeError, ValueError):
            return self._terminal_result(
                record,
                attempt_number,
                provider_id,
                provider_job_id,
                outcome="failed",
                reason="provider_collection_invalid",
                provider_state=provider_state,
            )

        receipt = {
            "collected_at": _utc_now(),
            "attempt": attempt_number,
            "provider_id": provider_id,
            "provider_job_id": provider_job_id,
            "output_manifest_sha256": manifest.sha256,
        }
        updated = self.store.transition(
            analysis_id,
            "collecting",
            reason="provider_outputs_collected",
            updates={
                "provider_collection_receipts": [
                    *record.get("provider_collection_receipts", []), receipt,
                ],
            },
            expected_state="running_remote",
        )
        return self._result(
            updated,
            attempt_number,
            provider_id,
            provider_job_id,
            outcome="collected",
            reason="provider_outputs_collected",
            provider_state=provider_state,
            output_manifest=manifest,
        )

    @staticmethod
    def _provider_method(provider: Any, name: str):
        method = getattr(provider, name, None)
        if not callable(method):
            raise AnalysisProviderRecoveryError(
                "provider_contract_invalid",
                f"The provider does not implement required {name} behavior.",
            )
        return method

    def _describe_capabilities(self, provider: Any) -> ProviderCapabilities:
        try:
            value = self._provider_method(provider, "describe_capabilities")()
        except AnalysisProviderRecoveryError:
            raise
        except Exception as exc:
            raise AnalysisProviderRecoveryError(
                "provider_unavailable",
                "The provider could not describe its recovery capabilities.",
            ) from exc
        if isinstance(value, ProviderCapabilities):
            return value
        if not isinstance(value, Mapping):
            raise AnalysisProviderRecoveryError(
                "provider_contract_invalid",
                "Provider capabilities must use the host capability contract.",
            )
        allowed = {field_info.name for field_info in fields(ProviderCapabilities)}
        if any(name not in allowed for name in value):
            raise AnalysisProviderRecoveryError(
                "provider_contract_invalid",
                "Provider capabilities contain undeclared fields.",
            )
        try:
            return ProviderCapabilities(**dict(value))
        except (AnalysisContractError, TypeError, ValueError) as exc:
            raise AnalysisProviderRecoveryError(
                "provider_contract_invalid",
                "Provider capabilities are invalid.",
            ) from exc

    @staticmethod
    def _normalize_status(value: Any, provider_job_id: str) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != _STATUS_FIELDS:
            raise TypeError("Provider status must use the bounded host contract")
        state = value.get("state")
        outputs_available = value.get("outputs_available")
        failure_code = value.get("failure_code")
        if (
            value.get("provider_job_id") != provider_job_id
            or state not in _PROVIDER_STATES
            or type(outputs_available) is not bool
            or (
                failure_code is not None
                and (
                    not isinstance(failure_code, str)
                    or not failure_code.strip()
                    or len(failure_code) > 80
                    or any(
                        character not in _REASON_CODE_CHARACTERS
                        for character in failure_code
                    )
                )
            )
            or (state == "completed") != outputs_available
            or (state == "failed") != (failure_code is not None)
        ):
            raise ValueError("Provider status is internally inconsistent")
        return {
            "provider_job_id": provider_job_id,
            "state": state,
            "outputs_available": outputs_available,
            "failure_code": failure_code,
        }

    def _normalize_manifest(
        self,
        value: Any,
        *,
        analysis_id: str,
        provider_id: str,
    ) -> ArtifactManifest:
        if isinstance(value, ArtifactManifest):
            manifest = value
        else:
            if (
                not isinstance(value, Mapping)
                or set(value) != {"version", "artifacts"}
                or not isinstance(value.get("artifacts"), list)
            ):
                raise TypeError("Collected outputs must be an artifact manifest")
            raw_artifacts = value["artifacts"]
            if not raw_artifacts or len(raw_artifacts) > self.maximum_artifacts:
                raise ValueError("Collected artifact count exceeds its bound")
            artifacts = []
            for item in raw_artifacts:
                if not isinstance(item, Mapping) or set(item) != _ARTIFACT_FIELDS:
                    raise ValueError("Collected artifact descriptor is invalid")
                artifacts.append(ArtifactDescriptor(**dict(item)))
            manifest = ArtifactManifest(
                str(value.get("version") or ""), tuple(artifacts)
            )
        if len(manifest.artifacts) > self.maximum_artifacts:
            raise ValueError("Collected artifact count exceeds its bound")
        if (
            sum(item.byte_count for item in manifest.artifacts)
            > self.maximum_artifact_bytes
        ):
            raise ValueError("Collected artifacts exceed the declared byte bound")
        if any(
            item.job_id != analysis_id or item.provider_id != provider_id
            for item in manifest.artifacts
        ):
            raise ValueError("Collected artifacts do not bind this provider attempt")
        return manifest

    def _terminal_result(
        self,
        record: Mapping[str, Any],
        attempt: int,
        provider_id: str,
        provider_job_id: str,
        *,
        outcome: str,
        reason: str,
        provider_state: str,
    ) -> AnalysisProviderRecoveryResult:
        attempts = deepcopy(record["attempts"])
        attempts[-1]["terminal_reason"] = reason
        updated = self.store.transition(
            record["analysis_id"],
            outcome,
            reason=reason,
            updates={"attempts": attempts},
            expected_state="running_remote",
        )
        return self._result(
            updated,
            attempt,
            provider_id,
            provider_job_id,
            outcome=outcome,
            reason=reason,
            provider_state=provider_state,
        )

    @staticmethod
    def _result(
        record: Mapping[str, Any],
        attempt: int,
        provider_id: str,
        provider_job_id: str,
        *,
        outcome: str,
        reason: str,
        provider_state: str,
        output_manifest: ArtifactManifest | None = None,
    ) -> AnalysisProviderRecoveryResult:
        return AnalysisProviderRecoveryResult(
            analysis_id=str(record["analysis_id"]),
            attempt=attempt,
            provider_id=provider_id,
            provider_job_id=provider_job_id,
            outcome=outcome,
            reason=reason,
            provider_state=provider_state,
            record=deepcopy(dict(record)),
            output_manifest=output_manifest,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
