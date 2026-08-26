# SPDX-License-Identifier: LGPL-2.1-or-later
"""Local subprocess compute provider for CFD jobs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

from AeroCFDContracts import ExecutionReceipt, JobState, PreparedJob


class LocalComputeProvider:
    name = "local"

    def __init__(self, *, timeout_s: float | None = None) -> None:
        self.timeout_s = timeout_s

    def execute(self, job: PreparedJob) -> ExecutionReceipt:
        workdir = Path(job.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        stdout_path = workdir / "stdout.log"
        stderr_path = workdir / "stderr.log"
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in job.environment.items()})
        started = time.monotonic()
        try:
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                proc = subprocess.run(
                    list(job.command),
                    cwd=str(workdir),
                    env=env,
                    stdout=out,
                    stderr=err,
                    timeout=self.timeout_s,
                    check=False,
                )
            state = JobState.SUCCEEDED if proc.returncode == 0 else JobState.FAILED
            error = None if proc.returncode == 0 else f"process exited with {proc.returncode}"
            return ExecutionReceipt(
                state=state,
                returncode=proc.returncode,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error=error,
            )
        except subprocess.TimeoutExpired:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error="local CFD job timed out",
            )
        except Exception as exc:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                wall_time_s=time.monotonic() - started,
                error=str(exc),
            )
