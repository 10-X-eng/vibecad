# SPDX-License-Identifier: LGPL-2.1-or-later

"""Low-barrier Study Setup surface for the Analyze ribbon."""

from __future__ import annotations

from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADAnalyzeStudySetup import (
    analyses_in_document,
    analysis_for_selection,
    apply_study,
    readiness_rows,
)
from VibeCADNativeAnalyzeStudy import (
    STUDY_PHYSICS,
    STUDY_REGIMES,
    evaluate_study_readiness,
    study_intent_state,
)
from VibeCADNativeAnalyzeStudyState import study_inventory


COMMAND_NAME = "VibeCAD_AnalyzeStudySetup"
DOCK_NAME = "VibeCADAnalyzeStudySetup"
_registered = False


_BLOCKER_LABELS = {
    "missing_study_intent": "Choose the physics and study type",
    "missing_geometry": "Assign model geometry",
    "missing_mechanical_material": "Assign a mechanical material",
    "missing_thermal_material": "Assign a thermal material",
    "missing_fluid_material": "Assign a fluid material",
    "missing_support": "Add a support",
    "missing_mechanical_load": "Add a mechanical load",
    "missing_thermal_condition": "Add a thermal condition",
    "missing_initial_temperature": "Add an initial temperature",
    "missing_fluid_boundary": "Define the fluid boundaries",
    "missing_initial_fluid_state": "Define the initial fluid state",
    "missing_electromagnetic_equation": "Add an electromagnetic equation",
    "missing_electromagnetic_constraint": "Add an electromagnetic condition",
    "missing_heat_equation": "Add a heat equation",
    "missing_flow_equation": "Add a flow equation",
    "missing_mesh_definition": "Create a mesh definition",
    "missing_generated_mesh": "Generate the mesh",
    "missing_solver": "Add a solver",
    "multiple_active_solvers": "Keep one active solver",
}


def _active_document() -> Any | None:
    return getattr(App, "ActiveDocument", None)


def _selected_objects() -> tuple[Any, ...]:
    try:
        return tuple(Gui.Selection.getSelection() or ())
    except Exception:
        return ()


def _active_analysis(document: Any) -> Any | None:
    try:
        import FemGui

        candidate = FemGui.getActiveAnalysis()
    except Exception:
        return None
    return candidate if candidate in analyses_in_document(document) else None


class _RuntimeProbeSignals(QtCore.QObject):
    finished = QtCore.Signal(int, object, str)


class _RuntimeProbe(QtCore.QRunnable):
    def __init__(self, generation: int, solvers: tuple[str, ...]) -> None:
        super().__init__()
        self.generation = generation
        self.solvers = solvers
        self.signals = _RuntimeProbeSignals()

    def run(self) -> None:
        try:
            from femsolver.runtime import solver_runtime_statuses

            result = tuple(solver_runtime_statuses(self.solvers))
            self.signals.finished.emit(self.generation, result, "")
        except Exception as exc:
            self.signals.finished.emit(self.generation, (), str(exc))


