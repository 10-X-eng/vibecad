# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded presentation projections over governed engineering contracts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson
from .engineering_contracts import EngineeringResultEnvelope


EXPERIENCE_SCHEMA_VERSION = 1
MAX_PRESENTATION_METRICS = 128
MAX_PRESENTATION_FIELDS = 128
MAX_LABEL_LENGTH = 160
MAX_UNIT_LENGTH = 48
ASSOCIATIONS = frozenset({"point", "cell", "object"})
PRESENTATIONS = frozenset({"scalar", "vector", "tensor"})
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
    unit: str
    minimum: float
    maximum: float
    presentation: str
    default_color_map: str

    def __post_init__(self) -> None:
        for field in ("field_id", "label", "semantic"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "unit", _text(self.unit, "unit", MAX_UNIT_LENGTH))
        if self.association not in ASSOCIATIONS:
            raise AnalysisContractError("Unknown field association.")
        if type(self.components) is not int or not 1 <= self.components <= 16:
            raise AnalysisContractError("Field components are outside the bounded range.")
        object.__setattr__(self, "minimum", _number(self.minimum, "minimum"))
        object.__setattr__(self, "maximum", _number(self.maximum, "maximum"))
        if self.minimum > self.maximum:
            raise AnalysisContractError("Field minimum cannot exceed maximum.")
        if self.presentation not in PRESENTATIONS:
            raise AnalysisContractError("Unknown field presentation.")
        if self.default_color_map not in SCIENTIFIC_COLOR_MAPS:
            raise AnalysisContractError("Unknown scientific color map.")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


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

