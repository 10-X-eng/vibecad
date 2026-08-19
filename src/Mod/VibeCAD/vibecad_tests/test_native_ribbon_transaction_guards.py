# SPDX-License-Identifier: LGPL-2.1-or-later

"""Static ownership contract for every command in the shipped Model ribbon."""

from __future__ import annotations

import ast
from pathlib import Path
import re

_REPOSITORY = Path(__file__).resolve().parents[4]
_RIBBON_GATE = (
    _REPOSITORY / "src/Mod/VibeCAD/vibecad_tests/qt_ribbon_theme_integration.py"
)

_READ_ONLY_OR_VIEW_COMMANDS = frozenset(
    {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "Inspection_InspectElement",
        "Part_CheckGeometry",
    }
)

# These child action IDs are dispatched by one guarded C++ command object.
_IMPLEMENTATION_ALIASES = {
    **{
        command: "PartDesign_DesignPrimitive"
        for command in (
            "PartDesign::DesignBox",
            "PartDesign::DesignCylinder",
            "PartDesign::DesignSphere",
            "PartDesign::DesignCone",
            "PartDesign::DesignEllipsoid",
            "PartDesign::DesignTorus",
            "PartDesign::DesignPrism",
            "PartDesign::DesignWedge",
            "PartDesign::DesignTube",
        )
    },
}

_PARTDESIGN_COMMAND = "src/Mod/PartDesign/Gui/Command.cpp"
_CPP_GUARDS = {
    "src/Mod/PartDesign/Gui/CommandBody.cpp": {
        "PartDesign_NewComponent": "canStartModelingCommand",
        "PartDesign_NewBody": "canStartModelingCommand",
    },
    "src/Mod/PartDesign/Gui/CommandPrimitive.cpp": {
        "PartDesign_DesignPrimitive": "canStartModelingCommand",
    },
    _PARTDESIGN_COMMAND: {
        "PartDesign_SubShapeBinder": "canStartModelingCommand",
        "PartDesign_Clone": "canStartModelingCommand",
        "PartDesign_DesignExtrude": "designProfileOperationActive",
        "PartDesign_DesignRevolve": "designProfileOperationActive",
        "PartDesign_DesignLoft": "designLoftOperationActive",
        "PartDesign_DesignSweep": "designSweepOperationActive",
        "PartDesign_DesignHelix": "designProfileOperationActive",
        "PartDesign_Hole": "designProfileOperationActive",
        "PartDesign_Fillet": "designDressupOperationActive",
        "PartDesign_Chamfer": "designDressupOperationActive",
        "PartDesign_Draft": "designDressupOperationActive",
        "PartDesign_Thickness": "designDressupOperationActive",
        "PartDesign_DesignMirror": "designPatternCommandActive",
        "PartDesign_DesignLinearPattern": "designPatternCommandActive",
        "PartDesign_DesignCircularPattern": "designPatternCommandActive",
        "PartDesign_Scale": "canStartModelingCommand",
        "PartDesign_Combine": "canStartModelingCommand",
        "PartDesign_Split": "canStartModelingCommand",
        "PartDesign_Separate": "canStartModelingCommand",
    },
    "src/Mod/Sketcher/Gui/Command.cpp": {
        "Sketcher_NewSketch": "isSketchSetupAvailable",
        "Sketcher_EditSketch": "isSketchSetupAvailable",
        "Sketcher_ValidateSketch": "canStartRetainedModelingTask",
    },
    "src/Mod/Part/Gui/Command.cpp": {
        **{
            command: "canStartRetainedModelingTask"
            for command in (
                "Part_Primitives",
                "Part_Builder",
                "Part_MakeFace",
                "Part_RuledSurface",
                "Part_Section",
                "Part_CrossSections",
                "Part_CompOffset",
                "Part_Offset",
                "Part_Offset2D",
                "Part_ProjectionOnSurface",
                "Part_CompCompoundTools",
                "Part_Compound",
                "Part_CompJoinFeatures",
            )
        },
    },
    "src/Mod/Part/Gui/CommandSimple.cpp": {
        "Part_Defeaturing": "canStartRetainedModelingTask",
    },
    "src/Mod/Measure/Gui/Command.cpp": {
        "Std_Measure": "canStartRetainedModelingTask",
        "Std_MassProperties": "canStartRetainedModelingTask",
    },
    "src/Mod/Inspection/Gui/Command.cpp": {
        "Inspection_VisualInspection": "canStartRetainedModelingTask",
    },
    "src/Mod/Surface/Gui/Command.cpp": {
        command: "canStartSurfaceOperation"
        for command in (
            "Surface_Filling",
            "Surface_GeomFillSurface",
            "Surface_Sections",
            "Surface_ExtendFace",
            "Surface_CurveOnMesh",
            "Surface_BlendCurve",
        )
    },
}

