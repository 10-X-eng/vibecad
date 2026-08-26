# SPDX-License-Identifier: LGPL-2.1-or-later
"""McMaster-Carr workbench (FreeCAD / VibeCAD)."""

try:
    import FreeCAD

    FreeCAD.Console.PrintMessage("McMasterInsert module discovered\n")
except Exception:
    pass
