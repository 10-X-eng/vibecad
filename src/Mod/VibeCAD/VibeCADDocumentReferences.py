# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, portable document-object references used by VibeScript Assembly.

The original two-field reference remains valid.  Saved assemblies may add a
POSIX ``document_path`` relative to the assembly document so a source document
can be found before FreeCAD has loaded its native external link.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import re
from typing import Any

REFERENCE_REQUIRED_FIELDS = frozenset({"document_uid", "object_name"})
REFERENCE_OPTIONAL_FIELDS = frozenset({"document_path"})
REFERENCE_FIELDS = REFERENCE_REQUIRED_FIELDS | REFERENCE_OPTIONAL_FIELDS
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_DOCUMENT_PATH_LENGTH = 2048


class DocumentReferenceError(ValueError):
    """A stable document-object reference cannot be authenticated."""


def is_document_reference(value: Any) -> bool:
    """Return whether *value* has one supported stable-reference shape."""

    if not isinstance(value, Mapping):
        return False
    fields = set(value)
    return REFERENCE_REQUIRED_FIELDS <= fields <= REFERENCE_FIELDS


def normalize_document_path(value: Any) -> str:
    """Validate one portable path relative to the owning CAD document."""

    if not isinstance(value, str):
        raise DocumentReferenceError("document_path must be a string.")
    raw = value.strip()
    if not raw:
        raise DocumentReferenceError("document_path must not be empty.")
    if len(raw) > _MAX_DOCUMENT_PATH_LENGTH:
        raise DocumentReferenceError(
            f"document_path exceeds {_MAX_DOCUMENT_PATH_LENGTH} characters."
        )
    if "\x00" in raw:
        raise DocumentReferenceError("document_path contains a NUL character.")
    if "\\" in raw:
        raise DocumentReferenceError("document_path must use portable forward slashes.")
    if raw.startswith("/") or _WINDOWS_DRIVE.match(raw):
        raise DocumentReferenceError("document_path must be relative to the owning CAD document.")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise DocumentReferenceError(
            "document_path must not contain empty, current, or parent segments."
        )
    path = PurePosixPath(raw)
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise DocumentReferenceError("document_path must name a CAD document.")
    if not normalized.casefold().endswith(".fcstd"):
        raise DocumentReferenceError("document_path must name an .FCStd document.")
    return normalized


def normalize_document_reference(value: Any) -> dict[str, str]:
    """Return one validated reference while preserving the legacy shape."""

    if not is_document_reference(value):
        raise DocumentReferenceError(
            "expected document_uid and object_name, with optional document_path."
        )
    document_uid = str(value.get("document_uid") or "").strip()
    object_name = str(value.get("object_name") or "").strip()
    if not document_uid or not object_name:
        raise DocumentReferenceError("document_uid and object_name must both be non-empty.")
    result = {
        "document_uid": document_uid,
        "object_name": object_name,
    }
    if "document_path" in value:
        result["document_path"] = normalize_document_path(value["document_path"])
    return result


def reference_key(value: Any) -> tuple[str, str]:
    """Return the immutable document/object identity of one reference."""

    clean = normalize_document_reference(value)
    return clean["document_uid"], clean["object_name"]


def _owner_directory(owner_document: Any) -> Path:
    file_name = str(getattr(owner_document, "FileName", "") or "").strip()
    if not file_name:
        raise DocumentReferenceError(
            "External Assembly components require the Assembly document to be "
            "saved before they are linked."
        )
    owner_file = Path(file_name).expanduser().resolve()
    if not owner_file.is_file():
        raise DocumentReferenceError(
            "The saved Assembly document file is unavailable; save it before "
            "linking an external component."
        )
    return owner_file.parent


