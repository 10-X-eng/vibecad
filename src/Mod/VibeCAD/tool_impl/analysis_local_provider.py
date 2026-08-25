# SPDX-License-Identifier: LGPL-2.1-or-later

"""Domain-neutral local external-process provider for VibeCAD Analysis jobs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from VibeCADScriptedProcess import ExternalProcessStage, run_process_sequence


class LocalProcessProvider:
    """Execute an exact prepared command sequence on the local host.

    This provider deliberately owns execution location/mechanics only. It does
    not choose a solver, interpret engineering meaning, mutate a document, or
    decide whether completed output is qualified evidence.
    """

    provider_id = "local-process"

    def run_sequence(
        self,
        commands: Sequence[tuple[str, Sequence[str]]],
        *,
        working_directory: str | Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
        cancellation_check: Callable[[], bool],
        log_name: Callable[[int], str] | None = None,
        stage_started: Callable[[int, int], None] | None = None,
        maximum_log_bytes: int | None = None,
    ) -> tuple[ExternalProcessStage, ...]:
        return run_process_sequence(
            commands,
            working_directory=working_directory,
            environment=environment,
            timeout_seconds=timeout_seconds,
            cancellation_check=cancellation_check,
            log_name=log_name,
            stage_started=stage_started,
            maximum_log_bytes=maximum_log_bytes,
        )

    def describe_capabilities(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "location": "local",
            "reconnect_supported": False,
            "cancel_supported": True,
            "log_streaming": False,
            "execution_environment": "host",
            "portable_bundle_required": False,
        }
