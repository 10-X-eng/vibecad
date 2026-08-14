# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact FEM post-processing graph mutations without task-panel automation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    prepare_analysis_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedPostPipeline:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    result: PreparedResultTarget
    label: str
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class PreparedPostBranch:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    mode: str
    output: str
    source_was_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPostWarp:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    vector_field: str
    factor: float
    source_was_visible: bool


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return result


def _view_visibility(document):
    result = []
    for obj in tuple(document.Objects):
        view = getattr(obj, "ViewObject", None)
        if view is None:
            continue
        try:
            result.append((obj, bool(view.Visibility)))
        except Exception:
            continue
    return tuple(result)


def _derived(obj: Any, type_name: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_name))
    except Exception:
        return False


def _direct_post_groups(document: Any, obj: Any) -> tuple[Any, ...]:
    groups = []
    for parent in tuple(getattr(obj, "InList", ()) or ()):
        if not is_live(document, parent) or not (
            _derived(parent, "Fem::FemPostPipeline")
            or _derived(parent, "Fem::FemPostBranchFilter")
        ):
            continue
        try:
            if obj in tuple(parent.Group or ()):
                groups.append(parent)
        except Exception:
            continue
    return tuple(dict.fromkeys(groups))


def _post_parent_group(document: Any, source: Any, kind: str) -> Any:
    if kind in {"pipeline", "branch_filter"}:
        return source
    groups = _direct_post_groups(document, source)
    if len(groups) != 1:
        raise NativeAnalyzeError(
            "The exact post-processing source must belong to exactly one direct "
            "pipeline or branch group.",
            error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
            repair={
                "source": {"object_name": str(source.Name)},
                "direct_group_count": len(groups),
                "direct_groups": [str(group.Name) for group in groups[:8]],
            },
        )
    return groups[0]


def _owning_post_pipeline(document: Any, group: Any) -> Any:
    current = group
    visited: set[int] = set()
    while is_live(document, current):
        identity = int(current.ID)
        if identity in visited:
            raise NativeAnalyzeError(
                "The post-processing group graph is cyclic.",
                error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
            )
        visited.add(identity)
        if _derived(current, "Fem::FemPostPipeline"):
            return current
        parents = _direct_post_groups(document, current)
        if len(parents) != 1:
            raise NativeAnalyzeError(
                "The exact post-processing source must resolve to exactly one owning "
                "pipeline.",
                error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
                repair={
                    "source": {"object_name": str(group.Name)},
                    "parent_group_count": len(parents),
                    "parent_groups": [str(parent.Name) for parent in parents[:8]],
                },
            )
        current = parents[0]
    raise NativeAnalyzeError(
        "The owning post-processing pipeline is no longer live.",
        error_code="NATIVE_ANALYZE_STATE_STALE",
    )


def _copy_none_field_color(source: Any, target: Any) -> None:
    try:
        target.ViewObject.NoneFieldColor = source.ViewObject.NoneFieldColor
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The source result color could not be copied to the post filter: {exc}"
        ) from exc


def _available_post_fields(source: Any, *, components: int | None = None) -> list[str]:
    state = result_state(source, include_ranges=False)
    names = []
    for field in state.get("fields", ()):
        if field.get("association") != "point":
            continue
        if components is not None and int(field.get("components", 0) or 0) != components:
            continue
        name = str(field.get("name", "") or "")
        if name and name not in names:
            names.append(name)
    return names


def prepare_post_pipeline(
    document: Any,
    document_uid: str,
    *,
    analysis: Any,
    result: Any,
    label: Any,
) -> PreparedPostPipeline:
    analysis_target = prepare_analysis_target(document, document_uid, analysis)
    result_target = prepare_result_target(
        document,
        document_uid,
        result,
        expected_kinds=frozenset({"result"}),
    )
    if result_target.result not in tuple(analysis_target.analysis.Group or ()):
        raise NativeAnalyzeError(
            "The exact mechanical result is not a member of the exact analysis.",
            error_code="NATIVE_ANALYZE_TARGET_RELATION_INVALID",
        )
    mesh = getattr(result_target.result, "Mesh", None)
    if not is_live(document, mesh):
        raise NativeAnalyzeError(
            "The exact mechanical result has no live result mesh.",
            error_code="NATIVE_ANALYZE_TARGET_INVALID",
        )
    return PreparedPostPipeline(
        creation_boundary(document),
        analysis_target,
        result_target,
        _label(label),
        _view_visibility(document),
    )


