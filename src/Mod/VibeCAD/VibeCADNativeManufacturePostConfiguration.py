# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared, path-free resolution of the post configuration for one CAM Job."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from VibeCADNativeManufactureErrors import NativeManufactureError


MAX_MACHINE_CONFIG_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ResolvedPostConfiguration:
    use_machine_flow: bool
    machine_name: str
    postprocessor_name: str
    configured_output: str
    machine_data: bytes | None = field(repr=False)


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def resolve_post_configuration(job: Any) -> ResolvedPostConfiguration:
    """Resolve the same machine, processor, and output settings used by posting."""

    try:
        import Path as PathModule
        from Machine.models.machine import MachineFactory
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM postprocessing configuration modules are unavailable.",
            error_code="NATIVE_MANUFACTURE_POST_UNAVAILABLE",
        ) from exc

    machine_name = str(getattr(job, "Machine", "") or "").strip()
    if len(machine_name) > 160 or any(ord(value) < 32 for value in machine_name):
        _error(
            "The exact Job's configured machine name is invalid.",
            "NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
        )
    machine_data = None
    if machine_name:
        try:
            machine = MachineFactory.get_machine(machine_name)
            postprocessor_name = str(machine.postprocessor_file_name or "").strip()
            machine_data = json.dumps(
                machine.to_dict(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except Exception as exc:
            raise NativeManufactureError(
                "The exact Job's configured machine could not be frozen.",
                error_code="NATIVE_MANUFACTURE_POST_MACHINE_INVALID",
            ) from exc
        if not postprocessor_name:
            _error(
                "The exact Job's machine does not configure a postprocessor.",
                "NATIVE_MANUFACTURE_POST_PROCESSOR_MISSING",
            )
        if len(machine_data) > MAX_MACHINE_CONFIG_BYTES:
            _error(
                "The configured machine exceeds the 16 MiB safety bound.",
                "NATIVE_MANUFACTURE_POST_LIMIT",
            )
        use_machine_flow = True
    else:
        postprocessor_name = str(getattr(job, "PostProcessor", "") or "").strip()
        if not postprocessor_name:
            postprocessor_name = str(
                PathModule.Preferences.defaultPostProcessor() or ""
            ).strip()
        use_machine_flow = False
    if (
        not postprocessor_name
        or not postprocessor_name.isascii()
        or not postprocessor_name[0].isalpha()
        or any(not (value.isalnum() or value == "_") for value in postprocessor_name)
    ):
        _error(
            "The exact Job has no valid configured postprocessor.",
            "NATIVE_MANUFACTURE_POST_PROCESSOR_MISSING",
        )
    configured_output = str(
        getattr(job, "PostProcessorOutputFile", "")
        or PathModule.Preferences.defaultOutputFile()
        or ""
    )
    if len(configured_output) > 4096 or any(
        ord(value) < 32 for value in configured_output
    ):
        _error(
            "The configured CAM output naming pattern is invalid.",
            "NATIVE_MANUFACTURE_POST_OUTPUT_INVALID",
        )
    return ResolvedPostConfiguration(
        use_machine_flow=use_machine_flow,
        machine_name=machine_name,
        postprocessor_name=postprocessor_name,
        configured_output=configured_output,
        machine_data=machine_data,
    )
