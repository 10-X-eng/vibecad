# SPDX-License-Identifier: LGPL-2.1-or-later
"""Source contract: hiding a Component also hides its owned Body result."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY_CPP = ROOT / "src" / "Mod" / "PartDesign" / "Gui" / "ViewProviderBody.cpp"


def _method(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_result_visibility_requires_an_enclosing_component_to_be_visible() -> None:
    source = BODY_CPP.read_text(encoding="utf-8")
    setter = _method(
        source,
        "void ViewProviderBody::setResultVisibility(bool visible)",
        "bool ViewProviderBody::",
    )
    # Fall back to next method if the file ends the function differently.
    if "enclosingComponentIsVisible" not in setter:
        setter = source.split("void ViewProviderBody::setResultVisibility(bool visible)", 1)[1][
            :2500
        ]
    assert "enclosingComponentIsVisible" in source
    assert "PartDesign::Component" in source


def test_component_visibility_changes_update_owned_bodies() -> None:
    changed = _method(
        BODY_CPP.read_text(encoding="utf-8"),
        "void ViewProviderBody::onChangedObject",
        "void ViewProviderBody::normalizeResultPresentation",
    )
    assert "enclosingComponent" in changed
    assert "adjustingComponentVisibility" in changed


def test_body_does_not_remount_when_its_component_is_hidden() -> None:
    source = BODY_CPP.read_text(encoding="utf-8")
    show = _method(
        source,
        "void ViewProviderBody::show()",
        "void ViewProviderBody::hide()",
    )
    hide = _method(
        source,
        "void ViewProviderBody::hide()",
        "bool ViewProviderBody::isShow()",
    )
    assert "enclosingComponentIsVisible" in show
    assert "enclosingComponentIsVisible" in hide
    assert show.find("enclosingComponentIsVisible") < show.find("Gui::ViewProvider::show()")
