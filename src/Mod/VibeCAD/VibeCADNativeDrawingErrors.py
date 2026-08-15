# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise provider-safe failures for Native Drawing capabilities."""

from __future__ import annotations

from typing import Any, Mapping


class NativeDrawingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "NATIVE_DRAWING_FAILED",
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code).strip() or "NATIVE_DRAWING_FAILED"
        self.repair = dict(repair or {})

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": str(self),
        }
        if self.repair:
            result["repair"] = self.repair
        return result
