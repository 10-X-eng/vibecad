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

            dialog.filter_edit.setText("m3")
            self.assertGreater(dialog.standard_combo.count(), 0)
            self.assertEqual(dialog.size_combo.currentData(), "M3")

            dialog.filter_edit.setText("m3 socket")
            self.assertGreater(dialog.standard_combo.count(), 0)
            self.assertEqual(dialog.size_combo.currentData(), "M3")
            matching_standards = {
                str(dialog.standard_combo.itemData(index))
                for index in range(dialog.standard_combo.count())
            }
            matching_rows = [
                row
                for row in dialog._rows
                if str(row["standard"]) in matching_standards
            ]
            self.assertEqual(
                len(matching_rows),
                dialog.standard_combo.count(),
            )
            self.assertTrue(
                all(
                    "socket"
                    in (
                        f"{row['standard']} {row['family']} "
                        f"{row['description']}"
                    ).casefold()
                    and any(
                        "m3" in str(size).casefold()
                        for size in row["nominal_threads"]
                    )
                    for row in matching_rows
                )
            )
            exact_m3_standard = next(
                row["standard"]
                for row in matching_rows
                if "M3" in row["nominal_threads"]
            )
            exact_m3_index = dialog.standard_combo.findData(
                exact_m3_standard
            )
            self.assertGreaterEqual(exact_m3_index, 0)
            dialog.standard_combo.setCurrentIndex(exact_m3_index)
            self.assertEqual(dialog.size_combo.currentData(), "M3")

            dialog.filter_edit.setText("476")
            self.assertGreaterEqual(
                dialog.standard_combo.findData("ISO4762"),
                0,
            )
            self.assertIn(
                str(dialog.standard_combo.count()),
                dialog.match_label.text(),
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
