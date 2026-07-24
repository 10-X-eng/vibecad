# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI release gate for bundled standard fasteners."""

from __future__ import annotations

import unittest


class TestVibeCADFastenersGui(unittest.TestCase):
    def test_commands_workbenches_icons_and_native_thread_boolean(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtWidgets

        import VibeCADFastenersGui
        from VibeCADFasteners import catalog_index, resolve_fastener

        self.assertTrue(App.GuiUp)
        VibeCADFastenersGui.ensure_commands_registered()
        expected = {
            "VibeCAD_InsertStandardFastener",
            "VibeCAD_EditStandardFastener",
            "VibeCAD_CreateMatchingFastenerHole",
            "VibeCAD_AttachStandardFastener",
        }
        self.assertTrue(expected.issubset(set(Gui.listCommands())))

        toolbar_actions = {}
        for workbench in (
            "FastenersWorkbench",
            "PartDesignWorkbench",
            "AssemblyWorkbench",
        ):
            Gui.activateWorkbench(workbench)
            self.assertEqual(Gui.activeWorkbench().name(), workbench)
            if workbench not in {"PartDesignWorkbench", "AssemblyWorkbench"}:
                continue
            matching = [
                toolbar
                for toolbar in Gui.getMainWindow().findChildren(
                    QtWidgets.QToolBar
                )
                if toolbar.windowTitle() == "Standard Components"
                and toolbar.isVisible()
            ]
            self.assertTrue(matching)
            actions = matching[-1].actions()
            self.assertGreaterEqual(
                len(actions),
                4 if workbench == "PartDesignWorkbench" else 2,
            )
            self.assertTrue(
                all(not action.icon().isNull() for action in actions)
            )
            toolbar_actions[workbench] = len(actions)

        identity = resolve_fastener(
            standard="ISO4762",
            nominal_thread="M6",
            length_mm=20,
            model_thread=True,
        )
        dialog = VibeCADFastenersGui._FastenerDialog(
            title="Fastener GUI integration",
            initial=identity,
            initial_label="Motor mount bolt",
        )
        try:
            self.assertIsInstance(dialog.model_thread, QtWidgets.QCheckBox)
            self.assertTrue(dialog.model_thread.isChecked())
            values = dialog.values()
            self.assertEqual(
                values["identity"]["canonical_key"],
                identity["canonical_key"],
            )
            self.assertIs(values["model_thread"], True)
            dialog.model_thread.setChecked(False)
            self.assertIs(dialog.values()["model_thread"], False)
            self.assertEqual(
                len(dialog._rows),
                len(catalog_index()["standards"]),
            )
        finally:
            dialog.dialog.close()

        print(
            "VIBECAD_FASTENERS_GUI_OK "
            f"commands={len(expected)} catalog_rows={len(dialog._rows)} "
            f"toolbar_actions={toolbar_actions}",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()
