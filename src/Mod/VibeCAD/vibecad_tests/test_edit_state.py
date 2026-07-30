# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for native GUI edit-state resolution across workbenches."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from VibeCADCore import VibeCADService
from VibeCADEditState import active_edit_state
import VibeCADSession as session


class _AssemblyViewProvider:
    """Match AssemblyGui::ViewProviderAssembly's relevant Python surface."""


def _assembly_object() -> SimpleNamespace:
    document = SimpleNamespace(Name="Mechanism", Uid="doc-assembly", Objects=[])
    assembly = SimpleNamespace(
        Name="Assembly",
        Label="Crank Assembly",
        TypeId="Assembly::AssemblyObject",
        Document=document,
    )
    document.Objects.append(assembly)
    return assembly


def _install_gui(monkeypatch: pytest.MonkeyPatch, gui_document: object) -> None:
    gui = ModuleType("FreeCADGui")
    gui.ActiveDocument = gui_document
    gui.Control = SimpleNamespace(activeDialog=lambda: False)
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)


def test_native_edit_info_resolves_assembly_before_touching_view_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly = _assembly_object()
    view_provider = _AssemblyViewProvider()
    fallback_calls: list[str] = []

    class _GuiDocument:
        @property
        def getInEditInfo(self):
            return (assembly, "Assembly.", "", 0)

        def getInEdit(self):
            fallback_calls.append("getInEdit")
            return view_provider

    gui_document = _GuiDocument()
    _install_gui(monkeypatch, gui_document)

    state = active_edit_state(gui_document)
    assert state.active is True
    assert state.document_object is assembly
    assert state.subname == "Assembly."
    assert state.subelement == ""
    assert state.mode == 0
    assert fallback_calls == []

    service = object.__new__(VibeCADService)
    assert service.provider_edit_object_summary() == {
        "name": "Assembly",
        "label": "Crank Assembly",
        "type": "Assembly::AssemblyObject",
    }
    assert session._minimal_runtime_state(service) == {
        "edit_mode": True,
        "edit_object": {
            "name": "Assembly",
            "label": "Crank Assembly",
            "type": "Assembly::AssemblyObject",
        },
        "active_sketch": None,
    }
    assert service.task_panel_summary()["edit_object"]["name"] == "Assembly"


def test_opaque_edit_view_provider_is_active_but_never_masquerades_as_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view_provider = _AssemblyViewProvider()

    class _GuiDocument:
        def getInEdit(self):
            return view_provider

    gui_document = _GuiDocument()
    _install_gui(monkeypatch, gui_document)

    state = active_edit_state(gui_document)
    assert state.active is True
    assert state.document_object is None
    assert state.view_provider is view_provider

    service = object.__new__(VibeCADService)
    unresolved = service.provider_edit_object_summary()
    assert unresolved == {
        "name": "",
        "label": "",
        "type": "_AssemblyViewProvider",
        "resolved": False,
    }
    assert session._minimal_runtime_state(service) == {
        "edit_mode": True,
        "edit_object": unresolved,
        "active_sketch": None,
    }


def test_legacy_view_provider_object_property_remains_supported() -> None:
    assembly = _assembly_object()
    view_provider = SimpleNamespace(Object=assembly)
    gui_document = SimpleNamespace(getInEdit=lambda: view_provider)

    state = active_edit_state(gui_document)

    assert state.active is True
    assert state.document_object is assembly
    assert state.view_provider is view_provider


def test_malformed_native_edit_info_fails_closed_without_touching_object_fields() -> None:
    view_provider = _AssemblyViewProvider()

    class _GuiDocument:
        @property
        def getInEditInfo(self):
            return (view_provider, "Assembly.", "", 0)

        def getInEdit(self):
            return None

    state = active_edit_state(_GuiDocument())

    assert state.active is True
    assert state.document_object is None
    assert state.view_provider is None
    assert state.error == "getInEditInfo returned no document object"


def test_edit_binding_failures_are_reported_instead_of_escaping() -> None:
    class _GuiDocument:
        @property
        def getInEditInfo(self):
            raise RuntimeError("native info unavailable")

        @property
        def getInEdit(self):
            raise RuntimeError("native view unavailable")

    state = active_edit_state(_GuiDocument())

    assert state.active is False
    assert state.document_object is None
    assert state.error == (
        "getInEditInfo failed: native info unavailable; "
        "getInEdit failed: native view unavailable"
    )
