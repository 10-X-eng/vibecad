# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded presentation projections over governed engineering contracts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .analysis_persistence import ANALYSIS_METADATA_SCHEMA_VERSION
from .analysis_workflow import WORKFLOW_SCHEMA_VERSION
from .engineering_contracts import EngineeringResultEnvelope
from .governed_optimization import OPTIMIZATION_SCHEMA_VERSION


EXPERIENCE_SCHEMA_VERSION = 1
MAX_PRESENTATION_METRICS = 128
MAX_PRESENTATION_FIELDS = 128
MAX_PRESENTATION_CHART_SERIES = 64
MAX_LABEL_LENGTH = 160
MAX_UNIT_LENGTH = 48
MAX_ACTIVITY_ATTEMPTS = 256
MAX_ACTIVITY_ARTIFACTS = 4096
MAX_ACTIVITY_CURRENTNESS_EVALUATIONS = 4096
MAX_ACTIVITY_EVENTS = 8192
MAX_MANUFACTURE_OUTPUTS = 64
ASSOCIATIONS = frozenset({"point", "cell", "object"})
PRESENTATIONS = frozenset({"scalar", "vector", "tensor"})
RANGE_MODES = frozenset({"auto", "manual", "clamped"})
SCIENTIFIC_COLOR_MAPS = frozenset(
    {"turbo", "viridis", "inferno", "blue-white-red", "safety-factor"}
)


