# SPDX-License-Identifier: LGPL-2.1-or-later

"""Record the exact label assigned by the document host."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


def assign_prepared_label(obj: Any, prepared: Any) -> Any:
    """Assign a requested label and return preparation with its exact result."""

    obj.Label = str(prepared.label)
    assigned = str(obj.Label or "").strip()
    if not assigned:
        raise NativeAnalyzeError("The document did not assign a usable object label.")
    return replace(prepared, label=assigned)
