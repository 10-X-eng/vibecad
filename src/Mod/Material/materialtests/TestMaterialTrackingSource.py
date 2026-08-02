# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for Material's document tracking integration."""

from pathlib import Path
import unittest


def _material_source_root():
    test_path = Path(__file__).resolve()
    direct_root = test_path.parents[1]
    if (direct_root / "Gui" / "Command.cpp").is_file():
        return direct_root
    for parent in test_path.parents:
        candidate = parent / "src" / "Mod" / "Material"
        if (candidate / "Gui" / "Command.cpp").is_file():
            return candidate
    raise RuntimeError("Could not locate the Material source tree")


_MATERIAL_ROOT = _material_source_root()


class MaterialTrackingSourceTest(unittest.TestCase):
    def test_standard_surface_is_exactly_five_commands(self):
        command = (
            _MATERIAL_ROOT / "Gui" / "Command.cpp"
        ).read_text(encoding="utf-8")
        names = {
            "Material_Edit",
            "Std_SetAppearance",
            "Std_SetMaterial",
            "Materials_InspectAppearance",
            "Materials_InspectMaterial",
        }
        for name in names:
            self.assertIn(f'"{name}"', command)
        self.assertEqual(len(names), 5)

    def test_document_editors_refuse_caller_transactions(self):
        command = (
            _MATERIAL_ROOT / "Gui" / "Command.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("document->getBookedTransactionID()", command)
        self.assertIn("document->hasPendingTransaction()", command)
        self.assertIn("getGlobalTransaction()", command)
        self.assertIn("isTransactionLocked()", command)
        self.assertIn("transacting()", command)
        self.assertIn("selected.pDoc != document", command)
        self.assertIn("activeMutationDocument()", command)

    def test_tasks_own_exact_rollback_capable_transactions(self):
        appearance = (
            _MATERIAL_ROOT / "Gui" / "DlgDisplayPropertiesImp.cpp"
        ).read_text(encoding="utf-8")
        material = (
            _MATERIAL_ROOT / "Gui" / "DlgMaterialImp.cpp"
        ).read_text(encoding="utf-8")
        for source in (appearance, material):
            self.assertIn("Gui::ExactTransaction", source)
            self.assertIn("transaction->ownsCurrentTransaction()", source)
            self.assertIn("transaction->commit()", source)
            self.assertIn("transaction->abort()", source)
            self.assertNotIn("Gui::Command::openDocumentCommand(", source)

    def test_targets_use_document_uid_name_and_object_id(self):
        identity = (
            _MATERIAL_ROOT / "Gui" / "SelectionTargetIdentity.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("document->Uid.getValueStr()", identity)
        self.assertIn("object->getID()", identity)
        self.assertIn("getObjectByID(objectId)", identity)
        self.assertIn("objectName == object->getNameInDocument()", identity)

    def test_linked_material_property_owner_is_explicitly_enlisted(self):
        identity = (
            _MATERIAL_ROOT / "Gui" / "SelectionTargetIdentity.cpp"
        ).read_text(encoding="utf-8")
        material = (
            _MATERIAL_ROOT / "Gui" / "DlgMaterialImp.cpp"
        ).read_text(encoding="utf-8")
        transaction = (
            _MATERIAL_ROOT.parents[1] / "Gui" / "ExactTransaction.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("property->getContainer()", identity)
        self.assertIn("property == propertyAddress", identity)
        self.assertIn("target.owner.resolveDocument()", material)
        self.assertIn("materialMutationDocuments", material)
        self.assertIn("App::Document* initiator", transaction)
        self.assertIn("initiator->openTransaction", transaction)


if __name__ == "__main__":
    unittest.main()