def _text(value: Any, field: str, maximum: int = MAX_LABEL_LENGTH) -> str:
    if not isinstance(value, str):
        raise AnalysisContractError(f"{field} must be a string.")
    clean = value.strip()
    if not clean or len(clean) > maximum:
        raise AnalysisContractError(f"{field} must contain 1 through {maximum} characters.")
    return clean


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisContractError(f"{field} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisContractError(f"{field} must be finite.")
    return result


@dataclass(frozen=True, slots=True)
class PresentationMetric:
    metric_id: str
    label: str
    value: float
    unit: str
    qualifier: str = "value"

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _text(self.metric_id, "metric_id"))
        object.__setattr__(self, "label", _text(self.label, "label"))
        object.__setattr__(self, "value", _number(self.value, "value"))
        object.__setattr__(self, "unit", _text(self.unit, "unit", MAX_UNIT_LENGTH))
        object.__setattr__(self, "qualifier", _text(self.qualifier, "qualifier", 32))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class EngineeringFieldProjection:
    field_id: str
    label: str
    semantic: str
    association: str
    components: int
    unit: str | None
    minimum: float | None
    maximum: float | None
    presentation: str
    default_color_map: str

    def __post_init__(self) -> None:
        for field in ("field_id", "label", "semantic"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if self.unit is not None:
            object.__setattr__(
                self, "unit", _text(self.unit, "unit", MAX_UNIT_LENGTH)
            )
        if self.association not in ASSOCIATIONS:
            raise AnalysisContractError("Unknown field association.")
        if type(self.components) is not int or not 1 <= self.components <= 16:
            raise AnalysisContractError("Field components are outside the bounded range.")
        if (self.minimum is None) != (self.maximum is None):
            raise AnalysisContractError(
                "Field minimum and maximum must both be known or both be unavailable."
            )
        if self.minimum is not None:
            object.__setattr__(self, "minimum", _number(self.minimum, "minimum"))
            object.__setattr__(self, "maximum", _number(self.maximum, "maximum"))
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise AnalysisContractError("Field minimum cannot exceed maximum.")
        if self.presentation not in PRESENTATIONS:
            raise AnalysisContractError("Unknown field presentation.")
        if self.default_color_map not in SCIENTIFIC_COLOR_MAPS:
            raise AnalysisContractError("Unknown scientific color map.")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class EngineeringFieldViewState:
    """Bounded UI state; never scientific data or presentation authority."""

    selected_field_id: str
    color_map: str
    range_mode: str = "auto"
    range_minimum: float | None = None
    range_maximum: float | None = None
    deformation_scale: float = 1.0
    show_mesh_edges: bool = False
    show_legend: bool = True
    show_undeformed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selected_field_id", _text(self.selected_field_id, "selected_field_id")
        )
        if self.color_map not in SCIENTIFIC_COLOR_MAPS:
            raise AnalysisContractError("Unknown scientific color map.")
        if self.range_mode not in RANGE_MODES:
            raise AnalysisContractError("Unknown engineering field range mode.")
        known_range = self.range_minimum is not None or self.range_maximum is not None
        if self.range_mode == "auto" and known_range:
            raise AnalysisContractError("Automatic range mode cannot carry a manual range.")
        if self.range_mode != "auto" and (
            self.range_minimum is None or self.range_maximum is None
        ):
            raise AnalysisContractError("Manual and clamped range modes require both limits.")
        if self.range_minimum is not None:
            object.__setattr__(self, "range_minimum", _number(self.range_minimum, "range_minimum"))
            object.__setattr__(self, "range_maximum", _number(self.range_maximum, "range_maximum"))
            if self.range_minimum > self.range_maximum:
                raise AnalysisContractError("View range minimum cannot exceed maximum.")
        object.__setattr__(self, "deformation_scale", _number(self.deformation_scale, "deformation_scale"))
        if not 0.0 <= self.deformation_scale <= 1_000_000.0:
            raise AnalysisContractError("deformation_scale must be between 0 and 1000000.")
        for name in ("show_mesh_edges", "show_legend", "show_undeformed"):
            if type(getattr(self, name)) is not bool:
                raise AnalysisContractError(f"{name} must be a boolean.")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class EngineeringChartAxis:
    label: str
    unit: str | None
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _text(self.label, "chart axis label"))
        if self.unit is not None:
            object.__setattr__(self, "unit", _text(self.unit, "chart axis unit", MAX_UNIT_LENGTH))
        object.__setattr__(self, "minimum", _number(self.minimum, "chart axis minimum"))
        object.__setattr__(self, "maximum", _number(self.maximum, "chart axis maximum"))
        if self.minimum > self.maximum:
            raise AnalysisContractError("Chart axis minimum cannot exceed maximum.")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class EngineeringChartSeries:
    """Bounded descriptor for data retained and rendered by an existing owner."""

    series_id: str
    label: str
    kind: str
    row_count: int
    x_axis: EngineeringChartAxis | None
    y_axes: tuple[EngineeringChartAxis, ...]
    owner_state_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _text(self.series_id, "series_id"))
        object.__setattr__(self, "label", _text(self.label, "chart label"))
        if self.kind not in {"table", "histogram", "line_plot"}:
            raise AnalysisContractError("Unknown engineering chart kind.")
        if type(self.row_count) is not int or self.row_count < 1:
            raise AnalysisContractError("Engineering chart row_count must be positive.")
        if self.x_axis is not None and not isinstance(self.x_axis, EngineeringChartAxis):
            raise AnalysisContractError("Engineering chart x_axis is invalid.")
        axes = tuple(self.y_axes)
        if len(axes) > MAX_PRESENTATION_CHART_SERIES or any(
            not isinstance(axis, EngineeringChartAxis) for axis in axes
        ):
            raise AnalysisContractError("Engineering chart series exceed their bounded contract.")
        if self.kind == "line_plot" and (self.x_axis is None or not axes):
            raise AnalysisContractError("Line plots require exact x and y axis descriptors.")
        if self.kind == "histogram" and (self.x_axis is None or axes):
            raise AnalysisContractError(
                "Histogram projections expose only their exact source-value axis."
            )
        if self.kind == "table" and (self.x_axis is not None or not axes):
            raise AnalysisContractError("Tables require one or more exact column descriptors.")
        object.__setattr__(self, "y_axes", axes)
        digest = _text(self.owner_state_sha256, "owner_state_sha256", 64)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise AnalysisContractError("owner_state_sha256 must be a lowercase SHA-256 digest.")
        object.__setattr__(self, "owner_state_sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "label": self.label,
            "kind": self.kind,
            "row_count": self.row_count,
            "x_axis": None if self.x_axis is None else self.x_axis.to_dict(),
            "y_axes": [axis.to_dict() for axis in self.y_axes],
            "owner_state_sha256": self.owner_state_sha256,
            "values_copied": False,
            "presentation_owner_unchanged": True,
        }


