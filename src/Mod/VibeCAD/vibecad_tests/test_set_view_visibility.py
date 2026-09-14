# SPDX-License-Identifier: LGPL-2.1-or-later

"""Visibility preconditions for the GUI view command."""

from __future__ import annotations

from types import SimpleNamespace

from tool_impl.service import core_set_view


class _Object:
    def __init__(
        self,
        name: str,
        type_id: str,
        visible: bool,
        parent: _Object | None = None,
    ) -> None:
        self.Name = name
        self.TypeId = type_id
        self.ViewObject = SimpleNamespace(Visibility=visible)
        self.parent = parent

    def getParentGeoFeatureGroup(self):
        return self.parent

    def getParentGroup(self):
        return None


class _Document:
    def __init__(self, *objects: _Object) -> None:
        self.Objects = list(objects)

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


def test_show_child_of_hidden_part_reports_blocking_parent_without_mutation() -> None:
    part = _Object("HiddenPart", "App::Part", False)
    child = _Object("Child", "Part::Feature", False, part)
    document = _Document(part, child)

    result = core_set_view._resolve_visibility(document, [child.Name], [])

    assert result["ok"] is False
    assert result["hidden_ancestors"] == {"Child": ["HiddenPart"]}
    assert part.ViewObject.Visibility is False
    assert child.ViewObject.Visibility is False


def test_show_child_and_hidden_part_together_is_allowed() -> None:
    part = _Object("HiddenPart", "App::Part", False)
    child = _Object("Child", "Part::Feature", False, part)

    result = core_set_view._resolve_visibility(
        _Document(part, child), [child.Name, part.Name], []
    )

    assert result["ok"] is True
    applied = core_set_view._apply_visibility(result["changes"])
    assert applied["shown"] == ["Child", "HiddenPart"]
    assert part.ViewObject.Visibility is True
    assert child.ViewObject.Visibility is True


def test_hidden_body_does_not_block_independent_child_visibility() -> None:
    body = _Object("HiddenBody", "PartDesign::Body", False)
    sketch = _Object("Sketch", "Sketcher::SketchObject", False, body)

    result = core_set_view._resolve_visibility(_Document(body, sketch), ["Sketch"], [])

    assert result["ok"] is True
    assert body.ViewObject.Visibility is False


def test_hidden_part_above_body_still_blocks_child_visibility() -> None:
    part = _Object("HiddenPart", "App::Part", False)
    body = _Object("Body", "PartDesign::Body", False, part)
    sketch = _Object("Sketch", "Sketcher::SketchObject", False, body)

    result = core_set_view._resolve_visibility(
        _Document(part, body, sketch), [sketch.Name], []
    )

    assert result["ok"] is False
    assert result["hidden_ancestors"] == {"Sketch": ["HiddenPart"]}
