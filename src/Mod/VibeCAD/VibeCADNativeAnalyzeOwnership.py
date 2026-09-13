# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM study ownership shared by foreground and background operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


def is_study(value: Any, document: Any) -> bool:
    if value is None or getattr(value, "Document", None) is not document:
        return False
    try:
        return bool(value.isDerivedFrom("Fem::FemAnalysis"))
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def studies_in_document(document: Any) -> tuple[Any, ...]:
    """Return every live FEM study in document order."""

    return tuple(
        value
        for value in tuple(getattr(document, "Objects", ()) or ())
        if is_study(value, document)
    )


def owning_studies(document: Any, resource: Any) -> tuple[Any, ...]:
    """Return exact studies whose group directly owns *resource*."""

    if getattr(resource, "Document", None) is not document:
        return ()
    return tuple(
        study
        for study in studies_in_document(document)
        if resource in tuple(getattr(study, "Group", ()) or ())
    )


def owning_study(document: Any, resource: Any) -> Any:
    """Require exactly one owning study for a mutable FEM resource."""

    owners = owning_studies(document, resource)
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The FEM resource must belong to exactly one study.",
            error_code="NATIVE_ANALYZE_STUDY_OWNERSHIP_INVALID",
        )
    return owners[0]


def study_resource_scope(study: Any) -> str:
    """Return the stable background-job lock shared by one study's resources."""

    document = getattr(study, "Document", None)
    if not is_study(study, document):
        raise NativeAnalyzeError(
            "The FEM study is no longer live.",
            error_code="NATIVE_ANALYZE_STUDY_OWNERSHIP_INVALID",
        )
    return f"analyze:{study.Name}"


def study_history_operations(study: Any) -> tuple[Any, ...]:
    """Return the exact History subsequence owned by one FEM study."""

    document = getattr(study, "Document", None)
    if not is_study(study, document):
        raise NativeAnalyzeError(
            "The FEM study is no longer live.",
            error_code="NATIVE_ANALYZE_STUDY_OWNERSHIP_INVALID",
        )
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    study_member_ids = {
        id(member) for member in tuple(getattr(study, "Group", ()) or ())
    }

    def belongs_to_study(operation: Any) -> bool:
        current = operation
        visited: set[int] = set()
        while current is not None:
            if id(current) in study_member_ids:
                return True
            identity = id(current)
            if identity in visited:
                raise NativeAnalyzeError(
                    "A FEM History ownership graph contains a cycle.",
                    error_code="NATIVE_ANALYZE_DEPENDENCY_CYCLE",
                )
            visited.add(identity)
            current = getattr(current, "VibeCADTimelineOwner", None)
        return False

    return tuple(operation for operation in operations if belongs_to_study(operation))
