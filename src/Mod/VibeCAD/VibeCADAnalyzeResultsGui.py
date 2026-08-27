# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-facing flow results in the Analyze Study Setup dock."""

from __future__ import annotations

import math
from typing import Any

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADNativeAnalyzeFlowPresentation import present_flow_result
from VibeCADNativeAnalyzeResultState import openfoam_flow_summary_state


def _vector_text(values: Any) -> str:
    vector = tuple(float(value) for value in values)
    magnitude = math.sqrt(sum(value * value for value in vector))
    return f"{magnitude:.6g} m/s"


class AnalyzeResultsBrowser(QtWidgets.QGroupBox):
    def __init__(self, parent: Any = None) -> None:
        super().__init__("Results", parent)
        self.setObjectName("VibeCADEngineeringResultsPanel")
        self.setProperty("vibeEngineeringSurface", True)
        self.setAccessibleName("Engineering results")
        self._document = None
        self._summaries: dict[str, dict[str, Any]] = {}
        layout = QtWidgets.QVBoxLayout(self)

        self.result_combo = QtWidgets.QComboBox()
        self.result_combo.setObjectName("VibeCADAnalyzeResultSelector")
        self.result_combo.currentIndexChanged.connect(self._render)
        layout.addWidget(self.result_combo)

        self.summary_label = QtWidgets.QLabel("Run a study to view results.")
        self.summary_label.setObjectName("VibeCADAnalyzeResultSummary")
        self.summary_label.setProperty("vibeResultCard", True)
        self.summary_label.setAccessibleName("Selected engineering result summary")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.boundary_table = QtWidgets.QTreeWidget()
        self.boundary_table.setObjectName("VibeCADAnalyzeFlowBoundaries")
        self.boundary_table.setHeaderLabels(
            ("Boundary", "Condition", "Area", "Pressure", "Velocity", "Flow")
        )
        self.boundary_table.setRootIsDecorated(False)
        self.boundary_table.setAlternatingRowColors(True)
        self.boundary_table.setMinimumHeight(150)
        layout.addWidget(self.boundary_table)

        actions = QtWidgets.QHBoxLayout()
        self.pressure_button = QtWidgets.QPushButton("Show Pressure")
        self.pressure_button.setObjectName("VibeCADAnalyzeShowPressure")
        self.pressure_button.clicked.connect(
            lambda: self._show_field("pressure")
        )
        actions.addWidget(self.pressure_button)
        self.velocity_button = QtWidgets.QPushButton("Show Velocity")
        self.velocity_button.setObjectName("VibeCADAnalyzeShowVelocity")
        self.velocity_button.clicked.connect(
            lambda: self._show_field("velocity")
        )
        actions.addWidget(self.velocity_button)
        self.turbulence_button = QtWidgets.QPushButton("Show Turbulence")
        self.turbulence_button.setObjectName("VibeCADAnalyzeShowTurbulence")
        self.turbulence_button.clicked.connect(
            lambda: self._show_field("turbulent_kinetic_energy")
        )
        actions.addWidget(self.turbulence_button)
        layout.addLayout(actions)

        performance = QtWidgets.QGroupBox("Flow Performance")
        performance.setObjectName("VibeCADEngineeringPerformanceCard")
        performance.setProperty("vibeResultCard", True)
        performance_layout = QtWidgets.QFormLayout(performance)
        self.upstream_combo = QtWidgets.QComboBox()
        self.upstream_combo.setObjectName("VibeCADAnalyzeFlowUpstream")
        performance_layout.addRow("Upstream", self.upstream_combo)
        self.downstream_combo = QtWidgets.QComboBox()
        self.downstream_combo.setObjectName("VibeCADAnalyzeFlowDownstream")
        performance_layout.addRow("Downstream", self.downstream_combo)
        self.flow_boundary_combo = QtWidgets.QComboBox()
        self.flow_boundary_combo.setObjectName("VibeCADAnalyzeFlowSection")
        performance_layout.addRow("Flow section", self.flow_boundary_combo)
        self.measure_button = QtWidgets.QPushButton("Measure Passage")
        self.measure_button.setObjectName("VibeCADAnalyzeMeasureFlow")
        self.measure_button.clicked.connect(self._measure_performance)
        performance_layout.addRow(self.measure_button)
        self.performance_label = QtWidgets.QLabel()
        self.performance_label.setObjectName("VibeCADAnalyzeFlowPerformance")
        self.performance_label.setWordWrap(True)
        performance_layout.addRow(self.performance_label)
        layout.addWidget(performance)

        comparison = QtWidgets.QGroupBox("Compare Flow")
        comparison.setObjectName("VibeCADEngineeringComparisonCard")
        comparison.setProperty("vibeResultCard", True)
        comparison_layout = QtWidgets.QFormLayout(comparison)
        self.compare_result_combo = QtWidgets.QComboBox()
        self.compare_result_combo.setObjectName("VibeCADAnalyzeCompareResult")
        self.compare_result_combo.currentIndexChanged.connect(
            self._render_comparison
        )
        comparison_layout.addRow("Candidate", self.compare_result_combo)
        self.compare_upstream_combo = QtWidgets.QComboBox()
        self.compare_upstream_combo.setObjectName("VibeCADAnalyzeCompareUpstream")
        comparison_layout.addRow("Upstream", self.compare_upstream_combo)
        self.compare_downstream_combo = QtWidgets.QComboBox()
        self.compare_downstream_combo.setObjectName("VibeCADAnalyzeCompareDownstream")
        comparison_layout.addRow("Downstream", self.compare_downstream_combo)
        self.compare_flow_combo = QtWidgets.QComboBox()
        self.compare_flow_combo.setObjectName("VibeCADAnalyzeCompareSection")
        comparison_layout.addRow("Flow section", self.compare_flow_combo)
        self.compare_button = QtWidgets.QPushButton("Compare to Current")
        self.compare_button.setObjectName("VibeCADAnalyzeCompareFlow")
        self.compare_button.clicked.connect(self._compare_flow)
        comparison_layout.addRow(self.compare_button)
        self.comparison_label = QtWidgets.QLabel()
        self.comparison_label.setObjectName("VibeCADAnalyzeFlowComparison")
        self.comparison_label.setWordWrap(True)
        comparison_layout.addRow(self.comparison_label)
        layout.addWidget(comparison)
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self.boundary_table.setVisible(enabled)
        self.pressure_button.setEnabled(enabled)
        self.velocity_button.setEnabled(enabled)
        self.turbulence_button.setEnabled(enabled)
        self.measure_button.setEnabled(enabled)
        self.compare_button.setEnabled(enabled and len(self._summaries) >= 2)

    def refresh(self, document: Any, analysis: Any) -> None:
        previous = str(self.result_combo.currentData() or "")
        previous_candidate = str(self.compare_result_combo.currentData() or "")
        self._document = document
        self._summaries = {}
        if document is not None and analysis is not None:
            for member in tuple(getattr(analysis, "Group", ()) or ()):
                try:
                    summary = openfoam_flow_summary_state(member)
                except Exception:
                    continue
                if summary is not None:
                    self._summaries[str(member.Name)] = summary
        self.result_combo.blockSignals(True)
        self.compare_result_combo.blockSignals(True)
        self.result_combo.clear()
        self.compare_result_combo.clear()
        for name in self._summaries:
            result = document.getObject(name)
            self.result_combo.addItem(str(result.Label), name)
            self.compare_result_combo.addItem(str(result.Label), name)
        index = self.result_combo.findData(previous)
        if index >= 0:
            self.result_combo.setCurrentIndex(index)
        candidate_index = self.compare_result_combo.findData(previous_candidate)
        if candidate_index >= 0:
            self.compare_result_combo.setCurrentIndex(candidate_index)
        elif self.compare_result_combo.count() > 1:
            self.compare_result_combo.setCurrentIndex(1)
        self.result_combo.blockSignals(False)
        self.compare_result_combo.blockSignals(False)
        self._render()

    def _selected(self) -> tuple[Any | None, dict[str, Any] | None]:
        name = str(self.result_combo.currentData() or "")
        result = self._document.getObject(name) if self._document is not None else None
        return result, self._summaries.get(name)

    def _render(self, _index: int = -1) -> None:
        _result, summary = self._selected()
        self.boundary_table.clear()
        previous_boundaries = (
            str(self.upstream_combo.currentData() or ""),
            str(self.downstream_combo.currentData() or ""),
            str(self.flow_boundary_combo.currentData() or ""),
        )
        for combo in (
            self.upstream_combo,
            self.downstream_combo,
            self.flow_boundary_combo,
        ):
            combo.clear()
        self.performance_label.clear()
        self.comparison_label.clear()
        if summary is None:
            self.summary_label.setText("Run a study to view results.")
            self._set_enabled(False)
            return
        pressure = summary["pressure_range_pa"]
        text = (
            f"Pressure {pressure[0]:.6g} to {pressure[1]:.6g} Pa · "
            f"Maximum velocity {summary['maximum_velocity_m_s']:.6g} m/s"
        )
        model = {
            "laminar": "Laminar",
            "kOmegaSST": "k-omega SST",
        }.get(summary.get("turbulence_model"), "Unknown model")
        convergence = (
            "Converged"
            if summary.get("converged") is True
            else "Not converged"
            if summary.get("converged") is False
            else "Convergence unknown"
        )
        text += f" · {model} · {convergence}"
        if "static_pressure_drop_pa" in summary:
            text += (
                f" · Pressure drop {summary['static_pressure_drop_pa']:.6g} Pa "
                f"({summary['pressure_drop_from']} → {summary['pressure_drop_to']})"
            )
        self.summary_label.setText(text)
        for boundary in summary["boundaries"]:
            flow = boundary.get("outward_volumetric_flow_rate_m3_s")
            self.boundary_table.addTopLevelItem(
                QtWidgets.QTreeWidgetItem(
                    (
                        str(boundary["name"]),
                        str(boundary["kind"]).replace("_", " ").title(),
                        f"{boundary.get('geometric_area_m2', boundary['area_m2']):.6g} "
                        "m²",
                        f"{boundary['pressure_area_average_pa']:.6g} Pa",
                        _vector_text(boundary["velocity_area_average_m_s"]),
                        "—" if flow is None else f"{flow:.6g} m³/s",
                    )
                )
            )
        for column in range(self.boundary_table.columnCount()):
            self.boundary_table.resizeColumnToContents(column)
        self._set_enabled(True)
        self.turbulence_button.setEnabled(
            summary.get("turbulence_model") == "kOmegaSST"
        )
        performance_ready = "density_kg_m3" in summary and all(
            "geometric_area_m2" in boundary
            and "outward_volumetric_flow_rate_m3_s" in boundary
            for boundary in summary["boundaries"]
        )
        for combo, previous in zip(
            (
                self.upstream_combo,
                self.downstream_combo,
                self.flow_boundary_combo,
            ),
            previous_boundaries,
        ):
            for boundary in summary["boundaries"]:
                name = str(boundary["name"])
                combo.addItem(name, name)
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.setEnabled(performance_ready)
        self.measure_button.setEnabled(performance_ready)
        self._render_comparison()

    def _render_comparison(self, _index: int = -1) -> None:
        previous = tuple(
            str(combo.currentData() or "")
            for combo in (
                self.compare_upstream_combo,
                self.compare_downstream_combo,
                self.compare_flow_combo,
            )
        )
        for combo in (
            self.compare_upstream_combo,
            self.compare_downstream_combo,
            self.compare_flow_combo,
        ):
            combo.clear()
        candidate = self._summaries.get(
            str(self.compare_result_combo.currentData() or "")
        )
        ready = candidate is not None and len(self._summaries) >= 2
        if candidate is not None:
            for combo, selected in zip(
                (
                    self.compare_upstream_combo,
                    self.compare_downstream_combo,
                    self.compare_flow_combo,
                ),
                previous,
            ):
                for boundary in candidate["boundaries"]:
                    name = str(boundary["name"])
                    combo.addItem(name, name)
                index = combo.findData(selected)
                if index >= 0:
                    combo.setCurrentIndex(index)
                combo.setEnabled(ready)
        _result, baseline = self._selected()
        ready = ready and baseline is not None
        self.compare_button.setEnabled(ready)

    def _compare_flow(self) -> None:
        _result, baseline = self._selected()
        candidate = self._summaries.get(
            str(self.compare_result_combo.currentData() or "")
        )
        if baseline is None or candidate is None:
            return
        from femsolver.openfoam.results import openfoam_flow_comparison

        try:
            values = openfoam_flow_comparison(
                baseline,
                candidate,
                baseline_passage={
                    "upstream_boundary": str(
                        self.upstream_combo.currentData() or ""
                    ),
                    "downstream_boundary": str(
                        self.downstream_combo.currentData() or ""
                    ),
                    "flow_boundary": str(
                        self.flow_boundary_combo.currentData() or ""
                    ),
                },
                candidate_passage={
                    "upstream_boundary": str(
                        self.compare_upstream_combo.currentData() or ""
                    ),
                    "downstream_boundary": str(
                        self.compare_downstream_combo.currentData() or ""
                    ),
                    "flow_boundary": str(
                        self.compare_flow_combo.currentData() or ""
                    ),
                },
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.comparison_label.setText(str(exc))
            return
        changes = values["changes"]
        self.comparison_label.setText(
            f"EFA {changes['effective_flow_area_m2']['value']:+.6g} m² · "
            f"Cd {changes['discharge_coefficient']['value']:+.6g} · "
            f"Flow {changes['volumetric_flow_rate_m3_s']['value']:+.6g} m³/s · "
            f"Δp {changes['static_pressure_drop_pa']['value']:+.6g} Pa"
        )

    def _measure_performance(self) -> None:
        _result, summary = self._selected()
        if summary is None:
            return
        from femsolver.openfoam.results import openfoam_flow_performance

        try:
            values = openfoam_flow_performance(
                summary,
                upstream_boundary=str(self.upstream_combo.currentData() or ""),
                downstream_boundary=str(self.downstream_combo.currentData() or ""),
                flow_boundary=str(self.flow_boundary_combo.currentData() or ""),
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self.performance_label.setText(str(exc))
            return
        self.performance_label.setText(
            f"GFA {values['geometric_flow_area_m2']:.6g} m² · "
            f"EFA {values['effective_flow_area_m2']:.6g} m² · "
            f"Cd {values['discharge_coefficient']:.6g} · "
            f"Flow {values['volumetric_flow_rate_m3_s']:.6g} m³/s · "
            f"Mass flow {values['mass_flow_rate_kg_s']:.6g} kg/s · "
            f"Δp {values['static_pressure_drop_pa']:.6g} Pa · "
            f"Continuity error {values['continuity_error_percent']:.6g}%"
        )

    def _show_field(self, field: str) -> None:
        result, summary = self._selected()
        if result is None or summary is None:
            return
        try:
            present_flow_result(result, field, visible=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Flow Results",
                str(exc),
            )
