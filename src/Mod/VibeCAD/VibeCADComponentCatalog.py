# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic discovery of reusable Assembly component definitions."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    reference_for_target,
)

MAX_COMPONENT_FILES = 512
MAX_COMPONENT_DIRECTORIES = 4096
MAX_DOCUMENT_XML_BYTES = 16 * 1024 * 1024
MAX_COMPONENT_FILE_BYTES = 512 * 1024 * 1024
_SAVED_COMPONENT_TYPES = frozenset(
    {
        "App::Part",
        "Assembly::AssemblyObject",
        "Part::Feature",
        "Part::FeaturePython",
        "PartDesign::Body",
    }
)
_SEARCH_PROPERTY_NAMES = frozenset(
    {
        "Description",
        "Label",
        "PartNumber",
        "StockCode",
    }
)


class ComponentCatalogError(ValueError):
    """The project component catalog could not be searched safely."""


def _object_is_derived(obj: Any, type_name: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_name))
    except Exception:
        return False


def _inside_partdesign_body(obj: Any) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
        return False
    return any(
        str(getattr(parent, "TypeId", "") or "") == "PartDesign::Body"
        for parent in list(getattr(obj, "InList", []) or [])
    )


def _live_component_candidate(
    owner_document: Any,
    source_document: Any,
    obj: Any,
) -> dict[str, Any] | None:
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in {"App::Link", "Assembly::AssemblyLink"}:
        return None
    if _inside_partdesign_body(obj):
        return None
    supported = (
        type_id == "PartDesign::Body"
        or _object_is_derived(obj, "Assembly::AssemblyObject")
        or (_object_is_derived(obj, "App::Part") and not _object_is_derived(obj, "Part::Feature"))
        or _object_is_derived(obj, "Part::Feature")
    )
    if not supported:
        return None
    try:
        shape = getattr(obj, "Shape", None)
        if (
            shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
            or len(list(getattr(shape, "Solids", []) or [])) < 1
        ):
            return None
    except Exception:
        return None
    try:
        reference = reference_for_target(owner_document, obj)
    except DocumentReferenceError:
        return None
    same_document = source_document is owner_document
    portable = same_document or "document_path" in reference
    return {
        "document_label": str(
            getattr(source_document, "Label", "") or getattr(source_document, "Name", "") or ""
        ),
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": type_id,
        "source": "open_document",
        "live_validated": True,
        "portable": portable,
        "reference": reference,
    }


def open_component_candidates(owner: Any) -> list[dict[str, Any]]:
    """Return live-validated component definitions from all open documents."""

    import FreeCAD as App

    open_candidates: list[dict[str, Any]] = []
    for document in list(App.listDocuments().values()):
        for obj in list(getattr(document, "Objects", []) or [])[:20_000]:
            candidate = _live_component_candidate(owner, document, obj)
            if candidate is not None:
                open_candidates.append(candidate)
    return open_candidates


def capture_component_catalog(service: Any) -> dict[str, Any]:
    """Capture open-document candidates on the FreeCAD document thread."""

    owner = service._active_document()
    if owner is None:
        raise ComponentCatalogError("Component search requires an active Assembly document.")
    import FreeCAD as App

    open_candidates = open_component_candidates(owner)
    open_paths: set[str] = set()
    for document in list(App.listDocuments().values()):
        file_name = str(getattr(document, "FileName", "") or "").strip()
        if file_name:
            open_paths.add(str(Path(file_name).expanduser().resolve()))
    owner_file_name = str(getattr(owner, "FileName", "") or "").strip()
    owner_file = Path(owner_file_name).expanduser().resolve() if owner_file_name else None
    return {
        "owner_document_uid": str(getattr(owner, "Uid", "") or ""),
        "project_directory": (str(owner_file.parent) if owner_file is not None else ""),
        "owner_file": str(owner_file) if owner_file is not None else "",
        "open_document_files": sorted(open_paths),
        "open_candidates": open_candidates,
    }


def _bounded_saved_files(root: Path) -> tuple[list[Path], bool, int]:
    files: list[Path] = []
    directory_count = 0
    truncated = False
    for current, directories, names in os.walk(root, followlinks=False):
        directory_count += 1
        directories[:] = sorted(
            name for name in directories if not Path(current, name).is_symlink()
        )
        if directory_count > MAX_COMPONENT_DIRECTORIES:
            truncated = True
            break
        for name in sorted(names):
            if not name.casefold().endswith(".fcstd"):
                continue
            path = Path(current, name)
            if path.is_symlink():
                continue
            files.append(path)
            if len(files) >= MAX_COMPONENT_FILES:
                truncated = True
                return files, truncated, directory_count
    return files, truncated, directory_count


def _property_text(property_node: ET.Element | None) -> str:
    if property_node is None:
        return ""
    for child_name in ("String", "Uuid"):
        child = property_node.find(child_name)
        if child is not None:
            return str(child.attrib.get("value") or "")
    return ""


def _document_property(root: ET.Element, name: str) -> str:
    properties = root.find("Properties")
    if properties is None:
        return ""
    for node in properties.findall("Property"):
        if str(node.attrib.get("name") or "") == name:
            return _property_text(node)
    return ""


