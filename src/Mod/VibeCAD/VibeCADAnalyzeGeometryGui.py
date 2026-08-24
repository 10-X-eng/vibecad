# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact geometry and face browser shared by the Analyze study panel."""

from __future__ import annotations

from typing import Any

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADNativeAnalyzeGeometryRead import inspect_geometry_source
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources
from VibeCADNativeAnalyzeSolidDomain import (
    create_solid_domain,
    prepare_solid_domain,
    verify_solid_domain,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import run_human_mutation


_PAGE_SIZE = 64


def active_geometry_sources(document: Any) -> tuple[Any, ...]:
    return tuple(
        source
        for source in active_analyze_geometry_sources(document)
        if len(source.Shape.Faces) > 0
    )


def create_solid_analysis_domain(
    document: Any,
    source_names: list[str],
    interface_mode: str,
    label: str,
) -> dict[str, Any]:
    """Create a derived domain through the production Analyze mutation."""

    available = {
        str(source.Name): source for source in active_geometry_sources(document)
    }
    names = tuple(str(name or "") for name in source_names)
    if not 2 <= len(names) <= 256 or len(names) != len(set(names)):
        raise ValueError("Select 2 to 256 distinct solid geometry sources.")
    missing = tuple(name for name in names if name not in available)
    if missing:
        raise ValueError(
            f"Geometry source {missing[0]} is no longer available for analysis."
        )
    targets = []
    for name in names:
        state = mesh_object_state(available[name])
        targets.append(
            {
                "object_name": str(state["object_name"]),
                "expected_state_sha256": str(state["state_sha256"]),
            }
        )
    prepared = prepare_solid_domain(
        document,
        str(document.Uid),
        sources=targets,
        interface_mode=interface_mode,
        label=label,
    )
    return run_human_mutation(
        document=document,
        transaction_name="Create Solid Analysis Domain",
        mutate=lambda current: create_solid_domain(current, prepared),
        verify=verify_solid_domain,
    )


def _vector_text(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return ""
    return ", ".join(f"{float(item):g}" for item in value)


class AnalyzeGeometryBrowser(QtWidgets.QGroupBox):
    def __init__(self, parent: Any = None) -> None:
        super().__init__("Geometry", parent)
        self.setObjectName("VibeCADAnalyzeGeometryBrowser")
        self._document: Any | None = None
        self._sources: dict[str, Any] = {}
        self._offset = 0
        self._visibility: dict[Any, bool] | None = None
        self._build()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        source_row = QtWidgets.QHBoxLayout()
        source_row.addWidget(QtWidgets.QLabel("Source"))
        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.setObjectName("VibeCADAnalyzeGeometrySource")
        self.source_combo.setEditable(True)
        self.source_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        completer = self.source_combo.completer()
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        source_row.addWidget(self.source_combo, 1)
        self.domain_button = QtWidgets.QPushButton("Create Domain…")
        self.domain_button.setObjectName("VibeCAD_AnalyzeCreateSolidDomain")
        self.domain_button.setToolTip(
            "Create one analysis domain from two or more solid sources."
        )
        self.domain_button.clicked.connect(self._open_domain_dialog)
        source_row.addWidget(self.domain_button)
        layout.addLayout(source_row)

        self.face_table = QtWidgets.QTreeWidget()
        self.face_table.setObjectName("VibeCADAnalyzeGeometryFaces")
        self.face_table.setHeaderLabels(
            ("Face", "Surface", "Area (mm²)", "Center (mm)", "Normal")
        )
        self.face_table.setRootIsDecorated(False)
        self.face_table.setAlternatingRowColors(True)
        self.face_table.setMinimumHeight(180)
        self.face_table.currentItemChanged.connect(self._face_selected)
        layout.addWidget(self.face_table)

        page_row = QtWidgets.QHBoxLayout()
        self.previous_button = QtWidgets.QPushButton("Previous")
        self.previous_button.clicked.connect(self._previous_page)
        page_row.addWidget(self.previous_button)
        self.page_label = QtWidgets.QLabel()
        self.page_label.setAlignment(QtCore.Qt.AlignCenter)
        page_row.addWidget(self.page_label, 1)
        self.next_button = QtWidgets.QPushButton("Next")
        self.next_button.clicked.connect(self._next_page)
        page_row.addWidget(self.next_button)
        layout.addLayout(page_row)

        action_row = QtWidgets.QHBoxLayout()
        self.highlight_button = QtWidgets.QPushButton("Highlight Face")
        self.highlight_button.clicked.connect(self.highlight_face)
        action_row.addWidget(self.highlight_button)
        self.isolate_button = QtWidgets.QPushButton("Isolate Source")
        self.isolate_button.clicked.connect(self.isolate_source)
        action_row.addWidget(self.isolate_button)
        self.restore_button = QtWidgets.QPushButton("Show All")
        self.restore_button.clicked.connect(self.restore_view)
        self.restore_button.setEnabled(False)
        action_row.addWidget(self.restore_button)
        layout.addLayout(action_row)

        self.message_label = QtWidgets.QLabel()
        self.message_label.setObjectName("VibeCADAnalyzeGeometryMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self._update_buttons()

    def refresh(self, document: Any | None) -> None:
        previous = str(self.source_combo.currentData() or "")
        self._document = document
        sources = active_geometry_sources(document) if document is not None else ()
        self._sources = {str(source.Name): source for source in sources}
        preferred = ""
        if document is not None:
            try:
                selected = tuple(Gui.Selection.getSelection(document.Name) or ())
            except Exception:
                selected = ()
            preferred = next(
                (
                    str(obj.Name)
                    for obj in selected
                    if str(getattr(obj, "Name", "")) in self._sources
                ),
                "",
            )
        if not preferred and previous in self._sources:
            preferred = previous

        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in sources:
            label = str(getattr(source, "Label", "") or source.Name)
            text = label if label == str(source.Name) else f"{label} ({source.Name})"
            self.source_combo.addItem(text, str(source.Name))
        if preferred:
            self.source_combo.setCurrentIndex(
                max(0, self.source_combo.findData(preferred))
            )
        self.source_combo.blockSignals(False)
        self._offset = 0
        self._load_page()

    def _source(self) -> Any | None:
        return self._sources.get(str(self.source_combo.currentData() or ""))

    def _source_changed(self, _index: int) -> None:
        self._offset = 0
        self._load_page()

    def _load_page(self) -> None:
        self.face_table.clear()
        source = self._source()
        if self._document is None or source is None:
            self.page_label.setText("No active geometry")
            self.message_label.clear()
            self._update_buttons()
            return
        try:
            state = mesh_object_state(source)
            result = inspect_geometry_source(
                self._document,
                str(self._document.Uid),
                {
                    "object_name": str(source.Name),
                    "expected_state_sha256": str(state["state_sha256"]),
                },
                offset=self._offset,
                page_size=_PAGE_SIZE,
            )["face_page"]
            for face in result["faces"]:
                item = QtWidgets.QTreeWidgetItem(
                    (
                        str(face["subelement"]),
                        str(face["surface"]).replace("_", " ").title(),
                        f"{float(face['area_mm2']):g}",
                        _vector_text(face.get("center_mm")),
                        _vector_text(face.get("normal")),
                    )
                )
                item.setData(0, QtCore.Qt.UserRole, str(face["subelement"]))
                self.face_table.addTopLevelItem(item)
            returned = int(result["returned"])
            total = int(result["total"])
            start = self._offset + 1 if returned else 0
            self.page_label.setText(f"{start}–{self._offset + returned} of {total}")
            self.previous_button.setEnabled(self._offset > 0)
            self.next_button.setEnabled(result.get("next_offset") is not None)
            self.message_label.clear()
            if returned:
                self.face_table.setCurrentItem(self.face_table.topLevelItem(0))
        except Exception as exc:
            self.page_label.clear()
            self.message_label.setText(str(exc))
        self.face_table.resizeColumnToContents(0)
        self.face_table.resizeColumnToContents(1)
        self._update_buttons()

    def _previous_page(self) -> None:
        self._offset = max(0, self._offset - _PAGE_SIZE)
        self._load_page()

    def _next_page(self) -> None:
        self._offset += _PAGE_SIZE
        self._load_page()

    def _face_selected(self, _current: Any, _previous: Any) -> None:
        self._update_buttons()

    def _face_name(self) -> str:
        item = self.face_table.currentItem()
        return str(item.data(0, QtCore.Qt.UserRole) or "") if item else ""

    def _update_buttons(self) -> None:
        source = self._source()
        self.domain_button.setEnabled(len(self._sources) > 1)
        self.highlight_button.setEnabled(source is not None and bool(self._face_name()))
        self.isolate_button.setEnabled(source is not None)
        self.restore_button.setEnabled(self._visibility is not None)

    def create_domain(
        self,
        source_names: list[str],
        interface_mode: str,
        label: str,
    ) -> dict[str, Any]:
        if self._document is None:
            raise RuntimeError("No analysis document is open.")
        result = create_solid_analysis_domain(
            self._document,
            source_names,
            interface_mode,
            label,
        )
        domain_name = str(result["domain"]["object_name"])
        self.refresh(self._document)
        index = self.source_combo.findData(domain_name)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)
        self.message_label.setText(
            f"Created {result['source_count']}-source {interface_mode} domain."
        )
        return result

    def _open_domain_dialog(self) -> None:
        if self._document is None or len(self._sources) < 2:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Create Solid Analysis Domain")
        dialog.setModal(True)
        layout = QtWidgets.QVBoxLayout(dialog)

        layout.addWidget(QtWidgets.QLabel("Solid sources"))
        sources = QtWidgets.QListWidget()
        sources.setObjectName("VibeCADAnalyzeSolidDomainSources")
        sources.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        for name, source in self._sources.items():
            item = QtWidgets.QListWidgetItem(
                str(getattr(source, "Label", "") or name)
            )
            item.setData(QtCore.Qt.UserRole, name)
            sources.addItem(item)
            item.setSelected(True)
        layout.addWidget(sources)

        form = QtWidgets.QFormLayout()
        interface = QtWidgets.QComboBox()
        interface.setObjectName("VibeCADAnalyzeSolidDomainInterface")
        interface.addItem("Separate — tie or contact", "separate")
        interface.addItem("Shared — bonded", "shared")
        form.addRow("Interfaces", interface)
        label = QtWidgets.QLineEdit("Solid analysis domain")
        label.setObjectName("VibeCADAnalyzeSolidDomainLabel")
        form.addRow("Name", label)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        create_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        create_button.setText("Create")
        selected_count = lambda: len(sources.selectedItems())
        sources.itemSelectionChanged.connect(
            lambda: create_button.setEnabled(selected_count() >= 2)
        )
        create_button.setEnabled(selected_count() >= 2)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        selected_names = [
            str(item.data(QtCore.Qt.UserRole) or "")
            for item in sources.selectedItems()
        ]
        try:
            self.create_domain(
                selected_names,
                str(interface.currentData()),
                label.text(),
            )
        except Exception as exc:
            self.message_label.setText(str(exc))
            QtWidgets.QMessageBox.critical(
                self,
                "Solid Analysis Domain",
                str(exc),
            )

    def highlight_face(self) -> None:
        source = self._source()
        face_name = self._face_name()
        if self._document is None or source is None or not face_name:
            return
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            str(self._document.Name),
            str(source.Name),
            face_name,
        )

    def isolate_source(self) -> None:
        source = self._source()
        if source is None or self._document is None:
            return
        if self._visibility is None:
            self._visibility = {
                candidate: bool(candidate.ViewObject.Visibility)
                for candidate in active_geometry_sources(self._document)
            }
        for candidate in self._visibility:
            candidate.ViewObject.Visibility = candidate is source
        self._update_buttons()

    def restore_view(self) -> None:
        if self._visibility is None:
            return
        for candidate, visible in self._visibility.items():
            if getattr(candidate, "Document", None) is not None:
                candidate.ViewObject.Visibility = visible
        self._visibility = None
        self._update_buttons()
