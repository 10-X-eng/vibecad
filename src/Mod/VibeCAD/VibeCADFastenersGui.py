# SPDX-License-Identifier: LGPL-2.1-or-later

"""Integrated standard-component commands for Part Design and Assembly."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADFastenerAttachment import attach_model_fastener_graph
from VibeCADFastenerAssembly import create_assembly_fastener_graph
from VibeCADFastenerModel import (
    copy_fastener_appearance as _copy_fastener_appearance,
    create_model_fastener_graph,
    ensure_timeline_property as _ensure_timeline_property,
    mark_timeline_operation as _mark_timeline_operation,
    safe_fastener_object_name as _safe_name,
)


_COMMANDS_REGISTERED = False
_ICON_ROOT = Path(__file__).resolve().parent


def _translate(text: str) -> str:
    return App.Qt.translate("VibeCADStandardComponents", text)


def _icon(name: str) -> str:
    return str(_ICON_ROOT / name)


def _active_workbench() -> str:
    try:
        return str(Gui.activeWorkbench().name())
    except Exception:
        return ""


def _catalog_available() -> bool:
    try:
        from VibeCADFasteners import require_available

        require_available()
        return True
    except Exception:
        return False


def _can_start_modeling_transaction() -> bool:
    import PartGui

    return bool(PartGui.canStartRetainedModelingTask())


def _document_transaction_is_clean(document: Any) -> bool:
    return (
        document is not None
        and int(document.getBookedTransactionID()) == 0
        and not bool(document.HasPendingTransaction)
    )


def _mark_timeline_resource(resource: Any, owner: Any) -> None:
    """Persist that *resource* is implementation owned by *owner*."""

    if resource is None or owner is None or resource is owner:
        raise ValueError(
            "A standard-fastener timeline resource requires a distinct owner"
        )
    resource_document = getattr(resource, "Document", None)
    if resource_document is None or resource_document is not getattr(
        owner, "Document", None
    ):
        raise ValueError(
            "A standard-fastener timeline resource and its owner must share a document"
        )

    _ensure_timeline_property(
        resource,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    _ensure_timeline_property(
        resource,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Visible standard-component operation which owns this implementation",
    )
    resource.VibeCADTimelineOwner = owner
    resource.VibeCADTimelineRole = "resource"


def migrate_assembly_fastener_timeline_resources(document: Any) -> list[Any]:
    """Migrate unambiguous legacy Assembly fastener definitions."""

    from VibeCADFasteners import COMPONENT_SCHEMA, PROP_SCHEMA

    if document is None:
        return []
    objects = list(getattr(document, "Objects", []) or [])
    assemblies = [
        obj
        for obj in objects
        if bool(
            getattr(obj, "isDerivedFrom", lambda _type: False)(
                "Assembly::AssemblyObject"
            )
        )
    ]
    assembly_members = {
        member
        for assembly in assemblies
        for member in list(getattr(assembly, "Group", []) or [])
    }
    migrated = []
    for source in objects:
        if (
            str(getattr(source, "TypeId", "") or "") != "Part::FeaturePython"
            or str(getattr(source, PROP_SCHEMA, "") or "") != COMPONENT_SCHEMA
        ):
            continue
        view = getattr(source, "ViewObject", None)
        if view is None or bool(getattr(view, "ShowInTree", True)):
            continue
        occurrences = [
            candidate
            for candidate in assembly_members
            if str(getattr(candidate, "TypeId", "") or "") == "App::Link"
            and getattr(candidate, "LinkedObject", None) is source
            and bool(
                getattr(
                    getattr(candidate, "ViewObject", None),
                    "ShowInTree",
                    True,
                )
            )
        ]
        if len(occurrences) != 1:
            continue
        occurrence = occurrences[0]
        role = str(getattr(source, "VibeCADTimelineRole", "") or "")
        owner = getattr(source, "VibeCADTimelineOwner", None)
        if (
            "VibeCADTimelineRole" not in source.PropertiesList
            and "VibeCADTimelineOwner" not in source.PropertiesList
        ):
            _mark_timeline_resource(source, occurrence)
            migrated.append(source)
        elif role != "resource" or owner is not occurrence:
            continue
        _mark_timeline_operation(occurrence, editor=source)
    return migrated


def _show_error(title: str, error: Any) -> None:
    from PySide import QtGui

    QtGui.QMessageBox.critical(
        Gui.getMainWindow(),
        _translate(title),
        str(error),
    )


def _show_information(title: str, message: str) -> None:
    from PySide import QtGui

    QtGui.QMessageBox.information(
        Gui.getMainWindow(),
        _translate(title),
        _translate(message),
    )


def _generated_fastener_operation(generator: Any) -> Any | None:
    """Return the one Design operation which owns *generator* as a dependency."""

    from VibeCADFastenerModel import generated_fastener_operation

    return generated_fastener_operation(generator)


def _generated_fastener_body(operation: Any) -> Any | None:
    """Resolve the one stable Body output declared by *operation*."""

    from VibeCADFastenerModel import generated_fastener_body

    return generated_fastener_body(operation)


def _timeline_root(obj: Any) -> Any | None:
    """Return one exact semantic History root without resolving App::Links."""

    current = obj
    seen: set[tuple[str, str]] = set()
    while current is not None:
        key = (
            str(
                getattr(getattr(current, "Document", None), "Uid", "")
                or getattr(getattr(current, "Document", None), "Name", "")
            ),
            str(getattr(current, "Name", "") or id(current)),
        )
        if key in seen:
            return None
        seen.add(key)
        owner = getattr(current, "VibeCADTimelineOwner", None)
        if owner is None:
            return current
        current = owner
    return None


def _timeline_successor_root(
    document: Any,
    root: Any | None,
    *,
    additional_members: tuple[Any, ...] = (),
) -> Any | None:
    """Return the semantic root after one complete legacy History footprint."""

    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", []) or [])
    indices = [
        index
        for index, candidate in enumerate(operations)
        if candidate in additional_members
        or (root is not None and _timeline_root(candidate) is root)
    ]
    if not indices:
        return None
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise RuntimeError(
            _translate("The legacy fastener has a non-contiguous History block.")
        )
    if indices[-1] + 1 >= len(operations):
        return None
    return _timeline_root(operations[indices[-1] + 1])


def _legacy_model_fastener_body(generator: Any) -> Any | None:
    """Return the exact one-feature legacy Body which owns *generator*."""

    from VibeCADFasteners import COMPONENT_SCHEMA, PROP_SCHEMA

    if str(getattr(generator, PROP_SCHEMA, "") or "") != COMPONENT_SCHEMA:
        return None
    try:
        body = generator.getParentGeoFeatureGroup()
    except Exception:
        body = None
    if (
        str(getattr(body, "TypeId", "") or "") != "PartDesign::Body"
        or getattr(body, "Tip", None) is not generator
        or any(
            str(getattr(member, "TypeId", "") or "")
            == "PartDesign::DesignBodyPublication"
            for member in list(getattr(body, "Group", []) or [])
        )
        or list(getattr(body, "Group", []) or []) != [generator]
    ):
        return None
    return body


def _legacy_fastener_history_root(body: Any, generator: Any) -> Any | None:
    """Validate and return the one old Model-fastener semantic root."""

    document = body.Document
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", []) or [])
    body_role = str(getattr(body, "VibeCADTimelineRole", "") or "")
    generator_role = str(
        getattr(generator, "VibeCADTimelineRole", "") or ""
    )
    generator_owner = getattr(generator, "VibeCADTimelineOwner", None)
    if (
        body_role == "operation"
        and generator_role == "resource"
        and generator_owner is body
        and body in operations
        and generator in operations
    ):
        return body
    if (
        generator_role == "operation"
        and generator_owner is None
        and generator in operations
        and (
            body not in operations
            or (
                body_role == ""
                and operations.index(body) + 1 == operations.index(generator)
            )
        )
    ):
        return generator
    if (
        body_role == ""
        and body in operations
        and generator not in operations
        and generator_role in {"", "internal"}
    ):
        return None
    if (
        body not in operations
        and generator not in operations
        and body_role in {"", "internal"}
        and generator_role in {"", "internal"}
    ):
        return None
    raise RuntimeError(
        _translate(
            "This legacy fastener has ambiguous History ownership and cannot be "
            "converted automatically. Its Body and generator were left unchanged."
        )
    )


def _make_timeline_internal(obj: Any) -> None:
    """Apply canonical retained-internal metadata to one untracked object."""

    _ensure_timeline_property(
        obj,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    if "VibeCADTimelineOwner" in obj.PropertiesList:
        obj.VibeCADTimelineOwner = None
    if "VibeCADTimelineEditor" in obj.PropertiesList:
        obj.VibeCADTimelineEditor = None
    if "VibeCADTimelineEditCommand" in obj.PropertiesList:
        obj.VibeCADTimelineEditCommand = ""
    if "VibeCADTimelineDeleteCommand" in obj.PropertiesList:
        obj.VibeCADTimelineDeleteCommand = ""
    if "VibeCADTimelineReplacedInputs" in obj.PropertiesList:
        obj.VibeCADTimelineReplacedInputs = []
    obj.VibeCADTimelineRole = "internal"


def _migrate_legacy_model_fastener(
    generator: Any,
    *,
    configure=None,
    output_label: str = "",
    preserve_history_position: bool = True,
) -> tuple[Any, Any, Any, Any]:
    """Convert one Body-owned Model fastener inside the caller's transaction."""

    import PartDesign

    body = _legacy_model_fastener_body(generator)
    if body is None:
        raise RuntimeError(
            _translate(
                "Only a one-feature Body-owned standard fastener can be converted automatically."
            )
        )
    document = body.Document
    if int(document.getBookedTransactionID()) == 0:
        raise RuntimeError(
            _translate("Legacy fastener conversion requires one active transaction.")
        )
    timeline = document.getObject("VibeCADTimeline")
    external_consumers = [
        consumer
        for consumer in list(getattr(generator, "InList", []) or [])
        if consumer is not body and consumer is not timeline
    ]
    if external_consumers:
        labels = ", ".join(
            str(getattr(consumer, "Label", "") or consumer.Name)
            for consumer in external_consumers
        )
        raise RuntimeError(
            _translate(
                "This legacy fastener is referenced by other document objects "
                f"({labels}). Convert those references to the Body before migrating it."
            )
        )

    old_root = _legacy_fastener_history_root(body, generator)
    operations = list(getattr(timeline, "Operations", []) or [])
    body_is_standalone_leaf = (
        body in operations
        and str(getattr(body, "VibeCADTimelineRole", "") or "") == ""
    )
    successor = (
        _timeline_successor_root(
            document,
            old_root,
            additional_members=(body,) if body_is_standalone_leaf else (),
        )
        if (old_root is not None or body_is_standalone_leaf)
        and preserve_history_position
        else None
    )
    if old_root is not None:
        document.classifyExistingTimelineSemanticBlockInternalObject(old_root)
    if body_is_standalone_leaf:
        document.classifyExistingTimelineLeafInternalObject(body)
    _make_timeline_internal(body)
    _make_timeline_internal(generator)

    visible_label = str(
        output_label or getattr(body, "Label", "") or generator.Label
    )
    initial_state = PartDesign.initializeDesignBodyFromLegacyFeature(
        body,
        generator,
    )
    _make_timeline_internal(body)
    _make_timeline_internal(generator)
    for obj in (initial_state, generator):
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            view.Visibility = False
            if hasattr(view, "ShowInTree"):
                view.ShowInTree = False

    operation = document.addObject(
        "PartDesign::DesignGeneratedOperation",
        _safe_name(f"{visible_label}_Feature", "StandardFastenerFeature"),
    )
    edit = PartDesign.beginDesignOperationEdit(operation)
    operation.Label = f"Fastener: {visible_label}"
    operation.GeneratorKind = "standard-fastener"
    operation.Generator = generator
    operation.OutputLabel = visible_label
    _mark_timeline_operation(operation)
    PartDesign.setDesignOperationTargets(edit, "Modify", [body])
    if configure is not None:
        configure(operation, generator)
    document.recompute()
    error = str(getattr(generator, "VibeCADFastenerError", "") or "")
    if error:
        raise RuntimeError(error)
    outputs = PartDesign.finalizeDesignOperationEdit(edit)
    if len(outputs) != 1 or outputs[0] is not body:
        raise RuntimeError(
            _translate(
                "Legacy fastener conversion did not retain its exact Body identity."
            )
        )
    body.Label = visible_label
    _copy_fastener_appearance(generator, body)
    if successor is not None:
        document.reorderTimelineOperationBlocksBefore([operation], successor)
    return body, operation, generator, initial_state


