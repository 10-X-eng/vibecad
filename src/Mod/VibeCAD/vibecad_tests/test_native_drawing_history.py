# SPDX-License-Identifier: LGPL-2.1-or-later

"""History contracts for whole-object Native Drawing sources."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys

import pytest

import VibeCADNativeDrawingView as drawing_view
from VibeCADNativeDrawingHistory import is_drawing_source_history_usable


class _Document:
    def __init__(self, objects: tuple[object, ...], usable: tuple[object, ...]) -> None:
        self.Objects = objects
        self._usable = usable

    def getObject(self, name: str) -> object | None:
        return next(
            (obj for obj in self.Objects if getattr(obj, "Name", None) == name),
            None,
        )

    def isObjectUsableAtCurrentTimelinePosition(self, obj: object) -> bool:
        return any(obj is candidate for candidate in self._usable)


def _object(name: str, type_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        Name=name,
        TypeId=type_id,
        ViewObject=SimpleNamespace(Visibility=True),
        PropertiesList=["Shape"],
        getParentGroup=lambda: None,
        getParentGeoFeatureGroup=lambda: None,
    )


def _install_part_gui(
    monkeypatch: pytest.MonkeyPatch,
    resolved: dict[int, object | None],
) -> None:
    module = ModuleType("PartGui")
    module.resolveModelingObject = lambda obj: resolved.get(id(obj), obj)
    monkeypatch.setitem(sys.modules, "PartGui", module)


def test_ordinary_drawing_source_keeps_the_document_history_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _object("Bracket", "Part::Feature")
    document = _Document((source,), ())
    _install_part_gui(monkeypatch, {})

    assert not is_drawing_source_history_usable(document, source)

    document._usable = (source,)
    assert is_drawing_source_history_usable(document, source)


def test_body_is_validated_through_its_active_modeling_state_without_replacing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _object("Body042", "PartDesign::Body")
    active_state = _object("Body042_State", "PartDesign::DesignBodyState")
    document = _Document((body, active_state), (active_state,))
    _install_part_gui(monkeypatch, {id(body): active_state})

    assert not document.isObjectUsableAtCurrentTimelinePosition(body)
    assert is_drawing_source_history_usable(document, body)


@pytest.mark.parametrize("resolved", [None, object()])
def test_body_without_one_live_active_modeling_state_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    resolved: object | None,
) -> None:
    body = _object("Body043", "PartDesign::Body")
    document = _Document((body,), ())
    _install_part_gui(monkeypatch, {id(body): resolved})

    assert not is_drawing_source_history_usable(document, body)


def test_source_from_another_document_is_rejected_before_history_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _object("ForeignBody", "PartDesign::Body")
    document = _Document((), (source,))
    _install_part_gui(monkeypatch, {id(source): source})

    assert not is_drawing_source_history_usable(document, source)


def test_standard_view_keeps_the_selected_body_as_its_techdraw_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(
        Name="Page",
        TypeId="TechDraw::DrawPage",
        Views=(),
    )
    body = _object("Body042", "PartDesign::Body")
    active_state = _object("Body042_State", "PartDesign::DesignBodyState")
    document = _Document((page, body, active_state), (page, active_state))
    document.Uid = "drawing-history-test"
    _install_part_gui(monkeypatch, {id(body): active_state})
    monkeypatch.setattr(
        drawing_view,
        "resolve_object",
        lambda current, target, **_kwargs: current.getObject(target["object_name"]),
    )
    monkeypatch.setattr(
        drawing_view,
        "drawing_page_state",
        lambda _page: {"state_sha256": "page-state"},
    )
    monkeypatch.setattr(
        drawing_view,
        "drawing_source_state",
        lambda source: {
            "object_name": source.Name,
            "state_sha256": "body-state",
        },
    )
    monkeypatch.setattr(drawing_view, "_current_selection", lambda _document: {})

    prepared = drawing_view.prepare_standard_view_create(
        document,
        values={
            "label": "Front",
            "page": {
                "object_name": page.Name,
                "expected_state_sha256": "page-state",
            },
            "sources": [
                {
                    "object_name": body.Name,
                    "expected_state_sha256": "body-state",
                }
            ],
            "orientation": "front",
            "position": {"x_mm": 100.0, "y_mm": 100.0},
            "scale": "page",
            "line_style": "visible",
        },
        validate_position=False,
    )

    assert prepared.sources == (body,)


def test_standard_view_rejects_a_source_hidden_after_provider_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(
        Name="Page",
        TypeId="TechDraw::DrawPage",
        Views=(),
    )
    body = _object("Body042", "PartDesign::Body")
    body.ViewObject.Visibility = False
    active_state = _object("Body042_State", "PartDesign::DesignBodyState")
    document = _Document((page, body, active_state), (page, active_state))
    document.Uid = "drawing-hidden-source-test"
    _install_part_gui(monkeypatch, {id(body): active_state})
    monkeypatch.setattr(
        drawing_view,
        "resolve_object",
        lambda current, target, **_kwargs: current.getObject(target["object_name"]),
    )
    monkeypatch.setattr(
        drawing_view,
        "drawing_page_state",
        lambda _page: {"state_sha256": "page-state"},
    )
    monkeypatch.setattr(
        drawing_view,
        "drawing_source_state",
        lambda source: {
            "object_name": source.Name,
            "state_sha256": "body-state",
        },
    )
    monkeypatch.setattr(drawing_view, "_current_selection", lambda _document: {})

    with pytest.raises(drawing_view.NativeDrawingError) as failure:
        drawing_view.prepare_standard_view_create(
            document,
            values={
                "label": "Front",
                "page": {
                    "object_name": page.Name,
                    "expected_state_sha256": "page-state",
                },
                "sources": [
                    {
                        "object_name": body.Name,
                        "expected_state_sha256": "body-state",
                    }
                ],
                "orientation": "front",
                "position": {"x_mm": 100.0, "y_mm": 100.0},
                "scale": "page",
                "line_style": "visible",
            },
            validate_position=False,
        )

    assert failure.value.error_code == "NATIVE_DRAWING_VIEW_SOURCE_HIDDEN"
