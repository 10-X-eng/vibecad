"""Reference-only durable publication descriptor/currentness semantics.

This file deliberately contains no FreeCAD mutation code.  It demonstrates the
architectural rule that persisted job provenance is inert and that publication
requires a separately supplied fresh host authorization after currentness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

PublicationState = Literal[
    "UNVALIDATED",
    "AWAITING_SOURCE",
    "AWAITING_PUBLICATION",
    "CURRENT",
    "STALE",
    "QUARANTINED",
    "PUBLISHED",
]


@dataclass(frozen=True, slots=True)
class PublicationDescriptor:
    publication_id: str
    job_id: str
    analysis_id: str
    submission_id: str
    domain_id: str
    adapter_id: str
    adapter_version: str
    source_document_uid: str
    frozen_dependency_snapshot_id: str
    output_manifest_id: str
    result_identity: str


@dataclass(frozen=True, slots=True)
class CurrentnessReport:
    current: bool
    source_resolved: bool
    changed_dependencies: tuple[str, ...] = ()
    ambiguous_dependencies: tuple[str, ...] = ()

    @property
    def disposition(self) -> PublicationState:
        if not self.source_resolved:
            return "AWAITING_SOURCE"
        if self.ambiguous_dependencies or not self.current:
            return "STALE"
        return "CURRENT"


def publication_disposition(
    descriptor: PublicationDescriptor,
    report: CurrentnessReport,
    *,
    fresh_host_authorization: bool,
    existing_receipt: Mapping[str, object] | None = None,
) -> PublicationState:
    """Pure decision helper; it cannot and does not mutate CAD."""
    if existing_receipt is not None:
        return "PUBLISHED"
    disposition = report.disposition
    if disposition != "CURRENT":
        return disposition
    if not fresh_host_authorization:
        return "AWAITING_PUBLICATION"
    # A real host now enters NativeMutationRunner/transaction/postcondition flow.
    return "CURRENT"
