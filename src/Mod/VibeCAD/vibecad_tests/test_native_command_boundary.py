# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fresh VibeCAD contract for the shared native-command boundary.

The inherited workbench suites are not the product specification. Every
shipped ribbon family passes through this boundary, which must refuse a
caller-owned transaction while still permitting a synchronously nested child
to continue the exact transaction opened by its outer command.
"""

from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[4]


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unterminated function: {signature}")


def test_every_command_entry_point_uses_the_same_invocation_boundary() -> None:
    header = (_REPOSITORY / "src/Gui/Command.h").read_text(encoding="utf-8")
    implementation = (_REPOSITORY / "src/Gui/Command.cpp").read_text(
        encoding="utf-8"
    )
    python = (_REPOSITORY / "src/Gui/CommandPyImp.cpp").read_text(
        encoding="utf-8"
    )

    assert "bool canInvoke();" in header
    assert "if (canInvoke())" in _function(
        implementation,
        "void Command::_invoke(",
    )
    assert "const bool bActive = canInvoke();" in _function(
        implementation,
        "void Command::testActive()",
    )
    assert "cmd->canInvoke()" in _function(
        python,
        "PyObject* CommandPy::isActive(",
    )


def test_delete_dependency_preflight_is_pure_and_execution_is_atomic() -> None:
    header = (_REPOSITORY / "src/Gui/ViewProvider.h").read_text(
        encoding="utf-8"
    )
    provider = (_REPOSITORY / "src/Gui/ViewProvider.cpp").read_text(
        encoding="utf-8"
    )
    delete = _function(
        (_REPOSITORY / "src/Gui/CommandDoc.cpp").read_text(
            encoding="utf-8"
        ),
        "void StdCmdDelete::activated(",
    )
    assembly = (
        _REPOSITORY
        / "src/Mod/Assembly/Gui/ViewProviderAssembly.cpp"
    ).read_text(encoding="utf-8")

    assert "getObjectsToDeleteWith(" in header
    assert "setDependentDeletionPlanner(" in header
    assert "virtual bool getObjectsToDeleteWith(" not in header
    assert "dependentDeletionPlanners()" in provider
    assert "dependentDeletionPlanners().erase(this);" in provider

    can_delete = _function(
        assembly,
        "bool ViewProviderAssembly::canDelete(",
    )
    assert "ViewProviderPart::canDelete(objBeingDeleted)" in can_delete
    assert "doCommand" not in can_delete
    assert "removeObject" not in can_delete

    planner = _function(
        assembly,
        "ViewProviderAssembly::dependentObjectsToDeleteWith(",
    )
    assert "document->getObjectByID(object->getID()) != object" in planner
    assert "document->getObjectByID(joint->getID()) != joint" in planner
    assert "getJointsOfObj(component)" in planner
    assert "getJointsOfPart(component)" in planner
    assert "doCommand" not in planner
    assert "removeObject" not in planner

    prompt = delete.index("QMessageBox::warning(")
    approved = delete.index("if (autoDeletion)", prompt)
    transaction = delete.index(
        "manageDocCommand(commandDocument);",
        approved,
    )
    assert prompt < approved < transaction
    assert "vp->getObjectsToDeleteWith(obj)" in delete[:prompt]
    assert "resolveIdentity(plan.dependent)" in delete[approved:transaction]
    assert "resolveIdentity(plan.owner)" in delete[approved:transaction]
    assert "resolveIdentity(companion)" in delete[approved:transaction]
    assert "if (tid != 0)" in delete


def test_mutating_commands_never_adopt_an_unowned_transaction() -> None:
    implementation = (_REPOSITORY / "src/Gui/Command.cpp").read_text(
        encoding="utf-8"
    )
    boundary = _function(implementation, "bool Command::canInvoke()")

    assert "eType & ForEdit" in boundary
    assert "eType & AlterDoc" in boundary
    assert "getBookedTransactionID()" in boundary
    assert "hasPendingTransaction()" in boundary
    assert "ownedEnclosingTransactionId(document)" in boundary
    assert "booked == App::NullTransaction" in boundary
    assert "owned != booked" in boundary


def test_recorded_python_factories_return_one_exact_document_object() -> None:
    header = (_REPOSITORY / "src/Gui/Command.h").read_text(encoding="utf-8")
    implementation = (_REPOSITORY / "src/Gui/Command.cpp").read_text(
        encoding="utf-8"
    )
    python = (_REPOSITORY / "src/Gui/ApplicationPy.cpp").read_text(
        encoding="utf-8"
    )
    stub = (_REPOSITORY / "src/Gui/FreeCADGui.module.pyi").read_text(
        encoding="utf-8"
    )

    assert "runDocumentObjectCommand(_type, _document, _expression" in header
    factory = _function(
        implementation,
        "App::DocumentObject* Command::_runDocumentObjectCommand(",
    )
    assert factory.count("runStringObject(") == 1
    assert "const App::Document* expectedDocumentAddress = &document;" in factory
    assert "const std::string documentUid = document.Uid.getValueStr();" in factory
    assert "resolvedDocument != expectedDocumentAddress" in factory
    assert "resolvedDocument->getObject(resultName.c_str()) != result" in factory
    assert "resolvedDocument->getObjectByID(resultId) != result" in factory
    assert "getActiveObject" not in factory

    binding = _function(
        python,
        "PyObject* ApplicationPy::sRunDocumentObjectCommand(",
    )
    assert '"O!s|z:runDocumentObjectCommand"' in binding
    assert "App::GetApplication().getDocuments()" in binding
    assert "App::DocumentObject::getClassTypeId()" in binding
    assert "Gui::Command::runDocumentObjectCommand(" in binding
    assert "def runDocumentObjectCommand(" in stub


def test_modeling_selection_rejects_inactive_history_inputs_at_one_boundary() -> None:
    header = (
        _REPOSITORY / "src/Mod/Part/Gui/ModelingSelection.h"
    ).read_text(encoding="utf-8")
    implementation = (
        _REPOSITORY / "src/Mod/Part/Gui/ModelingSelection.cpp"
    ).read_text(encoding="utf-8")

    assert (
        "isModelingObjectActive(const App::DocumentObject* object) noexcept;"
        in header
    )
    exact = _function(
        implementation,
        "bool isExactTimelineObjectActive(",
    )
    assert exact.count("isObjectUsableAtCurrentPosition(") == 2
    assert "object->getLinkedObject(true)" in exact

    active = _function(
        implementation,
        "bool isModelingObjectActive(",
    )
    assert "isExactTimelineObjectActive(object)" in active
    assert "resolveModelingObject(object)" in active
    assert "isExactTimelineObjectActive(resolved)" in active

    for signature in (
        "resolveModelingObjects(",
        "resolveModelingSelections(",
    ):
        assert "isModelingObjectActive(" in _function(
            implementation,
            signature,
        )


def test_model_and_sketch_commands_use_the_shared_history_selection_gate() -> None:
    part_design = (
        _REPOSITORY / "src/Mod/PartDesign/Gui/Command.cpp"
    ).read_text(encoding="utf-8")
    part_design_utils = (
        _REPOSITORY / "src/Mod/PartDesign/Gui/Utils.cpp"
    ).read_text(encoding="utf-8")
    sketcher = (
        _REPOSITORY / "src/Mod/Sketcher/Gui/Command.cpp"
    ).read_text(encoding="utf-8")

    assert "PartGui::isModelingObjectActive(object)" in _function(
        part_design_utils,
        "PartDesign::Body* selectedBodyForDocument(",
    )
    assert "PartGui::isModelingObjectActive(*s)" in _function(
        part_design,
        "unsigned validateSketches(",
    )
    assert "PartGui::isModelingObjectActive(object)" in _function(
        part_design,
        "bool isTransformCommandActive(",
    )
    assert "PartGui::isModelingObjectActive(selected.pObject)" in _function(
        part_design,
        "bool hasSubShapeBinderSourceSelection(",
    )
    assert "PartGui::isModelingObjectActive(" in _function(
        sketcher,
        "bool isSketchSetupAvailable(",
    )
    assert "isSketchSetupAvailable(document)" in _function(
        sketcher,
        "bool CmdSketcherValidateSketch::isActive()",
    )


def test_retained_python_model_tools_use_the_same_history_selection_gate() -> None:
    binding = (
        _REPOSITORY / "src/Mod/Part/Gui/AppPartGui.cpp"
    ).read_text(encoding="utf-8")
    assert '"isModelingObjectActive"' in binding
    assert "PartGui::isModelingObjectActive(object)" in _function(
        binding,
        "Py::Object isModelingObjectActive(",
    )

    for relative_path in (
        "src/Mod/Part/BOPTools/JoinFeatures.py",
        "src/Mod/Part/BOPTools/SplitFeatures.py",
        "src/Mod/Part/BOPTools/ToleranceFeatures.py",
        "src/Mod/Part/CompoundTools/_CommandCompoundFilter.py",
        "src/Mod/Part/CompoundTools/_CommandExplodeCompound.py",
        "src/Mod/Part/AttachmentEditor/Commands.py",
    ):
        source = (_REPOSITORY / relative_path).read_text(encoding="utf-8")
        assert "PartGui.isModelingObjectActive(" in source, relative_path


def test_retained_cpp_model_tools_revalidate_history_inputs() -> None:
    shape_from_mesh = (
        _REPOSITORY / "src/Mod/Part/Gui/ShapeFromMesh.cpp"
    ).read_text(encoding="utf-8")
    simple_commands = (
        _REPOSITORY / "src/Mod/Part/Gui/CommandSimple.cpp"
    ).read_text(encoding="utf-8")
    section_cut = (
        _REPOSITORY / "src/Mod/Part/Gui/SectionCutting.cpp"
    ).read_text(encoding="utf-8")

    assert "PartGui::isModelingObjectActive(object)" in _function(
        shape_from_mesh,
        "explicit SelectionState(",
    )
    perform = _function(
        shape_from_mesh,
        "bool ShapeFromMesh::perform()",
    )
    assert "PartGui::isModelingObjectActive(mesh)" in perform
    assert "attempt.trackReplacedInputs(*operation, replacedMeshes)" in perform
    assert "FCMD_OBJ_HIDE(mesh)" in perform
    assert "PartGui::isModelingObjectActive(mesh)" in _function(
        simple_commands,
        "bool CmdPartShapeFromMesh::isActive()",
    )
    assert "PartGui::isModelingObjectActive(anObject)" in _function(
        section_cut,
        "SectionCut::SectionCut(",
    )
    assert "PartGui::isModelingObjectActive(object)" in _function(
        section_cut,
        "void SectionCut::onRefreshCutPBclicked()",
    )


def test_ruled_surface_validates_before_committing() -> None:
    source = (
        _REPOSITORY / "src/Mod/Part/Gui/Command.cpp"
    ).read_text(encoding="utf-8")
    command = _function(source, "void CmdPartRuledSurface::activated(")
    recompute = command.index("updateDocument(document)")
    validate = command.index(
        "PartGui::TaskResultValidation::validatePartResult(result)"
    )
    commit = command.index("attempt.commit()")
    assert recompute < validate < commit
    assert "PartGui::ModelingTaskAttempt attempt(" in command
    assert "attempt.trackCreatedObject(*result)" in command
    assert "trackReplacedInputs" not in command
    assert "cmdAppObjectHide" not in command


def test_standalone_mirror_replaces_its_exact_presentation_source() -> None:
    source = (
        _REPOSITORY / "src/Mod/Part/Gui/Mirroring.cpp"
    ).read_text(encoding="utf-8")
    accept = _function(source, "bool Mirroring::accept()")

    resolve = accept.index(
        "PartGui::resolveModelingPresentationObject(src)"
    )
    track = accept.index(
        "attempt.trackReplacedInputs(*dst, {presentation})"
    )
    hide = accept.index("Gui::cmdAppObjectHide(src)")
    commit = accept.index("attempt.commit()")
    assert resolve < track < hide < commit
