# SPDX-License-Identifier: LGPL-2.1-or-later

"""Static contracts for native-domain document History metadata."""

from __future__ import annotations

import ast
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[4]
_CANONICAL_STATUSES = ("Hidden", "LockDynamic", "NoRecompute")


def _source(relative_path: str) -> str:
    return (_REPOSITORY / relative_path).read_text(encoding="utf-8")


def _python_function(relative_path: str, function_name: str) -> str:
    source = _source(relative_path)
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    assert function is not None, (relative_path, function_name)
    segment = ast.get_source_segment(source, function)
    assert segment is not None
    return segment


def _python_class_method(
    relative_path: str,
    class_name: str,
    method_name: str,
) -> str:
    source = _source(relative_path)
    tree = ast.parse(source)
    class_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    assert class_node is not None, (relative_path, class_name)
    method = next(
        (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        ),
        None,
    )
    assert method is not None, (relative_path, class_name, method_name)
    segment = ast.get_source_segment(source, method)
    assert segment is not None
    return segment


def _cpp_function(
    relative_path: str,
    signature: str,
    following_signature: str,
) -> str:
    source = _source(relative_path)
    start = source.index(signature)
    end = source.index(following_signature, start + len(signature))
    return source[start:end]


def test_platform_has_one_exact_history_input_usability_contract():
    timeline = _cpp_function(
        "src/App/DocumentTimeline.cpp",
        "bool DocumentTimeline::isObjectUsableAtCurrentPosition(",
        "bool DocumentTimeline::isOperationVisibleAtEnd(",
    )
    document = _cpp_function(
        "src/App/Document.cpp",
        "bool Document::isObjectUsableAtCurrentTimelinePosition(",
        "void Document::stageTimelineOperationSegmentReplacement(",
    )
    binding = _cpp_function(
        "src/App/DocumentPyImp.cpp",
        "PyObject* DocumentPy::isObjectUsableAtCurrentTimelinePosition(",
        "PyObject* DocumentPy::stageTimelineOperationSegmentReplacement(",
    )
    stub = _source("src/App/Document.pyi")

    assert "document->containsObject(object)" in timeline
    assert "hasTimelineInternalRole(object)" in timeline
    assert "hasValidTimelineOwnerChain(object)" in timeline
    assert "timeline->isOperationActive(object)" in timeline
    assert "timeline->SuppressionAtEnd.getValues()" in timeline
    assert "timelineOwner(current)" in timeline
    assert "operationSuppressed(current)" in timeline
    assert "Visibility" not in timeline
    assert "getLinkedObject" not in timeline

    assert "containsObject(object)" in document
    assert "object->getDocument() == this" in document
    assert "DocumentTimeline::isObjectUsableAtCurrentPosition(object)" in document
    assert "document->isObjectUsableAtCurrentTimelinePosition(" in binding
    assert "def isObjectUsableAtCurrentTimelinePosition(" in stub


def test_fem_member_activity_delegates_to_the_shared_history_contract():
    local_usability = _python_function(
        "src/Mod/Fem/femtools/membertools.py",
        "_is_usable_at_current_timeline_position",
    )
    compatibility = _python_function(
        "src/Mod/Fem/femtools/membertools.py",
        "_is_suppressed",
    )

    assert "document.isObjectUsableAtCurrentTimelinePosition(member)" in local_usability
    assert "getLinkedObject" not in local_usability
    assert "VibeCADTimeline" not in local_usability
    assert "_is_usable_at_current_timeline_position(member)" in compatibility


def test_python_domain_helpers_apply_the_complete_history_metadata_contract():
    helpers = {
        "src/Mod/Assembly/UtilsAssembly.py": "_ensure_timeline_property",
        "src/Mod/CAM/Path/Base/Util.py": "_ensureTimelineProperty",
        "src/Mod/MeshPart/Gui/MeshFlatteningCommand.py": "_ensure_hidden_property",
        "src/Mod/VibeCAD/VibeCADFastenerModel.py": "ensure_timeline_property",
    }

    for relative_path, helper_name in helpers.items():
        helper = _python_function(relative_path, helper_name)
        assert "setPropertyStatus" in helper, relative_path
        assert "setEditorMode" in helper, relative_path
        for status in _CANONICAL_STATUSES:
            assert repr(status) in helper or f'"{status}"' in helper, (
                relative_path,
                status,
            )

        # Newly created metadata must be canonical before the first History
        # publication. Existing metadata is normalized by setPropertyStatus.
        assert "attr=16" in helper, relative_path
        assert "hidden=True" in helper, relative_path
        assert "locked=True" in helper, relative_path


