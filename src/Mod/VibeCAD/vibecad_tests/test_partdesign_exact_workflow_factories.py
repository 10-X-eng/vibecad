# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for exact Part Design task-workflow factories."""

from __future__ import annotations

from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[4]


def _source(relative_path: str) -> str:
    return (_REPOSITORY / relative_path).read_text(encoding="utf-8")


def _scope(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    return source[start_index : source.index(end, start_index)]


def test_sketch_workflow_uses_the_exact_body_factory_return():
    source = _source("src/Mod/PartDesign/Gui/SketchWorkflow.cpp")

    assert "Sketcher::SketchObject* createSketchExact(" in source
    assert "Gui::Command::runDocumentObjectCommand(" in source
    assert "Sketcher::SketchObject::getClassTypeId()" in source
    assert "Gui::Command::getObjectCmd(body)" in source
    assert "body->hasObject(sketch)" in source
    assert source.count("createSketchExact(") == 4

    assert "getObject(FeatName.c_str())" not in source
    assert "getObject(featureName.c_str())" not in source
    assert "ActiveObject" not in source
    assert "doCommandEval(" not in source


def test_linked_body_placement_joins_the_new_sketch_transaction():
    source = _source("src/Mod/PartDesign/Gui/SketchWorkflow.cpp")

    should_create_body = _scope(
        source,
        "std::tuple<bool, PartDesign::Body*> SketchWorkflow::shouldCreateBody()",
        "bool SketchWorkflow::shouldAbort",
    )
    assert "linkedBodyOccurrence = topParent;" in should_create_body
    assert "Placement.setValue" not in should_create_body

    placement_helper = _scope(
        source,
        "bool applyLinkedBodyPlacement(",
        "struct RejectException",
    )
    assert "transactionIsActive(transactionId)" in placement_helper
    assert "bodyDocument->setActiveTransaction(" in placement_helper
    assert "bodyDocument->hasPendingTransaction()" in placement_helper
    assert placement_helper.index(
        "bodyDocument->setActiveTransaction("
    ) < placement_helper.index(
        "body->Placement.setValue(occurrencePlacement);"
    )

    fast_path = _scope(
        source,
        "void createSketchOnSupport(const std::string& supportString)",
        "\nprivate:",
    )
    assert (
        fast_path.index("guidocument->openCommand(")
        < fast_path.index("applyLinkedBodyPlacement(")
        < fast_path.index("createSketchExact(")
    )
    apply_failure = fast_path.index("if (!applyLinkedBodyPlacement(")
    assert fast_path.index(
        "guidocument->abortCommand();",
        apply_failure,
    ) < fast_path.index("throw RejectException();", apply_failure)

    request_path = _scope(
        source,
        "void findSupport()",
        "\nprivate:",
    )
    assert request_path.index("guidocument->openCommand(") < request_path.index(
        "applyLinkedBodyPlacement("
    ) < request_path.index("tryFindSupport();")
    reject_handler = request_path.index("catch (const RejectException&)")
    assert request_path.index(
        "guidocument->abortCommand();",
        reject_handler,
    ) < request_path.index("throw;", reject_handler)
    assert source.count("applyLinkedBodyPlacement(") == 3


def test_multi_transform_children_use_the_exact_body_factory_return():
    source = _source(
        "src/Mod/PartDesign/Gui/TaskMultiTransformParameters.cpp"
    )

    assert "FeatureT* createTransformationExact(" in source
    assert "Gui::Command::runDocumentObjectCommand(" in source
    assert "FeatureT::getClassTypeId()" in source
    assert "Gui::Command::getObjectCmd(body)" in source
    assert "body->hasObject(feature)" in source

    for feature_type in (
        "PartDesign::Mirrored",
        "PartDesign::LinearPattern",
        "PartDesign::PolarPattern",
        "PartDesign::Scaled",
    ):
        assert (
            f"createTransformationExact<{feature_type}>(" in source
        )

    assert "getObject(newFeatName.c_str())" not in source
    assert "ActiveObject" not in source
    assert "doCommandEval(" not in source
    assert source.count("abortCreationTransaction(") == 5