def _fastener_target(obj: Any) -> Any | None:
    from VibeCADFasteners import COMPONENT_SCHEMA, PROP_SCHEMA

    def resolve(candidate: Any, seen: set[tuple[str, str]]) -> Any | None:
        if candidate is None:
            return None
        key = (
            str(
                getattr(getattr(candidate, "Document", None), "Uid", "")
                or getattr(getattr(candidate, "Document", None), "Name", "")
            ),
            str(getattr(candidate, "Name", "") or id(candidate)),
        )
        if key in seen:
            return None
        seen.add(key)

        linked = getattr(candidate, "LinkedObject", None)
        if linked is not None and linked is not candidate:
            target = resolve(linked, seen)
            if target is not None:
                return target
        if (
            str(getattr(candidate, "TypeId", "") or "")
            == "PartDesign::DesignGeneratedOperation"
            and str(getattr(candidate, "GeneratorKind", "") or "")
            == "standard-fastener"
        ):
            target = getattr(candidate, "Generator", None)
            if (
                str(getattr(target, PROP_SCHEMA, "") or "")
                == COMPONENT_SCHEMA
            ):
                return target
        if str(getattr(candidate, PROP_SCHEMA, "") or "") == COMPONENT_SCHEMA:
            return candidate
        if str(getattr(candidate, "TypeId", "") or "") == "PartDesign::Body":
            publication = getattr(candidate, "Tip", None)
            state = getattr(publication, "CurrentState", None)
            operation = getattr(state, "Operation", None)
            if (
                str(getattr(operation, "TypeId", "") or "")
                == "PartDesign::DesignGeneratedOperation"
                and str(getattr(operation, "GeneratorKind", "") or "")
                == "standard-fastener"
            ):
                return resolve(operation, seen)
            # Legacy Body-owned standard fasteners remain editable for imported
            # documents, but new Model insertion never creates this layout.
            matches = [
                child
                for child in list(getattr(candidate, "Group", []) or [])
                if str(getattr(child, PROP_SCHEMA, "") or "")
                == COMPONENT_SCHEMA
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    return resolve(obj, set())


def _selected_fasteners() -> list[tuple[Any, Any]]:
    result: list[tuple[Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    # Keep the object the user actually selected.  The default resolved
    # selection replaces an App::Link occurrence with its linked definition,
    # which makes Assembly occurrences indistinguishable from source features.
    for selection in Gui.Selection.getSelectionEx("", 0):
        candidates: list[Any] = []
        for sub_name in selection.SubElementNames:
            try:
                resolved = selection.Object.resolve(str(sub_name))
            except Exception:
                resolved = ()
            resolved_objects = [
                obj
                for obj in resolved[:2]
                if obj is not None and hasattr(obj, "TypeId")
            ]
            # Prefer the actual Link over its linked definition or container.
            resolved_objects.sort(
                key=lambda obj: getattr(obj, "LinkedObject", None) is not None,
                reverse=True,
            )
            candidates.extend(resolved_objects)
        candidates.append(selection.Object)

        candidate_seen: set[tuple[str, str]] = set()
        for selected in candidates:
            candidate_key = (
                str(
                    getattr(getattr(selected, "Document", None), "Uid", "")
                    or getattr(getattr(selected, "Document", None), "Name", "")
                ),
                str(getattr(selected, "Name", "") or id(selected)),
            )
            if candidate_key in candidate_seen:
                continue
            candidate_seen.add(candidate_key)
            target = _fastener_target(selected)
            if target is None:
                continue
            key = (
                str(
                    getattr(target.Document, "Uid", "")
                    or target.Document.Name
                ),
                str(target.Name),
            )
            if key not in seen:
                seen.add(key)
                result.append((selected, target))
            break
    return result


def _fastener_label_owner(selected: Any, target: Any) -> Any:
    """Return the visible object whose label represents the fastener."""

    operation = _generated_fastener_operation(target)
    body = _generated_fastener_body(operation)
    if body is not None:
        return body
    if selected is not target:
        return selected
    try:
        body = target.getParentGeoFeatureGroup()
    except Exception:
        body = None
    if (
        body is not None
        and str(getattr(body, "TypeId", "") or "") == "PartDesign::Body"
    ):
        return body
    return target


def _activate_partdesign_body(body: Any) -> None:
    """Make the Body that owns the operation the active modeling context."""

    if body is None or str(getattr(body, "TypeId", "") or "") != "PartDesign::Body":
        return
    try:
        Gui.activeView().setActiveObject("pdbody", body)
    except Exception:
        # A document can exist without a compatible 3D view (for example while
        # restoring a file). The command result remains valid in that case.
        pass


class _FastenerDialog:
    """Catalog-backed selector shared by insertion and in-place editing."""

    def __init__(
        self,
        *,
        title: str,
        allowed_standards: list[str] | None = None,
        initial: Mapping[str, Any] | None = None,
        initial_label: str = "",
    ) -> None:
        from PySide import QtCore, QtGui
        from VibeCADFasteners import catalog_index

        self._QtCore = QtCore
        self._QtGui = QtGui
        self.dialog = QtGui.QDialog(Gui.getMainWindow())
        self.dialog.setWindowTitle(_translate(title))
        self.dialog.setMinimumWidth(540)
        self._initial = dict(initial or {})
        allowed = set(allowed_standards or [])
        self._rows = [
            dict(row)
            for row in catalog_index()["standards"]
            if not allowed or str(row["standard"]) in allowed
        ]

        outer = QtGui.QVBoxLayout(self.dialog)
        form = QtGui.QFormLayout()
        outer.addLayout(form)

        self.filter_edit = QtGui.QLineEdit()
        self.filter_edit.setPlaceholderText(
            _translate("Search standard, type, description, or size")
        )
        form.addRow(_translate("Find"), self.filter_edit)

        self.match_label = QtGui.QLabel()
        form.addRow("", self.match_label)

        self.family_combo = QtGui.QComboBox()
        self.family_combo.addItem(_translate("All families"), "")
        for family in sorted({str(row["family"]) for row in self._rows}):
            self.family_combo.addItem(family, family)
        form.addRow(_translate("Family"), self.family_combo)

        self.standard_combo = QtGui.QComboBox()
        form.addRow(_translate("Standard"), self.standard_combo)

        self.description_label = QtGui.QLabel()
        self.description_label.setWordWrap(True)
        form.addRow(_translate("Catalog description"), self.description_label)

        self.size_combo = QtGui.QComboBox()
        form.addRow(_translate("Nominal thread / size"), self.size_combo)

        self.length_combo = QtGui.QComboBox()
        form.addRow(_translate("Length (mm)"), self.length_combo)

        self.model_thread = QtGui.QCheckBox(
            _translate("Model real thread geometry")
        )
        form.addRow("", self.model_thread)

        self.left_handed = QtGui.QCheckBox(_translate("Left-handed thread"))
        form.addRow("", self.left_handed)

        self.label_edit = QtGui.QLineEdit(initial_label)
        self.label_edit.setPlaceholderText(
            _translate("Optional document label")
        )
        form.addRow(_translate("Label"), self.label_edit)

        self.status_label = QtGui.QLabel()
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        buttons = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.dialog.reject)
        outer.addWidget(buttons)

        self.filter_edit.textChanged.connect(self._refresh_standards)
        self.family_combo.currentIndexChanged.connect(self._refresh_standards)
        self.standard_combo.currentIndexChanged.connect(self._refresh_standard)
        self.size_combo.currentIndexChanged.connect(self._refresh_size)
        self._refresh_standards()

    @staticmethod
    def _data(combo: Any) -> Any:
        return combo.itemData(combo.currentIndex())

    def _select_data(self, combo: Any, value: Any) -> bool:
        requested = str(value)
        for index in range(combo.count()):
            if str(combo.itemData(index)) == requested:
                combo.setCurrentIndex(index)
                return True
        return False

    def _refresh_standards(self, *_args: Any) -> None:
        from VibeCADFasteners import _catalog_search_rank

        current = str(
            self._data(self.standard_combo)
            or self._initial.get("standard")
            or ""
        )
        query = self.filter_edit.text().strip()
        family = str(self._data(self.family_combo) or "")
        ranked_rows = [
            (rank, str(row["standard"]), row)
            for row in self._rows
            if (not family or str(row["family"]) == family)
            if (rank := _catalog_search_rank(query, row)) is not None
        ]
        ranked_rows.sort(key=lambda item: (item[0], item[1]))
        rows = [item[2] for item in ranked_rows]
        self.match_label.setText(
            _translate("Matching standards: {count}").format(count=len(rows))
        )
        self.standard_combo.blockSignals(True)
        self.standard_combo.clear()
        for row in rows:
            self.standard_combo.addItem(
                f"{row['standard']} — {row['description']}",
                str(row["standard"]),
            )
        self.standard_combo.blockSignals(False)
        if current and not query:
            self._select_data(self.standard_combo, current)
        if self.standard_combo.count() and self.standard_combo.currentIndex() < 0:
            self.standard_combo.setCurrentIndex(0)
        self._refresh_standard()

    def _preferred_size(self, sizes: list[str]) -> str:
        from VibeCADFasteners import _catalog_search_terms

        query_terms = _catalog_search_terms(self.filter_edit.text())
        normalized = [
            (size, re.sub(r"\s+", "", size).casefold())
            for size in sizes
        ]
        for term in query_terms:
            for size, candidate in normalized:
                if term == candidate:
                    return size
        for term in query_terms:
            for size, candidate in normalized:
                if term and term in candidate:
                    return size
        return ""

    def _refresh_standard(self, *_args: Any) -> None:
        from VibeCADFasteners import describe_standard

        standard = str(self._data(self.standard_combo) or "")
        self.size_combo.blockSignals(True)
        self.size_combo.clear()
        if not standard:
            self.description_label.setText(_translate("No catalog match."))
            self.size_combo.blockSignals(False)
            self._refresh_size()
            return
        details = describe_standard(standard)
        self.description_label.setText(str(details["description"]))
        for size in details["nominal_threads"]:
            self.size_combo.addItem(str(size), str(size))
        requested = self._preferred_size(list(details["nominal_threads"]))
        if not requested and standard == str(
            self._initial.get("standard") or ""
        ):
            requested = self._initial.get("nominal_size")
        if requested:
            self._select_data(self.size_combo, requested)
        self.size_combo.blockSignals(False)
        self._refresh_size()

    def _refresh_size(self, *_args: Any) -> None:
        from VibeCADFasteners import describe_standard

        standard = str(self._data(self.standard_combo) or "")
        size = str(self._data(self.size_combo) or "")
        self.length_combo.blockSignals(True)
        self.length_combo.clear()
        self.length_combo.setEditable(False)
        if not standard or not size:
            self.length_combo.setEnabled(False)
            self.model_thread.setEnabled(False)
            self.model_thread.setChecked(False)
            self.left_handed.setEnabled(False)
            self.length_combo.blockSignals(False)
            return
        details = describe_standard(standard, nominal_thread=size)
        requires_length = bool(details["requires_length"])
        self.length_combo.setEnabled(requires_length)
        if requires_length and details["arbitrary_length"]:
            self.length_combo.setEditable(True)
            default_length = float(details["default_length_mm"])
            self.length_combo.addItem(f"{default_length:g}", default_length)
        elif requires_length:
            for row in details.get("lengths", []):
                length = float(row["millimeters"])
                self.length_combo.addItem(f"{length:g}", length)
        initial_length = (
            self._initial.get("length_mm")
            if standard == str(self._initial.get("standard") or "")
            and size == str(self._initial.get("nominal_size") or "")
            else None
        )
        if initial_length is not None:
            selected = False
            for index in range(self.length_combo.count()):
                value = self.length_combo.itemData(index)
                if value is not None and abs(
                    float(value) - float(initial_length)
                ) <= 1.0e-7:
                    self.length_combo.setCurrentIndex(index)
                    selected = True
                    break
            if not selected and self.length_combo.isEditable():
                self.length_combo.setEditText(f"{float(initial_length):g}")
        self.length_combo.blockSignals(False)

        self.model_thread.setEnabled(bool(details["supports_model_thread"]))
        self.model_thread.setChecked(
            bool(self._initial.get("model_thread", True))
            if details["supports_model_thread"]
            else False
        )
        self.left_handed.setEnabled(bool(details["supports_left_handed"]))
        self.left_handed.setChecked(
            bool(self._initial.get("left_handed"))
            if details["supports_left_handed"]
            else False
        )

    def _accept(self) -> None:
        try:
            self.values()
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.dialog.accept()

    def values(self) -> dict[str, Any]:
        from VibeCADFasteners import describe_standard, resolve_fastener

        standard = str(self._data(self.standard_combo) or "")
        size = str(self._data(self.size_combo) or "")
        if not standard or not size:
            raise ValueError(_translate("Select an exact catalog standard and size."))
        details = describe_standard(standard, nominal_thread=size)
        length = None
        if details["requires_length"]:
            raw = self._data(self.length_combo)
            if self.length_combo.isEditable():
                raw = self.length_combo.currentText()
            try:
                length = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    _translate("Length must be a positive number in millimeters.")
                ) from exc
        identity = resolve_fastener(
            standard=standard,
            nominal_thread=size,
            length_mm=length,
            model_thread=bool(self.model_thread.isChecked()),
            left_handed=bool(self.left_handed.isChecked()),
        )
        return {
            "standard": identity["standard"],
            "nominal_thread": identity["nominal_size"],
            "length_mm": identity["length_mm"],
            "model_thread": identity["model_thread"],
            "left_handed": identity["left_handed"],
            "options": identity["options"],
            "label": self.label_edit.text().strip(),
            "identity": identity,
        }

    def exec(self) -> dict[str, Any] | None:
        if self.dialog.exec_() != self._QtGui.QDialog.Accepted:
            return None
        return self.values()


class _InsertStandardFastenerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-insert.svg"),
            "MenuText": _translate("Insert Standard Fastener"),
            "ToolTip": _translate(
                "Insert an exact native component from the bundled standards catalog"
            ),
            "CmdType": "AlterDoc",
        }

    def IsActive(self) -> bool:
        if Gui.Control.activeDialog():
            return False
        return (
            App.ActiveDocument is not None
            and _can_start_modeling_transaction()
            and _active_workbench()
            in {"PartDesignWorkbench", "AssemblyWorkbench"}
            and _catalog_available()
        )

    def Activated(self) -> None:
        dialog = _FastenerDialog(title="Insert Standard Fastener")
        values = dialog.exec()
        if values is None:
            return
        document = App.ActiveDocument
        if not _document_transaction_is_clean(document):
            return
        workbench = _active_workbench()
        visible_label = str(
            values["label"] or values["identity"]["part_number"]
        )
        document.openTransaction(_translate("Insert standard fastener"))
        try:
            if workbench == "AssemblyWorkbench":
                import UtilsAssembly

                assembly = UtilsAssembly.activeAssembly()
                if assembly is None:
                    raise RuntimeError(
                        _translate(
                            "Create or activate an Assembly before inserting "
                            "a standard fastener."
                        )
                    )
                graph = create_assembly_fastener_graph(
                    document,
                    assembly=assembly,
                    label=visible_label,
                    **{
                        key: values[key]
                        for key in (
                            "standard",
                            "nominal_thread",
                            "length_mm",
                            "model_thread",
                            "left_handed",
                            "options",
                        )
                    },
                )
                selected = graph.occurrence
            else:
                graph = create_model_fastener_graph(
                    document,
                    label=visible_label,
                    **{
                        key: values[key]
                        for key in (
                            "standard",
                            "nominal_thread",
                            "length_mm",
                            "model_thread",
                            "left_handed",
                            "options",
                        )
                    },
                )
                selected = graph.body
            document.recompute()
            document.commitTransaction()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(selected)
            if Gui.ActiveDocument is not None:
                Gui.ActiveDocument.ActiveView.fitAll()
        except Exception as exc:
            document.abortTransaction()
            _show_error("Insert Standard Fastener", exc)


