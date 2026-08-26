# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
from types import SimpleNamespace


class _Page:
    TypeId = "TechDraw::DrawPage"

    def __init__(self, name: str, label: str) -> None:
        self.Name = name
        self.Label = label

    def isDerivedFrom(self, expected: str) -> bool:
        return expected == self.TypeId


class _Document:
    Name = "DrawingDocument"

    def __init__(self, *pages: _Page) -> None:
        self.Objects = list(pages)


def test_named_drawing_page_capture_uses_exact_name_without_being_active(
    monkeypatch,
) -> None:
    from tool_impl.service import core_capture_view_screenshot as capture

    first = _Page("Page", "Fabrication")
    second = _Page("Page002", "Inspection")
    document = _Document(first, second)
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(ActiveDocument=document),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(ActiveDocument=None))
    monkeypatch.setattr(capture, "_active_drawing_page", lambda _document: None)
    observed = []
    monkeypatch.setattr(
        capture,
        "_capture_drawing_page",
        lambda service, target_document, page, *, requested: observed.append(
            (service, target_document, page, requested)
        )
        or {"ok": True, "captured": True, "page": page.Name},
    )
    service = SimpleNamespace()

    result = capture.run(
        service,
        camera={"mode": "front"},
        frame="objects",
        object_names=["Body"],
        page_name="Page002",
    )

    assert result == {"ok": True, "captured": True, "page": "Page002"}
    assert observed[0][:3] == (service, document, second)
    assert observed[0][3]["page_name"] == "Page002"


def test_active_drawing_page_capture_normalizes_3d_frame_requests(monkeypatch) -> None:
    from tool_impl.service import core_capture_view_screenshot as capture

    page = _Page("Page", "Fabrication")
    document = _Document(page)
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(ActiveDocument=document),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(ActiveDocument=None))
    monkeypatch.setattr(capture, "_active_drawing_page", lambda _document: page)
    monkeypatch.setattr(
        capture,
        "_capture_drawing_page",
        lambda _service, _document, target, *, requested: {
            "ok": True,
            "captured": True,
            "page": target.Name,
            "requested": requested,
        },
    )

    result = capture.run(
        SimpleNamespace(),
        frame="selection",
        camera={"mode": "isometric"},
    )

    assert result["ok"] is True
    assert result["page"] == "Page"
    assert result["requested"]["frame"] == "selection"


def test_missing_named_drawing_page_returns_bounded_candidates(monkeypatch) -> None:
    from tool_impl.service import core_capture_view_screenshot as capture

    document = _Document(_Page("Page", "Fabrication"))
    service = SimpleNamespace(_last_view_screenshot={})
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(ActiveDocument=document),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(ActiveDocument=None))

    result = capture.run(service, page_name="Missing")

    assert result["ok"] is False
    assert result["failure_code"] == "DRAWING_PAGE_TARGET_INVALID"
    assert result["candidates"] == [
        {"object_name": "Page", "label": "Fabrication"}
    ]
