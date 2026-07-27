# SPDX-License-Identifier: LGPL-2.1-or-later

"""Integrated standard-component commands for Part Design and Assembly."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui


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


def _safe_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not clean or not re.match(r"[A-Za-z_]", clean):
        clean = fallback
    return clean[:96]


def _fastener_target(obj: Any) -> Any | None:
    from VibeCADFasteners import COMPONENT_SCHEMA, PROP_SCHEMA

    if str(getattr(obj, PROP_SCHEMA, "") or "") == COMPONENT_SCHEMA:
        return obj
    linked = getattr(obj, "LinkedObject", None)
    if (
        linked is not None
        and str(getattr(linked, PROP_SCHEMA, "") or "") == COMPONENT_SCHEMA
    ):
        return linked
    if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
        matches = [
            child
            for child in list(getattr(obj, "Group", []) or [])
            if str(getattr(child, PROP_SCHEMA, "") or "") == COMPONENT_SCHEMA
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _selected_fasteners() -> list[tuple[Any, Any]]:
    result: list[tuple[Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    for selected in Gui.Selection.getSelection():
        target = _fastener_target(selected)
        if target is None:
            continue
        key = (
            str(getattr(target.Document, "Uid", "") or target.Document.Name),
            str(target.Name),
        )
        if key not in seen:
            seen.add(key)
            result.append((selected, target))
    return result


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
            bool(self._initial.get("model_thread"))
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
        }

    def IsActive(self) -> bool:
        return (
            App.ActiveDocument is not None
            and _active_workbench()
            in {"PartDesignWorkbench", "AssemblyWorkbench"}
            and _catalog_available()
        )

    def Activated(self) -> None:
        from VibeCADFasteners import create_fastener_feature

        dialog = _FastenerDialog(title="Insert Standard Fastener")
        values = dialog.exec()
        if values is None:
            return
        document = App.ActiveDocument
        workbench = _active_workbench()
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
                source, identity = create_fastener_feature(
                    document,
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
                    object_name=_safe_name(
                        f"{values['standard']}_{values['nominal_thread']}_Definition",
                        "StandardFastenerDefinition",
                    ),
                    label=str(identity_label(values)),
                )
                source.ViewObject.Visibility = False
                if hasattr(source.ViewObject, "ShowInTree"):
                    source.ViewObject.ShowInTree = False
                occurrence = assembly.newObject(
                    "App::Link",
                    _safe_name(
                        str(values["label"] or identity["part_number"]),
                        "StandardFastener",
                    ),
                )
                occurrence.LinkedObject = source
                occurrence.Label = str(values["label"] or identity["part_number"])
                selected = occurrence
            else:
                body = document.addObject(
                    "PartDesign::Body",
                    _safe_name(
                        str(values["label"] or "StandardFastener"),
                        "StandardFastener",
                    ),
                )
                feature, identity = create_fastener_feature(
                    body,
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
                    object_name="Fastener",
                    label=str(values["label"] or values["identity"]["part_number"]),
                )
                body.Label = str(values["label"] or identity["part_number"])
                body.Tip = feature
                selected = body
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
        }

    def IsActive(self) -> bool:
        return (
            App.ActiveDocument is not None
            and _catalog_available()
            and len(_selected_fasteners()) == 1
        )

    def Activated(self) -> None:
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
        try:
            initial = fastener_feature_identity(target)
            compatible = compatible_fastener_standards(target)
            dialog = _FastenerDialog(
                title="Edit Standard Fastener",
                allowed_standards=compatible,
                initial=initial,
                initial_label=str(getattr(occurrence, "Label", "") or ""),
            )
            values = dialog.exec()
            if values is None:
                return
            document = target.Document
            document.openTransaction(_translate("Edit standard fastener"))
            try:
                update_fastener_feature(
                    target,
                    **{
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
                    },
                )
                if occurrence is not target:
                    occurrence.Label = str(
                        values["label"] or values["identity"]["part_number"]
                    )
                document.recompute()
                document.commitTransaction()
            except Exception:
                document.abortTransaction()
                raise
        except Exception as exc:
            _show_error("Edit Standard Fastener", exc)


def _selected_hole_inputs() -> tuple[Any, Any]:
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
    _occurrence, fastener = fasteners[0]
    sketch = sketches[0]
    body = sketch.getParentGeoFeatureGroup()
    if body is None or str(getattr(body, "TypeId", "") or "") != "PartDesign::Body":
        raise RuntimeError(
            _translate("The selected hole-location sketch must belong to a Body.")
        )
    return sketch, fastener


class _CreateMatchingHoleCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-hole.svg"),
            "MenuText": _translate("Create Matching Fastener Hole"),
            "ToolTip": _translate(
                "Create a native Part Design hole derived from the selected standard component"
            ),
        }

    def IsActive(self) -> bool:
        return (
            App.ActiveDocument is not None
            and _active_workbench() == "PartDesignWorkbench"
            and _catalog_available()
        )

    def Activated(self) -> None:
        from PySide import QtGui
        from VibeCADFasteners import (
            configure_fastener_hole_feature,
            resolve_fastener_hole,
        )

        try:
            sketch, fastener = _selected_hole_inputs()
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
            body = sketch.getParentGeoFeatureGroup()
            document = sketch.Document
            document.openTransaction(_translate("Create matching fastener hole"))
            try:
                feature = body.newObject(
                    "PartDesign::Hole",
                    "StandardFastenerHole",
                )
                feature.Profile = sketch
                feature.DepthType = "ThroughAll"
                configure_fastener_hole_feature(
                    feature,
                    fastener,
                    purpose=str(purpose),
                    fit=str(fit),
                )
                feature.Refine = True
                feature.Label = _translate("Matching standard fastener hole")
                body.Tip = feature
                document.recompute()
                if (
                    feature.Shape.isNull()
                    or not feature.Shape.isValid()
                    or len(feature.Shape.Solids) != 1
                ):
                    raise RuntimeError(
                        _translate(
                            "The matching hole did not produce one valid solid; "
                            "check the sketch placement and base material."
                        )
                    )
                document.commitTransaction()
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(feature)
            except Exception:
                document.abortTransaction()
                raise
        except Exception as exc:
            _show_error("Create Matching Fastener Hole", exc)


class _AttachStandardFastenerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _icon("vibecad-fastener-attach.svg"),
            "MenuText": _translate("Attach Standard Fastener"),
            "ToolTip": _translate(
                "Align the selected standard fastener axis to one selected circular edge"
            ),
        }

    def IsActive(self) -> bool:
        return (
            App.ActiveDocument is not None
            and _active_workbench() == "PartDesignWorkbench"
            and _catalog_available()
        )

    def Activated(self) -> None:
        import Part

        try:
            fasteners = _selected_fasteners()
            if len(fasteners) != 1:
                raise RuntimeError(
                    _translate("Select exactly one standard fastener.")
                )
            occurrence, fastener = fasteners[0]
            if occurrence is not fastener:
                raise RuntimeError(
                    _translate(
                        "Use Assembly connectors and joints to place an "
                        "Assembly occurrence."
                    )
                )
            circular = []
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
                    if curve is not None and curve.isDerivedFrom(
                        "Part::GeomCircle"
                    ):
                        circular.append((selected.Object, str(sub_name)))
            if len(circular) != 1:
                raise RuntimeError(
                    _translate(
                        "Select exactly one circular hole edge with the "
                        "standard fastener."
                    )
                )
            host, sub_name = circular[0]
            document = fastener.Document
            document.openTransaction(_translate("Attach standard fastener"))
            try:
                fastener.BaseObject = (host, [sub_name])
                document.recompute()
                if str(getattr(fastener, "VibeCADFastenerError", "") or ""):
                    raise RuntimeError(str(fastener.VibeCADFastenerError))
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
    _COMMANDS_REGISTERED = True