_PYTHON_GUARDS = {
    "src/Mod/Part/BasicShapes/CommandShapes.py": {
        "Part_Tube": ("class CommandTube", "canStartRetainedModelingTask"),
    },
    "src/Mod/Part/CompoundTools/_CommandExplodeCompound.py": {
        "Part_ExplodeCompound": (
            "class _CommandExplodeCompound",
            "canStartRetainedModelingTask",
        ),
    },
    "src/Mod/Part/CompoundTools/_CommandCompoundFilter.py": {
        "Part_CompoundFilter": (
            "class _CommandCompoundFilter",
            "canStartRetainedModelingTask",
        ),
    },
    "src/Mod/Part/BOPTools/JoinFeatures.py": {
        "Part_JoinConnect": (
            "class CommandConnect",
            "canStartRetainedModelingTask",
        ),
        "Part_JoinEmbed": (
            "class CommandEmbed",
            "canStartRetainedModelingTask",
        ),
        "Part_JoinCutout": (
            "class CommandCutout",
            "canStartRetainedModelingTask",
        ),
    },
    "src/Mod/VibeCAD/VibeCADFastenersGui.py": {
        "VibeCAD_InsertStandardFastener": (
            "class _InsertStandardFastenerCommand",
            "_can_start_modeling_transaction",
        ),
        "VibeCAD_EditStandardFastener": (
            "class _EditStandardFastenerCommand",
            "_can_start_modeling_transaction",
        ),
        "VibeCAD_CreateMatchingFastenerHole": (
            "class _CreateMatchingHoleCommand",
            "_can_start_modeling_transaction",
        ),
        "VibeCAD_AttachStandardFastener": (
            "class _AttachStandardFastenerCommand",
            "_can_start_modeling_transaction",
        ),
    },
    "src/Mod/VibeCAD/VibeCADGui.py": {
        "VibeCAD_PublishInterface": (
            "class PublishComponentInterfaceCommand",
            "Gui.Control.activeDialog",
        ),
    },
}


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError(f"{name} is not a literal assignment in {path}")


def _shipped_graph() -> tuple[str, ...]:
    groups = _literal_assignment(_RIBBON_GATE, "_MODEL_GROUP_COMMANDS")
    composites = _literal_assignment(_RIBBON_GATE, "_MODEL_COMPOSITES")
    commands = []
    for _label, parents in groups:
        for parent in parents:
            commands.append(parent)
            commands.extend(composites.get(parent, ()))
    return tuple(commands)


def _cpp_command_section(source: str, command: str) -> str:
    match = re.search(
        rf"(?:Command|GroupCommand)\(\"{re.escape(command)}\"\)",
        source,
    )
    assert match is not None, command
    boundaries = [
        position
        for marker in (
            "\nDEF_STD_CMD",
            "\nclass ",
            "\n//===========================================================================",
        )
        if (position := source.find(marker, match.end())) >= 0
    ]
    end = min(boundaries) if boundaries else len(source)
    return source[match.start() : end]


def _python_class_section(source: str, anchor: str) -> str:
    start = source.index(anchor)
    next_class = source.find("\nclass ", start + len(anchor))
    return source[start : next_class if next_class >= 0 else len(source)]


def _python_function_section(source: str, signature: str) -> str:
    start = source.index(signature)
    next_definition = source.find("\n\ndef ", start + len(signature))
    next_class = source.find("\n\nclass ", start + len(signature))
    boundaries = [
        position for position in (next_definition, next_class) if position >= 0
    ]
    end = min(boundaries) if boundaries else len(source)
    return source[start:end]


def _function_section(source: str, signature: str) -> str:
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
    raise AssertionError(f"Unterminated function {signature}")


def test_every_shipped_model_command_has_an_explicit_transaction_classification() -> (
    None
):
    graph = _shipped_graph()
    assert len(graph) == len(set(graph))
    assert _READ_ONLY_OR_VIEW_COMMANDS < set(graph)

    transaction_commands = set(graph) - _READ_ONLY_OR_VIEW_COMMANDS
    implementations = {
        _IMPLEMENTATION_ALIASES.get(command, command)
        for command in transaction_commands
    }
    declared = {
        command for commands in _CPP_GUARDS.values() for command in commands
    } | {command for commands in _PYTHON_GUARDS.values() for command in commands}
    # Compatibility commands may remain guarded without being presented on the
    # shipped ribbon. Every shipped transaction command must still have one
    # explicit boundary classification.
    assert implementations <= declared


