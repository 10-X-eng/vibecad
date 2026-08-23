# SPDX-License-Identifier: LGPL-2.1-or-later

"""Register print commands from their exact file without module collisions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


COMMAND_IDS = (
    "VibeCADPrint_OpenInPrusaSlicer",
    "VibeCADPrint_Save3MF",
    "VibeCADPrint_Setup",
)
_COMMAND_MODULE_NAME = "_vibecad_print_commands"


def _registered_commands(gui: Any) -> set[str]:
    reader = getattr(gui, "listCommands", None)
    return {str(command) for command in reader()} if callable(reader) else set()


def _load_command_module() -> Any:
    source = Path(__file__).resolve().with_name("Commands.py")
    existing = sys.modules.get(_COMMAND_MODULE_NAME)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == source:
            return existing
    spec = importlib.util.spec_from_file_location(_COMMAND_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load VibeCAD print commands from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_COMMAND_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(_COMMAND_MODULE_NAME) is module:
            sys.modules.pop(_COMMAND_MODULE_NAME, None)
        raise
    return module


def ensure_commands_registered(gui: Any | None = None) -> None:
    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    registered = _registered_commands(gui)
    if registered and set(COMMAND_IDS) <= registered:
        return
    module = _load_command_module()
    module.register_commands(gui=gui)
    reader = getattr(gui, "listCommands", None)
    if callable(reader):
        missing = set(COMMAND_IDS) - _registered_commands(gui)
        if missing:
            raise RuntimeError(
                "VibeCAD print commands failed to register: "
                + ", ".join(sorted(missing))
            )
