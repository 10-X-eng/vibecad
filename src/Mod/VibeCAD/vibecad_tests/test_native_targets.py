# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeTargets as targets_module
from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    NativeTargetError,
    object_identity,
    read_current_selection,
    resolve_element,
    resolve_object,
)


class _Shape:
    def getElement(self, name: str):
        if name != "Face1":
            raise RuntimeError("missing")
        return "exact-face"


class _Object:
    def __init__(self, document, name="Box", type_id="PartDesign::Feature"):
        self.Document = document
        self.Name = name
        self.TypeId = type_id
        self.Shape = _Shape()

    def isDerivedFrom(self, expected: str) -> bool:
        return expected == "Part::Feature"


class _Document:
    Uid = "document-a"
    Name = "DocumentA"

    def __init__(self):
        self.obj = _Object(self)

    def getObject(self, name: str):
        return self.obj if name == self.obj.Name else None


def test_exact_object_reference_never_guesses_by_label() -> None:
    document = _Document()

    assert resolve_object(
        document,
        NativeObjectRef("document-a", "Box"),
        expected_types=("Part::Feature",),
    ) is document.obj
    assert object_identity(document.obj).summary() == {
        "document_uid": "document-a",
        "object_name": "Box",
        "type_id": "PartDesign::Feature",
    }

    with pytest.raises(NativeTargetError, match="internal object name"):
        resolve_object(document, NativeObjectRef("document-a", "Human Label"))


def test_wrong_document_and_type_fail_with_exact_target() -> None:
    document = _Document()

    with pytest.raises(NativeTargetError) as wrong_document:
        resolve_object(document, NativeObjectRef("document-b", "Box"))
    assert wrong_document.value.failure()["exact_target"] == {
        "document_uid": "document-b",
        "object_name": "Box",
    }

    with pytest.raises(NativeTargetError, match="accepts Mesh::Feature") as wrong_type:
        resolve_object(
            document,
            NativeObjectRef("document-a", "Box"),
            expected_types=("Mesh::Feature",),
        )
    assert wrong_type.value.failure()["actual_type"] == "PartDesign::Feature"
    assert wrong_type.value.failure()["accepted_types"] == ["Mesh::Feature"]


def test_subelement_reference_is_strict_and_resolved_on_live_shape() -> None:
    document = _Document()
    target = NativeElementRef(NativeObjectRef("document-a", "Box"), "Face1")

    assert resolve_element(document, target) == (document.obj, "exact-face")

    with pytest.raises(NativeTargetError, match="FaceN"):
        NativeElementRef(target.object, "Face0")
    with pytest.raises(NativeTargetError, match="no longer exists"):
        resolve_element(
            document,
            NativeElementRef(target.object, "Edge1"),
        )


def test_current_selection_is_exact_bounded_and_ordered(monkeypatch) -> None:
    document = _Document()
    entries = [
        SimpleNamespace(Object=document.obj, SubElementNames=["Face1", "Edge1"]),
        *[
            SimpleNamespace(
                Object=_Object(document, f"Object{index}"),
                SubElementNames=[],
            )
            for index in range(40)
        ],
    ]
    selection = SimpleNamespace(getSelectionEx=lambda name: entries)
    monkeypatch.setattr(targets_module, "MAX_SELECTION_OBJECTS", 3)

    result = read_current_selection(document, selection)

    assert result["selected_count"] == 41
    assert [item["object"]["object_name"] for item in result["items"]] == [
        "Box",
        "Object0",
        "Object1",
    ]
    assert result["items"][0]["subelements"] == ["Face1", "Edge1"]
    assert result["truncated"] is True
