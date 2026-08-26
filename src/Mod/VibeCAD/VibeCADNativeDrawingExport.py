# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Drawing output targets and bounded artifact verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping
from xml.etree import ElementTree

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeOutput import NativeOutputRequest
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import NativeObjectRef, read_current_selection, resolve_object


MAX_DRAWING_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_PRINTABLE_DRAWING_PAGES = 64
_FORMATS = {
    "svg": (".svg", "SVG drawing (*.svg)"),
    "dxf": (".dxf", "DXF drawing (*.dxf)"),
    "pdf": (".pdf", "PDF drawing (*.pdf)"),
}
_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True, slots=True)
class DrawingOutputBoundary:
    objects: tuple[Any, ...]
    selection: dict[str, Any]
    undo_count: int
    transaction_id: int
    gui_modified: bool | None


@dataclass(frozen=True, slots=True)
class PreparedDrawingPageExport:
    page_ref: NativeObjectRef
    page: Any
    page_state: dict[str, Any]
    format_name: str
    boundary: DrawingOutputBoundary
    output_request: NativeOutputRequest


@dataclass(frozen=True, slots=True)
class PreparedDrawingDocumentPdfExport:
    pages: tuple[Any, ...]
    page_states: tuple[dict[str, Any], ...]
    boundary: DrawingOutputBoundary
    output_request: NativeOutputRequest


@dataclass(frozen=True, slots=True)
class PreparedDrawingPrintAll:
    pages: tuple[Any, ...]
    page_states: tuple[dict[str, Any], ...]
    boundary: DrawingOutputBoundary


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeDrawingError(message, error_code=code, repair=repair)


def _transaction_id(document: Any) -> int:
    reader = getattr(document, "getBookedTransactionID", None)
    return int(reader() or 0) if callable(reader) else 0


def _transaction_open(document: Any) -> bool:
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or _transaction_id(document) != 0
    )


def _gui_modified(document: Any) -> bool | None:
    try:
        import FreeCADGui as Gui

        gui_document = Gui.getDocument(str(document.Name))
        return None if gui_document is None else bool(gui_document.Modified)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _capture_boundary(document: Any) -> DrawingOutputBoundary:
    return DrawingOutputBoundary(
        objects=tuple(document.Objects),
        selection=_selection(document),
        undo_count=int(getattr(document, "UndoCount", 0) or 0),
        transaction_id=_transaction_id(document),
        gui_modified=_gui_modified(document),
    )


def _require_ready(context: NativeRuntimeContext) -> None:
    context.guard()
    document = context.document
    if _transaction_open(document):
        _error(
            "Finish or cancel the open transaction before producing Drawing output.",
            "NATIVE_DRAWING_OUTPUT_TRANSACTION_OPEN",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for the Drawing document recompute to finish before producing output.",
            "NATIVE_DRAWING_OUTPUT_RECOMPUTE_PENDING",
        )


def _require_usable(document: Any, page: Any) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(page)):
        _error(
            "The exact Drawing page is not usable at the current History position.",
            "NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def _exact_page_target(
    context: NativeRuntimeContext,
    raw: Any,
) -> tuple[NativeObjectRef, Any, dict[str, Any]]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error(
            "page must contain exact object_name and expected_state_sha256 fields.",
            "NATIVE_DRAWING_OUTPUT_PARAMETERS_INVALID",
        )
    reference = NativeObjectRef(context.document_uid, str(raw["object_name"]))
    page = resolve_object(
        context.document,
        reference,
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    expected = str(raw["expected_state_sha256"])
    if expected != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after the provider read its state.",
            "NATIVE_DRAWING_PAGE_STALE",
            repair={
                "page": {"object_name": reference.object_name},
                "current_state_sha256": state["state_sha256"],
            },
        )
    _require_usable(context.document, page)
    return reference, page, state


def _safe_file_name(page: Any, suffix: str) -> str:
    label = str(getattr(page, "Label", "") or getattr(page, "Name", "") or "Drawing")
    stem = _UNSAFE_FILE_NAME.sub("_", label).strip(" ._")[:180] or "Drawing"
    return f"{stem}{suffix}"


def _safe_document_file_name(document: Any, suffix: str) -> str:
    label = str(
        getattr(document, "Label", "")
        or getattr(document, "Name", "")
        or "Drawing"
    )
    stem = _UNSAFE_FILE_NAME.sub("_", label).strip(" ._")[:180] or "Drawing"
    return f"{stem}{suffix}"