def test_native_domain_helpers_normalize_existing_history_metadata():
    helpers = {
        "Assembly": _cpp_function(
            "src/Mod/Assembly/App/AssemblyLink.cpp",
            "void markTimelineResource(",
            "void replaceRetainedConsumerLinks(",
        ),
        "Mesh resource": _cpp_function(
            "src/Mod/Mesh/Gui/ParametricMeshFilter.cpp",
            "void MeshGui::markMeshTimelineResource(",
            "void MeshGui::markMeshTimelineReplacement(",
        ),
        "Mesh replacement": _cpp_function(
            "src/Mod/Mesh/Gui/ParametricMeshFilter.cpp",
            "void MeshGui::markMeshTimelineReplacement(",
            "std::vector<Mesh::Feature*> MeshGui::createParametricMeshFilters(",
        ),
        "Inspection": _cpp_function(
            "src/Mod/Inspection/Gui/VisualInspection.cpp",
            "App::Property* ensureTimelineProperty(",
            "void markTimelineReplacedInputs(",
        ),
    }

    for domain, helper in helpers.items():
        for status in _CANONICAL_STATUSES:
            assert f"setStatus(App::Property::{status}, true)" in helper, (
                domain,
                status,
            )


def test_static_mesh_history_role_has_the_complete_property_status_contract():
    constructor = _cpp_function(
        "src/Mod/Mesh/App/FeatureMeshOperations.cpp",
        "OutputGroup::OutputGroup()",
        "short OutputGroup::mustExecute() const",
    )
    for status in _CANONICAL_STATUSES:
        assert (
            f"VibeCADTimelineRole.setStatus(App::Property::{status}, true)"
            in constructor
        ), status


def test_visual_inspection_keeps_exact_dialog_input_identities():
    header = _source("src/Mod/Inspection/Gui/VisualInspection.h")
    source = _source("src/Mod/Inspection/Gui/VisualInspection.cpp")

    assert "std::string targetDocumentUid;" in header
    assert "targetDocumentUid = doc->Uid.getValueStr();" in source
    assert "document->Uid.getValueStr() != targetDocumentUid" in source
    assert "ObjectIdRole" in source
    assert "document->getObjectByID(" in source
    assert "objectName == object->getNameInDocument()" in source


def test_inspection_and_material_reject_future_history_targets():
    inspection_candidates = _cpp_function(
        "src/Mod/Inspection/Gui/VisualInspection.cpp",
        "std::vector<App::DocumentObject*> VisualInspection::candidateObjects(",
        "namespace InspectionGui",
    )
    inspection_source = _cpp_function(
        "src/Mod/Inspection/App/InspectionSource.cpp",
        "bool isSourceUsable(",
        "bool resolveSource(",
    )
    inspection_commands = _cpp_function(
        "src/Mod/Inspection/Gui/Command.cpp",
        "bool CmdInspectElement::isActive()",
        "void CreateInspectionCommands()",
    )
    material_identity = _source("src/Mod/Material/Gui/SelectionTargetIdentity.cpp")
    material_commands = _cpp_function(
        "src/Mod/Material/Gui/Command.cpp",
        "App::Document* activeMutationDocument()",
        "template<typename Task>",
    )
    material_inspectors = (
        _source("src/Mod/Material/Gui/DlgInspectAppearance.cpp"),
        _source("src/Mod/Material/Gui/DlgInspectMaterial.cpp"),
    )

    assert (
        "Inspection::resolveSource(candidate, document, source)"
        in inspection_candidates
    )
    assert inspection_source.count("isObjectUsableAtCurrentPosition(") == 2
    assert "isObjectUsableAtCurrentPosition(feature)" in inspection_commands

    assert "isActiveTimelineTarget(object)" in material_identity
    assert material_identity.count("isObjectUsableAtCurrentPosition(") == 2
    assert "SelectionTargetIdentity::capture(" in material_commands
    assert "target->resolveObject() != selected.pObject" in material_commands
    for inspector in material_inspectors:
        assert "SelectionTargetIdentity::capture(" in inspector
        assert "target->resolveViewProvider()" in inspector