def test_every_transaction_owning_implementation_calls_its_boundary_guard() -> None:
    for relative_path, commands in _CPP_GUARDS.items():
        source = (_REPOSITORY / relative_path).read_text(encoding="utf-8")
        for command, guard in commands.items():
            section = _cpp_command_section(source, command)
            assert guard in section, f"{command} lacks {guard}"

    for relative_path, commands in _PYTHON_GUARDS.items():
        source = (_REPOSITORY / relative_path).read_text(encoding="utf-8")
        for command, (anchor, guard) in commands.items():
            section = _python_class_section(source, anchor)
            assert guard in section, f"{command} lacks {guard}"

    part_design = (_REPOSITORY / _PARTDESIGN_COMMAND).read_text(encoding="utf-8")
    feature_body = _function_section(
        part_design,
        "bool featureCommandBody(",
    )
    assert "canStartModelingCommand" in feature_body
    for helper in (
        "bool isProfileCommandActive(",
        "bool isDraftCommandActive(",
        "bool isDressupCommandActive(",
        "bool isTransformCommandActive(",
    ):
        assert "featureCommandBody" in _function_section(part_design, helper)

    sketcher = (_REPOSITORY / "src/Mod/Sketcher/Gui/Command.cpp").read_text(
        encoding="utf-8"
    )
    assert "canStartRetainedModelingTask" in _function_section(
        sketcher,
        "bool isSketchSetupAvailable(",
    )

    fasteners = (_REPOSITORY / "src/Mod/VibeCAD/VibeCADFastenersGui.py").read_text(
        encoding="utf-8"
    )
    helper = _python_function_section(
        fasteners,
        "def _can_start_modeling_transaction()",
    )
    assert "canStartRetainedModelingTask" in helper
    clean_boundary = _python_function_section(
        fasteners,
        "def _document_transaction_is_clean(",
    )
    assert "getBookedTransactionID" in clean_boundary
    assert "HasPendingTransaction" in clean_boundary
    for command, (anchor, _guard) in _PYTHON_GUARDS[
        "src/Mod/VibeCAD/VibeCADFastenersGui.py"
    ].items():
        assert "_document_transaction_is_clean" in _python_class_section(
            fasteners,
            anchor,
        ), command


def test_inspection_tasks_close_only_their_exact_locked_transactions() -> None:
    measure = (_REPOSITORY / "src/Mod/Measure/Gui/TaskMeasure.cpp").read_text(
        encoding="utf-8"
    )
    assert "mPreviewTransactionId" in measure
    assert "Gui::ExactTransaction" in measure
    assert "mPreviewTransaction->commit()" in measure
    assert "mPreviewTransaction->abort()" in measure
    assert "document->lockTransaction()" not in measure
    assert "document->unlockTransaction()" not in measure
    assert "mTargetDoc->commitCommand()" not in measure
    assert "mTargetDoc->abortCommand()" not in measure

    mass = (_REPOSITORY / "src/Mod/Measure/Gui/TaskMassProperties.cpp").read_text(
        encoding="utf-8"
    )
    assert "class OwnedMassPropertiesTransaction" in mass
    assert "ownsCurrentTransaction()" in mass
    assert "abortPreviewTransaction()" in mass
    assert '"Add Mass Properties"' in mass
    assert "Gui::ExactTransaction transaction;" in mass
    assert "transaction.commit()" in mass
    assert "transaction.abort()" in mass
    assert "document->lockTransaction()" not in mass

    visual = (_REPOSITORY / "src/Mod/Inspection/Gui/VisualInspection.cpp").read_text(
        encoding="utf-8"
    )
    assert "targetDocumentName" in visual
    assert "Gui::ExactTransaction" in visual
    assert "transaction->commit()" in visual
    assert "transaction->abort()" in visual
    assert "document->lockTransaction()" not in visual
    assert visual.index("transaction->commit()") < visual.index("QDialog::accept()")
    assert visual.index("transaction->commit()") < visual.rindex(
        "recordAcceptedVisualInspection("
    )
    assert "macroManager()" in visual

    modeling = (_REPOSITORY / "src/Mod/Part/Gui/ModelingSelection.cpp").read_text(
        encoding="utf-8"
    )
    constructor = _function_section(
        modeling,
        "ModelingTaskAttempt::ModelingTaskAttempt(",
    )
    commit = _function_section(
        modeling,
        "void ModelingTaskAttempt::commit()",
    )
    assert "std::make_unique<Gui::ExactTransaction>" in constructor
    assert "d->transaction->commit()" in commit
    assert "lockOwnedTransaction" not in constructor
    assert "releaseTransactionLock" not in commit
