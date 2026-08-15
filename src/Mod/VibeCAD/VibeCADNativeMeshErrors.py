# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise provider-safe failures for Native Mesh capabilities."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationError


class NativeMeshError(NativeMutationError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "NATIVE_MESH_OPERATION_FAILED",
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(error_code), str(message).strip())
        self.repair = dict(repair) if repair is not None else None

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": str(self),
        }
        if self.repair is not None:
            result["repair"] = dict(self.repair)
        return result
