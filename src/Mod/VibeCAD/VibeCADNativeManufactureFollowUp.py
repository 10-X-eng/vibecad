# SPDX-License-Identifier: LGPL-2.1-or-later

"""Background preparation and atomic creation of related CAM setups."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADMeshConversionJob import (
    PreparedMeshConversion,
    run_mesh_conversion,
)
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureFollowUpState import (
    resolve_simulation_result_target,
    setup_relationship_state,
    simulation_result_state,
)
from VibeCADNativeManufactureJob import (
    JobCreateSpec,
    JobModelInput,
    PreparedJobCreate,
    PreparedJobStock,
    create_job,
    preflight_job_create,
    validate_prepared_job_create,
    verify_created_job,
)
from VibeCADNativeManufactureState import candidate_model_state, is_job, job_state
from VibeCADNativeMeshConvert import (
    capture_mesh_conversion,
    load_verified_mesh_conversion_shape,
)
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_reference


@dataclass(frozen=True, slots=True)
class FrozenFollowUpSetup:
    result: Any
    result_state: Mapping[str, Any]
    source_job: Any
    source_job_state: Mapping[str, Any]
    job_create: PreparedJobCreate
    conversion_request: Any | None
    stock_shape: Any | None
    stock_shape_sha256: str
    stock_shape_type: str
    stock_topology: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PreparedFollowUpStock:
    shape: Any
    artifact_sha256: str
    shape_type: str
    topology: Mapping[str, int]
    cache_hit: bool
    source: str


def _conversion_tolerance(resolution_mm: Any) -> float:
    """Keep sewing tolerance below both CAM resolution and 0.01 mm."""

    try:
        resolution = float(resolution_mm)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The retained stock has no usable simulation resolution.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_INVALID",
        ) from exc
    if not math.isfinite(resolution) or resolution <= 0.0:
        raise NativeManufactureError(
            "The retained stock has no usable simulation resolution.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_INVALID",
        )
    return max(0.000001, min(0.01, resolution / 10.0))


def _source_models(source_job: Any) -> tuple[JobModelInput, ...]:
    proxy = getattr(source_job, "Proxy", None)
    reader = getattr(proxy, "baseObjects", None)
    try:
        sources = tuple(reader(source_job)) if callable(reader) else ()
    except Exception as exc:
        raise NativeManufactureError(
            "The previous setup's workpiece models could not be read.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        ) from exc
    if not sources:
        raise NativeManufactureError(
            "The previous setup has no workpiece model for a follow-up setup.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    result = []
    for source in sources:
        state = candidate_model_state(source)
        view = getattr(source, "ViewObject", None)
        if view is None:
            raise NativeManufactureError(
                "A previous-setup model has no current document presentation.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        result.append(
            JobModelInput(
                target={
                    "object_name": str(source.Name),
                    "expected_state_sha256": str(state["state_sha256"]),
                },
                replace_in_history=bool(view.Visibility),
            )
        )
    return tuple(result)


def preflight_follow_up_setup(
    document: Any,
    document_uid: str,
    *,
    remaining_stock: Mapping[str, Any],
    label: str,
    expected_creation_state_sha256: str,
) -> FrozenFollowUpSetup:
    """Freeze one retained result, its source setup, and new setup inputs."""

    result, result_state = resolve_simulation_result_target(
        document,
        remaining_stock,
    )
    source_job = getattr(result, "SimulationJob", None)
    source_state = job_state(source_job)
    if source_state["state_sha256"] != result_state["source_setup"][
        "expected_state_sha256"
    ]:
        raise NativeManufactureError(
            "The setup that produced this retained stock has changed.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_STALE",
        )
    jobs = tuple(
        obj
        for obj in tuple(document.Objects)
        if is_job(obj)
    )
    prepared_job = preflight_job_create(
        document,
        JobCreateSpec(
            label=label,
            models=_source_models(source_job),
            template={"kind": "none"},
            expected_creation_state_sha256=expected_creation_state_sha256,
            expected_job_count=len(jobs),
        ),
    )
    retained_shape = dict(result_state.get("retained_shape") or {})
    if retained_shape.get("available") is True:
        stock_shape = result.RetainedStockShape.copy()
        stock_shape_sha256 = str(retained_shape["shape_sha256"])
        stock_shape_type = str(retained_shape["shape_type"])
        stock_topology = {"solids": int(retained_shape["solid_count"])}
        request = None
    else:
        stock_shape = None
        stock_shape_sha256 = ""
        stock_shape_type = ""
        stock_topology = {}
        request = capture_mesh_conversion(
            document,
            document_uid,
            source={"object_name": str(result.Name)},
            expected_state_sha256=result_state["mesh_state_sha256"],
            label=f"{str(label).strip()} remaining stock",
            tolerance_mm=_conversion_tolerance(result_state["resolution_mm"]),
            sew_adjacent_faces=True,
            make_solid=True,
            source_topology="sewable",
        )
    return FrozenFollowUpSetup(
        result=result,
        result_state=result_state,
        source_job=source_job,
        source_job_state=source_state,
        job_create=prepared_job,
        conversion_request=request,
        stock_shape=stock_shape,
        stock_shape_sha256=stock_shape_sha256,
        stock_shape_type=stock_shape_type,
        stock_topology=stock_topology,
    )


def validate_follow_up_setup(
    document: Any,
    frozen: FrozenFollowUpSetup,
) -> None:
    if not isinstance(frozen, FrozenFollowUpSetup):
        raise TypeError("frozen must be a FrozenFollowUpSetup")
    validate_prepared_job_create(document, frozen.job_create)
    if frozen.conversion_request is not None and not mesh_target_still_exact(
        document,
        frozen.conversion_request.target,
    ):
        raise NativeManufactureError(
            "The retained CAM material result changed while stock was prepared.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    current_result = simulation_result_state(frozen.result)
    if current_result["state_sha256"] != frozen.result_state["state_sha256"]:
        raise NativeManufactureError(
            "The retained CAM material result changed while stock was prepared.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if job_state(frozen.source_job)["state_sha256"] != frozen.source_job_state[
        "state_sha256"
    ]:
        raise NativeManufactureError(
            "The previous setup changed while retained stock was prepared.",
            error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_STALE",
        )


def prepare_follow_up_stock(
    frozen: FrozenFollowUpSetup,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedFollowUpStock:
    if frozen.stock_shape is not None:
        if cancelled():
            raise NativeBackgroundCancelled()
        progress(20, "Reading retained stock")
        shape = frozen.stock_shape.copy()
        solids = tuple(getattr(shape, "Solids", ()) or ())
        if (
            shape.isNull()
            or not shape.isValid()
            or str(shape.ShapeType) != frozen.stock_shape_type
            or len(solids) != int(frozen.stock_topology.get("solids", 0))
            or any(solid.isNull() or not solid.isValid() for solid in solids)
        ):
            raise NativeManufactureError(
                "The retained CAM stock shape is invalid.",
                error_code="NATIVE_MANUFACTURE_REMAINING_STOCK_INVALID",
            )
        progress(85, "Prepared retained stock")
        return PreparedFollowUpStock(
            shape=shape,
            artifact_sha256=frozen.stock_shape_sha256,
            shape_type=frozen.stock_shape_type,
            topology=frozen.stock_topology,
            cache_hit=True,
            source="simulation_shape",
        )
    converted: PreparedMeshConversion = run_mesh_conversion(
        frozen.conversion_request,
        cancelled=cancelled,
        progress=progress,
    )
    return PreparedFollowUpStock(
        shape=load_verified_mesh_conversion_shape(converted),
        artifact_sha256=converted.artifact_sha256,
        shape_type=converted.shape_type,
        topology=converted.topology,
        cache_hit=converted.cache_hit,
        source="mesh_conversion",
    )


def create_follow_up_setup(
    document: Any,
    frozen: FrozenFollowUpSetup,
    prepared_stock: PreparedFollowUpStock,
) -> NativeMutationDraft:
    """Create the new setup and its initial remaining stock in one transaction."""

    validate_follow_up_setup(document, frozen)
    if not isinstance(prepared_stock, PreparedFollowUpStock):
        raise TypeError("prepared_stock must be a PreparedFollowUpStock")
    shape = prepared_stock.shape
    job_draft = create_job(
        document,
        prepared=frozen.job_create,
        initial_stock=PreparedJobStock(
            shape=shape,
            source=frozen.result,
            artifact_sha256=prepared_stock.artifact_sha256,
            shape_type=prepared_stock.shape_type,
            topology=prepared_stock.topology,
        ),
    )
    job = job_draft.value["job"]
    stock = getattr(job, "Stock", None)
    try:
        from Path.Main.JobRelationship import set_remaining_stock_relationship

        relationship = set_remaining_stock_relationship(
            job,
            previous_setup=frozen.source_job,
            remaining_stock_result=frozen.result,
            remaining_stock_solid=stock,
            previous_setup_state_sha256=frozen.source_job_state["state_sha256"],
            remaining_stock_result_state_sha256=frozen.result_state[
                "state_sha256"
            ],
            machining_program_sha256=frozen.result_state["program_sha256"],
            conversion_artifact_sha256=prepared_stock.artifact_sha256,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_MANUFACTURE_RELATIONSHIP_INVALID",
        ) from exc
    value = dict(job_draft.value)
    value.update(
        frozen=frozen,
        prepared_stock=prepared_stock,
        relationship=relationship,
    )
    return NativeMutationDraft(
        value=value,
        recompute_targets=job_draft.recompute_targets,
        created=job_draft.created,
        changed=job_draft.changed,
        replaced=job_draft.replaced,
    )


def verify_follow_up_setup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    frozen = draft.value["frozen"]
    prepared_stock = draft.value["prepared_stock"]
    job_result = verify_created_job(document, draft)
    job = draft.value["job"]
    stock = getattr(job, "Stock", None)
    shape = getattr(stock, "Shape", None)
    relationship = setup_relationship_state(job)
    if (
        stock is None
        or getattr(stock, "Source", None) is not frozen.result
        or str(getattr(stock, "ArtifactSHA256", "") or "")
        != prepared_stock.artifact_sha256
        or shape is None
        or shape.isNull()
        or str(shape.ShapeType) != prepared_stock.shape_type
        or len(tuple(shape.Solids)) != int(prepared_stock.topology["solids"])
        or not isinstance(relationship, Mapping)
        or relationship.get("current") is not True
        or job_state(frozen.source_job)["state_sha256"]
        != frozen.source_job_state["state_sha256"]
    ):
        raise NativeManufactureError(
            "The follow-up setup failed its retained-stock postcondition.",
            error_code="NATIVE_MANUFACTURE_RELATIONSHIP_POSTCONDITION_FAILED",
        )
    return {
        "follow_up_setup": {
            "setup": job_result["job"],
            "remaining_stock": object_reference(stock),
            "relationship": relationship,
            "conversion": {
                "background": True,
                "cache_hit": bool(prepared_stock.cache_hit),
                "artifact_sha256": prepared_stock.artifact_sha256,
                "solid_count": len(tuple(shape.Solids)),
                "source": prepared_stock.source,
            },
        }
    }