def identity_label(values: Mapping[str, Any]) -> str:
    return str(values.get("label") or dict(values["identity"])["part_number"])


class _EditStandardFastenerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-edit.svg"),
            "MenuText": _translate("Edit Standard Fastener"),
            "ToolTip": _translate(
                "Change exact dimensions, compatible standard, or real thread geometry"
            ),
            "CmdType": "AlterDoc",
        }

    def IsActive(self) -> bool:
        if Gui.Control.activeDialog():
            return False
        return (
            App.ActiveDocument is not None
            and _can_start_modeling_transaction()
            and _catalog_available()
            and len(_selected_fasteners()) == 1
        )

    def Activated(self) -> None:
        from VibeCADFastenerModel import edit_model_fastener_graph
        from VibeCADFasteners import (
            compatible_fastener_standards,
            fastener_feature_identity,
            update_fastener_feature,
        )

        selected = _selected_fasteners()
        if len(selected) != 1:
            _show_information(
                "Edit Standard Fastener",
                "Select exactly one standard fastener or Assembly occurrence.",
            )
            return
        occurrence, target = selected[0]
        label_owner = _fastener_label_owner(occurrence, target)
        try:
            initial = fastener_feature_identity(target)
            compatible = compatible_fastener_standards(target)
            dialog = _FastenerDialog(
                title="Edit Standard Fastener",
                allowed_standards=compatible,
                initial=initial,
                initial_label=str(getattr(label_owner, "Label", "") or ""),
            )
            values = dialog.exec()
            if values is None:
                return
            document = target.Document
            if not _document_transaction_is_clean(document):
                return
            document.openTransaction(_translate("Edit standard fastener"))
            try:
                operation = _generated_fastener_operation(target)
                visible_label = str(
                    values["label"] or values["identity"]["part_number"]
                )
                update_values = {
                    key: values[key]
                    for key in (
                        "standard",
                        "nominal_thread",
                        "length_mm",
                        "model_thread",
                        "left_handed",
                        "options",
                        "label",
                    )
                }
                legacy_body = (
                    _legacy_model_fastener_body(target)
                    if operation is None
                    else None
                )
                if legacy_body is not None:
                    update_values["label"] = f"{visible_label} generator"

                    def configure(converted_operation, converted_generator):
                        update_fastener_feature(
                            converted_generator,
                            **update_values,
                        )
                        converted_operation.Label = (
                            f"Fastener: {visible_label}"
                        )
                        converted_operation.OutputLabel = visible_label

                    body, operation, _generator, _initial = (
                        _migrate_legacy_model_fastener(
                            target,
                            configure=configure,
                            output_label=visible_label,
                        )
                    )
                    body.Label = visible_label
                else:
                    if operation is not None:
                        body = _generated_fastener_body(operation)
                        if body is None:
                            raise RuntimeError(
                                _translate(
                                    "The edited standard fastener has no exact Body."
                                )
                            )
                        edit_model_fastener_graph(
                            document,
                            body=body,
                            label=visible_label,
                            standard=values["standard"],
                            nominal_thread=values["nominal_thread"],
                            length_mm=values["length_mm"],
                            model_thread=values["model_thread"],
                            left_handed=values["left_handed"],
                            options=values["options"],
                        )
                    else:
                        if label_owner is not target:
                            label_owner.Label = visible_label
                        update_fastener_feature(target, **update_values)
                        document.recompute()
                document.commitTransaction()
            except Exception:
                document.abortTransaction()
                raise
        except Exception as exc:
            _show_error("Edit Standard Fastener", exc)