def test_cam_compound_and_shape_publish_exact_factory_results():
    input_guard = _cpp_function(
        "src/Mod/CAM/Gui/Command.cpp",
        "bool isCAMInputUsable(",
        "class ExactObjectIdentity",
    )
    compound = _cpp_function(
        "src/Mod/CAM/Gui/Command.cpp",
        "void CmdPathCompound::activated(",
        "bool CmdPathCompound::isActive()",
    )
    shape = _cpp_function(
        "src/Mod/CAM/Gui/Command.cpp",
        "void CmdPathShape::activated(",
        "bool CmdPathShape::isActive()",
    )

    assert "std::unordered_set<const App::DocumentObject*>" in input_guard
    assert "!current->isValid()" in input_guard
    assert "isObjectUsableAtCurrentPosition(current)" in input_guard
    assert "current->getLinkedObject(false)" in input_guard

    for command in (compound, shape):
        assert "Gui::Command::runDocumentObjectCommand(" in command
        assert "ExactDocumentIdentity" in command
        assert "ExactObjectIdentity" in command
        assert "finalizeProvisionalOperationBlock(" in command
        assert "PathTimeline.markTimelineOperation(" in command
        assert "FreeCAD.activeDocument().addObject(" not in command
        assert "document->getObject(featureName" not in command
        assert "ActiveObject" not in command
        assert "doCommandEval" not in command

    assert "result->Group.getValues() != sources" in compound
    assert "finalizeProvisionalOperationBlock(result, {result})" in compound
    assert "resolveExactObjects(" in compound

    # Face/edge extraction creates a real semantic graph. Only those exact
    # factory returns become hidden owned resources; pre-existing whole-shape
    # inputs stay independent.
    assert shape.count("Gui::Command::runDocumentObjectCommand(") >= 2
    assert "resolveUsable(" in shape
    assert "std::vector<std::optional<ExactObjectIdentity>> sourceIdentities" in shape
    assert "sourceIdentities[index].emplace(liveDocument, *resource);" in shape
    assert "PathTimeline.markTimelineResource(" in shape
    assert "for (const std::size_t index : resourceIndices)" in shape
    assert "block.push_back(result);" in shape
    assert "result->Sources.getValues() != sources" in shape


