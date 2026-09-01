# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for the native Part Design Body renderer contract."""

from __future__ import annotations

from typing import Any

import VibeCADScriptedPublication as scripted_publication
import VibeCADVibeScriptDomainPublication as publication


class _Shape:
    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        return True


class _View:
    def __init__(self, visible: bool) -> None:
        self.Visibility = bool(visible)


class _MaterialObject:
    def __init__(self, material: Any) -> None:
        self.ViewObject = _View(True)
        self._shape_material = material
        self.material_assignments = 0

    @property
    def ShapeMaterial(self) -> Any:
        return self._shape_material

    @ShapeMaterial.setter
    def ShapeMaterial(self, value: Any) -> None:
        self.material_assignments += 1
        self._shape_material = value


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
        self.Label = name
        self.TypeId = type_id
        self.ViewObject = _View(visible)
        self.PropertiesList: list[str] = []
        self.Group: list[_Object] = []
        self.Tip: _Object | None = None
        self.InList: list[_Object] = []
        self.LinkedObject: Any = None
        self.LinkTransform = False
        self.Shape = _Shape()
        self._result_feature = result_feature
        self.hidden_properties: list[str] = []

    def addProperty(
        self,
        _property_type: str,
        name: str,
        _group: str,
        _description: str = "",
    ) -> None:
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
        setattr(self, name, "")

    def setEditorMode(self, name: str, mode: int) -> None:
        if mode == 2:
            self.hidden_properties.append(name)

    def isDerivedFrom(self, type_id: str) -> bool:
        if not self._result_feature:
            return False
        return type_id in {"PartDesign::Feature", "Part::Feature"}

    def newObject(self, type_id: str, name: str) -> "_Object":
        obj = _Object(
            name,
            type_id,
            visible=True,
            result_feature=type_id == "PartDesign::Feature",
        )
        self.Group.append(obj)
        return obj


class _Root(_Object):
    def __init__(self, document: "_Document") -> None:
        super().__init__("Program", "App::Part", visible=True)
        self.Document = document

    def addObject(self, obj: _Object) -> None:
        if self not in obj.InList:
            obj.InList.append(self)


class _Document:
    def __init__(self) -> None:
        self.Objects: list[_Object] = []

    def addObject(self, type_id: str, name: str) -> _Object:
        obj = _Object(name, type_id, visible=True)
        self.Objects.append(obj)
        return obj

    def getUniqueObjectName(self, name: str) -> str:
        used = {obj.Name for obj in self.Objects}
        if name not in used:
            return name
        index = 1
        while f"{name}{index:03d}" in used:
            index += 1
        return f"{name}{index:03d}"


def _tag(obj: _Object, *, role: str) -> None:
    scripted_publication.tag_object(
        obj,
        role=role,
        engine="vibescript:partdesign",
        model_id="blade-program",
        output_key="Blade",
        revision="accepted",
    )


def _document_with_body(
    *,
    body_visible: bool,
    publication_visible: bool,
    schema: str = "",
) -> tuple[
    _Document,
    _Root,
    _Object,
    _Object,
    _Object,
    _Object,
    _Object,
]:
    document = _Document()
    root = _Root(document)
    document.Objects.append(root)
    _tag(root, role=scripted_publication.ROLE_MODEL)

    body = _Object("BladeBody", "PartDesign::Body", visible=body_visible)
    _tag(body, role=scripted_publication.ROLE_IMPLEMENTATION)
    root.addObject(body)
    document.Objects.append(body)

    sketch = _Object("BladeSketch", "Sketcher::SketchObject", visible=True)
    earlier = _Object(
        "BladePad",
        "PartDesign::Feature",
        visible=True,
        result_feature=True,
    )
    tip = _Object(
        "BladeResult",
        "PartDesign::Feature",
        visible=True,
        result_feature=True,
    )
    body.Group = [sketch, earlier, tip]
    body.Tip = tip
    document.Objects.extend((sketch, earlier, tip))

    stable = _Object("Blade", "App::Link", visible=publication_visible)
    _tag(stable, role=scripted_publication.ROLE_PUBLICATION)
    stable.LinkedObject = (root, "LegacySource.")
    document.Objects.append(stable)

    if schema:
        body.addProperty(
            "App::PropertyString",
            publication.PROP_PARTDESIGN_HISTORY_PRESENTATION,
            "VibeCAD",
        )
        setattr(
            body,
            publication.PROP_PARTDESIGN_HISTORY_PRESENTATION,
            schema,
        )
    return document, root, body, sketch, earlier, tip, stable


def test_configure_visible_body_renders_only_tip_and_preserves_sketch() -> None:
    (
        _document,
        _root,
        body,
        sketch,
        earlier,
        tip,
        _stable,
    ) = _document_with_body(body_visible=False, publication_visible=False)

    changed = publication._configure_partdesign_history_presentation(
        body,
        visible=True,
    )

    assert changed is True
    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is True
    assert (
        getattr(body, publication.PROP_PARTDESIGN_HISTORY_PRESENTATION)
        == publication.PARTDESIGN_HISTORY_PRESENTATION_SCHEMA
    )


