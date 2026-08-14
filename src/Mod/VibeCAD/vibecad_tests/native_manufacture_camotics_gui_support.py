# SPDX-License-Identifier: LGPL-2.1-or-later

"""Faithful optional-runtime fixtures for the CAMotics GUI lifecycle gate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import types
from typing import Any, Callable


def _binary_triangle() -> bytes:
    header = b"VibeCAD CAMotics lifecycle surface".ljust(80, b"\0")
    facet = struct.pack(
        "<12fH",
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        4.0,
        0.0,
        0.0,
        0.0,
        3.0,
        0.0,
        0,
    )
    return header + struct.pack("<I", 1) + facet


class FakeCamoticsSimulation:
    """Small asynchronous implementation of CAMotics' published Python API."""

    instances: list["FakeCamoticsSimulation"] = []
    call_threads: list[int] = []

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()
        cls.call_threads.clear()

    def __init__(self) -> None:
        type(self).instances.append(self)
        type(self).call_threads.append(threading.get_ident())
        self.compute_entered = threading.Event()
        self.simulation_entered = threading.Event()
        self._stop = threading.Event()
        self._running = threading.Event()
        self._task: threading.Thread | None = None
        self._path: list[dict[str, float]] | None = None
        self._surface: bytes | None = None
        self._program = ""
        self._metric = False
        self._resolution = ""
        self._workpiece: tuple[tuple[float, ...], tuple[float, ...]] | None = None
        self._tools: dict[int, dict[str, Any]] = {}

    @classmethod
    def _record_thread(cls) -> None:
        cls.call_threads.append(threading.get_ident())

    def _begin(self, target: Callable[[], None], name: str) -> None:
        if self._running.is_set():
            raise RuntimeError("A fake CAMotics task is already running")
        self._stop.clear()
        self._running.set()

        def run() -> None:
            try:
                target()
            finally:
                self._running.clear()

        self._task = threading.Thread(target=run, name=name, daemon=True)
        self._task.start()

    def set_metric(self, metric: bool = True) -> None:
        self._record_thread()
        self._metric = bool(metric)

    def set_resolution(self, resolution: str) -> None:
        self._record_thread()
        if resolution not in {"low", "medium", "high"}:
            raise ValueError("Unsupported fake CAMotics resolution")
        self._resolution = resolution

    def set_workpiece(
        self,
        *,
        min: tuple[float, ...],
        max: tuple[float, ...],
    ) -> None:
        self._record_thread()
        self._workpiece = (tuple(min), tuple(max))

    def set_tool(
        self,
        number: int,
        *,
        metric: bool,
        shape: str,
        length: float,
        diameter: float,
        description: str = "",
    ) -> None:
        self._record_thread()
        self._tools[int(number)] = {
            "metric": bool(metric),
            "shape": str(shape),
            "length": float(length),
            "diameter": float(diameter),
            "description": str(description),
        }

    def compute_path(self, gcode: str) -> None:
        self._record_thread()
        self._program = str(gcode)
        self.compute_entered.set()

        def compute() -> None:
            for _index in range(18):
                if self._stop.wait(0.01):
                    return
            self._path = [
                {"time": 0.04},
                {"time": 0.06},
                {"time": 0.08},
            ]

        self._begin(compute, "FakeCAMotics-Path")

    def get_path(self) -> list[dict[str, float]]:
        self._record_thread()
        if self._path is None:
            raise RuntimeError("Fake CAMotics path is unavailable")
        return list(self._path)

    def start(
        self,
        *,
        callback: Callable[[str, float], Any] | None = None,
        time: float = 0.0,
        **_options: Any,
    ) -> None:
        self._record_thread()
        if self._path is None or not self._metric or self._workpiece is None:
            raise RuntimeError("Fake CAMotics simulation is not configured")
        if not self._resolution or not self._tools or float(time) < 0.0:
            raise RuntimeError("Fake CAMotics simulation inputs are incomplete")
        self.simulation_entered.set()

        def simulate() -> None:
            for index in range(18):
                if self._stop.wait(0.01):
                    return
                if callback is not None and callback("SIMULATING", (index + 1) / 18) is False:
                    return
            self._surface = _binary_triangle()

        self._begin(simulate, "FakeCAMotics-Surface")

    def get_surface(self, format: str = "binary") -> bytes:
        self._record_thread()
        if format != "binary" or self._surface is None:
            raise RuntimeError("Fake CAMotics binary surface is unavailable")
        return self._surface

    def is_running(self) -> bool:
        return self._running.is_set()

    def interrupt(self) -> None:
        self._record_thread()
        self._stop.set()

    def wait(self) -> None:
        self._record_thread()
        task = self._task
        if task is not None:
            task.join(timeout=2.0)
            if task.is_alive():
                raise RuntimeError("Fake CAMotics task did not stop")


@dataclass(frozen=True, slots=True)
class FakeCamoticsInstallation:
    root: Path
    executable: Path
    audit_path: Path
    original_path: str
    previous_module: Any
    module_was_present: bool

    def restore(self) -> None:
        os.environ["PATH"] = self.original_path
        os.environ.pop("VIBECAD_CAMOTICS_AUDIT", None)
        if self.module_was_present:
            sys.modules["camotics"] = self.previous_module
        else:
            sys.modules.pop("camotics", None)


_EXECUTABLE = r'''#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import sys
import time

if len(sys.argv) == 2 and sys.argv[1] == "--version":
    sys.stderr.write("1.2.2\n")
    raise SystemExit(0)

if len(sys.argv) != 2:
    raise SystemExit(2)
project_path = Path(sys.argv[1]).resolve(strict=True)
project = json.loads(project_path.read_text(encoding="utf-8"))
program_path = (project_path.parent / project["files"][0]).resolve(strict=True)
program = program_path.read_bytes()
audit = {
    "project_path": str(project_path),
    "workspace": str(project_path.parent),
    "project": project,
    "program_bytes": len(program),
    "program_sha256": hashlib.sha256(program).hexdigest(),
    "program_prefix": program.decode("utf-8").splitlines()[:8],
}
Path(os.environ["VIBECAD_CAMOTICS_AUDIT"]).write_text(
    json.dumps(audit, sort_keys=True), encoding="utf-8"
)
time.sleep(0.18)
'''


def install_fake_camotics(root: Path) -> FakeCamoticsInstallation:
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "camotics"
    audit_path = root / "launch-audit.json"
    executable.write_text(_EXECUTABLE, encoding="utf-8")
    executable.chmod(0o700)
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{root}{os.pathsep}{original_path}"
    os.environ["VIBECAD_CAMOTICS_AUDIT"] = str(audit_path)
    module_was_present = "camotics" in sys.modules
    previous_module = sys.modules.get("camotics")
    module = types.ModuleType("camotics")
    module.Simulation = FakeCamoticsSimulation
    module.__version__ = "1.2.2"
    sys.modules["camotics"] = module
    FakeCamoticsSimulation.reset()
    return FakeCamoticsInstallation(
        root=root,
        executable=executable,
        audit_path=audit_path,
        original_path=original_path,
        previous_module=previous_module,
        module_was_present=module_was_present,
    )


def read_launch_audit(installation: FakeCamoticsInstallation) -> dict[str, Any]:
    return json.loads(installation.audit_path.read_text(encoding="utf-8"))


def program_sha256(program: str) -> str:
    return hashlib.sha256(program.encode("utf-8")).hexdigest()
