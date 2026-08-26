# SPDX-License-Identifier: LGPL-2.1-or-later
"""Kaggle compute-provider integration for VibeCADAero CFD jobs.

The current Kaggle CLI is the authority for notebook lifecycle.  This module
queries the installed CLI rather than hard-coding the old discussion's
"30 hours/week" assumption.  Current CLI releases provide ``kaggle quota`` for
weekly accelerator quota and ``kernels push/status/output`` for job lifecycle.

Kaggle is a *compute provider*, not a CFD solver.  A solver adapter must prepare
a Kaggle-runnable kernel directory and put its path in
``PreparedJob.metadata['kaggle_kernel_dir']``.  This prevents a local absolute
FluidX3D/OpenFOAM executable path from being accidentally treated as remotely
runnable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from AeroCFDContracts import ExecutionReceipt, JobState, PreparedJob


class KaggleError(RuntimeError):
    pass


class KaggleCLI:
    def __init__(self, executable: str = "kaggle") -> None:
        self.executable = executable

    def _run(self, args: list[str], *, cwd: str | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [self.executable, *args]
        try:
            return subprocess.run(
                cmd,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise KaggleError("Kaggle CLI is not installed or not on PATH.") from exc

    def version(self) -> str:
        proc = self._run(["--version"], timeout=15)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Unable to query Kaggle CLI version")
        return proc.stdout.strip()

    def quota_raw(self) -> str:
        """Return the live CLI quota report without assuming its numeric schema."""
        proc = self._run(["quota"], timeout=30)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Unable to query Kaggle accelerator quota")
        return proc.stdout.strip()

    def push(self, kernel_dir: Path, *, accelerator: str, timeout_s: int) -> str:
        proc = self._run(
            ["kernels", "push", "-p", str(kernel_dir), "--accelerator", accelerator, "-t", str(int(timeout_s))],
            timeout=max(60, timeout_s + 60),
        )
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or proc.stdout.strip() or "Kaggle kernel push failed")
        return (proc.stdout + "\n" + proc.stderr).strip()

    def status(self, kernel_ref: str) -> str:
        proc = self._run(["kernels", "status", kernel_ref], timeout=30)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Kaggle kernel status failed")
        return (proc.stdout + "\n" + proc.stderr).strip()

    def output(self, kernel_ref: str, output_dir: Path) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        proc = self._run(["kernels", "output", kernel_ref, "-p", str(output_dir), "-o"], timeout=600)
        if proc.returncode != 0:
            raise KaggleError(proc.stderr.strip() or "Kaggle kernel output download failed")
        return (proc.stdout + "\n" + proc.stderr).strip()


def write_kernel_metadata(
    kernel_dir: str | Path,
    *,
    owner: str,
    slug: str,
    code_file: str,
    title: str | None = None,
    accelerator: str = "NvidiaTeslaT4",
    enable_internet: bool = False,
) -> str:
    """Write current documented Kaggle kernel metadata for a private script."""

    directory = Path(kernel_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / code_file).is_file():
        raise KaggleError(f"Kaggle code_file does not exist: {directory / code_file}")
    payload = {
        "id": f"{owner}/{slug}",
        "title": title or slug.replace("-", " ").title(),
        "code_file": code_file,
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": bool(enable_internet),
        "machine_shape": accelerator,
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }
    path = directory / "kernel-metadata.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _classify_status(text: str) -> JobState:
    value = text.lower()
    # Different CLI versions have varied wording. Match failure before success.
    if any(token in value for token in ("error", "failed", "failure", "cancelled", "canceled")):
        return JobState.FAILED
    if any(token in value for token in ("complete", "completed", "success", "succeeded")):
        return JobState.SUCCEEDED
    return JobState.RUNNING


class KaggleComputeProvider:
    name = "kaggle"

    def __init__(
        self,
        *,
        accelerator: str = "NvidiaTeslaT4",
        poll_interval_s: float = 15.0,
        timeout_s: int = 9 * 60 * 60,
        cli: KaggleCLI | None = None,
    ) -> None:
        self.accelerator = accelerator
        self.poll_interval_s = max(1.0, float(poll_interval_s))
        self.timeout_s = int(timeout_s)
        self.cli = cli or KaggleCLI()

    def quota(self) -> str:
        return self.cli.quota_raw()

    def execute(self, job: PreparedJob) -> ExecutionReceipt:
        kernel_dir_value = job.metadata.get("kaggle_kernel_dir")
        if not kernel_dir_value:
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                error=(
                    "Solver job has no kaggle_kernel_dir. Remote execution must be explicitly "
                    "prepared by the solver adapter; local executables are not portable to Kaggle."
                ),
            )
        kernel_dir = Path(str(kernel_dir_value)).expanduser().resolve()
        metadata_path = kernel_dir / "kernel-metadata.json"
        if not metadata_path.is_file():
            return ExecutionReceipt(state=JobState.FAILED, returncode=None, error="kernel-metadata.json is missing")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        kernel_ref = str(metadata.get("id") or "")
        if "/" not in kernel_ref:
            return ExecutionReceipt(state=JobState.FAILED, returncode=None, error="Kaggle kernel metadata has no valid id")

        started = time.monotonic()
        provider_log = Path(job.workdir) / "kaggle_provider.log"
        try:
            # Capture the live quota report for provenance.  Failure to retrieve
            # quota should block automatic submission rather than invent a value.
            quota = self.cli.quota_raw()
            push_output = self.cli.push(kernel_dir, accelerator=self.accelerator, timeout_s=self.timeout_s)
            log_lines = ["# quota", quota, "# push", push_output]

            while True:
                elapsed = time.monotonic() - started
                if elapsed > self.timeout_s:
                    provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
                    return ExecutionReceipt(
                        state=JobState.FAILED,
                        returncode=None,
                        provider_job_id=kernel_ref,
                        wall_time_s=elapsed,
                        metadata={"quota_report": quota},
                        error="Kaggle kernel exceeded provider timeout",
                    )
                status_text = self.cli.status(kernel_ref)
                log_lines.extend(("# status", status_text))
                state = _classify_status(status_text)
                if state == JobState.SUCCEEDED:
                    break
                if state == JobState.FAILED:
                    provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
                    return ExecutionReceipt(
                        state=JobState.FAILED,
                        returncode=None,
                        provider_job_id=kernel_ref,
                        wall_time_s=elapsed,
                        metadata={"quota_report": quota, "status": status_text},
                        error="Kaggle kernel failed",
                    )
                time.sleep(self.poll_interval_s)

            output_dir = Path(job.workdir) / "kaggle_output"
            output_text = self.cli.output(kernel_ref, output_dir)
            log_lines.extend(("# output", output_text))
            expected_remote = output_dir / Path(job.expected_result).name
            expected_local = Path(job.workdir) / job.expected_result
            if expected_remote.is_file():
                expected_local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(expected_remote, expected_local)
            provider_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return ExecutionReceipt(
                state=JobState.SUCCEEDED,
                returncode=0,
                provider_job_id=kernel_ref,
                wall_time_s=time.monotonic() - started,
                stdout_path=str(provider_log),
                metadata={"quota_report": quota, "accelerator": self.accelerator, "output_dir": str(output_dir)},
            )
        except Exception as exc:
            try:
                provider_log.write_text(str(exc) + "\n", encoding="utf-8")
            except Exception:
                pass
            return ExecutionReceipt(
                state=JobState.FAILED,
                returncode=None,
                provider_job_id=kernel_ref,
                wall_time_s=time.monotonic() - started,
                stdout_path=str(provider_log),
                error=str(exc),
            )