@dataclass(frozen=True, slots=True)
class DomainPresentation:
    title: str
    metrics: tuple[PresentationMetric, ...] = ()
    fields: tuple[EngineeringFieldProjection, ...] = ()
    extension: CanonicalJson = CanonicalJson("{}")

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _text(self.title, "title"))
        metrics, fields = tuple(self.metrics), tuple(self.fields)
        if len(metrics) > MAX_PRESENTATION_METRICS or any(not isinstance(item, PresentationMetric) for item in metrics):
            raise AnalysisContractError("Presentation metrics exceed their bounded contract.")
        if len(fields) > MAX_PRESENTATION_FIELDS or any(not isinstance(item, EngineeringFieldProjection) for item in fields):
            raise AnalysisContractError("Presentation fields exceed their bounded contract.")
        if len({item.metric_id for item in metrics}) != len(metrics):
            raise AnalysisContractError("Presentation metric IDs must be unique.")
        if len({item.field_id for item in fields}) != len(fields):
            raise AnalysisContractError("Presentation field IDs must be unique.")
        if not isinstance(self.extension, CanonicalJson):
            raise AnalysisContractError("extension must be CanonicalJson.")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "fields", fields)


def governance_role(axis: str, value: str) -> str:
    """Return a semantic status role; never a scientific field color."""

    normalized = str(value or "").strip().lower().replace("_", "-")
    if axis == "execution":
        if normalized in {"succeeded", "solved", "completed"}: return "positive"
        if normalized in {"running", "queued", "pending", "cancel-requested"}: return "active"
        if normalized in {"failed", "error", "invalid"}: return "negative"
        return "historical"
    if axis == "verification":
        if normalized in {"pass", "passed", "verified"}: return "positive"
        if normalized in {"fail", "failed", "blocking"}: return "negative"
        return "caution"
    if axis == "currentness":
        if normalized == "current": return "positive"
        if normalized in {"stale", "indeterminate"}: return "caution"
        return "historical"
    if axis == "publication":
        if normalized == "published": return "positive"
        if normalized in {"publishing", "authorized"}: return "active"
        if normalized in {"failed", "rejected"}: return "negative"
        return "historical"
    raise AnalysisContractError("Unknown governance status axis.")


def project_engineering_result(
    envelope: EngineeringResultEnvelope,
    domain: DomainPresentation,
) -> dict[str, Any]:
    """Project an exact result for display without acquiring domain authority."""

    if not isinstance(envelope, EngineeringResultEnvelope):
        raise TypeError("envelope must be EngineeringResultEnvelope")
    if not isinstance(domain, DomainPresentation):
        raise TypeError("domain must be DomainPresentation")
    axes = {
        "execution": {"value": envelope.execution_status,
                      "role": governance_role("execution", envelope.execution_status)},
        "verification": {"value": envelope.verification_verdict,
                         "role": governance_role("verification", envelope.verification_verdict)},
        "currentness": {"value": envelope.currentness,
                        "role": governance_role("currentness", envelope.currentness)},
        "publication": {"value": envelope.publication_state,
                        "role": governance_role("publication", envelope.publication_state)},
    }
    value = {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "presentation_only": True,
        "authority": {"may_mutate": False, "may_execute": False,
                      "may_verify": False, "may_publish": False, "may_export": False},
        "title": domain.title,
        "domain": envelope.domain,
        "adapter_id": envelope.adapter_id,
        "result_id": envelope.result_id.to_dict(),
        "activity_id": envelope.activity_id.to_dict(),
        "source_identity": envelope.source_identity.to_dict(),
        "dependency_digest": envelope.dependency_digest,
        "provider_attempt_id": envelope.provider_attempt_id,
        "axes": axes,
        "metrics": [item.to_dict() for item in domain.metrics],
        "fields": [item.to_dict() for item in domain.fields],
        "findings": [item.to_dict() for item in envelope.findings],
        "artifacts": [item.to_dict() for item in envelope.artifacts],
        "provenance": envelope.provenance.to_dict(),
        "summary_metrics": envelope.summary_metrics.to_value(),
        "domain_extension": domain.extension.to_value(),
        "domain_payload": envelope.domain_payload.to_value(),
        "domain_payload_sha256": envelope.domain_payload.sha256(),
    }
    # Re-validate the complete projection as inert JSON and enforce canonicality.
    return CanonicalJson.from_value(value).to_value()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisContractError(f"{field} must be a mapping.")
    return CanonicalJson.from_value(value).to_value()