def test_cam_human_tools_share_exact_history_input_identity():
    boundary = _source("src/Mod/CAM/Path/CommandBoundary.py")
    command_gate = _python_function(
        "src/Mod/CAM/Path/CommandBoundary.py",
        "can_start_ui_command",
    )
    usable = _python_function(
        "src/Mod/CAM/Path/CommandBoundary.py",
        "is_timeline_input_usable",
    )
    identity = boundary

    assert "while current is not None" in usable
    assert "getLinkedObject(recursive=False)" in usable
    assert "is_document_object(current, current_document)" in usable
    assert "gui_document.Document is document" in command_gate
    assert "document_uid" in identity
    assert "object_id" in identity
    assert "document.getObject(self.object_name) is self.object" in identity
    assert "document.getObject(self.object_id) is self.object" in identity
    assert "is_timeline_input_usable(" in identity
    assert "or not is_timeline_input_usable(obj, document)" in boundary

    for relative_path in (
        "src/Mod/CAM/PathCommands.py",
        "src/Mod/CAM/Path/Op/Gui/Base.py",
        "src/Mod/CAM/Path/Op/Gui/Array.py",
        "src/Mod/CAM/Path/Op/Gui/SimpleCopy.py",
        "src/Mod/CAM/Path/Op/Gui/PathShapeTC.py",
        "src/Mod/CAM/Path/Dressup/Utils.py",
    ):
        source = _source(relative_path)
        assert "is_timeline_input_usable(" in source, relative_path

    for relative_path in (
        "src/Mod/CAM/Path/Main/Job.py",
        "src/Mod/CAM/Path/Main/Gui/JobDlg.py",
        "src/Mod/CAM/Path/Main/Gui/Inspect.py",
        "src/Mod/CAM/Path/Post/Command.py",
    ):
        source = _source(relative_path)
        assert "is_timeline_input_usable(" in source, relative_path

    for relative_path in (
        "src/Mod/CAM/PathCommands.py",
        "src/Mod/CAM/Path/Op/Gui/Base.py",
        "src/Mod/CAM/Path/Op/Gui/Array.py",
        "src/Mod/CAM/Path/Op/Gui/SimpleCopy.py",
        "src/Mod/CAM/Path/Op/Gui/PathShapeTC.py",
        "src/Mod/CAM/Path/Main/Gui/JobCmd.py",
    ):
        source = _source(relative_path)
        assert "ExactDocumentObjectIdentity(" in source, relative_path
        assert "resolve(require_timeline=True)" in source, relative_path

    for relative_path in (
        "src/Mod/CAM/Path/Main/Gui/SanityCmd.py",
        "src/Mod/CAM/Path/Post/Command.py",
    ):
        source = _source(relative_path)
        assert "ExactDocumentObjectIdentity(" in source, relative_path
        assert "resolve(require_timeline=True)" in source, relative_path


def test_cam_selected_subelements_are_persistent_parametric_dependencies():
    python = _source("src/Mod/CAM/PathCommands.py")
    command = _source("src/Mod/CAM/Gui/Command.cpp")
    area_header = _source("src/Mod/CAM/App/FeatureArea.h")
    area = _source("src/Mod/CAM/App/FeatureArea.cpp")
    path_shape = _source("src/Mod/CAM/App/FeaturePathShape.cpp")
    path_compound = _source("src/Mod/CAM/App/FeaturePathCompound.cpp")

    resource = python
    factory = _python_function(
        "src/Mod/CAM/PathCommands.py",
        "createSubshapeResource",
    )
    assert '"App::PropertyLinkSub"' in resource
    assert "obj.Source = (source, [subname])" in resource
    assert "shape = findShape(" in resource
    assert "obj.Shape = shape.copy()" in resource
    assert 'document.addObject("Part::FeaturePython"' in factory
    assert "_CAMSubshapeResource(" in factory

    assert command.count("PathCommands.createSubshapeResource(") == 2
    assert "setattr(__resource__, 'Shape'" not in command
    assert 'setattr(__resource__, "Shape"' not in command

    assert "App::PropertyLinkSub WorkPlaneSource;" in area_header
    assert "App::PropertyBool WorkPlaneSourceEnabled;" in area_header
    assert "WorkPlaneSourceCollection" in area_header
    assert "WorkPlaneSourceEnabled.getValue()" in area
    assert "WorkPlaneSource.getValue()" in area
    assert "WorkPlaneSource.getSubValues()" in area
    assert "WorkPlane.setValue(workPlane);" in area
    assert '"Compatibility"' in area
    assert '"Cached workplane shape retained for legacy callers"' in area
    assert "Shape.setValue(TopoDS_Shape());" in area
    assert "Linked shape source is invalid" in path_shape
    assert "Linked shape source is empty" in path_shape
    assert "Linked shapes produced no toolpath" in path_shape
    assert "A linked path source is invalid" in path_compound
    workplane = _cpp_function(
        "src/Mod/CAM/Gui/Command.cpp",
        "void CmdPathAreaWorkplane::activated(",
        "bool CmdPathAreaWorkplane::isActive()",
    )
    assert "WorkPlaneSourceEnabled = True" in workplane
    assert "WorkPlaneSource = (" in workplane
    assert "WorkPlaneSourceCollection" in workplane
    assert "result->WorkPlaneSource.getValue() == planeObject" in workplane


