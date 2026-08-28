# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider ports for the host-owned VibeCAD Analysis Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from VibeCADAnalysisContracts import AnalysisContractError, PreparedAnalysis


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider_id: str
    location: str
    reconnect_supported: bool
    cancel_supported: bool
    log_streaming: bool
    execution_environment: str
    accelerator_types: tuple[str, ...] = ()
    maximum_input_bytes: int | None = None
    maximum_output_bytes: int | None = None
    portable_bundle_required: bool = False
    job_survives_client_exit: bool = False

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip()
        if not provider_id:
            raise AnalysisContractError("provider_id must be non-empty.")
        location = str(self.location or "").strip().lower()
        if location not in {"local", "remote", "hybrid"}:
            raise AnalysisContractError(
                "provider location must be local, remote, or hybrid."
            )
        execution_environment = str(self.execution_environment or "").strip()
        if not execution_environment:
            raise AnalysisContractError(
                "execution_environment must be non-empty."
            )
        accelerators = tuple(
            str(value).strip()
            for value in self.accelerator_types
            if str(value).strip()
        )
        for field_name in ("maximum_input_bytes", "maximum_output_bytes"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 1):
                raise AnalysisContractError(
                    f"{field_name} must be a positive integer when provided."
                )
        for field_name in (
            "reconnect_supported", "cancel_supported", "log_streaming",
            "portable_bundle_required", "job_survives_client_exit",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise AnalysisContractError(f"{field_name} must be a boolean.")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "execution_environment", execution_environment)
        object.__setattr__(self, "accelerator_types", accelerators)

    def recovery_snapshot(self) -> dict[str, bool]:
        """Return only inert capabilities needed to classify host restart."""

        return {
            "reconnect_supported": self.reconnect_supported,
            "job_survives_client_exit": self.job_survives_client_exit,
        }


@runtime_checkable
class AnalysisProvider(Protocol):
    """Execution-location port. Providers never select engineering physics."""

    def describe_capabilities(self) -> ProviderCapabilities: ...

    def submit_or_launch(self, prepared_analysis: PreparedAnalysis) -> str: ...

    def status(self, provider_job_id: str) -> Mapping[str, Any]: ...

    def cancel(self, provider_job_id: str) -> bool: ...

    def collect(self, provider_job_id: str) -> Mapping[str, Any]: ...

    def reconnect(self, provider_job_id: str) -> Mapping[str, Any] | None: ...