def _selected_hole_inputs() -> tuple[Any, Any, list[Any]]:
    fasteners = _selected_fasteners()
    sketches = [
        obj
        for obj in Gui.Selection.getSelection()
        if str(getattr(obj, "TypeId", "") or "") == "Sketcher::SketchObject"
    ]
    if len(fasteners) != 1 or len(sketches) != 1:
        raise RuntimeError(
            _translate(
                "Select one standard fastener and one Part Design sketch "
                "containing the hole locations."
            )
        )
    occurrence, fastener = fasteners[0]
    sketch = sketches[0]
    if sketch.getParentGeoFeatureGroup() is not None:
        raise RuntimeError(
            _translate(
                "The selected hole-location sketch must be a reusable Design sketch, "
                "not a sketch owned by one Body."
            )
        )
    bodies: list[Any] = []
    for obj in Gui.Selection.getSelection():
        if str(getattr(obj, "TypeId", "") or "") != "PartDesign::Body":
            continue
        if obj is occurrence or _fastener_target(obj) is fastener:
            continue
        if obj.Document is not sketch.Document:
            raise RuntimeError(
                _translate("Every selected target Body must be in the sketch document.")
            )
        if obj not in bodies:
            bodies.append(obj)
    if not bodies:
        raise RuntimeError(
            _translate(
                "Select at least one target Body with the standard fastener "
                "and reusable hole-location sketch."
            )
        )
    return sketch, fastener, bodies