def _saved_document_candidates(
    project_root: Path,
    path: Path,
) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ComponentCatalogError(f"Could not stat {path.name!r}: {exc}") from exc
    if not 0 < size <= MAX_COMPONENT_FILE_BYTES:
        raise ComponentCatalogError(f"{path.name!r} is empty or exceeds the component-file limit.")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            info = archive.getinfo("Document.xml")
            if not 0 < info.file_size <= MAX_DOCUMENT_XML_BYTES:
                raise ComponentCatalogError(f"{path.name!r} has an invalid Document.xml size.")
            xml = archive.read(info)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ComponentCatalogError(
            f"{path.name!r} is not a readable FreeCAD document: {exc}"
        ) from exc
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ComponentCatalogError(f"{path.name!r} has malformed FreeCAD metadata: {exc}") from exc
    document_uid = _document_property(root, "Uid").strip()
    if not document_uid:
        raise ComponentCatalogError(f"{path.name!r} has no document uid.")
    document_label = _document_property(root, "Label").strip() or path.stem
    declarations = root.find("Objects")
    if declarations is None:
        return []
    types = {
        str(node.attrib.get("name") or ""): str(node.attrib.get("type") or "")
        for node in declarations.findall("Object")
    }
    metadata: dict[str, dict[str, str]] = {}
    object_data = root.find("ObjectData")
    if object_data is not None:
        for object_node in object_data.findall("Object"):
            object_name = str(object_node.attrib.get("name") or "")
            properties = object_node.find("Properties")
            if not object_name or properties is None:
                continue
            values: dict[str, str] = {}
            for property_node in properties.findall("Property"):
                property_name = str(property_node.attrib.get("name") or "")
                if property_name in _SEARCH_PROPERTY_NAMES:
                    text = _property_text(property_node).strip()
                    if text:
                        values[property_name] = text
            metadata[object_name] = values
    relative = path.relative_to(project_root)
    document_path = relative.as_posix()
    candidates = []
    for object_name, type_id in types.items():
        if not object_name or type_id not in _SAVED_COMPONENT_TYPES:
            continue
        values = metadata.get(object_name, {})
        label = values.get("Label") or object_name
        item = {
            "document_label": document_label,
            "object_name": object_name,
            "label": label,
            "type_id": type_id,
            "source": "saved_project_file",
            "live_validated": False,
            "portable": True,
            "reference": {
                "document_uid": document_uid,
                "object_name": object_name,
                "document_path": document_path,
            },
        }
        if values.get("PartNumber"):
            item["part_number"] = values["PartNumber"]
        if values.get("Description"):
            item["description"] = values["Description"]
        if values.get("StockCode"):
            item["stock_code"] = values["StockCode"]
        candidates.append(item)
    return candidates


def _query_matches(candidate: Mapping[str, Any], terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    text = " ".join(
        str(candidate.get(name) or "")
        for name in (
            "document_label",
            "object_name",
            "label",
            "type_id",
            "part_number",
            "description",
            "stock_code",
        )
    ).casefold()
    return all(term in text for term in terms)


def search_captured_component_catalog(
    captured: Mapping[str, Any],
    query: str = "",
    *,
    document_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search captured open objects and saved project metadata off-thread."""

    clean_query = str(query or "").strip()
    if len(clean_query) > 256:
        raise ComponentCatalogError("query exceeds 256 characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ComponentCatalogError("limit must be an integer between 1 and 100.")
    clean_document_path = str(document_path or "").strip()
    if clean_document_path:
        from VibeCADDocumentReferences import normalize_document_path

        try:
            clean_document_path = normalize_document_path(clean_document_path)
        except DocumentReferenceError as exc:
            raise ComponentCatalogError(str(exc)) from exc

    candidates = [
        dict(item)
        for item in list(captured.get("open_candidates") or [])
        if isinstance(item, Mapping)
    ]
    project_directory = str(captured.get("project_directory") or "").strip()
    scanned_files = 0
    skipped_files = 0
    search_truncated = False
    errors: list[dict[str, str]] = []
    if project_directory:
        root = Path(project_directory).expanduser().resolve()
        owner_file = str(captured.get("owner_file") or "")
        open_files = {
            str(Path(item).expanduser().resolve())
            for item in list(captured.get("open_document_files") or [])
            if str(item or "").strip()
        }
        files, search_truncated, _directory_count = _bounded_saved_files(root)
        for path in files:
            relative = path.relative_to(root).as_posix()
            if clean_document_path and relative != clean_document_path:
                continue
            resolved = str(path.resolve())
            if resolved == owner_file or resolved in open_files:
                continue
            scanned_files += 1
            try:
                candidates.extend(_saved_document_candidates(root, path))
            except ComponentCatalogError as exc:
                skipped_files += 1
                if len(errors) < 8:
                    errors.append(
                        {
                            "document_path": relative,
                            "error": str(exc),
                        }
                    )
    elif clean_document_path:
        raise ComponentCatalogError(
            "Save the Assembly document before searching a project-relative file."
        )

    terms = tuple(term for term in clean_query.casefold().split() if term)
    if clean_document_path:
        candidates = [
            item
            for item in candidates
            if str(dict(item.get("reference") or {}).get("document_path") or "")
            == clean_document_path
        ]
    matches = [item for item in candidates if _query_matches(item, terms)]
    matches.sort(
        key=lambda item: (
            str(dict(item.get("reference") or {}).get("document_path") or ""),
            str(item.get("document_label") or "").casefold(),
            str(item.get("label") or "").casefold(),
            str(item.get("object_name") or ""),
        )
    )
    return {
        "query": clean_query,
        "document_path": clean_document_path,
        "project_file_search_available": bool(project_directory),
        "saved_documents_scanned": scanned_files,
        "saved_documents_skipped": skipped_files,
        "search_truncated": search_truncated,
        "match_count": len(matches),
        "matches_truncated": len(matches) > limit,
        "matches": matches[:limit],
        "errors": errors,
    }


def search_component_catalog(
    service: Any,
    query: str = "",
    *,
    document_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Synchronous convenience entry point for tests and direct callers."""

    return search_captured_component_catalog(
        capture_component_catalog(service),
        query,
        document_path=document_path,
        limit=limit,
    )
