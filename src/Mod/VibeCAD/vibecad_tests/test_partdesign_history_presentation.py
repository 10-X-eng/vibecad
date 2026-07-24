# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for independently rendered Part Design history."""

from __future__ import annotations

from types import SimpleNamespace

import VibeCADScriptedPublication as scripted_publication
import VibeCADVibeScriptDomainPublication as publication


class _View:
    def __init__(self, visible: bool) -> None:
        self.Visibility = visible


class _BodyViewThatShowsTip:
    def __init__(self, visible: bool, tip: "_Object") -> None:
        self._visible = visible
        self._tip = tip

    @property
    def Visibility(self) -> bool:
        return self._visible

    @Visibility.setter
    def Visibility(self, visible: bool) -> None:
        was_visible = self._visible
        self._visible = bool(visible)
        if self._visible and not was_visible:
            self._tip.ViewObject.Visibility = True


class _Object:
    def __init__(
        self,
        name: str,
        type_id: str,
        *,
        visible: bool,
        result_feature: bool = False,
    ) -> None:
        self.Name = name
        self.TypeId = type_id
        self.ViewObject = _View(visible)
        self.PropertiesList: list[str] = []
        self._result_feature = result_feature
        self.hidden_properties: list[str] = []

    def addProperty(
        self,
        _property_type: str,
        name: str,
        _group: str,
        _description: str = "",
    ) -> None:
        self.PropertiesList.append(name)
        setattr(self, name, "")

    def setEditorMode(self, name: str, mode: int) -> None:
        if mode == 2:
            self.hidden_properties.append(name)

    def isDerivedFrom(self, type_id: str) -> bool:
        if not self._result_feature:
            return False
        if self.TypeId.startswith("PartDesign::"):
            return type_id in {"PartDesign::Feature", "Part::Feature"}
        return self.TypeId.startswith("Part::") and type_id == "Part::Feature"


def _tag(obj: _Object, *, role: str) -> None:
    setattr(obj, scripted_publication.PROP_ROLE, role)
    setattr(obj, scripted_publication.PROP_ENGINE, "vibescript:partdesign")
    setattr(obj, scripted_publication.PROP_MODEL_ID, "blade-program")
    setattr(obj, scripted_publication.PROP_OUTPUT_KEY, "Blade")


def _legacy_document(
    *,
    body_visible: bool,
    publication_visible: bool,
) -> tuple[SimpleNamespace, _Object, _Object, _Object, _Object]:
    body = _Object(
        "BladeBody",
        "PartDesign::Body",
        visible=body_visible,
    )
    sketch = _Object(
        "BladeSketch",
        "Sketcher::SketchObject",
        visible=True,
    )
    result = _Object(
        "BladeResult",
        "PartDesign::Pocket",
        visible=True,
        result_feature=True,
    )
    body.Group = [sketch, result]
    stable = _Object(
        "Blade",
        "App::Link",
        visible=publication_visible,
    )
    _tag(body, role=scripted_publication.ROLE_IMPLEMENTATION)
    _tag(stable, role=scripted_publication.ROLE_PUBLICATION)
    return SimpleNamespace(Objects=[body, sketch, result, stable]), body, sketch, result, stable


def test_legacy_hidden_body_becomes_a_visible_history_container() -> None:
    document, body, sketch, result, stable = _legacy_document(
        body_visible=False,
        publication_visible=False,
    )

    restored = publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert result.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is False
    assert restored["migrated_bodies"] == ["BladeBody"]
    assert publication.PROP_PARTDESIGN_HISTORY_PRESENTATION in body.PropertiesList
    assert (
        getattr(body, publication.PROP_PARTDESIGN_HISTORY_PRESENTATION)
        == publication.PARTDESIGN_HISTORY_PRESENTATION_SCHEMA
    )


def test_migration_preserves_the_previously_visible_body_output() -> None:
    document, body, _sketch, result, stable = _legacy_document(
        body_visible=True,
        publication_visible=False,
    )

    publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert result.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is True


def test_presentation_hides_retained_part_results_in_the_body() -> None:
    document, body, sketch, result, stable = _legacy_document(
        body_visible=True,
        publication_visible=True,
    )
    retained_part_result = _Object(
        "BooleanResult",
        "Part::Feature",
        visible=True,
        result_feature=True,
    )
    body.Group.append(retained_part_result)
    document.Objects.append(retained_part_result)

    publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert result.ViewObject.Visibility is False
    assert retained_part_result.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is True


def test_current_presentation_repairs_container_and_hides_native_solid() -> None:
    document, body, sketch, result, stable = _legacy_document(
        body_visible=True,
        publication_visible=False,
    )
    publication.restore_partdesign_history_presentation(document)
    result.ViewObject.Visibility = True
    sketch.ViewObject.Visibility = False
    body.ViewObject.Visibility = False

    restored = publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is False
    assert result.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is True
    assert restored["migrated_bodies"] == []
    assert restored["changed_objects"] == ["BladeBody", "BladeResult"]


def test_body_repair_undoes_freecad_automatically_showing_the_tip() -> None:
    document, body, _sketch, result, stable = _legacy_document(
        body_visible=True,
        publication_visible=True,
    )
    publication.restore_partdesign_history_presentation(document)
    result.ViewObject.Visibility = False
    body.ViewObject = _BodyViewThatShowsTip(False, result)

    publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert result.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is True


def test_headless_publication_defers_presentation_marker_until_gui_restore() -> None:
    _document, body, _sketch, result, _stable = _legacy_document(
        body_visible=False,
        publication_visible=True,
    )
    body.ViewObject = None
    result.ViewObject = None

    assert publication._configure_partdesign_history_presentation(body) is False
    assert publication.PROP_PARTDESIGN_HISTORY_PRESENTATION not in body.PropertiesList