def create_post_pipeline(
    document: Any,
    prepared: PreparedPostPipeline,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostPipeline):
        raise TypeError("prepared must be a PreparedPostPipeline")
    require_boundary(document, prepared.boundary)
    analysis = prepared.analysis.analysis
    result = prepared.result.result
    if result not in tuple(analysis.Group or ()):
        raise NativeAnalyzeError(
            "The result left the exact analysis after post-pipeline preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        pipeline = document.addObject(
            "Fem::FemPostPipeline",
            document.getUniqueObjectName("ResultPipeline"),
        )
        pipeline.Label = prepared.label
        analysis.addObject(pipeline)
        pipeline.load(result)
        pipeline.ViewObject.DisplayMode = "Surface"
        pipeline.ViewObject.SelectionStyle = "BoundBox"
        replaced = (result,) if dict(prepared.visibility_before).get(result, False) else ()
        publish_operation(
            document,
            prepared.boundary,
            pipeline,
            replaced_inputs=replaced,
        )
        for obj, _visible in prepared.visibility_before:
            if is_live(document, obj) and obj is not pipeline:
                obj.ViewObject.Visibility = False
        pipeline.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM post pipeline could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "pipeline": pipeline,
            "replaced": replaced,
        },
        recompute_targets=(pipeline, analysis),
        created=(object_identity(pipeline),),
        changed=(object_identity(analysis),),
    )


def verify_post_pipeline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    pipeline = draft.value["pipeline"]
    replaced = draft.value["replaced"]
    analysis = prepared.analysis.analysis
    result = prepared.result.result
    verify_operation_block(
        document,
        prepared.boundary,
        pipeline,
        replaced_inputs=replaced,
    )
    state = result_state(pipeline)
    checks = {
        "live pipeline": is_live(document, pipeline),
        "analysis membership": pipeline in tuple(analysis.Group or ()),
        "result retained": is_live(document, result)
        and result in tuple(analysis.Group or ()),
        "label": str(pipeline.Label) == prepared.label,
        "result data": state["result_kind"] == "pipeline"
        and state["data_available"]
        and state["point_count"] == len(tuple(result.NodeNumbers or ())),
        "pipeline visible": bool(pipeline.ViewObject.Visibility),
        "other objects hidden": all(
            not bool(obj.ViewObject.Visibility)
            for obj, _visible in prepared.visibility_before
            if is_live(document, obj) and obj is not pipeline
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM post pipeline failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_pipeline": state,
        "source_result": result_state(result, include_ranges=False),
        "analysis": analysis_state(analysis),
        "presentation": {
            "visible_pipeline": str(pipeline.Name),
            "hidden_object_count": sum(
                1
                for obj, visible in prepared.visibility_before
                if visible and is_live(document, obj) and obj is not pipeline
            ),
        },
    }


def prepare_post_branch(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    mode: Any,
    output: Any,
) -> PreparedPostBranch:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document,
        source_target.result,
        source_target.kind,
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    normalized_mode = str(mode or "").strip().lower()
    normalized_output = str(output or "").strip().lower()
    if normalized_mode not in {"serial", "parallel"}:
        raise NativeAnalyzeError("mode must be serial or parallel.")
    if normalized_output not in {"passthrough", "append"}:
        raise NativeAnalyzeError("output must be passthrough or append.")
    return PreparedPostBranch(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        normalized_mode,
        normalized_output,
        bool(source_target.result.ViewObject.Visibility),
    )


def create_post_branch(
    document: Any,
    prepared: PreparedPostBranch,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostBranch):
        raise TypeError("prepared must be a PreparedPostBranch")
    require_boundary(document, prepared.boundary)
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    if (
        not is_live(document, source)
        or not is_live(document, parent_group)
        or not is_live(document, pipeline)
        or _post_parent_group(document, source, prepared.source.kind) is not parent_group
        or _owning_post_pipeline(document, parent_group) is not pipeline
    ):
        raise NativeAnalyzeError(
            "The exact post-processing graph changed after branch preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        branch = document.addObject(
            "Fem::FemPostBranchFilter",
            document.getUniqueObjectName("Branch"),
        )
        branch.Label = prepared.label
        parent_group.addObject(branch)
        branch.Mode = prepared.mode.title()
        branch.Output = prepared.output.title()
        branch.ViewObject.DisplayMode = "Surface"
        branch.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, branch)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            branch,
            replaced_inputs=replaced,
        )
        source.ViewObject.Visibility = False
        branch.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM post branch could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed_objects = tuple(dict.fromkeys((parent_group, pipeline)))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "branch": branch,
            "replaced": replaced,
        },
        recompute_targets=(branch, parent_group, pipeline),
        created=(object_identity(branch),),
        changed=tuple(object_identity(obj) for obj in changed_objects),
    )