def resolve_relative_document_path(owner_document: Any, document_path: Any) -> Path:
    """Resolve and confine one portable locator to the Assembly directory."""

    normalized = normalize_document_path(document_path)
    root = _owner_directory(owner_document)
    candidate = root.joinpath(*PurePosixPath(normalized).parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DocumentReferenceError(
            "document_path escapes the Assembly document directory."
        ) from exc
    return candidate


def relative_document_path(owner_document: Any, source_document: Any) -> str | None:
    """Return a portable child path when both documents are saved together."""

    owner_name = str(getattr(owner_document, "FileName", "") or "").strip()
    source_name = str(getattr(source_document, "FileName", "") or "").strip()
    if not owner_name or not source_name:
        return None
    owner_file = Path(owner_name).expanduser().resolve()
    source_file = Path(source_name).expanduser().resolve()
    if owner_file == source_file:
        return None
    try:
        relative = source_file.relative_to(owner_file.parent)
    except ValueError:
        return None
    if any(part in {"", ".", ".."} for part in relative.parts):
        return None
    normalized = PurePosixPath(*relative.parts).as_posix()
    try:
        return normalize_document_path(normalized)
    except DocumentReferenceError:
        return None


def reference_for_target(owner_document: Any, target: Any) -> dict[str, str]:
    """Build the most portable exact reference available for a live object."""

    source_document = getattr(target, "Document", None)
    if source_document is None:
        raise DocumentReferenceError("The referenced object has no document.")
    result = normalize_document_reference(
        {
            "document_uid": str(getattr(source_document, "Uid", "") or ""),
            "object_name": str(getattr(target, "Name", "") or ""),
        }
    )
    path = relative_document_path(owner_document, source_document)
    if path:
        result["document_path"] = path
    return result


def _loaded_documents() -> list[Any]:
    import FreeCAD as App

    return list(App.listDocuments().values())


def resolve_reference_document(
    owner_document: Any,
    value: Any,
    *,
    open_missing: bool = True,
) -> Any:
    """Authenticate a referenced document, loading its portable file if needed."""

    clean = normalize_document_reference(value)
    owner_uid = str(getattr(owner_document, "Uid", "") or "")
    if clean["document_uid"] == owner_uid:
        return owner_document

    expected_path: Path | None = None
    if "document_path" in clean:
        expected_path = resolve_relative_document_path(
            owner_document,
            clean["document_path"],
        )

    uid_matches = [
        document
        for document in _loaded_documents()
        if str(getattr(document, "Uid", "") or "") == clean["document_uid"]
    ]
    if len(uid_matches) > 1:
        raise DocumentReferenceError(
            f"More than one open document has uid {clean['document_uid']!r}."
        )
    if uid_matches:
        document = uid_matches[0]
        if expected_path is not None:
            loaded_name = str(getattr(document, "FileName", "") or "").strip()
            if not loaded_name or Path(loaded_name).expanduser().resolve() != expected_path:
                raise DocumentReferenceError(
                    "The open source document uid matches, but its file does not "
                    "match document_path."
                )
        return document

    if expected_path is None:
        raise DocumentReferenceError(
            "The referenced document is not open. Open it, or use a catalog "
            "reference that includes document_path."
        )
    if not open_missing:
        raise DocumentReferenceError("The referenced document is not open.")
    if not expected_path.is_file():
        raise DocumentReferenceError(
            f"Referenced component document {clean['document_path']!r} does not exist."
        )

    path_matches = [
        document
        for document in _loaded_documents()
        if str(getattr(document, "FileName", "") or "").strip()
        and Path(str(document.FileName)).expanduser().resolve() == expected_path
    ]
    if path_matches:
        raise DocumentReferenceError("document_path is already open with a different document uid.")

    import FreeCAD as App

    opened = None
    try:
        opened = App.openDocument(str(expected_path))
        observed_uid = str(getattr(opened, "Uid", "") or "")
        if observed_uid != clean["document_uid"]:
            raise DocumentReferenceError(
                "The component file document uid does not match the catalog reference."
            )
        return opened
    except Exception:
        if opened is not None:
            try:
                App.closeDocument(str(getattr(opened, "Name", "") or ""))
            except Exception:
                pass
        raise


def resolve_reference_target(
    owner_document: Any,
    value: Any,
    label: str,
    *,
    open_missing: bool = True,
) -> Any:
    """Resolve and reauthenticate one exact object in its owning document."""

    clean = normalize_document_reference(value)
    try:
        document = resolve_reference_document(
            owner_document,
            clean,
            open_missing=open_missing,
        )
    except DocumentReferenceError as exc:
        raise DocumentReferenceError(f"{label}: {exc}") from exc
    target = document.getObject(clean["object_name"])
    if target is None:
        raise DocumentReferenceError(
            f"{label}: object {clean['object_name']!r} does not exist in the "
            "authenticated source document."
        )
    if str(getattr(document, "Uid", "") or "") != clean["document_uid"]:
        raise DocumentReferenceError(
            f"{label}: source document identity changed during resolution."
        )
    return target
