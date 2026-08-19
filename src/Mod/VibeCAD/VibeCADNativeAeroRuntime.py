# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Native aero.solve tools."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext

_AERO_DIR = Path(__file__).resolve().parent.parent / "VibeCADAero"
if _AERO_DIR.is_dir() and str(_AERO_DIR) not in sys.path:
    sys.path.insert(0, str(_AERO_DIR))


_OPERATIONS = frozenset(
    {
        "analyze",
        "section",
        "vlm",
        "export_jsbsim",
        "report",
        "propose_repairs",
        "apply_repairs",
        "flight_card",
    }
)


class NativeAeroRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def solve(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation = str(arguments.get("operation") or "")
        if operation not in _OPERATIONS:
            raise ValueError(f"Unsupported aero.solve operation {operation!r}.")
        import VibeCADAero

        document = self._context.document
        if operation == "analyze":
            return VibeCADAero.run_analyze(document, repair=False)
        if operation == "section":
            return VibeCADAero.run_section(document)
        if operation == "vlm":
            return VibeCADAero.run_vlm(document)
        if operation == "export_jsbsim":
            return VibeCADAero.export_jsbsim(document)
        if operation == "report":
            return VibeCADAero.write_last_report(document)
        if operation == "propose_repairs":
            return VibeCADAero.propose_repairs(document)
        if operation == "apply_repairs":
            return VibeCADAero.apply_repairs(document)
        return VibeCADAero.flight_card(document)
