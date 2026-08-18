# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Aero ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADAeroContext import document_aero_summary


def build_aero_snapshot(document: Any) -> dict[str, Any]:
    return {
        "kind": "aero",
        "aero": document_aero_summary(document),
    }
