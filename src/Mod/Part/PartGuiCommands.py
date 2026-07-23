# SPDX-License-Identifier: LGPL-2.1-or-later
"""Register the Part command set without registering a standalone workbench."""

import FreeCAD as App


_initialized = False


def initialize():
    """Load every command historically supplied by the Part workbench."""
    global _initialized
    if _initialized:
        return

    import PartGui

    try:
        import BasicShapes.CommandShapes  # noqa: F401 - import registers GUI commands
    except ImportError as err:
        App.Console.PrintError(
            "'BasicShapes' package cannot be loaded. {err}\n".format(err=err)
        )

    try:
        import CompoundTools._CommandCompoundFilter  # noqa: F401 - import registers GUI commands
        import CompoundTools._CommandExplodeCompound  # noqa: F401 - import registers GUI commands
    except ImportError as err:
        App.Console.PrintError(
            "'CompoundTools' package cannot be loaded. {err}\n".format(err=err)
        )

    try:
        bop = __import__("BOPTools")
        bop.importAll()
        bop.addCommands()
        PartGui.BOPTools = bop
    except Exception as err:
        App.Console.PrintError(
            "'BOPTools' package cannot be loaded. {err}\n".format(err=err)
        )

    _initialized = True
