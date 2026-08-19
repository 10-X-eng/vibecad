# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for Native aero.* tools."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import document_uid

_AERO_DIR = Path(__file__).resolve().parent.parent / "VibeCADAero"
if _AERO_DIR.is_dir() and str(_AERO_DIR) not in sys.path:
    sys.path.insert(0, str(_AERO_DIR))

_SOLVE_OPS = frozenset(
    {"analyze", "section", "vlm", "report", "propose_repairs", "apply_repairs"}
)


def native_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise RuntimeError("Aero wrapper returned a non-object result.")
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Aero operation failed."))
    return {key: value for key, value in result.items() if key != "ok"}


class NativeAeroRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def solve(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._run(str(arguments.get("operation") or ""), _SOLVE_OPS)

    def export(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._run("export_jsbsim", {"export_jsbsim"})

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._run("flight_card", {"flight_card"})

    def _run(self, operation: str, allowed: set[str] | frozenset[str]) -> dict[str, Any]:
        if operation not in allowed:
            raise ValueError(f"Unsupported Aero operation {operation!r}.")
        import VibeCADAero

        document = self._context.document
        native_revision = None
        try:
            native_revision = self._context.state.current_revision(document_uid(document))
        except Exception:
            native_revision = None
        if operation == "analyze":
            return native_payload(VibeCADAero.run_analyze(document, repair=False))
        if operation == "section":
            return native_payload(VibeCADAero.run_section(document))
        if operation == "vlm":
            return native_payload(VibeCADAero.run_vlm(document))
        if operation == "export_jsbsim":
            return native_payload(VibeCADAero.export_jsbsim(document))
        if operation == "report":
            return native_payload(VibeCADAero.write_last_report(document))
        if operation == "propose_repairs":
            return native_payload(
                VibeCADAero.propose_repairs(document, native_revision=native_revision)
            )
        if operation == "apply_repairs":
            return native_payload(
                VibeCADAero.apply_repairs(document, native_revision=native_revision)
            )
        return native_payload(VibeCADAero.flight_card(document))