def test_recorded_platform_python_factories_return_exact_objects():
    factories = {
        "src/Mod/Assembly/CommandCreateBom.py": (
            "createBomObject",
            "Assembly::BomObject",
        ),
        "src/Mod/Assembly/CommandCreateJoint.py": (
            "createGroundedJoint",
            "App::FeaturePython",
        ),
        "src/Mod/Assembly/CommandCreateView.py": (
            "createExplodedViewObject",
            "App::FeaturePython",
        ),
        "src/Mod/CAM/Path/Op/Gui/Array.py": (
            "Activated",
            "Path::FeaturePython",
        ),
        "src/Mod/CAM/Path/Op/Gui/SimpleCopy.py": (
            "Activated",
            "Path::FeaturePython",
        ),
        "src/Mod/CAM/Path/Dressup/Gui/Mirror.py": (
            "Activated",
            "Path::FeaturePython",
        ),
        "src/Mod/CAM/Path/Dressup/Gui/RampEntry.py": (
            "Activated",
            "Path::FeaturePython",
        ),
    }

    for relative_path, (function_name, expected_type) in factories.items():
        factory = _python_function(relative_path, function_name)
        assert "runDocumentObjectCommand(" in factory, relative_path
        assert expected_type in factory, relative_path
        assert "doCommandEval(" not in factory, relative_path
        assert "ActiveObject" not in factory, relative_path

    for relative_path in (
        "src/Mod/Fem/femcommands/commands.py",
        "src/Mod/Fem/femcommands/manager.py",
        "src/Mod/Fem/femguiutils/post_visualization.py",
    ):
        source = _source(relative_path)
        assert "runDocumentObjectCommand(" in source, relative_path
        assert "doCommandEval(" not in source, relative_path
        assert "_vibecad_fem_" not in source, relative_path


def test_targeted_assembly_gui_build_stages_its_runtime_python_commands():
    cmake = _source("src/Mod/Assembly/CMakeLists.txt")
    init_gui = _source("src/Mod/Assembly/InitGui.py")

    scripts_start = cmake.index("set(Assembly_Scripts")
    scripts_end = cmake.index("\n)", scripts_start)
    scripts = cmake[scripts_start:scripts_end]
    assert "CommandEditHistoryOperation.py" in scripts
    assert "CommandCreateView.py" in scripts
    assert "CommandCreateSimulation.py" in scripts
    assert "UtilsAssembly.py" in scripts

    target_start = cmake.index("ADD_CUSTOM_TARGET(AssemblyScripts ALL")
    target_end = cmake.index("\n)", target_start)
    target = cmake[target_start:target_end]
    assert "${Assembly_Scripts}" in target

    copy_start = cmake.index("fc_copy_sources(\n    AssemblyScripts")
    copy_end = cmake.index("\n)", copy_start)
    copy = cmake[copy_start:copy_end]
    assert "${Assembly_Scripts}" in copy
    assert "fc_copy_sources(AssemblyTests" not in cmake
    assert "ADD_CUSTOM_TARGET(AssemblyTests ALL)" in cmake
    assert "add_dependencies(AssemblyTests AssemblyScripts)" in cmake
    assert "add_dependencies(AssemblyGui AssemblyScripts)" in cmake
    assert "CommandEditHistoryOperation" in init_gui


def test_new_part_joint_preview_is_internal_before_it_gets_an_editor_contract():
    create_joint = _python_class_method(
        "src/Mod/Assembly/JointObject.py",
        "TaskAssemblyCreateJoint",
        "createJointObject",
    )
    new_part_init = _python_class_method(
        "src/Mod/Assembly/CommandInsertNewPart.py",
        "TaskAssemblyNewPart",
        "__init__",
    )

    classify = create_joint.index("classifyProvisionalTimelineInternalObject")
    construct = create_joint.index("Joint(")
    assert classify < construct
    assert (
        "register_timeline_editor=not self.provisional_timeline_internal"
        in create_joint
    )
    assert "provisional_timeline_internal=True" in new_part_init


