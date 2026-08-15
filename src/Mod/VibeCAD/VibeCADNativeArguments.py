# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict argument selection shared by Native capability runtimes."""

from __future__ import annotations

from typing import Any, Mapping


class NativeArgumentError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ARGUMENTS_INVALID",
            "message": str(self),
        }


def strict_variant_arguments(
    arguments: Mapping[str, Any],
    variants: Mapping[str, frozenset[str]],
) -> tuple[str, dict[str, Any]]:
    """Select one declared operation and reject every undeclared field."""

    if not isinstance(arguments, Mapping):
        raise NativeArgumentError("Native capability arguments must be an object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "").strip()
    expected = variants.get(operation)
    if expected is None:
        raise NativeArgumentError("Native capability operation is unavailable.")
    if set(values) != expected:
        raise NativeArgumentError(
            "Native capability arguments do not match the selected operation."
        )
    return operation, values