def prepare_drawing_page_export(
    context: NativeRuntimeContext,
    *,
    page_target: Any,
    format_name: str,
) -> PreparedDrawingPageExport:
    _require_ready(context)
    clean_format = str(format_name or "")
    if clean_format not in _FORMATS:
        _error(
            "Drawing export format must be svg, dxf, or pdf.",
            "NATIVE_DRAWING_OUTPUT_FORMAT_UNAVAILABLE",
        )
    reference, page, state = _exact_page_target(context, page_target)
    suffix, name_filter = _FORMATS[clean_format]
    return PreparedDrawingPageExport(
        page_ref=reference,
        page=page,
        page_state=state,
        format_name=clean_format,
        boundary=_capture_boundary(context.document),
        output_request=NativeOutputRequest(
            purpose=f"drawing_page_{clean_format}_export",
            title=f"Export Drawing Page as {clean_format.upper()}",
            suggested_file_name=_safe_file_name(page, suffix),
            allowed_suffixes=(suffix,),
            name_filter=name_filter,
            maximum_bytes=MAX_DRAWING_OUTPUT_BYTES,
        ),
    )


def _current_pages(document: Any) -> tuple[Any, ...]:
    pages = []
    for obj in tuple(document.Objects):
        if not is_drawing_page(obj):
            continue
        checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
        if callable(checker) and not bool(checker(obj)):
            continue
        pages.append(obj)
    return tuple(pages)


def _require_current_pages(
    context: NativeRuntimeContext,
    *,
    no_pages_message: str,
    no_pages_code: str,
    limit_message: str,
    limit_code: str,
) -> tuple[Any, ...]:
    pages = _current_pages(context.document)
    if not pages:
        _error(no_pages_message, no_pages_code)
    if len(pages) > MAX_PRINTABLE_DRAWING_PAGES:
        _error(limit_message, limit_code)
    return pages


def prepare_drawing_document_pdf_export(
    context: NativeRuntimeContext,
) -> PreparedDrawingDocumentPdfExport:
    _require_ready(context)
    pages = _require_current_pages(
        context,
        no_pages_message="The current History position has no Drawing pages to export.",
        no_pages_code="NATIVE_DRAWING_OUTPUT_NO_PAGES",
        limit_message=(
            f"Document PDF export supports at most {MAX_PRINTABLE_DRAWING_PAGES} "
            "current-History pages."
        ),
        limit_code="NATIVE_DRAWING_OUTPUT_PAGE_LIMIT",
    )
    return PreparedDrawingDocumentPdfExport(
        pages=pages,
        page_states=tuple(drawing_page_state(page) for page in pages),
        boundary=_capture_boundary(context.document),
        output_request=NativeOutputRequest(
            purpose="drawing_document_pdf_export",
            title="Export Drawing Document as PDF",
            suggested_file_name=_safe_document_file_name(context.document, ".pdf"),
            allowed_suffixes=(".pdf",),
            name_filter="PDF drawing (*.pdf)",
            maximum_bytes=MAX_DRAWING_OUTPUT_BYTES,
        ),
    )


def prepare_drawing_print_all(
    context: NativeRuntimeContext,
) -> PreparedDrawingPrintAll:
    _require_ready(context)
    pages = _require_current_pages(
        context,
        no_pages_message="The current History position has no Drawing pages to print.",
        no_pages_code="NATIVE_DRAWING_PRINT_NO_PAGES",
        limit_message=(
            f"Print All supports at most {MAX_PRINTABLE_DRAWING_PAGES} "
            "current-History pages."
        ),
        limit_code="NATIVE_DRAWING_PRINT_PAGE_LIMIT",
    )
    return PreparedDrawingPrintAll(
        pages=pages,
        page_states=tuple(drawing_page_state(page) for page in pages),
        boundary=_capture_boundary(context.document),
    )


def _verify_boundary(
    context: NativeRuntimeContext,
    boundary: DrawingOutputBoundary,
) -> None:
    document = context.document
    if (
        tuple(document.Objects) != boundary.objects
        or _selection(document) != boundary.selection
        or int(getattr(document, "UndoCount", 0) or 0) != boundary.undo_count
        or _transaction_id(document) != boundary.transaction_id
        or _transaction_open(document)
        or _gui_modified(document) != boundary.gui_modified
    ):
        _error(
            "The Drawing document or human UI state changed during output generation.",
            "NATIVE_DRAWING_OUTPUT_SOURCE_STALE",
        )


def verify_drawing_page_export_source(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingPageExport,
) -> None:
    context.guard()
    page = resolve_object(
        context.document,
        prepared.page_ref,
        expected_types=("TechDraw::DrawPage",),
    )
    _require_usable(context.document, page)
    if page is not prepared.page or drawing_page_state(page) != prepared.page_state:
        _error(
            "The exact Drawing page changed before output publication.",
            "NATIVE_DRAWING_PAGE_STALE",
        )
    _verify_boundary(context, prepared.boundary)