def _selected_attachment_inputs() -> tuple[Any, Any, Any, str]:
    """Return one native fastener and one circular host edge."""

    import Part

    fasteners = _selected_fasteners()
    if len(fasteners) != 1:
        raise RuntimeError(_translate("Select exactly one standard fastener."))
    occurrence, fastener = fasteners[0]
    if (
        occurrence is not fastener
        and getattr(occurrence, "LinkedObject", None) is not None
    ):
        raise RuntimeError(
            _translate(
                "Use Assembly connectors and joints to place an Assembly occurrence."
            )
        )
    if (
        _generated_fastener_operation(fastener) is None
        and _legacy_model_fastener_body(fastener) is None
    ):
        raise RuntimeError(
            _translate(
                "Select a Model fastener or one convertible legacy Body-owned fastener."
            )
        )

    circular: list[tuple[Any, str]] = []
    for selected in Gui.Selection.getSelectionEx("", 0):
        if selected.Object is occurrence:
            continue
        for sub_name in selected.SubElementNames:
            shape = Part.getShape(
                selected.Object,
                sub_name,
                needSubElement=True,
                noElementMap=True,
            )
            curve = getattr(shape, "Curve", None)
            if curve is not None and curve.isDerivedFrom("Part::GeomCircle"):
                circular.append((selected.Object, str(sub_name)))
    if len(circular) != 1:
        raise RuntimeError(
            _translate(
                "Select exactly one circular hole edge with the standard fastener."
            )
        )
    host, sub_name = circular[0]
    return occurrence, fastener, host, sub_name


