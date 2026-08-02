# SPDX-License-Identifier: LGPL-2.1-or-later

"""Transaction and Body-publication contracts for retained Part task dialogs."""

import os
from pathlib import Path
import re
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui


def _source_checkout_root():
    return next(
        (
            candidate
            for candidate in Path(__file__).resolve().parents
            if (candidate / "CMakeLists.txt").is_file()
            and (candidate / "src/Mod").is_dir()
        ),
        None,
    )


class TestModelingTaskAttemptSourceContract(unittest.TestCase):
    """The retained Part attempt delegates one exact locked transaction."""

    def test_retained_part_results_are_read_from_exact_creation_returns(self):
        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Part GUI source checkout is unavailable")

        gui_sources = source_root / "src/Mod/Part/Gui"
        audited = (
            "DlgPrimitives.cpp",
            "TaskShapeBuilder.cpp",
            "DlgBooleanOperation.cpp",
            "CommandSimple.cpp",
            "Command.cpp",
        )
        for filename in audited:
            with self.subTest(filename=filename):
                source = (gui_sources / filename).read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("captureObjectIds", source)
                self.assertNotIn("newObjects(", source)
                self.assertNotIn("existingObjectIds", source)

        validation = (
            gui_sources / "TaskResultValidation.h"
        ).read_text(encoding="utf-8")
        self.assertIn("requireExactPartResult", validation)
        self.assertIn("requirePythonPartResult", validation)
        self.assertNotIn("document.getObjects()", validation)

        attempt = (
            gui_sources / "ModelingSelection.cpp"
        ).read_text(encoding="utf-8")
        commit = attempt[
            attempt.index("void ModelingTaskAttempt::commit()"):
            attempt.index(
                "\n}\n\n}  // namespace PartGui",
                attempt.index("void ModelingTaskAttempt::commit()"),
            )
        ]
        self.assertNotIn("d->document->getObjects()", commit)

    def test_retained_model_factories_do_not_recover_outputs_by_global_name(self):
        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Part GUI source checkout is unavailable")

        part_gui = source_root / "src/Mod/Part/Gui"
        primitive_source = (
            part_gui / "DlgPrimitives.cpp"
        ).read_text(encoding="utf-8")
        primitive_create = primitive_source[
            primitive_source.index(
                "bool DlgPrimitives::tryCreatePrimitive"
            ):
            primitive_source.index(
                "\nApp::DocumentObject* "
                "DlgPrimitives::lastCreatedResult",
                primitive_source.index(
                    "bool DlgPrimitives::tryCreatePrimitive"
                ),
            )
        ]
        self.assertIn("runDocumentObjectCommand", primitive_create)
        self.assertIn("primitive->change(resultExpression", primitive_create)
        self.assertNotIn("primitive->create(", primitive_create)
        self.assertNotIn(
            "requirePythonPartResult",
            primitive_create,
        )
        picker_create = primitive_source[
            primitive_source.index(
                "App::DocumentObject* Picker::createPrimitiveAndReport"
            ):
            primitive_source.index(
                "\nQString Picker::toPlacement",
                primitive_source.index(
                    "App::DocumentObject* "
                    "Picker::createPrimitiveAndReport"
                ),
            )
        ]
        self.assertIn("runDocumentObjectCommand", picker_create)
        self.assertIn("exactConfigurationCommand", picker_create)
        picker_subclasses = [
            line
            for line in primitive_source.splitlines()
            if ": public Picker" in line
        ]
        self.assertEqual(
            picker_subclasses,
            ["class CircleFromThreePoints: public Picker"],
        )
        circle_picker = primitive_source[
            primitive_source.index(
                "class CircleFromThreePoints: public Picker"
            ):
            primitive_source.index(
                "\nprivate:",
                primitive_source.index(
                    "class CircleFromThreePoints: public Picker"
                ),
            )
        ]
        self.assertIn("exactTypeName() const override", circle_picker)
        self.assertIn("exactDefaultName() const override", circle_picker)
        self.assertIn(
            "exactConfigurationCommand(",
            circle_picker,
        )

        for filename in (
            "TaskShapeBuilder.cpp",
            "DlgBooleanOperation.cpp",
        ):
            with self.subTest(exact_retained_dialog=filename):
                source = (part_gui / filename).read_text(
                    encoding="utf-8"
                )
                self.assertIn(
                    "runDocumentObjectCommand",
                    source,
                )
                self.assertNotIn(
                    "requirePythonPartResult",
                    source,
                )
                self.assertNotIn(
                    "__vibecad_part_result__",
                    source,
                )
                self.assertNotIn(
                    "App.ActiveDocument.",
                    source,
                )

        part_command = (
            part_gui / "Command.cpp"
        ).read_text(encoding="utf-8")
        for command_name in (
            "CmdPartCut",
            "CmdPartCommon",
            "CmdPartFuse",
            "CmdPartCompound",
            "CmdPartMakeSolid",
            "CmdPartMakeFace",
        ):
            with self.subTest(exact_model_command=command_name):
                command = part_command[
                    part_command.index(
                        f"void {command_name}::activated"
                    ):
                    part_command.index(
                        f"bool {command_name}::isActive",
                        part_command.index(
                            f"void {command_name}::activated"
                        ),
                    )
                ]
                self.assertIn("runDocumentObjectCommand", command)
                self.assertNotIn(
                    "requirePythonPartResult",
                    command,
                )
                self.assertNotIn(
                    "App.ActiveDocument.ActiveObject",
                    command,
                )

        command_simple = (
            part_gui / "CommandSimple.cpp"
        ).read_text(encoding="utf-8")
        defeaturing = command_simple[
            command_simple.index(
                "void CmdPartDefeaturing::activated"
            ):
            command_simple.index(
                "bool CmdPartDefeaturing::isActive",
                command_simple.index(
                    "void CmdPartDefeaturing::activated"
                ),
            )
        ]
        self.assertIn(
            "runDocumentObjectCommand",
            defeaturing,
        )
        self.assertNotIn(
            "requirePythonPartResult",
            defeaturing,
        )
        self.assertNotIn(
            "__vibecad_part_result__",
            defeaturing,
        )
        app_part_gui = (
            part_gui / "AppPartGui.cpp"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            '"runDocumentObjectCommand"',
            app_part_gui,
        )

        python_commands = (
            "src/Mod/Part/BOPTools/JoinFeatures.py",
            "src/Mod/Part/BOPTools/SplitFeatures.py",
            "src/Mod/Part/BOPTools/ToleranceFeatures.py",
            "src/Mod/Part/CompoundTools/_CommandExplodeCompound.py",
            "src/Mod/Part/CompoundTools/_CommandCompoundFilter.py",
        )
        for relative_path in python_commands:
            with self.subTest(exact_python_command=relative_path):
                source = (source_root / relative_path).read_text(
                    encoding="utf-8"
                )
                self.assertIn(
                    "FreeCADGui.runDocumentObjectCommand(",
                    source,
                )
                self.assertRegex(
                    source,
                    r"FreeCADGui\.runDocumentObjectCommand\(\s+"
                    r"document,",
                )
                self.assertNotIn(
                    "PartGui.runDocumentObjectCommand(",
                    source,
                )
                self.assertNotIn("doCommandEval(", source)
                self.assertNotIn(
                    'FreeCADGui.doCommand("j = ',
                    source,
                )
                self.assertNotIn(
                    'FreeCADGui.doCommand("f = ',
                    source,
                )

    def test_model_commands_preserve_exact_factory_and_import_identities(self):
        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("GUI source checkout is unavailable")

        command_header = (
            source_root / "src/Gui/Command.h"
        ).read_text(encoding="utf-8")
        command_implementation = (
            source_root / "src/Gui/Command.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("runDocumentObjectCommand", command_header)
        for contract in (
            "runStringObject(expression.constData())",
            "App::DocumentObjectPy::Type",
            "resolvedDocument->getObjectByID(resultId) != result",
            "macroManager()->addLine",
        ):
            with self.subTest(shared_contract=contract):
                self.assertIn(contract, command_implementation)
        exact_bridge = command_implementation[
            command_implementation.index(
                "App::DocumentObject* Command::_runDocumentObjectCommand"
            ):
            command_implementation.index(
                "\nvoid Command::addModule",
                command_implementation.index(
                    "App::DocumentObject* "
                    "Command::_runDocumentObjectCommand"
                ),
            )
        ]
        self.assertNotIn("__vibecad_document_object_result_", exact_bridge)
        self.assertNotIn("PyObject_HasAttrString", exact_bridge)
        self.assertNotIn("PyObject_DelAttrString", exact_bridge)
        self.assertNotIn("getActiveObject", exact_bridge)
        self.assertNotIn("_runCommand(", exact_bridge)
        self.assertEqual(1, exact_bridge.count("runStringObject"))

        part_gui = source_root / "src/Mod/Part/Gui"
        exact_factory_sources = (
            "CommandSimple.cpp",
            "Command.cpp",
            "DlgExtrusion.cpp",
            "DlgScale.cpp",
            "DlgRevolution.cpp",
            "Mirroring.cpp",
            "ShapeFromMesh.cpp",
            "CommandParametric.cpp",
        )
        for filename in exact_factory_sources:
            with self.subTest(exact_factory_source=filename):
                source = (part_gui / filename).read_text(
                    encoding="utf-8"
                )
                self.assertIn("runDocumentObjectCommand", source)

        for filename in (
            "DlgExtrusion.cpp",
            "DlgScale.cpp",
            "DlgRevolution.cpp",
            "Mirroring.cpp",
        ):
            with self.subTest(exact_retained_source=filename):
                source = (part_gui / filename).read_text(
                    encoding="utf-8"
                )
                self.assertIn("Qt::UserRole + 1", source)
                self.assertIn("getObjectByID", source)

        mirror_source = (part_gui / "Mirroring.cpp").read_text(
            encoding="utf-8"
        )
        for exact_identity_contract in (
            "activeDoc != documentAddress",
            "activeDoc->Uid.getValueStr() != documentUid",
            "src != sourceAddress",
            "activeDoc->getObject(sourceName.constData()) != src",
            "!PartGui::isModelingObjectActive(src)",
        ):
            with self.subTest(
                mirror_exact_identity=exact_identity_contract
            ):
                self.assertIn(exact_identity_contract, mirror_source)

        simple = (part_gui / "CommandSimple.cpp").read_text(
            encoding="utf-8"
        )
        copy_command = simple[
            simple.index("static void _copyShape("):
            simple.index("static bool hasSelectedShapeElement()")
        ]
        refine_command = simple[
            simple.index("void CmdPartRefineShape::activated"):
            simple.index("bool CmdPartRefineShape::isActive")
        ]
        self.assertNotIn("getActiveObject()", copy_command)
        self.assertNotIn("getActiveObject()", refine_command)

        duplicate_source = (
            source_root / "src/Mod/PartDesign/Gui/CommandBody.cpp"
        ).read_text(encoding="utf-8")
        duplicate_command = duplicate_source[
            duplicate_source.index(
                "void CmdPartDesignDuplicateSelection::activated"
            ):
            duplicate_source.index(
                "bool CmdPartDesignDuplicateSelection::isActive"
            )
        ]
        self.assertIn("copyTimelineObjects", duplicate_command)
        self.assertIn("imported.selectedObjects", duplicate_command)
        self.assertIn("imported.sourceOrder", duplicate_command)
        self.assertIn("adoptTimelineImport(imported)", duplicate_command)
        self.assertNotIn("beforeObjectIds", duplicate_command)
        self.assertNotIn("getObjects()", duplicate_command)

        sketcher_source = (
            source_root / "src/Mod/Sketcher/Gui/Command.cpp"
        ).read_text(encoding="utf-8")
        mirror_command = sketcher_source[
            sketcher_source.index(
                "void CmdSketcherMirrorSketch::activated"
            ):
            sketcher_source.index(
                "bool CmdSketcherMirrorSketch::isActive"
            )
        ]
        self.assertIn("runDocumentObjectCommand", mirror_command)
        self.assertNotIn("doc->getObject(FeatName", mirror_command)

        merge_command = sketcher_source[
            sketcher_source.index(
                "void CmdSketcherMergeSketches::activated"
            ):
            sketcher_source.index(
                "bool CmdSketcherMergeSketches::isActive"
            )
        ]
        self.assertIn("runDocumentObjectCommand", merge_command)
        self.assertNotIn("doc->getObject(FeatName", merge_command)
        self.assertNotIn("ActiveObject.Placement", merge_command)

        part_design_utils = (
            source_root / "src/Mod/PartDesign/Gui/Utils.cpp"
        ).read_text(encoding="utf-8")
        make_body = part_design_utils[
            part_design_utils.index("PartDesign::Body* makeBody("):
            part_design_utils.index(
                "PartDesign::Body* getBodyFor(",
                part_design_utils.index("PartDesign::Body* makeBody("),
            )
        ]
        self.assertIn("runDocumentObjectCommand", make_body)
        self.assertIn(
            "classifyProvisionalTimelineInternalObject",
            make_body,
        )
        self.assertIn("getObjectCmd(body)", make_body)
        self.assertNotIn("getObject(bodyName", make_body)

        body_command_source = (
            source_root / "src/Mod/PartDesign/Gui/CommandBody.cpp"
        ).read_text(encoding="utf-8")
        create_body_command = body_command_source[
            body_command_source.index(
                "void CmdPartDesignBody::activated"
            ):
            body_command_source.index(
                "bool CmdPartDesignBody::isActive",
                body_command_source.index(
                    "void CmdPartDesignBody::activated"
                ),
            )
        ]
        self.assertIn(
            "runDocumentObjectCommand",
            create_body_command,
        )
        self.assertIn(
            "resolveBodyCreationObject(createdBodyIdentity)",
            create_body_command,
        )
        self.assertIn("exactBodyName.c_str()", create_body_command)
        self.assertNotIn(
            "document->getObject(bodyString)",
            create_body_command,
        )

        part_design_command = (
            source_root / "src/Mod/PartDesign/Gui/Command.cpp"
        ).read_text(encoding="utf-8")
        clone_command = part_design_command[
            part_design_command.index("void CmdPartDesignClone::activated"):
            part_design_command.index(
                "bool CmdPartDesignClone::isActive",
                part_design_command.index(
                    "void CmdPartDesignClone::activated"
                ),
            )
        ]
        self.assertGreaterEqual(
            clone_command.count("runDocumentObjectCommand"),
            2,
        )
        self.assertIn(
            "classifyProvisionalTimelineInternalObject",
            clone_command,
        )
        self.assertNotIn("getObject(bodyName", clone_command)
        self.assertNotIn("getObject(cloneName", clone_command)
        self.assertNotIn("ActiveDocument.ActiveObject", clone_command)

        part_design_primitive = (
            source_root / "src/Mod/PartDesign/Gui/CommandPrimitive.cpp"
        ).read_text(encoding="utf-8")
        primitive_factory = part_design_primitive[
            part_design_primitive.index(
                "static PartDesign::FeaturePrimitive* "
                "createPrimitiveExact("
            ):
            part_design_primitive.index(
                "\n}\n",
                part_design_primitive.index(
                    "static PartDesign::FeaturePrimitive* "
                    "createPrimitiveExact("
                ),
            )
            + 2
        ]
        self.assertIn("runDocumentObjectCommand", primitive_factory)
        for command_name, command_end in (
            (
                "CmdPrimtiveCompAdditive",
                "Gui::Action* CmdPrimtiveCompAdditive::createAction",
            ),
            (
                "CmdPrimtiveCompSubtractive",
                "Gui::Action* CmdPrimtiveCompSubtractive::createAction",
            ),
        ):
            with self.subTest(exact_part_design_primitive=command_name):
                command_start = part_design_primitive.index(
                    f"void {command_name}::activated"
                )
                command = part_design_primitive[
                    command_start:
                    part_design_primitive.index(
                        command_end,
                        command_start,
                    )
                ]
                self.assertIn("createPrimitiveExact", command)
                self.assertIn("resolveExactPrimitive", command)
                self.assertNotIn("getObject(FeatName", command)

        part_design_factory = part_design_command[
            part_design_command.index(
                "App::DocumentObject* createBodyFeatureExact("
            ):
            part_design_command.index(
                "App::DocumentObject* createDocumentFeatureExact(",
                part_design_command.index(
                    "App::DocumentObject* createBodyFeatureExact("
                ),
            )
        ]
        self.assertIn("runDocumentObjectCommand", part_design_factory)
        self.assertIn("resolveBody", part_design_factory)
        for helper_start, helper_end in (
            (
                "void UnifiedDatumCommand(",
                "/* Datum feature commands",
            ),
            (
                "void CmdPartDesignShapeBinder::activated",
                "bool CmdPartDesignShapeBinder::isActive",
            ),
            (
                "void CmdPartDesignSubShapeBinder::activated",
                "bool CmdPartDesignSubShapeBinder::isActive",
            ),
            (
                "void prepareProfileBased(",
                "void finishProfileBased(",
            ),
            (
                "void finishDressupFeature(",
                "void makeChamferOrFillet(",
            ),
            (
                "void prepareTransformed(",
                "void finishTransformed(",
            ),
            (
                "void CmdPartDesignMultiTransform::activated",
                "bool CmdPartDesignMultiTransform::isActive",
            ),
            (
                "void CmdPartDesignBoolean::activated",
                "bool CmdPartDesignBoolean::isActive",
            ),
        ):
            with self.subTest(exact_part_design_factory=helper_start):
                start = part_design_command.index(helper_start)
                command = part_design_command[
                    start:
                    part_design_command.index(helper_end, start)
                ]
                self.assertIn("createBodyFeatureExact", command)
                self.assertNotIn("getObject(FeatName", command)
        self.assertNotIn(
            "getObject(FeatName.c_str())",
            part_design_command,
        )

        parametric = (
            source_root / "src/Mod/Part/Gui/CommandParametric.cpp"
        ).read_text(encoding="utf-8")
        primitive_helper = parametric[
            parametric.index("Feature* createParametricPrimitive("):
            parametric.index(
                "\n}  // namespace",
                parametric.index(
                    "Feature* createParametricPrimitive("
                ),
            )
        ]
        self.assertIn("runDocumentObjectCommand", primitive_helper)
        self.assertIn("getObjectCmd(result)", primitive_helper)
        for command_name in (
            "CmdPartBox",
            "CmdPartCylinder",
            "CmdPartSphere",
            "CmdPartCone",
            "CmdPartTorus",
        ):
            with self.subTest(exact_primitive_factory=command_name):
                command = parametric[
                    parametric.index(
                        f"void {command_name}::activated"
                    ):
                    parametric.index(
                        f"bool {command_name}::isActive",
                        parametric.index(
                            f"void {command_name}::activated"
                        ),
                    )
                ]
                self.assertIn("createParametricPrimitive", command)
                self.assertNotIn("ActiveDocument.ActiveObject", command)
        self.assertNotIn(
            "App.ActiveDocument.ActiveObject",
            parametric,
        )

        part_command = (
            source_root / "src/Mod/Part/Gui/Command.cpp"
        ).read_text(encoding="utf-8")
        for command_name, command_end, expected_type in (
            (
                "CmdPartOffset",
                "bool CmdPartOffset::isActive",
                "Part::Offset",
            ),
            (
                "CmdPartOffset2D",
                "bool CmdPartOffset2D::isActive",
                "Part::Offset2D",
            ),
            (
                "CmdPartThickness",
                "bool CmdPartThickness::isActive",
                "Part::Thickness",
            ),
        ):
            with self.subTest(exact_task_factory=command_name):
                command = part_command[
                    part_command.index(
                        f"void {command_name}::activated"
                    ):
                    part_command.index(
                        command_end,
                        part_command.index(
                            f"void {command_name}::activated"
                        ),
                    )
                ]
                self.assertIn("runDocumentObjectCommand", command)
                self.assertIn(
                    f"{expected_type}::getClassTypeId()",
                    command,
                )
                self.assertIn("getObjectCmd(result)", command)
                self.assertIn("Gui::cmdSetEdit(result)", command)
                self.assertNotIn("ActiveDocument.addObject", command)
                self.assertNotIn("isActiveObjectValid", command)
                self.assertNotIn("Gui.ActiveDocument.setEdit", command)

        ruled_surface = part_command[
            part_command.index(
                "void CmdPartRuledSurface::activated"
            ):
            part_command.index(
                "bool CmdPartRuledSurface::isActive",
                part_command.index(
                    "void CmdPartRuledSurface::activated"
                ),
            )
        ]
        self.assertIn("runDocumentObjectCommand", ruled_surface)
        self.assertIn(
            "Part::RuledSurface::getClassTypeId()",
            ruled_surface,
        )
        self.assertIn("getObjectCmd(result)", ruled_surface)
        self.assertNotIn("ActiveDocument.ActiveObject", ruled_surface)

        datum_helper = part_command[
            part_command.index("Datum* createDatumObject("):
            part_command.index(
                "\n}  // namespace",
                part_command.index("Datum* createDatumObject("),
            )
        ]
        self.assertIn("runDocumentObjectCommand", datum_helper)
        self.assertIn("autoGroupDatumObject(result)", datum_helper)
        datum_grouping = part_command[
            part_command.index("void autoGroupDatumObject("):
            part_command.index(
                "\ntemplate<typename Datum>",
                part_command.index("void autoGroupDatumObject("),
            )
        ]
        self.assertIn("getObjectCmd(object)", datum_grouping)
        for command_name in (
            "CmdPartCoordinateSystem",
            "CmdPartDatumPlane",
            "CmdPartDatumLine",
            "CmdPartDatumPoint",
        ):
            with self.subTest(exact_datum_factory=command_name):
                command = part_command[
                    part_command.index(
                        f"void {command_name}::activated"
                    ):
                    part_command.index(
                        f"bool {command_name}::isActive",
                        part_command.index(
                            f"void {command_name}::activated"
                        ),
                    )
                ]
                self.assertIn("createDatumObject", command)
                self.assertIn("getObjectCmd(result)", command)
                self.assertNotIn("App.activeDocument().addObject", command)
                self.assertNotIn("obj.ViewObject", command)

        for filename, helper_start, helper_end in (
            (
                "src/Mod/Part/Gui/ShapeFromMesh.cpp",
                "App::Property* ensureTimelineProperty(",
                "\nApp::DocumentObject* createMultiResultController(",
            ),
            (
                "src/Mod/Sketcher/Gui/Command.cpp",
                "App::Property* ensureSketchTimelineProperty(",
                "\nvoid markSketchCommandOutputs(",
            ),
        ):
            with self.subTest(timeline_metadata_helper=filename):
                source = (source_root / filename).read_text(
                    encoding="utf-8"
                )
                helper = source[
                    source.index(helper_start):
                    source.index(
                        helper_end,
                        source.index(helper_start),
                    )
                ]
                for status in ("Hidden", "LockDynamic", "NoRecompute"):
                    self.assertIn(
                        f"setStatus(App::Property::{status}, true)",
                        helper,
                    )

    def test_commit_and_rollback_release_only_for_exact_id_close(self):
        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("ModelingSelection source checkout is unavailable")

        implementation = (
            source_root / "src/Mod/Part/Gui/ModelingSelection.cpp"
        ).read_text(encoding="utf-8")
        private = implementation[
            implementation.index(
                "class ModelingTaskAttempt::Private"
            ):
            implementation.index(
                "ModelingTaskAttempt::ModelingTaskAttempt("
            )
        ]
        self.assertIn(
            "std::unique_ptr<Gui::ExactTransaction> transaction;",
            private,
        )
        rollback = private[
            private.index("void rollback() noexcept"):
            private.index("void restoreGuiDocumentModified() noexcept")
        ]
        rollback_owner_check = rollback.index(
            "currentTransactionId != transactionId"
        )
        rollback_abort = rollback.index(
            "transaction && transaction->abort()"
        )
        self.assertLess(
            rollback_owner_check,
            rollback_abort,
        )
        self.assertNotIn("document->unlockTransaction()", rollback)

        commit = implementation[
            implementation.index("void ModelingTaskAttempt::commit()"):
            implementation.index(
                "\n}\n\n}  // namespace PartGui",
                implementation.index(
                    "void ModelingTaskAttempt::commit()"
                ),
            )
        ]
        self.assertIn(
            "d->document->getBookedTransactionID() != d->transactionId",
            commit,
        )
        self.assertIn(
            "!d->transaction || !d->transaction->commit()",
            commit,
        )
        self.assertLess(
            commit.index(
                "d->document->getBookedTransactionID() != d->transactionId"
            ),
            commit.index("!d->transaction || !d->transaction->commit()"),
        )
        self.assertIn("d->transaction.reset();", commit)
        self.assertNotIn("releaseTransactionLock", commit)
        self.assertNotIn("lockOwnedTransaction", commit)
        self.assertNotIn("App::GetApplication().commitTransaction", commit)
        self.assertNotIn("d->document->commitTransaction()", commit)
        self.assertNotIn("d->document->abortTransaction()", commit)


class TestRetainedPartDialogs(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("RetainedPartDialogs")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        names = {
            "RetainedPartDialogs",
            getattr(getattr(self, "document", None), "Name", ""),
        }
        for name in names:
            if name and name in App.listDocuments():
                App.closeDocument(name)
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _body_feature(self, body_name, feature_name, shape):
        body = self.document.addObject("PartDesign::Body", body_name)
        feature = body.newObject("PartDesign::Feature", feature_name)
        feature.Shape = shape
        body.Tip = feature
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        self.assertTrue(feature.isValid(), feature.getStatusString())
        self.assertFalse(feature.Shape.isNull())
        return body, feature

    def _task_button(self, standard_button):
        self._process_events()
        for button_box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            button = button_box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
        return None

    def _visible_widget(self, widget_type, object_name):
        return next(
            (
                widget
                for widget in Gui.getMainWindow().findChildren(
                    widget_type,
                    object_name,
                )
                if widget.isVisible()
            ),
            None,
        )

    def _close_task(self):
        button = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(60)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)

    def _dismiss_next_message(self):
        def dismiss():
            for widget in QtGui.QApplication.topLevelWidgets():
                if isinstance(widget, QtGui.QMessageBox) and widget.isVisible():
                    widget.accept()
                    return
            QtCore.QTimer.singleShot(10, dismiss)

        QtCore.QTimer.singleShot(0, dismiss)

    def _start_macro_recording(self, directory, name):
        def start():
            widgets = list(QtGui.QApplication.topLevelWidgets())
            main_window = Gui.getMainWindow()
            if main_window is not None:
                widgets.extend(main_window.findChildren(QtGui.QDialog))
            dialog = next(
                (
                    widget for widget in widgets
                    if widget.isVisible()
                    and (
                        widget.objectName()
                        == "Gui::Dialog::DlgMacroRecord"
                        or (
                            widget.findChild(
                                QtGui.QLineEdit,
                                "lineEditMacroPath",
                            )
                            is not None
                            and widget.findChild(
                                QtGui.QPushButton,
                                "buttonStart",
                            )
                            is not None
                        )
                    )
                ),
                None,
            )
            if dialog is None:
                QtCore.QTimer.singleShot(10, start)
                return
            path = dialog.findChild(QtGui.QLineEdit, "lineEditMacroPath")
            filename = dialog.findChild(QtGui.QLineEdit, "lineEditPath")
            button = dialog.findChild(QtGui.QPushButton, "buttonStart")
            self.assertIsNotNone(path)
            self.assertIsNotNone(filename)
            self.assertIsNotNone(button)
            path.setText(str(directory) + os.sep)
            filename.setText(name)
            button.click()

        QtCore.QTimer.singleShot(0, start)
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()

    def _stop_macro_recording(self, path):
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events()
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def _snapshot(self):
        objects = tuple(self.document.Objects)
        return (
            objects,
            tuple(
                (
                    body,
                    tuple(body.Group),
                    body.Tip,
                )
                for body in objects
                if body.TypeId == "PartDesign::Body"
            ),
            self.document.ActiveObject,
            tuple(
                (
                    selection.Object,
                    tuple(selection.SubElementNames),
                    tuple(
                        (point.x, point.y, point.z)
                        for point in selection.PickedPoints
                    ),
                )
                for selection in Gui.Selection.getSelectionEx()
            ),
            Gui.activeView().getActiveObject("pdbody"),
            tuple(
                (obj, bool(obj.ViewObject.Visibility))
                for obj in objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            bool(self.document.HasPendingTransaction),
            bool(Gui.activeDocument().Modified),
        )

    def _assert_snapshot(self, expected):
        self._process_events(60)
        self.assertEqual(self._snapshot(), expected)
        self.assertFalse(self.document.HasPendingTransaction)

    def _timeline_operations(self):
        timeline = self.document.getObject("VibeCADTimeline")
        return tuple(timeline.Operations) if timeline is not None else ()

    def _assert_body_result(self, body, source, result):
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        self.assertIs(result.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, result)
        self.assertIn(source, body.Group)
        self.assertIn(result, body.Group)
        self.assertFalse(
            [
                obj
                for obj in self.document.Objects
                if obj is result and obj.getParentGeoFeatureGroup() is None
            ]
        )

    def _assert_same_shape_geometry(self, actual, expected):
        self.assertFalse(actual.isNull())
        self.assertTrue(actual.isValid())
        self.assertEqual(len(actual.Solids), len(expected.Solids))
        self.assertAlmostEqual(actual.Volume, expected.Volume, places=7)
        self.assertAlmostEqual(actual.Area, expected.Area, places=7)
        for attribute in (
            "XMin",
            "XMax",
            "YMin",
            "YMax",
            "ZMin",
            "ZMax",
        ):
            self.assertAlmostEqual(
                getattr(actual.BoundBox, attribute),
                getattr(expected.BoundBox, attribute),
                places=7,
                msg=attribute,
            )

    def test_root_replaced_input_helper_drives_exact_marker_playback(self):
        import PartGui

        source = self.document.addObject(
            "Part::Feature",
            "TimelineReplacementSource",
        )
        source.Shape = Part.makeBox(10, 8, 6)
        result = self.document.addObject(
            "Part::Feature",
            "TimelineReplacementResult",
        )
        result.Shape = Part.makeBox(14, 8, 6)
        self.document.recompute()
        source_shape = source.Shape.exportBrepToString()
        result_shape = result.Shape.exportBrepToString()

        source.Visibility = False
        result.Visibility = True
        self.assertTrue(
            PartGui.setModelingReplacedInputs(result, [source])
        )
        self._process_events(60)

        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            result.getTypeIdOfProperty("VibeCADTimelineRole"),
            "App::PropertyString",
        )
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertEqual(
            result.getTypeIdOfProperty("VibeCADTimelineReplacedInputs"),
            "App::PropertyLinkListHidden",
        )

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        result_index = list(timeline.Operations).index(result)

        previous = self._visible_widget(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        self.assertIsNotNone(previous)
        self.assertTrue(previous.isEnabled())
        previous.click()
        self._process_events(60)
        self.assertEqual(timeline.Position, result_index)
        self.assertTrue(source.Visibility)
        self.assertFalse(result.Visibility)
        self.assertEqual(source.Shape.exportBrepToString(), source_shape)

        self.document.undo()
        self._process_events(60)
        self.assertEqual(timeline.Position, result_index + 1)
        self.assertFalse(source.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(result.Shape.exportBrepToString(), result_shape)

        self.document.redo()
        self._process_events(60)
        self.assertEqual(timeline.Position, result_index)
        self.assertTrue(source.Visibility)
        self.assertFalse(result.Visibility)

        next_button = self._visible_widget(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineNext",
        )
        self.assertIsNotNone(next_button)
        self.assertTrue(next_button.isEnabled())
        next_button.click()
        self._process_events(60)
        self.assertEqual(timeline.Position, result_index + 1)
        self.assertFalse(source.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(result.Shape.exportBrepToString(), result_shape)

    def test_boolean_apply_then_close_is_durable_with_and_without_undo(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                suffix = "Undo" if undo_enabled else "NoUndo"
                body, left = self._body_feature(
                    f"Boolean{suffix}Body",
                    f"Boolean{suffix}Left",
                    Part.makeBox(10, 10, 10),
                )
                right = body.newObject(
                    "PartDesign::Feature",
                    f"Boolean{suffix}Right",
                )
                right.Shape = Part.makeBox(
                    10,
                    10,
                    10,
                    App.Vector(5, 0, 0),
                )
                body.Tip = right
                self.document.recompute()
                Gui.activeView().setActiveObject("pdbody", body)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(left)
                Gui.Selection.addSelection(right)
                before = set(self.document.Objects)

                Gui.runCommand("Part_Boolean", 0)
                self._process_events(50)
                self.assertTrue(Gui.Control.activeDialog())
                union = self._visible_widget(
                    QtGui.QRadioButton,
                    "unionButton",
                )
                self.assertIsNotNone(union)
                union.setChecked(True)

                apply_button = self._task_button(
                    QtGui.QDialogButtonBox.Apply
                )
                self.assertIsNotNone(apply_button)
                apply_button.click()
                self._process_events(100)

                created = [
                    obj
                    for obj in self.document.Objects
                    if obj not in before
                ]
                self.assertEqual(
                    len(created),
                    1,
                    [(obj.Name, obj.TypeId) for obj in created],
                )
                self.assertEqual(created[0].TypeId, "Part::Fuse")
                self._assert_body_result(body, right, created[0])
                self.assertTrue(Gui.Control.activeDialog())
                self.assertFalse(self.document.HasPendingTransaction)

                self._close_task()
                self.assertIs(
                    self.document.getObject(created[0].Name),
                    created[0],
                )
                self.assertIs(body.Tip, created[0])

    def test_boolean_invalid_result_aborts_without_any_interaction_junk(self):
        from BOPTools import BOPFeatures

        self.document.UndoMode = False
        body, left = self._body_feature(
            "InvalidBooleanBody",
            "InvalidBooleanLeft",
            Part.makeBox(10, 10, 10),
        )
        right = body.newObject(
            "PartDesign::Feature",
            "InvalidBooleanRight",
        )
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        body.Tip = right
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(left)
        Gui.Selection.addSelection(right)

        Gui.runCommand("Part_Boolean", 0)
        self._process_events(50)
        union = self._visible_widget(QtGui.QRadioButton, "unionButton")
        self.assertIsNotNone(union)
        union.setChecked(True)
        expected = self._snapshot()
        history_before = self._timeline_operations()

        original_make_fuse = BOPFeatures.BOPFeatures.make_fuse

        def make_invalid_result(features, input_names):
            inputs = [features.doc.getObject(name) for name in input_names]
            result = features.doc.addObject(
                "Part::Feature",
                "InvalidBooleanResult",
            )
            result.Shape = Part.Shape()
            extra = features.doc.addObject(
                "Part::Feature",
                "UnexpectedBooleanResult",
            )
            extra.Shape = Part.makeBox(1, 1, 1)
            owner = features.common_input_owner(inputs)
            features.add_result_to_target(owner, result)
            features.add_result_to_target(owner, extra)
            return result

        BOPFeatures.BOPFeatures.make_fuse = make_invalid_result
        try:
            apply_button = self._task_button(
                QtGui.QDialogButtonBox.Apply
            )
            self.assertIsNotNone(apply_button)
            apply_button.click()
            self._process_events(100)
        finally:
            BOPFeatures.BOPFeatures.make_fuse = original_make_fuse

        self.assertTrue(Gui.Control.activeDialog())
        self._assert_snapshot(expected)
        self.assertEqual(self._timeline_operations(), history_before)
        self._close_task()
        self.assertEqual(self._timeline_operations(), history_before)

    def test_boolean_groups_linked_outputs_as_one_body_operation(self):
        from BOPTools import BOPFeatures

        self.document.UndoMode = False
        body, left = self._body_feature(
            "ExactBooleanBody",
            "ExactBooleanLeft",
            Part.makeBox(10, 10, 10),
        )
        right = body.newObject(
            "PartDesign::Feature",
            "ExactBooleanRight",
        )
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        body.Tip = right
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(left)
        Gui.Selection.addSelection(right)

        Gui.runCommand("Part_Boolean", 0)
        self._process_events(50)
        union = self._visible_widget(QtGui.QRadioButton, "unionButton")
        self.assertIsNotNone(union)
        union.setChecked(True)

        original_make_fuse = BOPFeatures.BOPFeatures.make_fuse

        def make_result_with_linked_resource(features, input_names):
            inputs = [
                features.doc.getObject(name) for name in input_names
            ]
            resource = features.doc.addObject(
                "Part::Feature",
                "LinkedBooleanResource",
            )
            resource.addProperty("App::PropertyLink", "Source")
            resource.Source = inputs[0]
            resource.Shape = Part.makeBox(
                1,
                1,
                1,
                App.Vector(30, 0, 0),
            )

            result = features.doc.addObject(
                "Part::Feature",
                "ExactBooleanResult",
            )
            result.addProperty("App::PropertyLink", "Auxiliary")
            result.Auxiliary = resource
            result.Shape = inputs[0].Shape.fuse(inputs[1].Shape)
            return result

        BOPFeatures.BOPFeatures.make_fuse = make_result_with_linked_resource
        try:
            apply_button = self._task_button(
                QtGui.QDialogButtonBox.Apply
            )
            self.assertIsNotNone(apply_button)
            apply_button.click()
            self._process_events(100)
        finally:
            BOPFeatures.BOPFeatures.make_fuse = original_make_fuse

        result = self.document.getObject("ExactBooleanResult")
        resource = self.document.getObject("LinkedBooleanResource")
        self.assertIsNotNone(result)
        self.assertIsNotNone(resource)
        self.assertIs(result.getParentGeoFeatureGroup(), body)
        self.assertIs(resource.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, result)
        self.assertEqual(
            list(body.Group[-2:]),
            [resource, result],
        )
        self.assertIs(result.Auxiliary, resource)

        operations = self._timeline_operations()
        self.assertEqual(list(operations[-2:]), [resource, result])
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(resource.VibeCADTimelineRole, "resource")
        self.assertIs(resource.VibeCADTimelineOwner, result)
        self.assertFalse(self.document.HasPendingTransaction)
        self._close_task()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "linked-output.FCStd"
            self.document.saveAs(str(path))
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(str(path))

            body = self.document.getObject("ExactBooleanBody")
            resource = self.document.getObject("LinkedBooleanResource")
            result = self.document.getObject("ExactBooleanResult")
            self.assertIsNotNone(body)
            self.assertIsNotNone(resource)
            self.assertIsNotNone(result)
            self.assertIs(resource.getParentGeoFeatureGroup(), body)
            self.assertIs(result.getParentGeoFeatureGroup(), body)
            self.assertIs(body.Tip, result)
            self.assertEqual(
                list(body.Group[-2:]),
                [resource, result],
            )
            self.assertEqual(
                list(self._timeline_operations()[-2:]),
                [resource, result],
            )
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, result)
            self.assertEqual(result.VibeCADTimelineRole, "operation")

    def test_boolean_cancel_without_apply_publishes_no_history(self):
        body, left = self._body_feature(
            "CancelledBooleanBody",
            "CancelledBooleanLeft",
            Part.makeBox(10, 10, 10),
        )
        right = body.newObject(
            "PartDesign::Feature",
            "CancelledBooleanRight",
        )
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        body.Tip = right
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(left)
        Gui.Selection.addSelection(right)
        objects_before = tuple(self.document.Objects)
        history_before = self._timeline_operations()

        Gui.runCommand("Part_Boolean", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        self._close_task()

        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self._timeline_operations(), history_before)
        self.assertIs(body.Tip, right)

    def test_shape_builder_create_is_durable_without_undo(self):
        self.document.UndoMode = False
        body, source = self._body_feature(
            "ShapeBuilderBody",
            "ShapeBuilderSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.runCommand("Part_Builder", 0)
        self._process_events(50)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Vertex1")
        Gui.Selection.addSelection(source, "Vertex2")
        before = set(self.document.Objects)

        create = self._visible_widget(QtGui.QPushButton, "createButton")
        self.assertIsNotNone(create)
        create.click()
        self._process_events(100)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in before
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].Shape.ShapeType, "Edge")
        self._assert_body_result(body, source, created[0])
        self.assertFalse(self.document.HasPendingTransaction)

        self._close_task()
        self.assertIs(body.Tip, created[0])

    def test_shape_builder_invalid_selection_aborts_exactly(self):
        self.document.UndoMode = False
        body, source = self._body_feature(
            "InvalidShapeBuilderBody",
            "InvalidShapeBuilderSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.runCommand("Part_Builder", 0)
        self._process_events(50)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Vertex1")
        expected = self._snapshot()

        create = self._visible_widget(QtGui.QPushButton, "createButton")
        self.assertIsNotNone(create)
        self._dismiss_next_message()
        create.click()
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self._assert_snapshot(expected)
        self.assertIs(Gui.activeView().getActiveObject("pdbody"), body)
        self._close_task()

    def test_primitive_create_is_durable_without_undo(self):
        self.document.UndoMode = False
        body, source = self._body_feature(
            "PrimitiveBody",
            "PrimitiveSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.runCommand("Part_Primitives", 0)
        self._process_events(50)
        selector = self._visible_widget(
            QtGui.QComboBox,
            "PrimitiveTypeCB",
        )
        self.assertIsNotNone(selector)
        self.assertEqual(selector.currentText(), "Plane")
        before = set(self.document.Objects)

        create = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(create)
        create.click()
        self._process_events(100)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in before
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].TypeId, "Part::Plane")
        self._assert_body_result(body, source, created[0])
        self.assertTrue(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)

        self._close_task()
        self.assertIs(body.Tip, created[0])

    def test_multi_copy_does_not_adopt_a_creation_callback_distractor(self):
        first = self.document.addObject(
            "Part::Feature",
            "ExactCopyFirst",
        )
        first.Shape = Part.makeBox(5, 6, 7)
        second = self.document.addObject(
            "Part::Feature",
            "ExactCopySecond",
        )
        second.Shape = Part.makeCylinder(3, 8, App.Vector(12, 0, 0))
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        Gui.Selection.addSelection(second)
        before = tuple(self.document.Objects)
        document = self.document

        class SameTransactionDistractor:
            def __init__(self):
                self.injected = False
                self.distractor = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj in before
                    or obj.TypeId != "Part::Feature"
                ):
                    return
                self.injected = True
                self.distractor = document.addObject(
                    "Part::Feature",
                    "SameTransactionCopyDistractor",
                )
                self.distractor.Shape = Part.makeSphere(
                    1,
                    App.Vector(30, 0, 0),
                )

        observer = SameTransactionDistractor()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("Part_SimpleCopy", 0)
            self._process_events(100)
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.distractor)
        created_features = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Part::Feature"
        ]
        self.assertEqual(len(created_features), 3)
        results = [
            obj
            for obj in created_features
            if obj is not observer.distractor
        ]
        self.assertEqual(len(results), 2)

        resources = [
            obj
            for obj in results
            if getattr(obj, "VibeCADTimelineRole", "")
            == "resource"
        ]
        operations = [
            obj
            for obj in results
            if getattr(obj, "VibeCADTimelineRole", "")
            == "operation"
        ]
        self.assertEqual(len(resources), 1)
        self.assertEqual(len(operations), 1)
        self.assertIs(resources[0].VibeCADTimelineOwner, operations[0])
        self.assertNotEqual(
            getattr(observer.distractor, "VibeCADTimelineRole", ""),
            "resource",
        )
        self.assertIsNot(
            getattr(
                observer.distractor,
                "VibeCADTimelineOwner",
                None,
            ),
            operations[0],
        )

        created_names = tuple(obj.Name for obj in created_features)
        self.document.undo()
        self._process_events(80)
        for name in created_names:
            self.assertIsNone(self.document.getObject(name), name)
        self.assertIn(first, self.document.Objects)
        self.assertIn(second, self.document.Objects)

    def test_design_definition_block_never_enters_the_active_body(self):
        body = self.document.addObject(
            "PartDesign::Body",
            "DefinitionIsolationBody",
        )
        first = body.newObject(
            "PartDesign::Feature",
            "DefinitionIsolationFirst",
        )
        first.Shape = Part.makeBox(5, 6, 7)
        second = body.newObject(
            "PartDesign::Feature",
            "DefinitionIsolationSecond",
        )
        second.Shape = Part.makeCylinder(
            3,
            8,
            App.Vector(12, 0, 0),
        )
        body.Tip = second
        self.document.recompute()
        original_group = tuple(body.Group)
        original_tip = body.Tip

        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        Gui.Selection.addSelection(second)
        before = set(self.document.Objects)
        Gui.runCommand("Part_SimpleCopy", 0)
        self._process_events(100)

        results = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Part::Feature"
        ]
        self.assertEqual(len(results), 2)
        operation = next(
            obj
            for obj in results
            if obj.VibeCADTimelineRole == "operation"
        )
        resource = next(
            obj
            for obj in results
            if obj.VibeCADTimelineRole == "resource"
        )
        self.assertIs(resource.VibeCADTimelineOwner, operation)
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertIsNone(resource.getParentGeoFeatureGroup())
        self.assertTrue(str(operation.VibeCADDefinitionId))
        self.assertTrue(str(operation.DesignId))
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)

        created_names = tuple(obj.Name for obj in results)
        self.document.undo()
        self._process_events(80)
        for name in created_names:
            self.assertIsNone(self.document.getObject(name), name)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)

    def test_immediate_part_tools_publish_global_design_definitions(self):
        def assert_definition(result, bodies):
            self._process_events(80)
            self.document.recompute()
            self.assertIsNotNone(result)
            self.assertIsNone(result.getParentGeoFeatureGroup())
            self.assertEqual(result.VibeCADTimelineRole, "operation")
            self.assertTrue(str(result.VibeCADDefinitionId))
            self.assertTrue(str(result.DesignId))
            for body, original_group, original_tip in bodies:
                self.assertEqual(tuple(body.Group), original_group)
                self.assertIs(body.Tip, original_tip)

        reverse_body, reverse_source = self._body_feature(
            "GlobalReverseBody",
            "GlobalReverseSource",
            Part.makeBox(5, 6, 7),
        )
        reverse_state = (
            reverse_body,
            tuple(reverse_body.Group),
            reverse_body.Tip,
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(reverse_source)
        Gui.runCommand("Part_ReverseShape", 0)
        assert_definition(self.document.ActiveObject, [reverse_state])

        compound_body, compound_source = self._body_feature(
            "GlobalCompoundBody",
            "GlobalCompoundSource",
            Part.makeBox(4, 5, 6, App.Vector(12, 0, 0)),
        )
        compound_tool_body, compound_tool = self._body_feature(
            "GlobalCompoundToolBody",
            "GlobalCompoundTool",
            Part.makeCylinder(2, 7, App.Vector(20, 0, 0)),
        )
        compound_states = [
            (
                compound_body,
                tuple(compound_body.Group),
                compound_body.Tip,
            ),
            (
                compound_tool_body,
                tuple(compound_tool_body.Group),
                compound_tool_body.Tip,
            ),
        ]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(compound_source)
        Gui.Selection.addSelection(compound_tool)
        Gui.runCommand("Part_Compound", 0)
        assert_definition(self.document.ActiveObject, compound_states)

        section_body, section_source = self._body_feature(
            "GlobalSectionBody",
            "GlobalSectionSource",
            Part.makeBox(8, 8, 8, App.Vector(30, 0, 0)),
        )
        section_tool_body, section_tool = self._body_feature(
            "GlobalSectionToolBody",
            "GlobalSectionTool",
            Part.makeBox(8, 8, 8, App.Vector(34, 0, 0)),
        )
        section_states = [
            (
                section_body,
                tuple(section_body.Group),
                section_body.Tip,
            ),
            (
                section_tool_body,
                tuple(section_tool_body.Group),
                section_tool_body.Tip,
            ),
        ]
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(section_source)
        Gui.Selection.addSelection(section_tool)
        Gui.runCommand("Part_Section", 0)
        assert_definition(self.document.ActiveObject, section_states)

    def test_primitive_attempt_rejects_reentrant_transaction_operations(self):
        """Creation callbacks cannot steal or close the modeling attempt."""

        body, source = self._body_feature(
            "ReentrantPrimitiveBody",
            "ReentrantPrimitiveSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.runCommand("Part_Primitives", 0)
        self._process_events(50)
        before = set(self.document.Objects)
        document = self.document

        class ReentrantTransactionObserver:
            def __init__(self):
                self.observed = None
                self.application_replacement = None
                self.error = None

            def slotCreatedObject(self, obj):
                if (
                    self.observed is not None
                    or self.error is not None
                    or obj.Document.Name != document.Name
                    or obj in before
                ):
                    return
                try:
                    transaction_id = document.getBookedTransactionID()
                    observed = [transaction_id]

                    document.openTransaction(
                        "Reentrant primitive replacement"
                    )
                    observed.append(document.getBookedTransactionID())

                    self.application_replacement = App.setActiveTransaction(
                        "Reentrant primitive application replacement"
                    )
                    observed.append(document.getBookedTransactionID())

                    document.commitTransaction()
                    observed.append(document.getBookedTransactionID())

                    document.abortTransaction()
                    observed.append(document.getBookedTransactionID())
                    self.observed = tuple(observed)
                except Exception as error:  # pragma: no cover - diagnostic
                    self.error = error

        observer = ReentrantTransactionObserver()
        App.addDocumentObserver(observer)
        try:
            create = self._task_button(QtGui.QDialogButtonBox.Ok)
            self.assertIsNotNone(create)
            create.click()
            self._process_events(100)
        finally:
            App.removeDocumentObserver(observer)

        self.assertIsNone(observer.error)
        self.assertIsNotNone(observer.observed)
        transaction_id = observer.observed[0]
        self.assertNotEqual(0, transaction_id)
        self.assertEqual(
            (transaction_id,) * len(observer.observed),
            observer.observed,
        )
        self.assertEqual(0, observer.application_replacement)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in before
        ]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].TypeId, "Part::Plane")
        self._assert_body_result(body, source, created[0])
        self.assertTrue(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())
        self._close_task()

    def test_primitive_prefers_active_body_over_nested_app_part(self):
        assembly = self.document.addObject("App::Part", "PrimitiveAssembly")
        body = self.document.addObject("PartDesign::Body", "NestedPrimitiveBody")
        assembly.addObject(body)
        source = body.newObject("PartDesign::Feature", "NestedPrimitiveSource")
        source.Shape = Part.makeBox(10, 10, 10)
        body.Tip = source
        self.document.recompute()
        Gui.activeView().setActiveObject("part", assembly)
        Gui.activeView().setActiveObject("pdbody", body)
        original_objects = set(self.document.Objects)

        Gui.runCommand("Part_Primitives", 0)
        self._process_events(50)
        self._task_button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events(100)

        created = [
            obj for obj in self.document.Objects if obj not in original_objects
        ]
        self.assertEqual(
            len(created),
            1,
            [(obj.Name, obj.TypeId) for obj in created],
        )
        result = created[0]
        self.assertIs(result.getParentGeoFeatureGroup(), body)
        self.assertIn(result, body.Group)
        self.assertEqual(body.Group.count(result), 1)
        self.assertIs(body.Tip, result)
        self.assertNotIn(result, assembly.Group)
        self.assertFalse(self.document.HasPendingTransaction)
        self._close_task()

    def test_primitive_failed_edit_and_cancel_restore_exact_existing_state(self):
        body, _source = self._body_feature(
            "PrimitiveEditBody",
            "PrimitiveEditSource",
            Part.makeBox(4, 4, 4),
        )
        primitive = self.document.addObject("Part::Box", "EditedPrimitive")
        primitive.Length = 12.0
        primitive.Width = 8.0
        primitive.Height = 5.0
        primitive.Placement.Base = App.Vector(1, 2, 3)
        body.addObject(primitive)
        body.Tip = primitive
        self.document.recompute()
        self.document.UndoMode = False
        original_objects = tuple(self.document.Objects)
        original_group = tuple(body.Group)
        original_tip = body.Tip
        original_values = (
            primitive.Length.Value,
            primitive.Width.Value,
            primitive.Height.Value,
            tuple(primitive.Placement.Base),
            primitive.Shape.copy(),
        )
        Gui.activeDocument().Modified = False

        Gui.activeDocument().setEdit(primitive.Name)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        length = self._visible_widget(QtGui.QWidget, "boxLength")
        position = self._visible_widget(QtGui.QWidget, "XPositionQSB")
        self.assertIsNotNone(length)
        self.assertIsNotNone(position)
        length.setValue(0.0)
        position.setValue(99.0)
        self._dismiss_next_message()
        self._task_button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(primitive.Name), primitive)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self.assertAlmostEqual(primitive.Length.Value, original_values[0])
        self.assertAlmostEqual(primitive.Width.Value, original_values[1])
        self.assertAlmostEqual(primitive.Height.Value, original_values[2])
        self.assertEqual(tuple(primitive.Placement.Base), original_values[3])
        # Recompute may replace OCC's internal TShape identity. The durable
        # contract is the same object, parameters, placement, and geometry.
        self._assert_same_shape_geometry(
            primitive.Shape,
            original_values[4],
        )
        rollback_journal = (
            bool(self.document.HasPendingTransaction),
            bool(self.document.UndoMode),
        )
        # The replacement journal is booked but remains empty until the user
        # makes the next correction, so no undo transaction is materialized.
        self.assertEqual(
            rollback_journal,
            (False, True),
            rollback_journal,
        )
        self.assertFalse(Gui.activeDocument().Modified)

        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(80)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(primitive.Name), primitive)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self._assert_same_shape_geometry(
            primitive.Shape,
            original_values[4],
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(self.document.UndoMode)
        self.assertFalse(Gui.activeDocument().Modified)

    def test_projection_edit_cancel_restores_properties_without_undo(self):
        projection = self.document.addObject(
            "Part::ProjectOnSurface",
            "EditedProjection",
        )
        projection.Direction = App.Vector(0, 0, 1)
        projection.Height = 3.0
        projection.Offset = 0.25
        self.document.recompute()
        self.document.UndoMode = False
        original_objects = tuple(self.document.Objects)
        original_direction = tuple(projection.Direction)
        original_height = projection.Height.Value
        original_offset = projection.Offset.Value
        Gui.activeDocument().Modified = False

        Gui.activeDocument().setEdit(projection.Name)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        projection.Direction = App.Vector(1, 0, 0)
        projection.Height = 17.0
        projection.Offset = 2.0
        self.document.recompute()

        cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(cancel)
        cancel.click()
        self._process_events(80)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(projection.Name), projection)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(tuple(projection.Direction), original_direction)
        self.assertAlmostEqual(projection.Height.Value, original_height)
        self.assertAlmostEqual(projection.Offset.Value, original_offset)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(self.document.UndoMode)
        self.assertFalse(Gui.activeDocument().Modified)

    def test_primitive_invalid_shape_aborts_without_any_junk(self):
        self.document.UndoMode = False
        body, _source = self._body_feature(
            "InvalidPrimitiveBody",
            "InvalidPrimitiveSource",
            Part.makeBox(10, 10, 10),
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(body)
        Gui.activeDocument().Modified = False
        Gui.runCommand("Part_Primitives", 0)
        self._process_events(50)
        length = self._visible_widget(QtGui.QWidget, "planeLength")
        self.assertIsNotNone(length)
        length.setValue(0.0)
        self._process_events()
        expected = self._snapshot()

        create = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(create)
        self._dismiss_next_message()
        create.click()
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self._assert_snapshot(expected)
        self.assertIs(Gui.activeView().getActiveObject("pdbody"), body)
        self._close_task()

    def test_primitive_rejected_second_create_preserves_first_result_and_trace(self):
        self.document.UndoMode = False
        body, source = self._body_feature(
            "PrimitiveRetryBody",
            "PrimitiveRetrySource",
            Part.makeBox(8, 8, 8),
        )

        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            macro_path = temporary / "PrimitiveRetry.FCMacro"
            self._start_macro_recording(temporary, "PrimitiveRetry")

            original_objects = tuple(self.document.Objects)
            Gui.runCommand("Part_Primitives", 0)
            self._process_events()
            create = self._task_button(QtGui.QDialogButtonBox.Ok)
            self.assertIsNotNone(create)
            create.click()
            self._process_events(80)

            created = [
                obj
                for obj in self.document.Objects
                if obj not in original_objects
            ]
            self.assertEqual(len(created), 1)
            accepted = created[0]
            self.assertEqual(accepted.TypeId, "Part::Plane")
            self.assertTrue(Gui.Control.activeDialog())
            self._assert_body_result(body, source, accepted)

            objects_after_accept = tuple(self.document.Objects)
            self._visible_widget(
                QtGui.QWidget,
                "planeLength",
            ).setValue(0.0)
            self._dismiss_next_message()
            create.click()
            self._process_events(80)

            self.assertTrue(Gui.Control.activeDialog())
            self.assertEqual(tuple(self.document.Objects), objects_after_accept)
            self.assertIs(body.Tip, accepted)
            self._close_task()
            self.assertIs(self.document.getObject(accepted.Name), accepted)
            self.assertIs(body.Tip, accepted)

            macro = self._stop_macro_recording(macro_path)

        primitive_trace = re.compile(
            r"addObject\((['\"])Part::Plane\1\s*,"
        )
        self.assertEqual(len(primitive_trace.findall(macro)), 1, macro)

    def test_macros_publish_only_accepted_boolean_builder_and_primitive_attempts(self):
        from BOPTools import BOPFeatures

        self.document.UndoMode = False
        body, left = self._body_feature(
            "MacroBody",
            "MacroLeft",
            Part.makeBox(10, 10, 10),
        )
        right = body.newObject("PartDesign::Feature", "MacroRight")
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        body.Tip = right
        self.document.recompute()

        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            rejected_path = temporary / "RejectedAttempts.FCMacro"
            self._start_macro_recording(temporary, "RejectedAttempts")

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(left)
            Gui.Selection.addSelection(right)
            Gui.runCommand("Part_Boolean", 0)
            self._process_events()
            union = self._visible_widget(QtGui.QRadioButton, "unionButton")
            self.assertIsNotNone(union)
            union.setChecked(True)
            original_make_fuse = BOPFeatures.BOPFeatures.make_fuse

            def make_invalid_result(features, input_names):
                inputs = [
                    features.doc.getObject(name)
                    for name in input_names
                ]
                result = features.doc.addObject(
                    "Part::Feature",
                    "RejectedMacroBoolean",
                )
                result.Shape = Part.Shape()
                features.add_result_to_target(
                    features.common_input_owner(inputs),
                    result,
                )
                return result

            BOPFeatures.BOPFeatures.make_fuse = make_invalid_result
            try:
                self._dismiss_next_message()
                self._task_button(
                    QtGui.QDialogButtonBox.Apply
                ).click()
                self._process_events(80)
            finally:
                BOPFeatures.BOPFeatures.make_fuse = original_make_fuse
            self._close_task()

            Gui.runCommand("Part_Builder", 0)
            self._process_events()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(left, "Vertex1")
            self._dismiss_next_message()
            self._visible_widget(
                QtGui.QPushButton,
                "createButton",
            ).click()
            self._process_events(80)
            self._close_task()

            Gui.runCommand("Part_Primitives", 0)
            self._process_events()
            self._visible_widget(
                QtGui.QWidget,
                "planeLength",
            ).setValue(0.0)
            self._dismiss_next_message()
            self._task_button(QtGui.QDialogButtonBox.Ok).click()
            self._process_events(80)
            self._close_task()

            rejected_macro = self._stop_macro_recording(rejected_path)
            self.assertNotIn(".make_fuse(", rejected_macro)
            self.assertNotIn("Part.makeLine", rejected_macro)
            self.assertIsNone(
                re.search(
                    r"addObject\((['\"])Part::Plane\1\s*,",
                    rejected_macro,
                )
            )

            accepted_path = temporary / "AcceptedAttempts.FCMacro"
            self._start_macro_recording(temporary, "AcceptedAttempts")

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(left)
            Gui.Selection.addSelection(right)
            Gui.runCommand("Part_Boolean", 0)
            self._process_events()
            self._visible_widget(
                QtGui.QRadioButton,
                "unionButton",
            ).setChecked(True)
            self._task_button(QtGui.QDialogButtonBox.Apply).click()
            self._process_events(80)
            fuse = body.Tip
            self._close_task()

            Gui.runCommand("Part_Builder", 0)
            self._process_events()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fuse, "Vertex1")
            Gui.Selection.addSelection(fuse, "Vertex2")
            self._visible_widget(
                QtGui.QPushButton,
                "createButton",
            ).click()
            self._process_events(80)
            edge = body.Tip
            self._close_task()

            Gui.runCommand("Part_Primitives", 0)
            self._process_events()
            objects_before_primitive = tuple(self.document.Objects)
            self._task_button(QtGui.QDialogButtonBox.Ok).click()
            self._process_events(80)
            created_primitives = [
                obj
                for obj in self.document.Objects
                if obj not in objects_before_primitive
            ]
            self.assertEqual(
                [(obj.Name, obj.TypeId) for obj in created_primitives],
                [("Plane", "Part::Plane")],
            )
            plane = created_primitives[0]
            self.assertTrue(Gui.Control.activeDialog())
            self._close_task()
            self.assertIs(self.document.getObject(plane.Name), plane)
            self.assertIs(body.Tip, plane)

            accepted_macro = self._stop_macro_recording(accepted_path)
            self.assertIn(".make_fuse(", accepted_macro)
            self.assertIn("Part.makeLine", accepted_macro)
            primitive_trace = re.search(
                r"addObject\((['\"])Part::Plane\1\s*,",
                accepted_macro,
            )
            self.assertIsNotNone(primitive_trace)
            self.assertLess(
                accepted_macro.index(".make_fuse("),
                accepted_macro.index("Part.makeLine"),
            )
            self.assertLess(
                accepted_macro.index("Part.makeLine"),
                primitive_trace.start(),
            )
            self._assert_body_result(body, edge, plane)
            self.assertFalse(fuse.Shape.isNull())

    def test_offset_invalid_accept_stays_retryable_and_cancel_is_exact(self):
        sources = (
            (
                "Part_Offset",
                "RetryOffsetSource",
                Part.makeBox(12, 10, 8),
            ),
            (
                "Part_Offset2D",
                "RetryOffset2DSource",
                Part.makePolygon(
                    [
                        App.Vector(0, 0, 0),
                        App.Vector(8, 0, 0),
                        App.Vector(8, 6, 0),
                        App.Vector(0, 6, 0),
                        App.Vector(0, 0, 0),
                    ]
                ),
            ),
        )
        for command_name, source_name, shape in sources:
            with self.subTest(command=command_name):
                source = self.document.addObject("Part::Feature", source_name)
                source.Shape = shape
                self.document.recompute()
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(source)
                self._process_events()
                expected_cancel = self._snapshot()
                undo_before = self.document.UndoCount

                Gui.runCommand(command_name, 0)
                self._process_events(60)
                self.assertTrue(Gui.Control.activeDialog())
                preview = Gui.activeDocument().getInEdit().Object
                self.assertIsNotNone(preview)
                preview_name = preview.Name
                preview_id = preview.ID
                transaction_id = self.document.getBookedTransactionID()
                self.assertNotEqual(transaction_id, 0)
                self.assertIs(
                    Gui.activeDocument().getInEdit().Object,
                    preview,
                )

                preview.Source = None
                self.document.recompute()
                self._dismiss_next_message()
                self._task_button(QtGui.QDialogButtonBox.Ok).click()
                self._process_events(80)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertIs(self.document.getObject(preview_name), preview)
                self.assertEqual(preview.ID, preview_id)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    transaction_id,
                )
                self.assertIs(
                    Gui.activeDocument().getInEdit().Object,
                    preview,
                )
                # UndoCount includes the one live journal.  A failed Accept
                # must retain that same journal for correction, not close it
                # or open another one.
                self.assertEqual(self.document.UndoCount, undo_before + 1)

                preview.Source = source
                self.document.recompute()
                self._task_button(QtGui.QDialogButtonBox.Ok).click()
                self._process_events(100)

                self.assertFalse(Gui.Control.activeDialog())
                self.assertFalse(self.document.HasPendingTransaction)
                self.assertIs(self.document.getObject(preview_name), preview)
                self.assertTrue(preview.isValid(), preview.getStatusString())
                self.assertFalse(preview.Shape.isNull())
                self.assertTrue(preview.Shape.isValid())
                self.assertEqual(self.document.UndoCount, undo_before + 1)

                self.document.undo()
                self._process_events(80)
                self.assertIsNone(self.document.getObject(preview_name))
                self.assertFalse(self.document.HasPendingTransaction)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(source)
                self._process_events()
                expected_cancel = self._snapshot()
                undo_before = self.document.UndoCount

                Gui.runCommand(command_name, 0)
                self._process_events(60)
                cancelled_preview = self.document.ActiveObject
                cancelled_preview.Source = None
                self.document.recompute()
                self._dismiss_next_message()
                self._task_button(QtGui.QDialogButtonBox.Ok).click()
                self._process_events(80)
                self.assertTrue(Gui.Control.activeDialog())

                self._task_button(QtGui.QDialogButtonBox.Cancel).click()
                self._process_events(100)
                self._assert_snapshot(expected_cancel)
                self.assertEqual(self.document.UndoCount, undo_before)

    def test_offset_cancel_stress_preserves_exact_state_with_and_without_undo(self):
        solid = self.document.addObject("Part::Feature", "StressOffsetSource")
        solid.Shape = Part.makeBox(12, 10, 8)
        wire = self.document.addObject("Part::Feature", "StressOffset2DSource")
        wire.Shape = Part.makePolygon(
            [
                App.Vector(0, 0, 0),
                App.Vector(8, 0, 0),
                App.Vector(8, 6, 0),
                App.Vector(0, 6, 0),
                App.Vector(0, 0, 0),
            ]
        )
        self.document.recompute()

        for undo_enabled in (True, False):
            self.document.UndoMode = undo_enabled
            for command_name, source in (
                ("Part_Offset", solid),
                ("Part_Offset2D", wire),
            ):
                for iteration in range(25):
                    with self.subTest(
                        undo_enabled=undo_enabled,
                        command=command_name,
                        iteration=iteration,
                    ):
                        Gui.Selection.clearSelection()
                        Gui.Selection.addSelection(source)
                        self._process_events(5)
                        expected = self._snapshot()
                        undo_before = self.document.UndoCount

                        Gui.runCommand(command_name, 0)
                        self._process_events(10)
                        self.assertTrue(Gui.Control.activeDialog())
                        self.assertIsNotNone(Gui.activeDocument().getInEdit())
                        self._task_button(
                            QtGui.QDialogButtonBox.Cancel
                        ).click()
                        self._process_events(10)

                        self.assertFalse(Gui.Control.activeDialog())
                        self.assertIsNone(Gui.activeDocument().getInEdit())
                        self.assertEqual(self._snapshot(), expected)
                        self.assertEqual(self.document.UndoCount, undo_before)
                        self.assertEqual(
                            self.document.getBookedTransactionID(),
                            0,
                        )

    def test_thickness_invalid_accept_retries_and_cancel_stress_is_exact(self):
        source = self.document.addObject("Part::Feature", "ThicknessLifecycleSource")
        source.Shape = Part.makeBox(12, 10, 8)
        self.document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        self._process_events()
        undo_before = self.document.UndoCount
        Gui.runCommand("Part_Thickness", 0)
        self._process_events(60)
        self.assertTrue(Gui.Control.activeDialog())
        preview = self.document.ActiveObject
        preview_name = preview.Name
        preview_id = preview.ID
        transaction_id = self.document.getBookedTransactionID()

        preview.Faces = None
        self.document.recompute()
        self._dismiss_next_message()
        self._task_button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events(80)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(preview_name), preview)
        self.assertEqual(preview.ID, preview_id)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction_id,
        )
        self.assertIs(Gui.activeDocument().getInEdit().Object, preview)

        preview.Faces = (source, ["Face1"])
        self.document.recompute()
        self._task_button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events(100)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertTrue(preview.isValid(), preview.getStatusString())
        self.assertFalse(preview.Shape.isNull())
        self.assertTrue(preview.Shape.isValid())
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.undo()
        self._process_events(80)
        self.assertIsNone(self.document.getObject(preview_name))
        self.assertFalse(self.document.HasPendingTransaction)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        self._process_events()

        for undo_enabled in (True, False):
            self.document.UndoMode = undo_enabled
            for iteration in range(10):
                with self.subTest(
                    undo_enabled=undo_enabled,
                    iteration=iteration,
                ):
                    Gui.Selection.clearSelection()
                    Gui.Selection.addSelection(source, "Face1")
                    self._process_events(5)
                    iteration_expected = self._snapshot()
                    iteration_undo = self.document.UndoCount
                    Gui.runCommand("Part_Thickness", 0)
                    self._process_events(10)
                    self.assertTrue(Gui.Control.activeDialog())
                    self._task_button(
                        QtGui.QDialogButtonBox.Cancel
                    ).click()
                    self._process_events(10)
                    self.assertFalse(Gui.Control.activeDialog())
                    self.assertIsNone(Gui.activeDocument().getInEdit())
                    self.assertEqual(self._snapshot(), iteration_expected)
                    self.assertEqual(
                        self.document.UndoCount,
                        iteration_undo,
                    )
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        0,
                    )

    def test_retained_part_tasks_refuse_caller_owned_transactions(self):
        """A retained Part task must never borrow somebody else's transaction."""

        _solid_body, solid = self._body_feature(
            "CallerSolidBody",
            "CallerSolid",
            Part.makeBox(12, 10, 8),
        )
        _second_body, second_solid = self._body_feature(
            "CallerSecondSolidBody",
            "CallerSecondSolid",
            Part.makeBox(10, 10, 10, App.Vector(5, 0, 0)),
        )
        _wire_body, wire = self._body_feature(
            "CallerWireBody",
            "CallerWire",
            Part.makePolygon(
                [
                    App.Vector(0, 0, 0),
                    App.Vector(6, 0, 0),
                    App.Vector(6, 6, 0),
                    App.Vector(0, 6, 0),
                    App.Vector(0, 0, 0),
                ]
            ),
        )
        _second_wire_body, second_wire = self._body_feature(
            "CallerSecondWireBody",
            "CallerSecondWire",
            Part.makePolygon(
                [
                    App.Vector(0, 0, 8),
                    App.Vector(5, 0, 8),
                    App.Vector(5, 5, 8),
                    App.Vector(0, 5, 8),
                    App.Vector(0, 0, 8),
                ]
            ),
        )
        probe = self.document.addObject(
            "Part::Feature",
            "CallerTransactionProbe",
        )
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "outside"
        probe.Shape = Part.makeBox(1, 2, 3)
        self.document.recompute()

        cases = (
            ("Part_Tube", 0, (solid,)),
            ("Part_Primitives", 0, (solid,)),
            ("Part_Builder", 0, (solid,)),
            ("Part_Extrude", 0, (wire,)),
            ("Part_Revolve", 0, (wire,)),
            ("Part_Fillet", 0, ((solid, "Edge1"),)),
            ("Part_Chamfer", 0, ((solid, "Edge1"),)),
            ("Part_Thickness", 0, ((solid, "Face1"),)),
            ("Part_Mirror", 0, (solid,)),
            ("Part_Scale", 0, (solid,)),
            ("Part_Loft", 0, (wire, second_wire)),
            ("Part_Sweep", 0, (wire, second_wire)),
            ("Part_CrossSections", 0, (solid,)),
            ("Part_Boolean", 0, (solid, second_solid)),
            ("Part_ProjectionOnSurface", 0, (solid,)),
            # Exercise both visible children through their shipped composite.
            ("Part_CompOffset", 0, (solid,)),
            ("Part_CompOffset", 1, (wire,)),
        )

        for case_index, (command_name, action_index, selection) in enumerate(cases):
            with self.subTest(command=command_name, action_index=action_index):
                Gui.Selection.clearSelection()
                for selected in selection:
                    if isinstance(selected, tuple):
                        Gui.Selection.addSelection(selected[0], selected[1])
                    else:
                        Gui.Selection.addSelection(selected)
                self._process_events()

                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    f"{command_name}[{action_index}] lacks valid test input",
                )
                actions = Gui.Command.get(command_name).getAction()
                if actions:
                    self.assertGreater(len(actions), action_index, command_name)
                    self.assertTrue(actions[action_index].isEnabled(), command_name)

                outside_value = f"outside {case_index}"
                inside_value = f"inside {case_index}"
                probe.ContractValue = outside_value
                original_undo_count = self.document.UndoCount
                self.document.openTransaction(
                    f"Caller-owned transaction for {command_name}"
                )
                caller_transaction_id = self.document.getBookedTransactionID()
                self.assertNotEqual(caller_transaction_id, 0)
                probe.ContractValue = inside_value
                expected = self._snapshot()

                try:
                    self._process_events()
                    self.assertFalse(
                        Gui.isCommandActive(command_name),
                        f"{command_name}[{action_index}] must refuse caller ownership",
                    )
                    if actions:
                        self.assertFalse(
                            actions[action_index].isEnabled(),
                            f"{command_name}[{action_index}] is visibly clickable",
                        )

                    if actions:
                        actions[action_index].trigger()
                    Gui.runCommand(command_name, action_index)
                    self._process_events(60)

                    self.assertFalse(Gui.Control.activeDialog(), command_name)
                    self.assertEqual(self._snapshot(), expected, command_name)
                    self.assertTrue(self.document.HasPendingTransaction, command_name)
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        caller_transaction_id,
                        command_name,
                    )
                    # The caller's still-open journal is visible in
                    # UndoCount.  The guarded command must neither close it
                    # nor add a second journal.
                    self.assertEqual(
                        self.document.UndoCount,
                        original_undo_count + 1,
                        command_name,
                    )
                    self.assertEqual(probe.ContractValue, inside_value, command_name)
                finally:
                    if (
                        self.document.getBookedTransactionID()
                        == caller_transaction_id
                    ):
                        self.document.abortTransaction()
                    self._process_events(60)

                self.assertFalse(self.document.HasPendingTransaction, command_name)
                self.assertEqual(self.document.getBookedTransactionID(), 0, command_name)
                self.assertEqual(self.document.UndoCount, original_undo_count, command_name)
                self.assertEqual(probe.ContractValue, outside_value, command_name)

    def test_synchronous_and_python_composite_commands_refuse_caller_transaction(self):
        left = self.document.addObject("Part::Feature", "SyncGuardLeft")
        left.Shape = Part.makeBox(10, 10, 10)
        right = self.document.addObject("Part::Feature", "SyncGuardRight")
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        probe = self.document.addObject("Part::Feature", "SyncGuardProbe")
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "outside"
        probe.Shape = Part.makeBox(1, 1, 1)
        self.document.recompute()

        cases = (
            ("Part_Cut", 0),
            ("Part_CompJoinFeatures", 0),
        )
        for case_index, (command_name, action_index) in enumerate(cases):
            with self.subTest(command=command_name):
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(left)
                Gui.Selection.addSelection(right)
                self._process_events()
                self.assertTrue(Gui.isCommandActive(command_name))
                actions = Gui.Command.get(command_name).getAction()
                self.assertGreater(len(actions), action_index)
                self.assertTrue(actions[action_index].isEnabled())

                outside_value = f"outside {case_index}"
                inside_value = f"inside {case_index}"
                probe.ContractValue = outside_value
                objects_before = tuple(self.document.Objects)
                visibility_before = tuple(
                    (obj, bool(obj.ViewObject.Visibility))
                    for obj in objects_before
                    if getattr(obj, "ViewObject", None) is not None
                )
                selection_before = tuple(
                    (
                        selected.Object,
                        tuple(selected.SubElementNames),
                    )
                    for selected in Gui.Selection.getSelectionEx()
                )
                undo_before = self.document.UndoCount
                self.document.openTransaction(
                    f"Caller transaction for {command_name}"
                )
                caller_id = self.document.getBookedTransactionID()
                probe.ContractValue = inside_value

                try:
                    self._process_events()
                    self.assertFalse(Gui.isCommandActive(command_name))
                    self.assertFalse(actions[action_index].isEnabled())
                    actions[action_index].trigger()
                    Gui.runCommand(command_name, action_index)
                    self._process_events(60)

                    self.assertEqual(tuple(self.document.Objects), objects_before)
                    self.assertEqual(
                        tuple(
                            (obj, bool(obj.ViewObject.Visibility))
                            for obj in objects_before
                            if getattr(obj, "ViewObject", None) is not None
                        ),
                        visibility_before,
                    )
                    self.assertEqual(
                        tuple(
                            (
                                selected.Object,
                                tuple(selected.SubElementNames),
                            )
                            for selected in Gui.Selection.getSelectionEx()
                        ),
                        selection_before,
                    )
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        caller_id,
                    )
                    self.assertEqual(probe.ContractValue, inside_value)
                    self.assertEqual(
                        self.document.UndoCount,
                        undo_before + 1,
                    )
                finally:
                    if self.document.getBookedTransactionID() == caller_id:
                        self.document.abortTransaction()
                    self._process_events(60)

                self.assertEqual(probe.ContractValue, outside_value)
                self.assertEqual(self.document.getBookedTransactionID(), 0)
                self.assertEqual(self.document.UndoCount, undo_before)

    def test_open_panel_apply_never_closes_a_later_caller_transaction(self):
        left = self.document.addObject("Part::Feature", "LateCallerLeft")
        left.Shape = Part.makeBox(10, 10, 10)
        right = self.document.addObject("Part::Feature", "LateCallerRight")
        right.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        probe = self.document.addObject("Part::Feature", "LateCallerProbe")
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "outside"
        probe.Shape = Part.makeBox(1, 1, 1)
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(left)
        Gui.Selection.addSelection(right)
        self._process_events()

        Gui.runCommand("Part_Boolean", 0)
        self._process_events(60)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        union = self._visible_widget(QtGui.QRadioButton, "unionButton")
        self.assertIsNotNone(union)
        union.setChecked(True)

        objects_before = tuple(self.document.Objects)
        visibility_before = tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in objects_before
            if getattr(obj, "ViewObject", None) is not None
        )
        undo_before = self.document.UndoCount
        self.document.openTransaction("Caller opened while panel was live")
        caller_id = self.document.getBookedTransactionID()
        probe.ContractValue = "inside"
        apply_button = self._task_button(QtGui.QDialogButtonBox.Apply)
        self.assertIsNotNone(apply_button)
        apply_button.click()
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(
            tuple(
                (obj, bool(obj.ViewObject.Visibility))
                for obj in objects_before
                if getattr(obj, "ViewObject", None) is not None
            ),
            visibility_before,
        )
        self.assertEqual(self.document.getBookedTransactionID(), caller_id)
        self.assertEqual(probe.ContractValue, "inside")
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        close_button = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close_button)
        close_button.click()
        self._process_events(80)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self.document.getBookedTransactionID(), caller_id)
        self.assertEqual(probe.ContractValue, "inside")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.abortTransaction()
        self._process_events(60)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(probe.ContractValue, "outside")
        self.assertEqual(self.document.UndoCount, undo_before)


if __name__ == "__main__":
    unittest.main()