def test_exploded_view_replaces_its_previous_temporary_application():
    apply_moves = _python_class_method(
        "src/Mod/Assembly/CommandCreateView.py",
        "ExplodedView",
        "applyMoves",
    )
    prepare = _python_class_method(
        "src/Mod/Assembly/CommandCreateView.py",
        "ExplodedView",
        "_prepareApplicationBaseline",
    )

    assert "_prepareApplicationBaseline" in apply_moves
    assert "_rememberAppliedPlacements" in apply_moves
    assert "finally:" in apply_moves
    assert "part.Placement == applied" in prepare
    assert "part.Placement = App.Placement(baseline)" in prepare


def test_targeted_domain_gui_builds_stage_their_runtime_python():
    fem = _source("src/Mod/Fem/CMakeLists.txt")
    cam = _source("src/Mod/CAM/CMakeLists.txt")
    techdraw = _source("src/Mod/TechDraw/CMakeLists.txt")
    material = _source("src/Mod/Material/CMakeLists.txt")

    for dependency in (
        "FemScriptsTarget",
        "FemGuiScriptsTarget",
        "FemPythonUi",
    ):
        assert dependency in fem[fem.index("add_dependencies(\n        FemGui") :]

    path_target = cam[
        cam.index("ADD_CUSTOM_TARGET(PathScripts ALL") : cam.index("SET(test_files")
    ]
    assert "${Path_Scripts}" in path_target
    path_copy = cam[
        cam.index("fc_copy_sources(\n    PathScripts") : cam.index(
            "fc_copy_sources(Tests"
        )
    ]
    assert "${Path_Scripts}" in path_copy
    assert "add_dependencies(PathGui PathScripts)" in cam

    assert "add_dependencies(TechDrawGui TechDraw_Data)" in techdraw
    material_dependencies = material[
        material.index("add_dependencies(\n        MatGui") : material.index(
            "\n    )", material.index("add_dependencies(\n        MatGui")
        )
    ]
    for dependency in (
        "MaterialScripts",
        "MaterialTest",
        "MaterialPythonTestData",
    ):
        assert dependency in material_dependencies


def test_multi_output_replacements_publish_before_hiding_existing_inputs():
    inspection = _cpp_function(
        "src/Mod/Inspection/Gui/VisualInspection.cpp",
        "void VisualInspection::accept()",
        '#include "moc_VisualInspection.cpp"',
    )
    erase_elements = _cpp_function(
        "src/Mod/Fem/Gui/TaskCreateElementSet.cpp",
        "void TaskCreateElementSet::finalizeTimelineBlock()",
        "bool TaskCreateElementSet::publishWorkingMesh(",
    )

    inspection_publish = inspection.index("timeline->publishProvisionalOperationBlock(")
    inspection_hide = inspection.index(
        "view->Visibility.setValue(false)",
        inspection_publish,
    )
    assert inspection_publish < inspection_hide
    assert "view->Visibility.setValue(false)" not in inspection[:inspection_publish]

    erase_restore = erase_elements.index(
        "sourceViewProvider->Visibility.setValue(true)"
    )
    erase_publish = erase_elements.index("timeline->publishProvisionalOperationBlock(")
    erase_hide = erase_elements.index(
        "sourceViewProvider->Visibility.setValue(false)",
        erase_publish,
    )
    assert erase_restore < erase_publish < erase_hide