class _CreateMatchingHoleCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-hole.svg"),
            "MenuText": _translate("Create Matching Fastener Hole"),
            "ToolTip": _translate(
                "Create a native Part Design hole derived from the selected standard component"
            ),
            "CmdType": "AlterDoc",
        }

    def IsActive(self) -> bool:
        if Gui.Control.activeDialog():
            return False
        if (
            App.ActiveDocument is None
            or not _can_start_modeling_transaction()
            or _active_workbench() != "PartDesignWorkbench"
            or not _catalog_available()
        ):
            return False
        try:
            _selected_hole_inputs()
            return True
        except Exception:
            return False

    def Activated(self) -> None:
        from PySide import QtGui
        from VibeCADFasteners import (
            configure_fastener_hole_feature,
            resolve_fastener_hole,
        )

        try:
            sketch, fastener, bodies = _selected_hole_inputs()
            supported = []
            for purpose in (
                "clearance",
                "tapped",
                "counterbore",
                "countersink",
            ):
                try:
                    resolve_fastener_hole(
                        fastener,
                        purpose=purpose,
                        fit="normal",
                    )
                    supported.append(purpose)
                except Exception:
                    continue
            if not supported:
                raise RuntimeError(
                    _translate(
                        "The selected standard has no exact matching native "
                        "Part Design hole definition."
                    )
                )
            purpose, accepted = QtGui.QInputDialog.getItem(
                Gui.getMainWindow(),
                _translate("Create Matching Fastener Hole"),
                _translate("Purpose"),
                supported,
                0,
                False,
            )
            if not accepted:
                return
            fit = "normal"
            if str(purpose) != "tapped":
                fit, accepted = QtGui.QInputDialog.getItem(
                    Gui.getMainWindow(),
                    _translate("Create Matching Fastener Hole"),
                    _translate("Fit"),
                    ["normal", "close", "loose"],
                    0,
                    False,
                )
                if not accepted:
                    return
            document = sketch.Document
            if not _document_transaction_is_clean(document):
                return
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(sketch)
            for body in bodies:
                Gui.Selection.addSelection(body)
            Gui.runCommand("PartDesign_Hole", 0)
            feature = document.ActiveObject
            if (
                feature is None
                or str(getattr(feature, "TypeId", "") or "")
                != "PartDesign::DesignHole"
                or not Gui.Control.activeDialog()
            ):
                raise RuntimeError(
                    _translate("The global matching-hole task did not open.")
                )
            feature.DepthType = "ThroughAll"
            configure_fastener_hole_feature(
                feature,
                fastener,
                purpose=str(purpose),
                fit=str(fit),
            )
            feature.Refine = True
            feature.Label = _translate("Matching standard fastener hole")
            if bodies:
                document.recompute()
        except Exception as exc:
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except (AttributeError, RuntimeError):
                    Gui.Control.closeDialog()
            _show_error("Create Matching Fastener Hole", exc)