def test_copy_native_body_presentation_does_not_reassign_equal_material(
    monkeypatch,
) -> None:
    material = object()
    source = _MaterialObject(material)
    body = _MaterialObject(material)
    monkeypatch.setattr(
        publication,
        "_material_card_state",
        lambda value: {"identity": id(value)},
    )

    publication._copy_native_body_presentation(source, body)

    assert body.material_assignments == 0


def test_configure_hidden_body_hides_all_results_but_not_sketch() -> None:
    (
        _document,
        _root,
        body,
        sketch,
        earlier,
        tip,
        _stable,
    ) = _document_with_body(body_visible=True, publication_visible=False)

    publication._configure_partdesign_history_presentation(
        body,
        visible=False,
    )

    assert body.ViewObject.Visibility is False
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is False


def test_legacy_publication_visibility_moves_to_native_body() -> None:
    (
        document,
        root,
        body,
        sketch,
        earlier,
        tip,
        stable,
    ) = _document_with_body(
        body_visible=True,
        publication_visible=True,
        schema=publication._LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
    )
    earlier.ViewObject.Visibility = False
    tip.ViewObject.Visibility = False

    restored = publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is True
    assert stable.ViewObject.Visibility is False
    assert stable.LinkedObject == (root, "BladeBody.")
    assert stable.LinkTransform is True
    assert restored["migrated_bodies"] == ["BladeBody"]


def test_legacy_hidden_output_ignores_always_visible_container() -> None:
    (
        document,
        root,
        body,
        sketch,
        earlier,
        tip,
        stable,
    ) = _document_with_body(
        body_visible=True,
        publication_visible=False,
        schema=publication._LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
    )
    earlier.ViewObject.Visibility = False
    tip.ViewObject.Visibility = False

    publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is False
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is False
    assert stable.LinkedObject == (root, "BladeBody.")


def test_current_contract_repairs_duplicate_results_and_hides_publication() -> None:
    (
        document,
        root,
        body,
        sketch,
        earlier,
        tip,
        stable,
    ) = _document_with_body(
        body_visible=True,
        publication_visible=True,
        schema=publication.PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
    )

    restored = publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is True
    assert stable.ViewObject.Visibility is False
    assert stable.LinkedObject == (root, "BladeBody.")
    assert restored["migrated_bodies"] == []
    assert restored["changed_objects"] == ["Blade", "BladeBody"]


def test_current_contract_hides_private_publication_targets() -> None:
    (
        document,
        root,
        body,
        sketch,
        earlier,
        tip,
        stable,
    ) = _document_with_body(
        body_visible=True,
        publication_visible=False,
        schema=publication.PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
    )
    target = _Object(
        "BladeDetachedPublicationTarget",
        "Part::Feature",
        visible=True,
    )
    _tag(target, role=scripted_publication.ROLE_PUBLICATION_TARGET)
    root.addObject(target)
    document.Objects.append(target)
    earlier.ViewObject.Visibility = False
    stable.LinkedObject = (root, "BladeBody.")
    stable.LinkTransform = True

    restored = publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is True
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is True
    assert stable.ViewObject.Visibility is False
    assert target.ViewObject.Visibility is False
    assert target in document.Objects
    assert target in root.Document.Objects
    assert restored["changed_objects"] == [
        "BladeDetachedPublicationTarget",
    ]


def test_current_hidden_body_remains_hidden_across_restore() -> None:
    (
        document,
        _root,
        body,
        sketch,
        earlier,
        tip,
        stable,
    ) = _document_with_body(
        body_visible=False,
        publication_visible=False,
        schema=publication.PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
    )

    publication.restore_partdesign_history_presentation(document)

    assert body.ViewObject.Visibility is False
    assert sketch.ViewObject.Visibility is True
    assert earlier.ViewObject.Visibility is False
    assert tip.ViewObject.Visibility is False
    assert stable.ViewObject.Visibility is False


def test_publication_without_body_is_migrated_to_native_result() -> None:
    document = _Document()
    root = _Root(document)
    document.Objects.append(root)
    _tag(root, role=scripted_publication.ROLE_MODEL)
    stable = _Object("LegacyBlade", "App::Link", visible=True)
    stable.Label = "Utility Blade"
    stable.LinkedObject = (root, "LegacySource.")
    _tag(stable, role=scripted_publication.ROLE_PUBLICATION)
    document.Objects.append(stable)

    restored = publication.restore_partdesign_history_presentation(document)

    body = next(obj for obj in document.Objects if obj.TypeId == "PartDesign::Body")
    assert body.Label == "Utility Blade"
    assert body.ViewObject.Visibility is True
    assert body.Tip is not None
    assert body.Tip in body.Group
    assert body.Tip.Label == "Result"
    assert body.Tip.ViewObject.Visibility is True
    assert stable.ViewObject.Visibility is False
    assert stable.LinkedObject == (root, f"{body.Name}.")
    assert restored["migrated_bodies"] == [body.Name]


def test_headless_body_defers_presentation_marker() -> None:
    (
        _document,
        _root,
        body,
        _sketch,
        earlier,
        tip,
        _stable,
    ) = _document_with_body(body_visible=False, publication_visible=True)
    body.ViewObject = None
    earlier.ViewObject = None
    tip.ViewObject = None

    assert (
        publication._configure_partdesign_history_presentation(body)
        is False
    )
    assert (
        publication.PROP_PARTDESIGN_HISTORY_PRESENTATION
        not in body.PropertiesList
    )