def test_measure_acceptance_has_one_exact_history_candidate_per_save():
    measure = _source("src/Mod/Measure/Gui/TaskMeasure.cpp")
    mass = _source("src/Mod/Measure/Gui/TaskMassProperties.cpp")
    measure_header = _source("src/Mod/Measure/Gui/TaskMeasure.h")
    mass_header = _source("src/Mod/Measure/Gui/TaskMassProperties.h")
    timeline = _cpp_function(
        "src/App/DocumentTimeline.cpp",
        "bool DocumentTimeline::isOperationCandidate(",
        "const DocumentObject* DocumentTimeline::timelineOwner(",
    )

    # The persistent Measurements folder is structural UI organization, not a
    # second feature-history operation. Therefore each Save transaction has
    # one exact candidate: the directly returned measurement/result object.
    assert "return !operation->isDerivedFrom<DocumentObjectGroup>();" in timeline
    assert "_mMeasureObject = dynamic_cast<Measure::MeasureBase*>(" in measure
    assert "auto* acceptedMeasurement = _mMeasureObject;" in measure
    assert "ensureGroup(_mMeasureObject);" in measure
    assert "markCommandInteractionStateDurable();" in measure

    assert 'auto* obj = doc->addObject("Measure::Result", "MassProperties");' in mass
    assert "group->addObject(obj);" in mass
    assert "markCommandInteractionStateDurable();" in mass

    # The mass-properties preview is never accepted by name. Its exact ID and
    # name are retained together, and its transaction is aborted before any
    # durable result transaction begins.
    assert "previewObjectId = obj->getID();" in mass
    assert "previewObjectName = obj->getNameInDocument();" in mass
    assert "document->getObjectByID(previewObjectId)" in mass
    assert "if (!abortPreviewTransaction())" in mass

    # Long-lived panels resolve their launch document by name and persistent
    # UID, so closing it and opening a same-name document cannot retarget an
    # in-flight preview, Save, or Cancel.
    assert "std::string mTargetDocumentUid;" in measure_header
    assert "mTargetDocumentUid = doc->Uid.getValueStr();" in measure
    assert "document->Uid.getValueStr() == mTargetDocumentUid" in measure
    assert "std::string targetDocumentUid;" in mass_header
    assert "targetDocumentUid = document->Uid.getValueStr();" in mass
    assert "document->Uid.getValueStr() == targetDocumentUid" in mass
    assert "std::string currentDatumDocumentUid;" in mass_header
    assert "document->Uid.getValueStr() != currentDatumDocumentUid" in mass


def test_measurements_only_read_the_current_history_state():
    selection = _source("src/Mod/Measure/Gui/TimelineSelection.h")
    quick = _source("src/Mod/Measure/Gui/QuickMeasure.cpp")
    measure = _source("src/Mod/Measure/Gui/TaskMeasure.cpp")
    mass = _source("src/Mod/Measure/Gui/TaskMassProperties.cpp")
    cmake = _source("src/Mod/Measure/Gui/CMakeLists.txt")

    assert selection.count("isObjectUsableAtCurrentPosition(") == 2
    assert "object->getLinkedObject(true)" in selection
    assert "SuppressibleExtension" not in selection
    assert "isTimelineSelectionActive(rootObj)" in quick
    assert "isTimelineSelectionActive(obj)" in quick
    assert "isTimelineSelectionActive(object)" in measure
    assert "isTimelineSelectionActive(subObject)" in measure
    assert "isTimelineSelectionActive(pickedObject)" in mass
    assert "isPresentedForMassProperties" in mass
    assert "viewProvider->isShow()" in mass
    assert "viewProvider && !viewProvider->Visibility.getValue()" not in mass
    assert (
        "isTimelineSelectionActive(\n                occurrence.materialOwner" in mass
    )
    assert "isTimelineSelectionActive(resolved)" in mass
    assert "isTimelineSelectionActive(coordSystem)" in mass
    assert "TimelineSelection.h" in cmake


def test_coordinate_system_children_are_infrastructure_not_history_steps():
    candidate = _cpp_function(
        "src/App/DocumentTimeline.cpp",
        "bool DocumentTimeline::isOperationCandidate(",
        "const DocumentObject* DocumentTimeline::timelineOwner(",
    )
    publication = _cpp_function(
        "src/App/DocumentTimeline.cpp",
        "void DocumentTimeline::publishProvisionalOperationBlock(",
        "void DocumentTimeline::adoptExistingOperationBlock(",
    )

    # A user LCS owns the same seven controlled datum objects as the document
    # Origin. They are presentation/reference infrastructure; the LCS itself
    # is the single user-created History operation.
    for contract in (candidate, publication):
        assert "object->isDerivedFrom<DatumElement>()" in contract or (
            "operation->isDerivedFrom<DatumElement>()" in contract
        )
        assert "static_cast<const DatumElement*>(" in contract
        assert ")->getLCS()" in contract
