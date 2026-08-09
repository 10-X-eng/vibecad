# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider-safe failures for exact Native Model operations."""

from __future__ import annotations

from VibeCADNativeMutation import NativeMutationError


class NativeModelError(NativeMutationError):
    def __init__(self, message: str) -> None:
        super().__init__("NATIVE_MODEL_INVALID", message)