def verify_post_branch(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    branch = draft.value["branch"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    verify_operation_block(
        document,
        prepared.boundary,
        branch,
        replaced_inputs=replaced,
    )
    state = result_state(branch)
    expected_mode = prepared.mode.title()
    expected_output = prepared.output.title()
    checks = {
        "live branch": is_live(document, branch),
        "parent group membership": branch in tuple(parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, branch) is pipeline,
        "source retained": is_live(document, source),
        "label": str(branch.Label) == prepared.label,
        "mode": str(branch.Mode) == expected_mode,
        "output": str(branch.Output) == expected_output,
        "branch visible": bool(branch.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
        "empty branch": len(tuple(branch.Group or ())) == 0,
    }
    if prepared.output == "passthrough":
        checks["passthrough data"] = (
            state["data_available"]
            and state["point_count"]
            == result_state(source, include_ranges=False)["point_count"]
        )
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM post branch failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_branch": state,
        "source": result_reference_state(source),
        "parent_group": result_reference_state(parent_group),
        "pipeline": result_reference_state(pipeline),
        "presentation": {
            "visible_object": str(branch.Name),
            "hidden_source": str(source.Name),
        },
    }


def prepare_post_warp(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    vector_field: Any,
    factor: Any,
) -> PreparedPostWarp:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document,
        source_target.result,
        source_target.kind,
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    field = str(vector_field or "").strip()
    available = _available_post_fields(source_target.result, components=3)
    if field not in available:
        raise NativeAnalyzeError(
            f"vector_field {field!r} is not an available three-component point field.",
            error_code="NATIVE_ANALYZE_FIELD_UNAVAILABLE",
            repair={
                "source": {"object_name": str(source_target.result.Name)},
                "available_vector_fields": available[:16],
                "available_vector_fields_truncated": len(available) > 16,
            },
        )
    if type(factor) not in {int, float} or not math.isfinite(float(factor)):
        raise NativeAnalyzeError("factor must be one finite number.")
    normalized_factor = float(factor)
    if not -1_000_000.0 <= normalized_factor <= 1_000_000.0:
        raise NativeAnalyzeError("factor must be between -1000000 and 1000000.")
    return PreparedPostWarp(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        field,
        normalized_factor,
        bool(source_target.result.ViewObject.Visibility),
    )


def create_post_warp(
    document: Any,
    prepared: PreparedPostWarp,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostWarp):
        raise TypeError("prepared must be a PreparedPostWarp")
    require_boundary(document, prepared.boundary)
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    if (
        not is_live(document, source)
        or not is_live(document, parent_group)
        or not is_live(document, pipeline)
        or _post_parent_group(document, source, prepared.source.kind) is not parent_group
        or _owning_post_pipeline(document, parent_group) is not pipeline
        or prepared.vector_field not in _available_post_fields(source, components=3)
    ):
        raise NativeAnalyzeError(
            "The exact post-processing source or vector field changed after warp preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    try:
        warp = document.addObject(
            "Fem::FemPostWarpVectorFilter",
            document.getUniqueObjectName("WarpVector"),
        )
        warp.Label = prepared.label
        parent_group.addObject(warp)
        warp.ViewObject.DisplayMode = "Surface"
        warp.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, warp)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            warp,
            replaced_inputs=replaced,
        )
        if document.recompute() is False:
            raise NativeAnalyzeError(
                "The FEM warp filter could not populate its input-field choices."
            )
        available = tuple(warp.getEnumerationsOfProperty("Vector") or ())
        if prepared.vector_field not in available:
            raise NativeAnalyzeError(
                "The exact vector field was no longer available after the warp filter "
                "joined its pipeline.",
                error_code="NATIVE_ANALYZE_STATE_STALE",
                repair={
                    "source": {"object_name": str(source.Name)},
                    "available_vector_fields": list(available[:16]),
                    "available_vector_fields_truncated": len(available) > 16,
                },
            )
        warp.Vector = prepared.vector_field
        warp.Factor = prepared.factor
        source.ViewObject.Visibility = False
        warp.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM warp filter could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed_objects = tuple(dict.fromkeys((parent_group, pipeline)))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "warp": warp,
            "replaced": replaced,
        },
        recompute_targets=(warp, parent_group, pipeline),
        created=(object_identity(warp),),
        changed=tuple(object_identity(obj) for obj in changed_objects),
    )


def verify_post_warp(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    warp = draft.value["warp"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    parent_group = prepared.parent_group
    pipeline = prepared.pipeline
    verify_operation_block(
        document,
        prepared.boundary,
        warp,
        replaced_inputs=replaced,
    )
    state = result_state(warp)
    source_state = result_state(source, include_ranges=False)
    checks = {
        "live warp": is_live(document, warp),
        "parent group membership": warp in tuple(parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, warp) is pipeline,
        "source retained": is_live(document, source),
        "label": str(warp.Label) == prepared.label,
        "vector field": str(warp.Vector) == prepared.vector_field,
        "factor": math.isclose(
            float(warp.Factor), prepared.factor, rel_tol=0.0, abs_tol=1e-12
        ),
        "result data": state["data_available"]
        and state["point_count"] == source_state["point_count"],
        "warp visible": bool(warp.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM warp filter failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_warp": state,
        "source": result_reference_state(source),
        "parent_group": result_reference_state(parent_group),
        "pipeline": result_reference_state(pipeline),
        "presentation": {
            "visible_object": str(warp.Name),
            "hidden_source": str(source.Name),
        },
    }
