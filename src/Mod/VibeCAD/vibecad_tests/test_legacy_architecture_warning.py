# SPDX-License-Identifier: LGPL-2.1-or-later

from types import SimpleNamespace

from VibeCADLegacyArchitecture import (
    find_legacy_architecture_objects,
    is_legacy_architecture_object,
    warning_text,
)


def _object(*, type_id="Part::Feature", properties=(), proxy=None):
    return SimpleNamespace(TypeId=type_id, PropertiesList=list(properties), Proxy=proxy)


def test_detector_accepts_removed_native_and_python_signatures():
    assert is_legacy_architecture_object(_object(type_id="TechDraw::DrawViewArch"))
    assert is_legacy_architecture_object(_object(properties=("IfcType",)))
    assert is_legacy_architecture_object(
        _object(proxy=SimpleNamespace(Type="Wall"))
    )


def test_detector_ignores_general_draft_and_mechanical_objects():
    document = SimpleNamespace(
        Objects=[
            _object(type_id="PartDesign::Feature"),
            _object(properties=("PredefinedType",)),
            _object(proxy=SimpleNamespace(Type="Wire")),
            _object(type_id="TechDraw::DrawViewDraft"),
        ]
    )
    assert find_legacy_architecture_objects(document) == []


def test_warning_is_explicit_and_migration_oriented():
    message = warning_text(2)
    assert "has been removed" in message
    assert "unsupported" in message
    assert "without saving" in message
    assert "STEP" in message
