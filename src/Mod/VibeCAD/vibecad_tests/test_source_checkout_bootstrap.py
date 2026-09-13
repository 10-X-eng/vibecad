# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source-checkout import contracts for the VibeCAD test bootstrap."""

from __future__ import annotations

from importlib import import_module


def test_source_checkout_exposes_techdraw_python_helpers() -> None:
    helper = import_module("SectionViewPosition")

    assert callable(helper.apply_section_view_position)