class StudySetupWidget(QtWidgets.QWidget):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADAnalyzeStudySetupContent")
        self._generation = 0
        self._analysis_name = ""
        self._inventory: dict[str, Any] = {}
        self._intent: dict[str, Any] = {"declared": False}
        self._runtime_statuses: tuple[dict[str, Any], ...] = ()
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        heading = QtWidgets.QLabel("Study Setup")
        font = heading.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        heading.setFont(font)
        outer.addWidget(heading)

        selector_row = QtWidgets.QHBoxLayout()
        self.analysis_combo = QtWidgets.QComboBox()
        self.analysis_combo.setObjectName("VibeCADAnalyzeStudySelector")
        self.analysis_combo.currentIndexChanged.connect(self._analysis_changed)
        selector_row.addWidget(self.analysis_combo, 1)
        refresh_button = QtWidgets.QToolButton()
        refresh_button.setText("Refresh")
        refresh_button.setToolTip("Refresh study state")
        refresh_button.clicked.connect(self.refresh)
        selector_row.addWidget(refresh_button)
        outer.addLayout(selector_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        definition = QtWidgets.QGroupBox("Definition")
        form = QtWidgets.QFormLayout(definition)
        self.label_edit = QtWidgets.QLineEdit("Analysis")
        self.label_edit.setObjectName("VibeCADAnalyzeStudyLabel")
        form.addRow("Name", self.label_edit)

        physics_widget = QtWidgets.QWidget()
        physics_layout = QtWidgets.QGridLayout(physics_widget)
        physics_layout.setContentsMargins(0, 0, 0, 0)
        self.physics_checks: dict[str, QtWidgets.QCheckBox] = {}
        for index, physics in enumerate(STUDY_PHYSICS):
            check = QtWidgets.QCheckBox(physics.title())
            check.setObjectName(f"VibeCADAnalyzePhysics_{physics}")
            self.physics_checks[physics] = check
            physics_layout.addWidget(check, index // 2, index % 2)
        form.addRow("Physics", physics_widget)

        self.regime_combo = QtWidgets.QComboBox()
        self.regime_combo.setObjectName("VibeCADAnalyzeStudyRegime")
        for regime in STUDY_REGIMES:
            self.regime_combo.addItem(regime.title(), regime)
        form.addRow("Study type", self.regime_combo)
        layout.addWidget(definition)

        progress = QtWidgets.QGroupBox("Study State")
        progress_layout = QtWidgets.QVBoxLayout(progress)
        self.state_table = QtWidgets.QTreeWidget()
        self.state_table.setObjectName("VibeCADAnalyzeStudyState")
        self.state_table.setHeaderLabels(("Stage", "State"))
        self.state_table.setRootIsDecorated(False)
        self.state_table.setAlternatingRowColors(True)
        self.state_table.setMinimumHeight(170)
        progress_layout.addWidget(self.state_table)
        self.readiness_label = QtWidgets.QLabel()
        self.readiness_label.setObjectName("VibeCADAnalyzeReadiness")
        self.readiness_label.setWordWrap(True)
        progress_layout.addWidget(self.readiness_label)
        self.runtime_label = QtWidgets.QLabel()
        self.runtime_label.setObjectName("VibeCADAnalyzeRuntime")
        self.runtime_label.setWordWrap(True)
        progress_layout.addWidget(self.runtime_label)
        layout.addWidget(progress)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.apply_button = QtWidgets.QPushButton("Create Study")
        self.apply_button.setObjectName("VibeCADAnalyzeApplyStudy")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.apply)
        outer.addWidget(self.apply_button)

    def _document_and_analysis(self) -> tuple[Any | None, Any | None]:
        document = _active_document()
        if document is None:
            return None, None
        name = str(self.analysis_combo.currentData() or "")
        candidate = document.getObject(name) if name else None
        return document, (
            candidate if candidate in analyses_in_document(document) else None
        )

    def refresh(self) -> None:
        document = _active_document()
        previous = str(self.analysis_combo.currentData() or self._analysis_name)
        selected = (
            analysis_for_selection(document, _selected_objects())
            if document is not None
            else None
        )
        active = _active_analysis(document) if document is not None else None
        analyses = analyses_in_document(document) if document is not None else ()
        preferred = selected or active
        if preferred is None and previous:
            preferred = next(
                (
                    candidate
                    for candidate in analyses
                    if str(candidate.Name) == previous
                ),
                None,
            )

        self.analysis_combo.blockSignals(True)
        self.analysis_combo.clear()
        self.analysis_combo.addItem("New study", "")
        for analysis in analyses:
            self.analysis_combo.addItem(str(analysis.Label), str(analysis.Name))
        if preferred is not None:
            index = self.analysis_combo.findData(str(preferred.Name))
            self.analysis_combo.setCurrentIndex(max(index, 0))
        self.analysis_combo.blockSignals(False)
        self._load_current()

    def _analysis_changed(self, _index: int) -> None:
        self._load_current()

    def _load_current(self) -> None:
        document, analysis = self._document_and_analysis()
        self._generation += 1
        self._runtime_statuses = ()
        self._analysis_name = str(getattr(analysis, "Name", "") or "")
        self.label_edit.setEnabled(analysis is None)
        self.apply_button.setText(
            "Create Study" if analysis is None else "Update Study"
        )
        if document is None:
            self.label_edit.setText("Analysis")
            self._inventory = {}
            self._intent = {"declared": False}
            self._render("Open a document to create a study.")
            self.apply_button.setEnabled(False)
            return
        self.apply_button.setEnabled(True)
        if analysis is None:
            self.label_edit.setText("Analysis")
            self._inventory = {}
            self._intent = {"declared": False}
            for physics, check in self.physics_checks.items():
                check.setChecked(physics == "mechanical")
            self.regime_combo.setCurrentIndex(self.regime_combo.findData("steady"))
            self._render("Choose the study definition.")
            return

        try:
            import FemGui

            FemGui.setActiveAnalysis(analysis)
            self.label_edit.setText(str(analysis.Label))
            self._intent = study_intent_state(analysis)
            self._inventory = study_inventory(analysis)
        except Exception as exc:
            self._inventory = {}
            self._intent = {"declared": False}
            self._render(str(exc))
            return
        declared_physics = set(self._intent.get("physics") or ())
        for physics, check in self.physics_checks.items():
            check.setChecked(physics in declared_physics)
        regime = str(self._intent.get("regime") or "steady")
        self.regime_combo.setCurrentIndex(max(self.regime_combo.findData(regime), 0))
        solvers = tuple(dict.fromkeys(self._inventory.get("solver_kinds") or ()))
        self._render()
        if solvers:
            self.runtime_label.setText("Solver: checking runtime")
            probe = _RuntimeProbe(self._generation, solvers)
            probe.signals.finished.connect(self._runtime_finished)
            QtCore.QThreadPool.globalInstance().start(probe)

    def _runtime_finished(self, generation: int, statuses: Any, error: str) -> None:
        if generation != self._generation:
            return
        if error:
            self.runtime_label.setText(f"Solver: {error}")
            return
        self._runtime_statuses = tuple(dict(status) for status in statuses)
        self._render()

    def _render(self, message: str = "") -> None:
        self.state_table.clear()
        for stage, state in readiness_rows(self._inventory):
            self.state_table.addTopLevelItem(QtWidgets.QTreeWidgetItem((stage, state)))
        self.state_table.resizeColumnToContents(0)
        if message:
            self.readiness_label.setText(message)
            self.runtime_label.clear()
            return
        runtimes = {
            str(status.get("solver") or ""): status for status in self._runtime_statuses
        }
        readiness = evaluate_study_readiness(self._intent, self._inventory, runtimes)
        blockers = []
        for blocker in readiness["blockers"]:
            if blocker.startswith("solver_runtime_unavailable:"):
                solver = blocker.split(":", 1)[1].title()
                blockers.append(f"{solver} runtime is unavailable")
            else:
                blockers.append(
                    _BLOCKER_LABELS.get(blocker, blocker.replace("_", " ").title())
                )
        if readiness["ready_to_solve"]:
            self.readiness_label.setText("Ready to solve")
        elif blockers:
            self.readiness_label.setText("Next: " + " · ".join(blockers))
        else:
            self.readiness_label.setText("Study definition saved")
        if self._runtime_statuses:
            values = []
            for status in self._runtime_statuses:
                solver = str(status.get("solver") or "Solver").title()
                values.append(
                    f"{solver}: "
                    + ("available" if status.get("engine_ready") else "unavailable")
                )
            self.runtime_label.setText(" · ".join(values))
        elif not tuple(self._inventory.get("solver_kinds") or ()):
            self.runtime_label.setText("Solver: not set")

    def apply(self) -> None:
        document, analysis = self._document_and_analysis()
        if document is None:
            return
        physics = tuple(
            name for name, check in self.physics_checks.items() if check.isChecked()
        )
        try:
            analysis, _result = apply_study(
                document,
                analysis=analysis,
                label=self.label_edit.text(),
                physics=physics,
                regime=str(self.regime_combo.currentData()),
            )
            import FemGui

            FemGui.setActiveAnalysis(analysis)
            self._analysis_name = str(analysis.Name)
            self.refresh()
            App.Console.PrintMessage(f"FEM study {analysis.Label} is active.\n")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Study Setup",
                str(exc),
            )


class StudySetupCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": "FEM_Analysis",
            "MenuText": "Study Setup",
            "ToolTip": "Create a study and review its analysis state",
        }

    def IsActive(self) -> bool:
        return _active_document() is not None

    def Activated(self) -> None:
        show_study_setup()


def show_study_setup() -> Any:
    main_window = Gui.getMainWindow()
    if main_window is None:
        raise RuntimeError("VibeCAD main window is not available.")
    dock = main_window.findChild(QtWidgets.QDockWidget, DOCK_NAME)
    if dock is None:
        dock = QtWidgets.QDockWidget("Study Setup", main_window)
        dock.setObjectName(DOCK_NAME)
        dock.setAllowedAreas(
            QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
        )
        dock.setMinimumWidth(330)
        dock.setWidget(StudySetupWidget(dock))
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    else:
        widget = dock.widget()
        if isinstance(widget, StudySetupWidget):
            widget.refresh()
    dock.show()
    dock.raise_()
    return dock


def ensure_command_registered() -> None:
    global _registered
    if _registered:
        return
    Gui.addCommand(COMMAND_NAME, StudySetupCommand())
    _registered = True
