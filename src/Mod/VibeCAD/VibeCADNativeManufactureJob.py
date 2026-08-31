# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic, human-equivalent Native CAM Job creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureJobState import (
    JobCreationEnvironment,
    PreparedJobTemplate,
    capture_job_creation_environment,
    prepare_job_template,
)
from VibeCADNativeManufactureState import (
    candidate_model_state,
    is_job,
    job_state,
    resolve_model_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    object_identity,
    object_reference,
    read_current_selection,
)


MAX_JOB_MODELS = 32


@dataclass(frozen=True, slots=True)
class JobModelInput:
    target: Mapping[str, Any]
    replace_in_history: bool


@dataclass(frozen=True, slots=True)
class JobCreateSpec:
    label: str
    models: tuple[JobModelInput, ...]
    template: Mapping[str, Any]
    expected_creation_state_sha256: str
    expected_job_count: int


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any | None
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedJobCreate:
    spec: JobCreateSpec
    environment: JobCreationEnvironment
    template: PreparedJobTemplate
    sources: tuple[Any, ...]
    source_states: tuple[Mapping[str, Any], ...]
    replacements: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    jobs_before: tuple[Any, ...]
    selection_before: tuple[Any, ...]
    timeline_before: _TimelineState


@dataclass(frozen=True, slots=True)
class PreparedJobStock:
    shape: Any
    source: Any
    artifact_sha256: str
    shape_type: str
    topology: Mapping[str, int]