def verify_drawing_print_all_source(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingPrintAll,
) -> None:
    context.guard()
    pages = _current_pages(context.document)
    if pages != prepared.pages or tuple(
        drawing_page_state(page) for page in pages
    ) != prepared.page_states:
        _error(
            "The exact set of current-History Drawing pages changed before printing.",
            "NATIVE_DRAWING_PRINT_SOURCE_STALE",
        )
    _verify_boundary(context, prepared.boundary)


def verify_drawing_document_pdf_source(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingDocumentPdfExport,
) -> None:
    context.guard()
    pages = _current_pages(context.document)
    if pages != prepared.pages or tuple(
        drawing_page_state(page) for page in pages
    ) != prepared.page_states:
        _error(
            "The exact set of current-History Drawing pages changed before PDF export.",
            "NATIVE_DRAWING_OUTPUT_SOURCE_STALE",
        )
    _verify_boundary(context, prepared.boundary)


def write_drawing_page(prepared: PreparedDrawingPageExport, path: str) -> None:
    try:
        import TechDrawGui

        TechDrawGui.export([prepared.page], path)
    except Exception as exc:
        raise NativeDrawingError(
            f"The TechDraw {prepared.format_name.upper()} writer failed.",
            error_code="NATIVE_DRAWING_OUTPUT_WRITE_FAILED",
        ) from exc


def write_drawing_document_pdf(
    prepared: PreparedDrawingDocumentPdfExport,
    path: str,
) -> None:
    try:
        import TechDrawGui

        TechDrawGui.exportAllDrawingPagesAsPdf(
            prepared.pages[0].Document,
            path,
        )
    except Exception as exc:
        raise NativeDrawingError(
            "The TechDraw document PDF writer failed.",
            error_code="NATIVE_DRAWING_OUTPUT_WRITE_FAILED",
        ) from exc


def validate_drawing_output(path: Path, format_name: str) -> None:
    try:
        size = int(path.stat().st_size)
    except OSError as exc:
        raise NativeDrawingError(
            "The generated Drawing output could not be inspected.",
            error_code="NATIVE_DRAWING_OUTPUT_INVALID",
        ) from exc
    if not 1 <= size <= MAX_DRAWING_OUTPUT_BYTES:
        _error(
            "The generated Drawing output is empty or exceeds its size bound.",
            "NATIVE_DRAWING_OUTPUT_INVALID",
        )
    try:
        if format_name == "svg":
            content = path.read_bytes()
            root = ElementTree.fromstring(content)
            if str(root.tag).rsplit("}", 1)[-1].casefold() != "svg":
                raise ValueError("missing SVG root")
        elif format_name == "pdf":
            with path.open("rb") as stream:
                header = stream.read(8)
                stream.seek(max(0, size - 4096))
                tail = stream.read(4096)
            if not header.startswith(b"%PDF-") or b"%%EOF" not in tail:
                raise ValueError("invalid PDF envelope")
        elif format_name == "dxf":
            with path.open("rb") as stream:
                head = stream.read(min(size, 65536)).upper()
                stream.seek(max(0, size - 65536))
                tail = stream.read(65536).upper()
            if b"SECTION" not in head or b"EOF" not in tail:
                raise ValueError("invalid DXF envelope")
        else:
            raise ValueError("unsupported format")
    except (ElementTree.ParseError, OSError, ValueError) as exc:
        raise NativeDrawingError(
            f"The generated {format_name.upper()} file failed bounded validation.",
            error_code="NATIVE_DRAWING_OUTPUT_INVALID",
        ) from exc


def drawing_output_source_summary(
    prepared: PreparedDrawingPageExport,
) -> dict[str, Any]:
    return {
        "page": {"object_name": prepared.page_ref.object_name},
        "state_sha256": prepared.page_state["state_sha256"],
        "format": prepared.format_name,
    }


def drawing_document_pdf_source_summary(
    prepared: PreparedDrawingDocumentPdfExport,
) -> dict[str, Any]:
    return {
        "page_count": len(prepared.pages),
        "pages": [
            {
                "object_name": str(page.Name),
                "state_sha256": state["state_sha256"],
            }
            for page, state in zip(prepared.pages, prepared.page_states, strict=True)
        ],
        "format": "pdf",
    }


def drawing_print_source_summary(
    prepared: PreparedDrawingPrintAll,
) -> dict[str, Any]:
    return {
        "page_count": len(prepared.pages),
        "pages": [
            {
                "object_name": str(page.Name),
                "state_sha256": state["state_sha256"],
            }
            for page, state in zip(prepared.pages, prepared.page_states)
        ],
    }