class _AttachStandardFastenerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-attach.svg"),
            "MenuText": _translate("Attach Standard Fastener"),
            "ToolTip": _translate(
                "Align the selected standard fastener axis to one selected circular edge"
            ),
            "CmdType": "AlterDoc",
        }

    def IsActive(self) -> bool:
        if Gui.Control.activeDialog():
            return False
        if (
            App.ActiveDocument is None
            or not _can_start_modeling_transaction()
            or _active_workbench() != "PartDesignWorkbench"
            or not _catalog_available()
        ):
            return False
        try:
            _selected_attachment_inputs()
            return True
        except Exception:
            return False

    def Activated(self) -> None:
        try:
            _occurrence, fastener, host, sub_name = (
                _selected_attachment_inputs()
            )
            operation = _generated_fastener_operation(fastener)
            import PartDesign

            document = fastener.Document
            if not _document_transaction_is_clean(document):
                return
            document.openTransaction(_translate("Attach standard fastener"))
            try:
                if operation is None:
                    def configure(converted_operation, converted_generator):
                        exact_host, exact_subelements = (
                            PartDesign.resolveDesignDefinitionSubelementReference(
                                converted_operation,
                                host,
                                [sub_name],
                            )
                        )
                        converted_generator.BaseObject = (
                            exact_host,
                            list(exact_subelements),
                        )

                    outputs = [
                        _migrate_legacy_model_fastener(
                            fastener,
                            configure=configure,
                            preserve_history_position=False,
                        )[0]
                    ]
                else:
                    body = _generated_fastener_body(operation)
                    if body is None:
                        raise RuntimeError(
                            _translate(
                                "The attached standard fastener has no exact Body."
                            )
                        )
                    attachment = attach_model_fastener_graph(
                        document,
                        body=body,
                        host=host,
                        subelement=sub_name,
                    )
                    outputs = [attachment.graph.body]
                if len(outputs) != 1:
                    raise RuntimeError(
                        _translate(
                            "The attached standard fastener did not retain one Body."
                        )
                    )
                _copy_fastener_appearance(fastener, outputs[0])
                document.commitTransaction()
            except Exception:
                document.abortTransaction()
                raise
        except Exception as exc:
            _show_error("Attach Standard Fastener", exc)


def ensure_commands_registered() -> None:
    global _COMMANDS_REGISTERED
    if _COMMANDS_REGISTERED:
        return
    Gui.addCommand(
        "VibeCAD_InsertStandardFastener",
        _InsertStandardFastenerCommand(),
    )
    Gui.addCommand(
        "VibeCAD_EditStandardFastener",
        _EditStandardFastenerCommand(),
    )
    Gui.addCommand(
        "VibeCAD_CreateMatchingFastenerHole",
        _CreateMatchingHoleCommand(),
    )
    Gui.addCommand(
        "VibeCAD_AttachStandardFastener",
        _AttachStandardFastenerCommand(),
    )
    for action in Gui.Command.get(
        "VibeCAD_EditStandardFastener"
    ).ensureAction():
        action.setProperty("VibeCADTimelineOperationEditor", True)
    _COMMANDS_REGISTERED = True
