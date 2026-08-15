# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact contract for preferred FreeCAD document labels."""

from __future__ import annotations


def matches_preferred_document_label(actual: str, preferred: str) -> bool:
    """Accept a preferred label or FreeCAD's deterministic unique-label form."""

    if actual == preferred:
        return True
    base = preferred.rstrip("0123456789")
    suffix = actual[len(base) :] if actual.startswith(base) else ""
    return len(suffix) >= 3 and suffix.isdigit()