def _inert(kind: str) -> dict[str, Any]:
    return {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "projection_kind": kind,
        "presentation_only": True,
        "authority": {"may_mutate": False, "may_execute": False,
                      "may_recover": False, "may_schedule": False,
                      "may_rank": False, "may_select": False,
                      "may_publish": False, "may_export": False},
    }


def project_analysis_activity(
    record: Mapping[str, Any],
    *,
    restart_disposition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one validated durable Analysis record without acting on it."""

    source = _mapping(record, "analysis record")
    required = ("schema_version", "analysis_id", "domain", "adapter_id",
                "source_document_uid", "state", "attempts", "artifacts",
                "currentness_evaluations", "publication", "events")
    if any(name not in source for name in required):
        raise AnalysisContractError("Analysis activity record is incomplete.")
    if source["schema_version"] != ANALYSIS_METADATA_SCHEMA_VERSION:
        raise AnalysisContractError("Unsupported Analysis activity schema version.")
    attempts, artifacts, events = source["attempts"], source["artifacts"], source["events"]
    currentness = source["currentness_evaluations"]
    if not isinstance(attempts, list) or len(attempts) > MAX_ACTIVITY_ATTEMPTS:
        raise AnalysisContractError("Analysis attempts exceed their presentation bound.")
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ACTIVITY_ARTIFACTS:
        raise AnalysisContractError("Analysis artifacts exceed their presentation bound.")
    if not isinstance(currentness, list) or len(currentness) > MAX_ACTIVITY_CURRENTNESS_EVALUATIONS:
        raise AnalysisContractError("Analysis currentness evaluations exceed their presentation bound.")
    if not isinstance(events, list) or len(events) > MAX_ACTIVITY_EVENTS:
        raise AnalysisContractError("Analysis events exceed their presentation bound.")
    disposition = None if restart_disposition is None else _mapping(
        restart_disposition, "restart disposition"
    )
    if disposition is not None and disposition.get("analysis_id") != source["analysis_id"]:
        raise AnalysisContractError("Restart disposition belongs to another Analysis identity.")
    publication = source["publication"]
    if not isinstance(publication, Mapping):
        raise AnalysisContractError("Analysis publication state is invalid.")
    value = {
        **_inert("analysis_activity"),
        "analysis_id": source["analysis_id"],
        "domain": source["domain"],
        "adapter_id": source["adapter_id"],
        "source_document_uid": source["source_document_uid"],
        "state": source["state"],
        "created_at": source.get("created_at"),
        "updated_at": source.get("updated_at"),
        "terminal_reason": source.get("terminal_reason"),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "currentness_evaluations": currentness,
        "publication": publication,
        "publication_axes": {
            "intent_recorded": publication.get("intent") is not None,
            "authorization_recorded": publication.get("authorization") is not None,
            "receipt_recorded": publication.get("receipt") is not None,
        },
        "restart_disposition": disposition,
        "latest_event": events[-1] if events else None,
    }
    return CanonicalJson.from_value(value).to_value()


def project_workflow_run(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project the authoritative durable workflow state without scheduling."""

    source = _mapping(record, "workflow record")
    required = ("schema_version", "run_id", "workflow_id", "workflow_version",
                "definition_sha256", "state", "nodes")
    if any(name not in source for name in required):
        raise AnalysisContractError("Workflow run record is incomplete.")
    if source["schema_version"] != WORKFLOW_SCHEMA_VERSION:
        raise AnalysisContractError("Unsupported workflow schema version.")
    nodes = source.get("nodes")
    if not isinstance(nodes, Mapping) or not 1 <= len(nodes) <= 128:
        raise AnalysisContractError("Workflow nodes exceed their presentation bound.")
    projected_nodes = []
    for node_id in sorted(nodes):
        node = nodes[node_id]
        if not isinstance(node, Mapping):
            raise AnalysisContractError("Workflow node state is invalid.")
        attempts = node.get("attempts")
        if not isinstance(attempts, list) or len(attempts) > 17:
            raise AnalysisContractError("Workflow node attempts exceed their bound.")
        projected_nodes.append({
            "node_id": node_id,
            "state": node.get("state"),
            "analysis_id": node.get("analysis_id"),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "outcome": node.get("outcome"),
            "publication_receipt_id": node.get("publication_receipt_id"),
        })
    value = {
        **_inert("workflow_run"),
        "run_id": source["run_id"],
        "workflow_id": source["workflow_id"],
        "workflow_version": source["workflow_version"],
        "definition_sha256": source["definition_sha256"],
        "state": source["state"],
        "cancel_requested": source.get("cancel_requested"),
        "created_at": source.get("created_at"),
        "updated_at": source.get("updated_at"),
        "nodes": projected_nodes,
        "counts": {state: sum(node["state"] == state for node in projected_nodes)
                   for state in sorted({str(node["state"]) for node in projected_nodes})},
    }
    return CanonicalJson.from_value(value).to_value()


def project_optimization_run(
    record: Mapping[str, Any],
    ranking: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project persisted candidates plus an authoritative precomputed ranking."""

    source = _mapping(record, "optimization record")
    required = ("schema_version", "run_id", "definition_sha256",
                "source_document_uid", "source_revision", "source_sha256",
                "workflow_definition_sha256", "candidates")
    if any(name not in source for name in required):
        raise AnalysisContractError("Optimization run record is incomplete.")
    if source["schema_version"] != OPTIMIZATION_SCHEMA_VERSION:
        raise AnalysisContractError("Unsupported optimization schema version.")
    candidates = source.get("candidates")
    if not isinstance(candidates, Mapping) or not 1 <= len(candidates) <= 4096:
        raise AnalysisContractError("Optimization candidates exceed their presentation bound.")
    ranking_value = CanonicalJson.from_value(list(ranking)).to_value()
    if len(ranking_value) > len(candidates):
        raise AnalysisContractError("Optimization ranking contains excess candidates.")
    ranks = {}
    for item in ranking_value:
        if not isinstance(item, Mapping) or item.get("candidate_id") not in candidates:
            raise AnalysisContractError("Optimization ranking references an unknown candidate.")
        candidate_id = item["candidate_id"]
        if candidate_id in ranks:
            raise AnalysisContractError("Optimization ranking contains a duplicate candidate.")
        ranks[candidate_id] = item
    projected = []
    for candidate_id in sorted(candidates):
        candidate = candidates[candidate_id]
        if not isinstance(candidate, Mapping):
            raise AnalysisContractError("Optimization candidate is invalid.")
        rank = ranks.get(candidate_id)
        projected.append({
            "candidate_id": candidate_id,
            "candidate_sha256": candidate.get("candidate_sha256"),
            "values": candidate.get("values"),
            "mutation_proposal": candidate.get("mutation_proposal"),
            "state": candidate.get("state"),
            "currentness": candidate.get("currentness"),
            "workflow_run_id": candidate.get("workflow_run_id"),
            "workflow_attempt_count": len(candidate.get("workflow_run_ids") or []),
            "metrics": candidate.get("metrics"),
            "findings": candidate.get("findings"),
            "rank": None if rank is None else rank.get("rank"),
            "constraint_failures": [] if rank is None else rank.get("constraint_failures", []),
        })
    value = {
        **_inert("optimization_run"),
        "run_id": source["run_id"],
        "definition_sha256": source["definition_sha256"],
        "source_document_uid": source["source_document_uid"],
        "source_revision": source["source_revision"],
        "source_sha256": source["source_sha256"],
        "workflow_definition_sha256": source["workflow_definition_sha256"],
        "created_at": source.get("created_at"),
        "updated_at": source.get("updated_at"),
        "candidates": projected,
        "selection": source.get("selection"),
        "publication": source.get("publication"),
    }
    return CanonicalJson.from_value(value).to_value()


def project_manufacture_post_evidence(
    result: Mapping[str, Any],
    *,
    analysis_record: Mapping[str, Any],
    workflow_record: Mapping[str, Any],
    provider_attempt_id: str,
) -> dict[str, Any]:
    """Project an owning Manufacture post result without certifying a process."""

    source = _mapping(result, "Manufacture post result")
    activity = project_analysis_activity(analysis_record)
    workflow = project_workflow_run(workflow_record)
    if activity["domain"] != "manufacture":
        raise AnalysisContractError("Manufacture evidence requires a Manufacture Analysis record.")
    attempt_id = _text(provider_attempt_id, "provider_attempt_id")
    attempts = activity["attempts"]
    if not any(str(item.get("attempt")) == attempt_id for item in attempts):
        raise AnalysisContractError("Manufacture provider attempt is not in the Analysis record.")
    if not any(node["analysis_id"] == activity["analysis_id"] for node in workflow["nodes"]):
        raise AnalysisContractError("Manufacture Analysis is not bound to the workflow run.")
    governance = source.get("governance")
    if governance is not None:
        if not isinstance(governance, Mapping):
            raise AnalysisContractError("Manufacture governance references are invalid.")
        expected_governance = {
            "analysis_id": activity["analysis_id"],
            "workflow_run_id": workflow["run_id"],
            "provider_attempt_id": attempt_id,
        }
        if any(
            str(governance.get(name) or "") != value
            for name, value in expected_governance.items()
        ):
            raise AnalysisContractError(
                "Manufacture governance references do not match evidence."
            )
    required = ("operation", "job", "postprocessor", "outputs", "output_count",
                "total_size_bytes", "document_unchanged", "history_unchanged",
                "selection_unchanged", "visibility_unchanged", "claim_ceiling",
                "proven_toolpath", "manufacturable")
    if any(name not in source for name in required):
        raise AnalysisContractError("Manufacture post result is incomplete.")
    if source["operation"] not in {"complete_job", "selected_operations"}:
        raise AnalysisContractError("Unknown Manufacture post operation.")
    if (source["claim_ceiling"] != "not_proven_toolpath"
            or source["proven_toolpath"] is not False
            or source["manufacturable"] is not False):
        raise AnalysisContractError("Manufacture post evidence exceeds its claim ceiling.")
    job, postprocessor = source["job"], source["postprocessor"]
    if not isinstance(job, Mapping) or not isinstance(postprocessor, Mapping):
        raise AnalysisContractError("Manufacture owner evidence is invalid.")
    if not str(job.get("object_name") or "").strip() or len(str(job.get("state_sha256") or "")) != 64:
        raise AnalysisContractError("Manufacture Job identity is invalid.")
    outputs = source["outputs"]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_MANUFACTURE_OUTPUTS:
        raise AnalysisContractError("Manufacture outputs exceed their presentation bound.")
    if source["output_count"] != len(outputs):
        raise AnalysisContractError("Manufacture output count does not match its evidence.")
    digests = set()
    total = 0
    for output in outputs:
        if not isinstance(output, Mapping):
            raise AnalysisContractError("Manufacture output evidence is invalid.")
        name = str(output.get("file_name") or "")
        digest = str(output.get("sha256") or "").lower()
        size = output.get("size_bytes")
        if (not name or name in {".", ".."} or "/" in name or "\\" in name
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or type(size) is not int or size < 0):
            raise AnalysisContractError("Manufacture output descriptor is invalid.")
        if digest in digests:
            raise AnalysisContractError("Manufacture output digests must be unique.")
        digests.add(digest)
        total += size
    if governance is not None:
        admitted = {
            str(item.get("sha256") or "").lower()
            for item in activity["artifacts"]
            if isinstance(item, Mapping)
        }
        if not digests <= admitted:
            raise AnalysisContractError(
                "Manufacture output is absent from durable artifacts."
            )
    if source["total_size_bytes"] != total:
        raise AnalysisContractError("Manufacture output byte total does not match its evidence.")
    unchanged = {name: source[name] for name in (
        "document_unchanged", "history_unchanged", "selection_unchanged",
        "visibility_unchanged",
    )}
    if set(unchanged.values()) != {True}:
        raise AnalysisContractError("Manufacture publication changed unrelated domain state.")
    value = {
        **_inert("manufacture_post_evidence"),
        "analysis_id": activity["analysis_id"],
        "workflow_run_id": workflow["run_id"],
        "provider_attempt_id": attempt_id,
        "operation": source["operation"],
        "job": job,
        "operations": source.get("operations", []),
        "postprocessor": postprocessor,
        "outputs": outputs,
        "output_count": len(outputs),
        "total_size_bytes": total,
        "unchanged_state": unchanged,
        "publication_state": "human_authorized_outputs_written",
        "claim_ceiling": source["claim_ceiling"],
        "proven_toolpath": False,
        "manufacturable": False,
    }
    return CanonicalJson.from_value(value).to_value()


def project_manufacture_simulation_evidence(
    kind: str,
    result: Mapping[str, Any],
    *,
    analysis_record: Mapping[str, Any],
    workflow_record: Mapping[str, Any],
    provider_attempt_id: str,
) -> dict[str, Any]:
    """Project bounded Native simulation evidence without process certification."""

    evidence_kind = _text(kind, "kind")
    if evidence_kind not in {"camotics", "gl_simulation", "retained_simulation"}:
        raise AnalysisContractError("Unknown Manufacture simulation evidence kind.")
    source = _mapping(result, "Manufacture simulation result")
    activity = project_analysis_activity(analysis_record)
    workflow = project_workflow_run(workflow_record)
    attempt_id = _text(provider_attempt_id, "provider_attempt_id")
    if activity["domain"] != "manufacture":
        raise AnalysisContractError("Manufacture evidence requires a Manufacture Analysis record.")
    if not any(str(item.get("attempt")) == attempt_id for item in activity["attempts"]):
        raise AnalysisContractError("Manufacture provider attempt is not in the Analysis record.")
    if not any(node["analysis_id"] == activity["analysis_id"] for node in workflow["nodes"]):
        raise AnalysisContractError("Manufacture Analysis is not bound to the workflow run.")
    governance = _mapping(source.get("governance"), "Manufacture governance references")
    expected = {
        "analysis_id": activity["analysis_id"],
        "workflow_run_id": workflow["run_id"],
        "provider_attempt_id": attempt_id,
    }
    if any(str(governance.get(name) or "") != value for name, value in expected.items()):
        raise AnalysisContractError("Manufacture governance references do not match evidence.")

    if evidence_kind == "camotics":
        surface_value = source.get("surface")
        surface = (
            _mapping(surface_value, "CAMotics surface")
            if surface_value is not None else {}
        )
        digest = str(surface.get("sha256") or source.get("program_sha256") or "").lower()
        facts = {
            name: source.get(name)
            for name in (
                "request", "launched", "job", "operation_count", "command_count", "resolution",
                "path_step_count", "duration_seconds", "surface", "program_sha256",
            )
            if name in source
        }
    elif evidence_kind == "gl_simulation":
        simulation = _mapping(source.get("simulation"), "GL simulation")
        if simulation.get("document_changed") is not False:
            raise AnalysisContractError("GL simulation changed the document.")
        digest = str(simulation.get("program_sha256") or "").lower()
        facts = dict(simulation)
    else:
        simulation = _mapping(source.get("simulation_result"), "retained simulation")
        state = _mapping(simulation.get("result"), "retained simulation result")
        digest = str(state.get("state_sha256") or "").lower()
        facts = dict(simulation)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AnalysisContractError("Manufacture simulation digest is invalid.")
    admitted = {
        str(item.get("sha256") or "").lower()
        for item in activity["artifacts"]
        if isinstance(item, Mapping)
    }
    if digest not in admitted:
        raise AnalysisContractError("Manufacture simulation evidence is not durably admitted.")
    value = {
        **_inert("manufacture_simulation_evidence"),
        "kind": evidence_kind,
        "analysis_id": activity["analysis_id"],
        "workflow_run_id": workflow["run_id"],
        "provider_attempt_id": attempt_id,
        "facts": facts,
        "evidence_sha256": digest,
        "publication_state": "evidence_published",
        "claim_ceiling": "simulation_evidence_only",
        "proven_toolpath": False,
        "manufacturable": False,
    }
    return CanonicalJson.from_value(value).to_value()


def project_assembly_state(
    simulation_state: Mapping[str, Any],
    solver_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    """Project exact Native Assembly graph evidence without solving or inferring."""

    state = _mapping(simulation_state, "Assembly simulation state")
    diagnostics = _mapping(solver_diagnostics, "Assembly solver diagnostics")
    required = ("available", "state_sha256", "component_count", "grounded_count",
                "joint_count", "eligible_joint_count", "simulation_count",
                "motion_count", "eligible_joints", "simulations")
    if any(name not in state for name in required) or state["available"] is not True:
        raise AnalysisContractError("Assembly simulation state is incomplete.")
    digest = str(state["state_sha256"] or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AnalysisContractError("Assembly graph state identity is invalid.")
    for field in ("component_count", "grounded_count", "joint_count",
                  "eligible_joint_count", "simulation_count", "motion_count"):
        if type(state[field]) is not int or state[field] < 0:
            raise AnalysisContractError("Assembly graph counts are invalid.")
    joints, simulations = state["eligible_joints"], state["simulations"]
    if not isinstance(joints, list) or len(joints) > 128:
        raise AnalysisContractError("Assembly joint preview exceeds its bound.")
    if not isinstance(simulations, list) or len(simulations) > 128:
        raise AnalysisContractError("Assembly simulation preview exceeds its bound.")
    if "solver_status" not in diagnostics:
        raise AnalysisContractError("Assembly solver diagnostics are incomplete.")
    value = {
        **_inert("assembly_state"),
        "graph_state_sha256": digest,
        "counts": {field: state[field] for field in (
            "component_count", "grounded_count", "joint_count",
            "eligible_joint_count", "simulation_count", "motion_count",
        )},
        "eligible_joints": joints,
        "eligible_joints_truncated": bool(state.get("eligible_joints_truncated", False)),
        "simulations": simulations,
        "simulations_truncated": bool(state.get("simulations_truncated", False)),
        "solver_diagnostics": diagnostics,
        "claim_ceiling": "graph_and_sampled_motion_evidence_only",
        "continuous_motion_certified": False,
        "joint_proposals": [],
        "sequence_proposals": [],
        "service_proposals": [],
    }
    return CanonicalJson.from_value(value).to_value()