def _label(value: Any) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 160:
        raise NativeManufactureError(
            "A CAM Job label must contain 1 to 160 characters.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return clean


def _digest(value: Any, noun: str) -> str:
    clean = str(value or "").strip()
    if len(clean) != 64 or any(character not in "0123456789abcdef" for character in clean):
        raise NativeManufactureError(
            f"The expected {noun} fingerprint must be 64 lowercase hexadecimal characters.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return clean


def _job_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NativeManufactureError(
            "expected_job_count must be a nonnegative integer.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return value


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return _TimelineState(None, (), ())
    if str(getattr(timeline, "TypeId", "") or "") != "App::DocumentTimeline":
        raise NativeManufactureError(
            "The active document History is malformed.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeManufactureError(
            "The active document History is malformed.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return _TimelineState(timeline, operations, visibility)


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _jobs(document: Any) -> tuple[Any, ...]:
    return tuple(obj for obj in document.Objects if is_job(obj))


def _model_inputs(value: tuple[JobModelInput, ...]) -> tuple[JobModelInput, ...]:
    if not 1 <= len(value) <= MAX_JOB_MODELS:
        raise NativeManufactureError(
            "A CAM Job requires 1 through 32 exact model inputs.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    names: set[str] = set()
    for item in value:
        if not isinstance(item, JobModelInput) or not isinstance(item.target, Mapping):
            raise NativeManufactureError(
                "Every CAM Job model input must contain one exact target and replacement decision.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        name = str(item.target.get("object_name") or "").strip()
        if not name or name in names:
            raise NativeManufactureError(
                "CAM Job model inputs must name distinct exact objects.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        names.add(name)
    return value


def preflight_job_create(document: Any, spec: JobCreateSpec) -> PreparedJobCreate:
    """Freeze the exact document, sources, environment, and replacement intent."""

    if not isinstance(spec, JobCreateSpec):
        raise TypeError("spec must be a JobCreateSpec")
    clean_spec = JobCreateSpec(
        label=_label(spec.label),
        models=_model_inputs(tuple(spec.models)),
        template=spec.template,
        expected_creation_state_sha256=_digest(
            spec.expected_creation_state_sha256,
            "CAM Job creation state",
        ),
        expected_job_count=_job_count(spec.expected_job_count),
    )
    if _transaction_open(document):
        raise NativeManufactureError(
            "Finish or cancel the open task before creating a CAM Job.",
            error_code="NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeManufactureError(
            "Wait for the document recompute to finish before creating a CAM Job.",
            error_code="NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    jobs_before = _jobs(document)
    if len(jobs_before) != clean_spec.expected_job_count:
        raise NativeManufactureError(
            "The CAM Job inventory changed after turn start.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
            repair={"current_job_count": len(jobs_before)},
        )
    environment = capture_job_creation_environment()
    if environment.state_sha256 != clean_spec.expected_creation_state_sha256:
        raise NativeManufactureError(
            "The CAM Job creation environment changed after turn start.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
            repair={"current_creation_state_sha256": environment.state_sha256},
        )
    prepared_template = prepare_job_template(environment, clean_spec.template)

    try:
        import Path.Base.Util as PathUtil
    except ImportError as exc:
        raise NativeManufactureError(
            "The CAM Job model validator is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc
    sources: list[Any] = []
    source_states: list[Mapping[str, Any]] = []
    replacements: list[Any] = []
    for item in clean_spec.models:
        source, state = resolve_model_target(document, item.target)
        if not PathUtil.isValidBaseObject(source):
            raise NativeManufactureError(
                f"{source.Name!r} is not accepted as a CAM Job base model.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        view = getattr(source, "ViewObject", None)
        if view is None:
            raise NativeManufactureError(
                f"{source.Name!r} has no GUI representation for Job replacement.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        current_replacement = bool(view.Visibility)
        if current_replacement != bool(item.replace_in_history):
            raise NativeManufactureError(
                f"The History replacement decision for {source.Name!r} changed after turn start.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
                repair={
                    "object_name": str(source.Name),
                    "replace_in_history": current_replacement,
                },
            )
        sources.append(source)
        source_states.append(state)
        if current_replacement:
            replacements.append(source)
    return PreparedJobCreate(
        spec=clean_spec,
        environment=environment,
        template=prepared_template,
        sources=tuple(sources),
        source_states=tuple(source_states),
        replacements=tuple(replacements),
        objects_before=tuple(document.Objects),
        jobs_before=jobs_before,
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def _assert_preflight_current(document: Any, prepared: PreparedJobCreate) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The document graph changed before CAM Job creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if _jobs(document) != prepared.jobs_before:
        raise NativeManufactureError(
            "The CAM Job inventory changed before creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The human selection changed before CAM Job creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if _timeline_state(document) != prepared.timeline_before:
        raise NativeManufactureError(
            "The document History changed before CAM Job creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    environment = capture_job_creation_environment()
    if environment.state_sha256 != prepared.environment.state_sha256:
        raise NativeManufactureError(
            "The CAM Job creation environment changed before creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    for source, state, item in zip(
        prepared.sources,
        prepared.source_states,
        prepared.spec.models,
    ):
        current = candidate_model_state(source)
        if current["state_sha256"] != state["state_sha256"]:
            raise NativeManufactureError(
                f"CAM model {source.Name!r} changed before Job creation.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
            )
        if bool(source.ViewObject.Visibility) != bool(item.replace_in_history):
            raise NativeManufactureError(
                f"The History replacement decision for {source.Name!r} changed before Job creation.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
            )


def validate_prepared_job_create(
    document: Any,
    prepared: PreparedJobCreate,
) -> None:
    """Validate an already-frozen Job creation request without mutating it."""

    if not isinstance(prepared, PreparedJobCreate):
        raise TypeError("prepared must be a PreparedJobCreate")
    _assert_preflight_current(document, prepared)


def _create_core_job(
    document: Any,
    prepared: PreparedJobCreate,
    initial_stock: PreparedJobStock | None,
) -> Any:
    import Path.Main.Job as PathJob

    if initial_stock is not None:
        if prepared.template.content is not None:
            raise NativeManufactureError(
                "A retained-stock setup cannot also load template stock.",
                error_code="NATIVE_ARGUMENTS_INVALID",
            )
        return PathJob.CreateWithPreparedStock(
            "Job",
            list(prepared.sources),
            shape=initial_stock.shape,
            source=initial_stock.source,
            artifact_sha256=initial_stock.artifact_sha256,
            shape_type=initial_stock.shape_type,
            topology=initial_stock.topology,
        )
    if prepared.template.content is None:
        return PathJob.Create("Job", list(prepared.sources), templateFile=None)
    with tempfile.TemporaryDirectory(prefix="vibecad-native-cam-job-") as directory:
        template_path = Path(directory) / "job_authorized.json"
        template_path.write_bytes(prepared.template.content)
        if template_path.read_bytes() != prepared.template.content:
            raise NativeManufactureError(
                "The authorized CAM Job template could not be staged exactly.",
                error_code="NATIVE_MANUFACTURE_TEMPLATE_STALE",
            )
        return PathJob.Create(
            "Job",
            list(prepared.sources),
            templateFile=str(template_path),
        )


def create_job(
    document: Any,
    *,
    prepared: PreparedJobCreate,
    initial_stock: PreparedJobStock | None = None,
) -> NativeMutationDraft:
    """Create the complete Job graph and publish accepted replacements atomically."""

    if not isinstance(prepared, PreparedJobCreate):
        raise TypeError("prepared must be a PreparedJobCreate")
    _assert_preflight_current(document, prepared)
    try:
        import Path.Main.Gui.Job as PathJobGui

        if initial_stock is not None and not isinstance(
            initial_stock,
            PreparedJobStock,
        ):
            raise TypeError("initial_stock must be a PreparedJobStock or None")
        job = _create_core_job(document, prepared, initial_stock)
        if job is None or not is_job(job):
            raise NativeManufactureError(
                "The CAM Job factory returned no valid Job.",
                error_code="NATIVE_MANUFACTURE_JOB_CREATE_FAILED",
            )
        job.Label = prepared.spec.label
        provider = PathJobGui.ViewProvider(job.ViewObject)
        job.ViewObject.Proxy = provider
        job.ViewObject.addExtension("Gui::ViewProviderGroupExtensionPython")
        provider.setupEditVisibility(job)
        try:
            provider.syncTimelineReplacedInputs(job)
        finally:
            provider.resetEditVisibility(job)
        provider.applyAcceptedReplacementVisibilityTransition(job)
        provider.deleteOnReject = False
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The complete CAM Job graph could not be created.",
            error_code="NATIVE_MANUFACTURE_JOB_CREATE_FAILED",
        ) from exc

    created_objects = tuple(
        obj for obj in document.Objects if obj not in prepared.objects_before
    )
    if not created_objects:
        raise NativeManufactureError(
            "CAM Job creation produced no resource graph.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    recompute_targets = tuple(
        obj
        for obj in created_objects
        if str(getattr(obj, "TypeId", "") or "") != "App::DocumentTimeline"
    )
    return NativeMutationDraft(
        value={
            "job": job,
            "provider": provider,
            "prepared": prepared,
            "created_objects": created_objects,
        },
        recompute_targets=recompute_targets,
        created=(object_identity(job),),
        replaced=tuple(object_identity(source) for source in prepared.replacements),
    )


def _verify_resource_graph(document: Any, draft: NativeMutationDraft) -> tuple[Any, ...]:
    value = draft.value
    job = value["job"]
    prepared = value["prepared"]
    created_objects = tuple(value["created_objects"])
    if tuple(obj for obj in document.Objects if obj not in prepared.objects_before) != created_objects:
        raise NativeManufactureError(
            "CAM Job creation changed objects outside its exact resource graph.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    timeline = document.getObject("VibeCADTimeline")
    allowed_roots = {job, timeline}
    before_count = len(prepared.timeline_before.operations)
    operations = tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()
    appended = operations[before_count:]
    if not appended or appended[-1] is not job:
        raise NativeManufactureError(
            "The CAM Job has no exact resource-first History block.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    resources = tuple(appended[:-1])
    if not resources or len(set(resources)) != len(resources):
        raise NativeManufactureError(
            "The CAM Job has no distinct published resource graph.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    for obj in resources:
        if obj not in created_objects or str(
            getattr(obj, "VibeCADTimelineRole", "") or ""
        ) != "resource":
            raise NativeManufactureError(
                f"Published CAM object {obj.Name!r} is not an exact Job resource.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
        owner = obj
        visited: set[int] = set()
        while str(getattr(owner, "VibeCADTimelineRole", "") or "") == "resource":
            if id(owner) in visited:
                raise NativeManufactureError(
                    "The CAM Job contains a cyclic resource-owner chain.",
                    error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
                )
            visited.add(id(owner))
            owner = getattr(owner, "VibeCADTimelineOwner", None)
            if (
                owner is None
                or getattr(owner, "Document", None) is not document
                or document.getObject(str(getattr(owner, "Name", "") or "")) is not owner
            ):
                raise NativeManufactureError(
                    f"Created CAM object {obj.Name!r} has an invalid resource owner.",
                    error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
                )
        if owner is not job:
            raise NativeManufactureError(
                f"Created CAM object {obj.Name!r} is outside the Job resource graph.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
    implicit: set[Any] = set()
    for resource in resources:
        origin = getattr(resource, "Origin", None)
        if origin is None:
            continue
        implicit.add(origin)
        implicit.update(tuple(getattr(origin, "OriginFeatures", ()) or ()))
    for obj in created_objects:
        if obj in allowed_roots or obj in resources:
            continue
        if obj not in implicit or str(
            getattr(obj, "VibeCADTimelineRole", "") or ""
        ):
            raise NativeManufactureError(
                f"Created CAM object {obj.Name!r} is outside the exact Job graph.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
    return resources


def _verify_timeline(
    document: Any,
    prepared: PreparedJobCreate,
    job: Any,
    resources: tuple[Any, ...],
) -> None:
    before = prepared.timeline_before
    after = _timeline_state(document)
    if after.timeline is None or (
        before.timeline is not None and after.timeline is not before.timeline
    ):
        raise NativeManufactureError(
            "CAM Job creation changed the History identity.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    if after.operations[: len(before.operations)] != before.operations:
        raise NativeManufactureError(
            "CAM Job creation reordered existing History operations.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    appended = after.operations[len(before.operations) :]
    if not appended or appended[-1] is not job or set(appended[:-1]) != set(resources):
        raise NativeManufactureError(
            "The CAM Job resource block is not exact or contiguous in History.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    if len(appended) != len(resources) + 1 or len(set(appended)) != len(appended):
        raise NativeManufactureError(
            "The CAM Job resource block contains duplicate or missing operations.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    replaced = set(prepared.replacements)
    try:
        import PartGui

        for source in prepared.replacements:
            state = PartGui.resolveModelingObject(source)
            if state is not None:
                replaced.add(state)
                operation = getattr(state, "Operation", None)
                if operation is not None:
                    replaced.add(operation)
    except ImportError as exc:
        raise NativeManufactureError(
            "The CAM modeling-state resolver is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc
    for index, operation in enumerate(before.operations):
        if operation not in replaced and after.visibility[index] != before.visibility[index]:
            raise NativeManufactureError(
                "CAM Job creation changed unrelated History presentation.",
                error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
            )


def verify_created_job(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    job = value["job"]
    provider = value["provider"]
    prepared: PreparedJobCreate = value["prepared"]
    if (
        document.getObject(str(job.Name)) is not job
        or not is_job(job)
        or str(job.Label) != prepared.spec.label
        or str(getattr(job, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(job, "VibeCADTimelineOwner", None) is not None
        or job.ViewObject.Proxy is not provider
        or not job.ViewObject.hasExtension("Gui::ViewProviderGroupExtensionPython")
    ):
        raise NativeManufactureError(
            "The created CAM Job failed its exact object postcondition.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    model_resources = tuple(getattr(getattr(job, "Model", None), "Group", ()) or ())
    if len(model_resources) != len(prepared.sources):
        raise NativeManufactureError(
            "The CAM Job did not retain one model resource per exact input.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    for resource, source in zip(model_resources, prepared.sources):
        if job.Proxy.baseObject(job, resource) is not source:
            raise NativeManufactureError(
                "A CAM Job model resource lost its exact public source.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
    replacements = tuple(getattr(job, "VibeCADTimelineReplacedInputs", ()) or ())
    if replacements != prepared.replacements:
        raise NativeManufactureError(
            "The CAM Job retained the wrong History replacement set.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    for source, before, item in zip(
        prepared.sources,
        prepared.source_states,
        prepared.spec.models,
    ):
        if candidate_model_state(source)["state_sha256"] != before["state_sha256"]:
            raise NativeManufactureError(
                "CAM Job creation changed a public model input.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
        if bool(source.ViewObject.Visibility):
            raise NativeManufactureError(
                "The CAM Job did not preserve the accepted model replacement visibility.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
        if bool(item.replace_in_history) != (source in replacements):
            raise NativeManufactureError(
                "The CAM Job replacement metadata disagrees with the exact request.",
                error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "CAM Job creation changed the human selection.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    resources = _verify_resource_graph(document, draft)
    _verify_timeline(document, prepared, job, resources)
    state = job_state(job)
    if (
        state["counts"]["models"] != len(prepared.sources)
        or state["counts"]["tools"] < 1
        or "stock" not in state
        or _jobs(document) != (*prepared.jobs_before, job)
    ):
        raise NativeManufactureError(
            "The CAM Job is missing its stock, tool, or model setup.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    return {
        "job": state,
        "template": prepared.template.summary(),
        "history_replacements": [
            object_reference(source) for source in prepared.replacements
        ],
        "resource_count": len(resources),
    }
