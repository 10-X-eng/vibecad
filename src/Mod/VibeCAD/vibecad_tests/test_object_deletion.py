# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADObjectDeletion import ObjectDeletionError, delete_exact_object


class _Object:
    def __init__(self, name: str, type_id: str = "Part::Feature") -> None:
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Group: list[_Object] = []
        self.InList: list[_Object] = []
        self.Document = None


class _Document:
    Name = "DeleteTest"
    Uid = "delete-test-document"

    def __init__(self, objects: list[_Object]) -> None:
        self._objects = {obj.Name: obj for obj in objects}
        self.transaction_open = False
        for obj in objects:
            obj.Document = self

    def getObject(self, name: str):
        return self._objects.get(name)

    def openTransaction(self, _label: str) -> None:
        self.transaction_open = True

    def commitTransaction(self) -> None:
        self.transaction_open = False

    def abortTransaction(self) -> None:
        self.transaction_open = False

    def removeObject(self, name: str) -> None:
        self._objects.pop(name, None)


class _Service:
    def __init__(self, document: _Document) -> None:
        self.document = document

    def _active_document(self) -> _Document:
        return self.document


def _reference(name: str) -> dict[str, str]:
    return {
        "document_uid": _Document.Uid,
        "object_name": name,
    }


def test_delete_exact_object_removes_unowned_containment_closure() -> None:
    body = _Object("ImportedBody", "PartDesign::Body")
    result = _Object("OrphanedResult", "PartDesign::Feature")
    body.Group = [result]
    result.InList = [body]
    document = _Document([body, result])

    deleted = delete_exact_object(
        _Service(document),
        {"reference": _reference(body.Name), "reason": "Remove imported duplicate."},
    )

    assert deleted["ok"] is True
    assert deleted["cad_objects_removed"] == 2
    assert document.getObject(body.Name) is None
    assert document.getObject(result.Name) is None
    assert document.transaction_open is False


def test_delete_exact_object_rejects_managed_source_output() -> None:
    body = _Object("ManagedBody", "PartDesign::Body")
    body.VibeCADVibeScriptProgramId = "a" * 32
    body.VibeCADVibeScriptDomain = "partdesign"
    document = _Document([body])

    with pytest.raises(ObjectDeletionError) as caught:
        delete_exact_object(
            _Service(document),
            {"reference": _reference(body.Name), "reason": "Wrong deletion path."},
        )

    assert caught.value.code == "MANAGED_OBJECT_REQUIRES_PROGRAM_DELETE"
    assert caught.value.observed["source_id"] == "a" * 32
    assert document.getObject(body.Name) is body


def test_delete_exact_object_rejects_external_references() -> None:
    imported = _Object("ImportedMotor")
    bracket = _Object("Bracket")
    imported.InList = [bracket]
    document = _Document([imported, bracket])

    with pytest.raises(ObjectDeletionError) as caught:
        delete_exact_object(
            _Service(document),
            {"reference": _reference(imported.Name), "reason": "Remove motor."},
        )

    assert caught.value.code == "OBJECT_HAS_EXTERNAL_REFERENCES"
    assert document.getObject(imported.Name) is imported
