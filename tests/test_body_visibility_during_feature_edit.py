# SPDX-License-Identifier: LGPL-2.1-or-later
"""Source contract: Body eye still updates the Tip during a native feature task."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BODY_CPP = ROOT / "src" / "Mod" / "PartDesign" / "Gui" / "ViewProviderBody.cpp"
TREE_CPP = ROOT / "src" / "Gui" / "Tree.cpp"


def _method(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_show_applies_result_visibility_before_full_normalize() -> None:
    show = _method(
        BODY_CPP.read_text(encoding="utf-8"),
        "void ViewProviderBody::show()",
        "void ViewProviderBody::hide()",
    )
    assert "setResultVisibility(Visibility.getValue())" in show
    assert show.find("setResultVisibility") < show.find("normalizeResultPresentation")


def test_hide_applies_result_visibility_before_full_normalize() -> None:
    hide = _method(
        BODY_CPP.read_text(encoding="utf-8"),
        "void ViewProviderBody::hide()",
        "bool ViewProviderBody::isShow()",
    )
    assert "setResultVisibility(Visibility.getValue())" in hide
    assert hide.find("setResultVisibility") < hide.find("normalizeResultPresentation")


def test_body_eye_property_change_updates_tip() -> None:
    changed = _method(
        BODY_CPP.read_text(encoding="utf-8"),
        "void ViewProviderBody::onChangedObject",
        "void ViewProviderBody::normalizeResultPresentation",
    )
    assert "changedObj == body" in changed
    assert "setResultVisibility(Visibility.getValue())" in changed


def test_browser_proxy_eye_routes_through_show_hide() -> None:
    setter = _method(
        TREE_CPP.read_text(encoding="utf-8"),
        "void TreeWidget::setObjectItemVisibility",
        "TreeWidget::resolveModelBrowserVisibilityTarget",
    )
    assert "isBrowserProxy()" in setter
    assert "vp->show()" in setter
    assert "vp->hide()" in setter
