# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained-input contract for the native Part Loft and Sweep tasks."""

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


def _source(name: str) -> str:
    return (_REPOSITORY / f"src/Mod/Part/Gui/{name}.cpp").read_text(encoding="utf-8")


def test_loft_and_sweep_persist_exact_document_and_profile_identities() -> None:
    for name in ("TaskLoft", "TaskSweep"):
        source = _source(name)
        assert "struct ExactDocumentIdentity" in source
        assert "App::Document* address = nullptr;" in source
        assert "std::string uid;" in source
        assert "struct ExactObjectIdentity" in source
        assert "const App::DocumentObject* address = nullptr;" in source
        assert "long id = -1;" in source
        assert (
            "std::map<const QTreeWidgetItem*, ExactObjectIdentity> profileIdentities;"
        ) in source

        document_resolver = _function(
            source,
            "App::Document* resolveExactDocument(",
        )
        assert "document == identity.address" in document_resolver
        assert "document->Uid.getValueStr() == identity.uid" in document_resolver

        object_resolver = _function(
            source,
            "App::DocumentObject* resolveExactObject(",
        )
        assert "document->getObjectByID(identity.id)" in object_resolver
        assert "object == identity.address" in object_resolver
        assert "document->getObject(identity.name.c_str()) == object" in object_resolver

        find_shapes = _function(source, f"void {name[4:]}Widget::findShapes()")
        assert "exactDocumentIdentity(activeDoc)" in find_shapes
        assert "profileIdentities.emplace" in find_shapes
        assert "exactObjectIdentity(object)" in find_shapes


def test_loft_revalidates_exact_active_profiles_before_starting_attempt() -> None:
    source = _source("TaskLoft")
    accept = _function(source, "bool LoftWidget::accept()")

    document = accept.index("resolveExactDocument(d->document)")
    exact_profile = accept.index("resolveExactObject(identity->second)")
    active_profile = accept.index("PartGui::isModelingObjectActive(object)")
    attempt = accept.index('ModelingTaskAttempt attempt(*appDocument, "Loft")')
    assert document < exact_profile < active_profile < attempt
    assert "appDocument->getObject(name.constData())" not in accept


def test_sweep_persists_and_revalidates_exact_path_and_profiles() -> None:
    source = _source("TaskSweep")
    assert "struct ExactSelectionIdentity" in source
    assert "std::optional<ExactSelectionIdentity> path;" in source
    assert "std::optional<Gui::SelectionObject> path;" not in source

    path_capture = _function(
        source,
        "void SweepWidget::onButtonPathToggled(",
    )
    assert "ExactSelectionIdentity {" in path_capture
    assert "exactObjectIdentity(object)" in path_capture
    assert "selection.front().getSubNames()" in path_capture

    accept = _function(source, "bool SweepWidget::accept()")
    document = accept.index("resolveExactDocument(d->document)")
    exact_path = accept.index("resolveExactObject(d->path->object)")
    active_path = accept.index("PartGui::isModelingObjectActive(pathObject)")
    exact_profile = accept.index("resolveExactObject(identity->second)")
    active_profile = accept.index("PartGui::isModelingObjectActive(object)")
    attempt = accept.index('ModelingTaskAttempt attempt(*appDocument, "Sweep")')
    assert (
        document < exact_path < active_path < exact_profile < active_profile < attempt
    )
    assert "appDocument->getObject(name.constData())" not in accept


def test_loft_and_sweep_commit_replacement_and_macro_state_atomically() -> None:
    for name, widget, recorder in (
        ("TaskLoft", "Loft", "recordAcceptedLoft"),
        ("TaskSweep", "Sweep", "recordAcceptedSweep"),
    ):
        source = _source(name)
        accept = _function(source, f"bool {widget}Widget::accept()")
        owner = accept.index("PartGui::inferModelingOperandOwner")
        attempt = accept.index(
            f'ModelingTaskAttempt attempt(*appDocument, "{widget}")'
        )
        replacement = accept.index("attempt.trackReplacedInputs")
        record = accept.index(f"{recorder}(")
        hide = accept.index("Gui::cmdAppObjectHide(object)")
        commit = accept.index("attempt.commit()")
        assert owner < attempt < replacement < record < hide < commit
        assert "attempt.targetResultBody" in accept
        assert "attempt.keepResultAtDocumentRoot" in accept

        record_function = _function(source, f"void {recorder}(")
        assert "App::DocumentObject* parent" in record_function
        assert "GeoFeatureGroupExtension::getGroupOfObject" not in record_function
