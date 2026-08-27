# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tool_impl.analysis_contracts import AnalysisContractError
from tool_impl.engineering_chart_series import (
    chart_series_from_analysis,
    chart_series_from_visualization,
)


class _Array:
    def __init__(self, name, values):
        self._name = name
        self._values = tuple(values)

    def GetNumberOfComponents(self):
        return 1

    def GetTuple1(self, row):
        return self._values[row]

    def GetName(self):
        return self._name


class _Table:
    def __init__(self, *columns):
        self._columns = tuple(_Array(name, values) for name, values in columns)

    def GetNumberOfRows(self):
        return len(self._columns[0]._values)

    def GetNumberOfColumns(self):
        return len(self._columns)

    def GetColumn(self, index):
        return self._columns[index]


def _visualization(kind="Lineplot"):
    extractor = SimpleNamespace(
        Proxy=SimpleNamespace(VisualizationType=kind),
        XField="Position" if kind == "Lineplot" else "Stress",
        YField="Stress",
    )
    columns = (
        ("Distance", (0.0, 1.0, 2.0)),
        ("Stress", (10.0, 20.0, 15.0)),
    ) if kind == "Lineplot" else (("Stress", (10.0, 20.0, 15.0)),)
    return SimpleNamespace(
        Name="Lineplot",
        Label="Stress along path",
        Proxy=SimpleNamespace(
            Type="Fem::FemPostVisualization", VisualizationType=kind
        ),
        Group=(extractor,),
        Table=_Table(*columns),
        ViewObject=SimpleNamespace(
            XLabel="Distance" if kind == "Lineplot" else "Stress",
            YLabel="Stress",
        ),
    )


def test_projects_real_owner_table_as_bounded_series_metadata() -> None:
    chart = chart_series_from_visualization(_visualization())
    assert chart.kind == "line_plot"
    assert chart.row_count == 3
    assert chart.x_axis.to_dict() == {
        "label": "Distance", "unit": "mm", "minimum": 0.0, "maximum": 2.0,
    }
    assert chart.y_axes[0].label == "Stress"
    assert chart.y_axes[0].minimum == 10.0
    assert chart.y_axes[0].maximum == 20.0
    assert chart.to_dict()["values_copied"] is False
    assert len(chart.owner_state_sha256) == 64


def test_discovers_only_real_visualization_owners() -> None:
    line = _visualization()
    histogram = _visualization("Histogram")
    analysis = SimpleNamespace(Group=(object(), line, histogram))
    charts = chart_series_from_analysis(analysis)
    assert [chart.kind for chart in charts] == ["line_plot", "histogram"]
    assert charts[1].x_axis.label == "Stress"
    assert charts[1].x_axis.unit is None
    assert charts[1].y_axes == ()


def test_digest_changes_when_owner_data_changes() -> None:
    first = _visualization()
    second = _visualization()
    first.Table = _Table(
        ("Distance", (0.0, 1.0, 2.0, 3.0)),
        ("Stress", (10.0, 18.0, 20.0, 15.0)),
    )
    second.Table = _Table(
        ("Distance", (0.0, 1.0, 2.0, 3.0)),
        ("Stress", (10.0, 19.0, 20.0, 15.0)),
    )
    assert (
        chart_series_from_visualization(first).owner_state_sha256
        != chart_series_from_visualization(second).owner_state_sha256
    )


@pytest.mark.parametrize("mutation", ("kind", "group", "shape"))
def test_refuses_malformed_owner_contracts(mutation) -> None:
    visualization = _visualization()
    if mutation == "kind":
        visualization.Proxy.VisualizationType = "Pie"
    elif mutation == "group":
        visualization.Group = ()
    else:
        visualization.Table = _Table(("only", (1.0, 2.0)))
    with pytest.raises(AnalysisContractError):
        chart_series_from_visualization(visualization)
