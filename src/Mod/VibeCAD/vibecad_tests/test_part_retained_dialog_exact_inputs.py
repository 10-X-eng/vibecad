# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained-input contract for native Part single-input dialogs."""

from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[4]
_PART_GUI = _REPOSITORY / "src/Mod/Part/Gui"
_DIALOGS = {
    "DlgExtrusion": ("apply", "getShapesToExtrude", "Extrude"),
    "DlgRevolution": ("accept", "getShapesToRevolve", "Revolve"),
    "DlgScale": ("apply", "getShapesToScale", "Scale"),
}


def _source(name: str, suffix: str = "cpp") -> str:
    return (_PART_GUI / f"{name}.{suffix}").read_text(encoding="utf-8")


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


def test_dialogs_capture_exact_document_and_source_identities() -> None:
    for dialog in _DIALOGS:
        header = _source(dialog, "h")
        source = _source(dialog)

        assert "App::Document* documentAddress {nullptr};" in header
        assert "std::string documentUid;" in header
        assert "documentAddress = activeDoc;" in source
        assert "documentUid = activeDoc->Uid.getValueStr();" in source
        assert "sourceNameRole" in source
        assert "sourceIdRole" in source
        assert "sourceAddressRole" in source
        assert "reinterpret_cast<quintptr>(obj)" in source


def test_exact_resolvers_reject_replaced_documents_and_sources() -> None:
    for dialog in _DIALOGS:
        source = _source(dialog)

        document_resolver = _function(
            source,
            "App::Document* resolveRetainedTaskDocument(",
        )
        assert "document == address" in document_resolver
        assert "document->Uid.getValueStr() == uid" in document_resolver

        source_resolver = _function(
            source,
            "App::DocumentObject* resolveRetainedTaskSource(",
        )
        assert "document.getObjectByID(id)" in source_resolver
        assert "object == address" in source_resolver
        assert "document.containsObject(object)" in source_resolver
        assert "name == object->getNameInDocument()" in source_resolver
        assert "document.getObject(name.constData()) == object" in source_resolver
        assert "PartGui::isModelingObjectActive(object)" in source_resolver


def test_sources_are_revalidated_before_any_modeling_attempt() -> None:
    for dialog, (entry_point, source_getter, operation) in _DIALOGS.items():
        source = _source(dialog)
        action = _function(source, f"void {dialog}::{entry_point}()")

        exact_document = action.index("resolveRetainedTaskDocument(")
        exact_sources = action.index(f"{source_getter}()")
        attempt = action.index(
            f'ModelingTaskAttempt attempt(*activeDoc, "{operation}")'
        )
        assert exact_document < exact_sources < attempt

        source_lookup = _function(
            source,
            f"std::vector<App::DocumentObject*> {dialog}::{source_getter}() const",
        )
        assert "resolveRetainedTaskDocument(" in source_lookup
        assert "resolveRetainedTaskSource(" in source_lookup
        assert "resolveModelingObject(" not in source_lookup
