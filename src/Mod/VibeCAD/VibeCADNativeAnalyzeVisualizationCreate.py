# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic FEM table, histogram, and line-plot visualization graphs."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzePost import _owning_post_pipeline, _post_parent_group
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    analysis_target_still_exact,
    prepare_analysis_target,
)
from VibeCADNativeAnalyzeVisualizationState import (
    PreparedVisualizationExtraction,
    optional_visible_text,
    prepare_extraction,
    table_summary,
    visible_text,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_LEGEND_LOCATIONS = frozenset(
    {
        "best",
        "upper right",
        "upper left",
        "lower left",
        "lower right",
        "right",
        "center left",
        "center right",
        "lower center",
        "upper center",
        "center",
    }
)
_LINE_SCALES = {
    "linear": "linear",
    "log_x": "semi-log x",
    "log_y": "semi-log y",
    "log_xy": "log",
}
_HISTOGRAM_TYPES = frozenset({"bar", "barstacked", "step", "stepfilled"})


@dataclass(frozen=True, slots=True)
class PreparedVisualization:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    analysis_members: tuple[Any, ...]
    source: PreparedResultTarget
    pipeline: Any
    kind: str
    label: str
    extraction: PreparedVisualizationExtraction
    view: Mapping[str, Any]


def _legend(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"show", "location"}:
        raise NativeAnalyzeError(
            "view.legend must contain only show and location."
        )
    show = value["show"]
    location = str(value["location"] or "")
    if type(show) is not bool or location not in _LEGEND_LOCATIONS:
        raise NativeAnalyzeError(
            "view.legend.show must be boolean and location must be a supported legend location."
        )
    return {"show": show, "location": location}


def _histogram_view(value: Any, expected_rows: int) -> dict[str, Any]:
    required = {
        "bins",
        "type",
        "cumulative",
        "bar_width",
        "hatch_line_width",
        "title",
        "x_label",
        "y_label",
        "legend",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "Histogram view settings must contain their exact nine fields."
        )
    bins = value["bins"]
    histogram_type = str(value["type"] or "")
    cumulative = value["cumulative"]
    bar_width = value["bar_width"]
    hatch_width = value["hatch_line_width"]
    if type(bins) is not int or not 1 <= bins <= min(10_000, expected_rows):
        raise NativeAnalyzeError(
            "view.bins must be an integer no greater than the extracted row count.",
            repair={"minimum_bins": 1, "maximum_bins": min(10_000, expected_rows)},
        )
    if histogram_type not in _HISTOGRAM_TYPES:
        raise NativeAnalyzeError(
            "view.type must be bar, barstacked, step, or stepfilled."
        )
    if type(cumulative) is not bool:
        raise NativeAnalyzeError("view.cumulative must be true or false.")
    if (
        type(bar_width) not in {int, float}
        or not math.isfinite(float(bar_width))
        or not 0.0 < float(bar_width) <= 1.0
    ):
        raise NativeAnalyzeError("view.bar_width must be greater than 0 and at most 1.")
    if (
        type(hatch_width) not in {int, float}
        or not math.isfinite(float(hatch_width))
        or not 0.0 <= float(hatch_width) <= 99.0
    ):
        raise NativeAnalyzeError(
            "view.hatch_line_width must be between 0 and 99."
        )
    return {
        "bins": bins,
        "type": histogram_type,
        "cumulative": cumulative,
        "bar_width": float(bar_width),
        "hatch_line_width": float(hatch_width),
        "title": optional_visible_text(value["title"], "view.title"),
        "x_label": optional_visible_text(value["x_label"], "view.x_label"),
        "y_label": optional_visible_text(value["y_label"], "view.y_label"),
        "legend": _legend(value["legend"]),
    }


def _line_view(value: Any) -> dict[str, Any]:
    required = {
        "scale",
        "grid",
        "title",
        "x_label",
        "y_label",
        "legend",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "Line-plot view settings must contain their exact six fields."
        )
    scale = str(value["scale"] or "")
    grid = value["grid"]
    if scale not in _LINE_SCALES:
        raise NativeAnalyzeError("view.scale must be linear, log_x, log_y, or log_xy.")
    if type(grid) is not bool:
        raise NativeAnalyzeError("view.grid must be true or false.")
    return {
        "scale": scale,
        "grid": grid,
        "title": optional_visible_text(value["title"], "view.title"),
        "x_label": optional_visible_text(value["x_label"], "view.x_label"),
        "y_label": optional_visible_text(value["y_label"], "view.y_label"),
        "legend": _legend(value["legend"]),
    }


def prepare_visualization(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    source: Any,
    label: Any,
    data: Any,
    view: Any | None = None,
) -> PreparedVisualization:
    if kind not in {"table", "histogram", "line_plot"}:
        raise ValueError(f"Unsupported visualization kind: {kind}")
    analysis_target = prepare_analysis_target(document, document_uid, analysis)
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document, source_target.result, source_target.kind
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    if pipeline not in tuple(analysis_target.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The exact post source does not belong to the exact analysis.",
            error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
        )
    source_state = result_state(source_target.result, include_ranges=False)
    source_point_count = int(source_state["point_count"])
    if not bool(source_state["data_available"]) or source_point_count < 1:
        raise NativeAnalyzeError(
            "The exact post source has no point data to visualize."
        )
    extraction = prepare_extraction(
        source_target.result,
        data,
        dimension=2 if kind == "line_plot" else 1,
        source_point_count=source_point_count,
    )
    if kind == "table":
        if view is not None:
            raise NativeAnalyzeError("A table visualization does not accept plot settings.")
        normalized_view = {}
    elif kind == "histogram":
        normalized_view = _histogram_view(view, extraction.expected_rows)
    else:
        normalized_view = _line_view(view)
    return PreparedVisualization(
        creation_boundary(document),
        analysis_target,
        tuple(analysis_target.analysis.Group or ()),
        source_target,
        pipeline,
        kind,
        visible_text(label, "label"),
        extraction,
        normalized_view,
    )


def _require_current_targets(document: Any, prepared: PreparedVisualization) -> None:
    source = prepared.source.result
    source_state = result_state(source, include_ranges=False)
    if (
        not analysis_target_still_exact(prepared.analysis)
        or tuple(prepared.analysis.analysis.Group or ()) != prepared.analysis_members
        or not is_live(document, source)
        or not is_live(document, prepared.pipeline)
        or source_state["state_sha256"] != prepared.source.expected_state_sha256
        or prepared.pipeline not in tuple(prepared.analysis.analysis.Group or ())
    ):
        raise NativeAnalyzeError(
            "The exact analysis or visualization source changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def _factory_names(prepared: PreparedVisualization) -> tuple[str, str, str]:
    extraction = "FieldData" if prepared.extraction.mode == "field" else "IndexOverFrames"
    if prepared.kind == "table":
        return "makePostTable", "makePostTable" + extraction, "Table"
    if prepared.kind == "histogram":
        return "makePostHistogram", "makePostHistogram" + extraction, "Histogram"
    return "makePostLineplot", "makePostLineplot" + extraction, "Lineplot"


def _set_enum(obj: Any, property_name: str, value: str) -> None:
    choices = tuple(obj.getEnumerationsOfProperty(property_name) or ())
    if value not in choices:
        raise NativeAnalyzeError(
            f"The visualization extractor does not offer {value!r} for {property_name}.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={"property": property_name, "available_values": list(choices[:32])},
        )
    setattr(obj, property_name, value)


def _configure_extractor(extractor: Any, prepared: PreparedVisualization) -> None:
    extraction = prepared.extraction
    extractor.Source = prepared.source.result
    if extraction.mode == "field":
        extractor.ExtractFrames = extraction.extract_all_frames
        _set_enum(extractor, "XField", extraction.x.native_field)
        _set_enum(extractor, "XComponent", extraction.x.native_component)
        if extraction.y is not None:
            _set_enum(extractor, "YField", extraction.y.native_field)
            _set_enum(extractor, "YComponent", extraction.y.native_component)
    else:
        extractor.Index = extraction.point_index
        if extraction.y is None:
            _set_enum(extractor, "XField", extraction.x.native_field)
            _set_enum(extractor, "XComponent", extraction.x.native_component)
        else:
            _set_enum(extractor, "YField", extraction.y.native_field)
            _set_enum(extractor, "YComponent", extraction.y.native_component)
    if prepared.kind == "table":
        extractor.ViewObject.Name = extraction.series_name
    else:
        extractor.ViewObject.Legend = extraction.series_name


def _apply_view(visualization: Any, prepared: PreparedVisualization) -> None:
    if prepared.kind == "table":
        return
    view = visualization.ViewObject
    settings = prepared.view
    view.Title = settings["title"]
    view.XLabel = settings["x_label"]
    view.YLabel = settings["y_label"]
    view.Legend = settings["legend"]["show"]
    view.LegendLocation = settings["legend"]["location"]
    if prepared.kind == "histogram":
        view.Bins = settings["bins"]
        view.Type = settings["type"]
        view.Cumulative = settings["cumulative"]
        view.BarWidth = settings["bar_width"]
        view.HatchLineWidth = settings["hatch_line_width"]
    else:
        view.Scale = _LINE_SCALES[settings["scale"]]
        view.Grid = settings["grid"]


def create_visualization(
    document: Any, prepared: PreparedVisualization
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedVisualization):
        raise TypeError("prepared must be a PreparedVisualization")
    require_boundary(document, prepared.boundary)
    _require_current_targets(document, prepared)
    try:
        import ObjectsFem

        root_factory_name, extractor_factory_name, base_name = _factory_names(prepared)
        visualization = getattr(ObjectsFem, root_factory_name)(
            document, document.getUniqueObjectName(base_name)
        )
        extractor = getattr(ObjectsFem, extractor_factory_name)(
            document, document.getUniqueObjectName(base_name + "Data")
        )
        visualization.Label = prepared.label
        extractor.Label = prepared.extraction.series_name + " Data"
        visualization.addObject(extractor)
        prepared.analysis.analysis.addObject(visualization)
        publish_operation(
            document,
            prepared.boundary,
            visualization,
            resources=(extractor,),
        )
        _configure_extractor(extractor, prepared)
        _apply_view(visualization, prepared)
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM {prepared.kind} visualization could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "visualization": visualization,
            "extractor": extractor,
        },
        recompute_targets=(extractor, visualization, prepared.analysis.analysis),
        created=(object_identity(extractor), object_identity(visualization)),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def _verify_view(visualization: Any, prepared: PreparedVisualization) -> dict[str, bool]:
    if prepared.kind == "table":
        return {}
    view = visualization.ViewObject
    settings = prepared.view
    checks = {
        "title": str(view.Title) == settings["title"],
        "x label": str(view.XLabel) == settings["x_label"],
        "y label": str(view.YLabel) == settings["y_label"],
        "legend": bool(view.Legend) == settings["legend"]["show"],
        "legend location": str(view.LegendLocation) == settings["legend"]["location"],
    }
    if prepared.kind == "histogram":
        checks.update(
            {
                "bins": int(view.Bins) == settings["bins"],
                "histogram type": str(view.Type) == settings["type"],
                "cumulative": bool(view.Cumulative) == settings["cumulative"],
                "bar width": math.isclose(
                    float(view.BarWidth), settings["bar_width"], abs_tol=1.0e-12
                ),
                "hatch line width": math.isclose(
                    float(view.HatchLineWidth),
                    settings["hatch_line_width"],
                    abs_tol=1.0e-12,
                ),
            }
        )
    else:
        checks.update(
            {
                "axis scale": str(view.Scale) == _LINE_SCALES[settings["scale"]],
                "grid": bool(view.Grid) == settings["grid"],
            }
        )
    return checks


def _validate_log_data(summary: Mapping[str, Any], prepared: PreparedVisualization) -> None:
    if prepared.kind != "line_plot" or prepared.view["scale"] == "linear":
        return
    scale = prepared.view["scale"]
    columns = list(summary["columns"])
    invalid = []
    for index, column in enumerate(columns):
        is_x = index % 2 == 0
        requires_positive = (is_x and scale in {"log_x", "log_xy"}) or (
            not is_x and scale in {"log_y", "log_xy"}
        )
        if requires_positive and float(column["range"][0]) <= 0.0:
            invalid.append(column["name"])
    if invalid:
        raise NativeAnalyzeError(
            "A logarithmic plot axis contains zero or negative extracted values.",
            error_code="NATIVE_ANALYZE_VISUALIZATION_SCALE_INVALID",
            repair={"scale": scale, "nonpositive_columns": invalid[:16]},
        )


def verify_visualization(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    visualization = draft.value["visualization"]
    extractor = draft.value["extractor"]
    analysis = prepared.analysis.analysis
    source = prepared.source.result
    verify_operation_block(
        document,
        prepared.boundary,
        visualization,
        resources=(extractor,),
    )
    root_summary = table_summary(visualization.Table)
    extractor_summary = table_summary(extractor.Table)
    _validate_log_data(extractor_summary, prepared)
    expected_dimension = "2D" if prepared.kind == "line_plot" else "1D"
    expected_type = "Field" if prepared.extraction.mode == "field" else "Index"
    checks = {
        "live visualization": is_live(document, visualization),
        "live extractor": is_live(document, extractor),
        "visualization type": str(getattr(visualization.Proxy, "Type", ""))
        == "Fem::FemPostVisualization",
        "visualization kind": str(
            getattr(visualization.Proxy, "VisualizationType", "")
        )
        == {"table": "Table", "histogram": "Histogram", "line_plot": "Lineplot"}[
            prepared.kind
        ],
        "extractor type": str(getattr(extractor.Proxy, "ExtractionType", ""))
        == expected_type,
        "extractor dimension": str(
            getattr(extractor.Proxy, "ExtractionDimension", "")
        )
        == expected_dimension,
        "extractor visualization": str(
            getattr(extractor.Proxy, "VisualizationType", "")
        )
        == str(getattr(visualization.Proxy, "VisualizationType", "")),
        "analysis membership": tuple(analysis.Group or ())
        == (*prepared.analysis_members, visualization),
        "extractor membership": tuple(visualization.Group or ()) == (extractor,),
        "source link": extractor.Source is source,
        "source retained": is_live(document, source),
        "label": str(visualization.Label) == prepared.label,
        "row count": root_summary["row_count"] == prepared.extraction.expected_rows,
        "column count": root_summary["column_count"]
        == prepared.extraction.expected_columns,
        "extractor/root table parity": (
            root_summary["row_count"], root_summary["column_count"]
        )
        == (extractor_summary["row_count"], extractor_summary["column_count"]),
        **_verify_view(visualization, prepared),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM visualization failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    current_analysis = analysis_state(analysis)
    return {
        "created_visualization": result_state(visualization, include_ranges=False),
        "extractor": {
            "object_name": str(extractor.Name),
            "object_id": int(extractor.ID),
            "label": str(extractor.Label),
            "history_role": str(extractor.VibeCADTimelineRole),
            "data": prepared.extraction.response(),
        },
        "table": root_summary,
        "view": dict(prepared.view),
        "source": result_reference_state(source),
        "analysis": {
            "object_name": str(analysis.Name),
            "object_id": int(analysis.ID),
            "member_count": int(current_analysis["member_count"]),
            "state_sha256": current_analysis["state_sha256"],
        },
    }
