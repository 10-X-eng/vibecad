# SPDX-License-Identifier: LGPL-2.1-or-later

"""Versioned, domain-neutral engineering result and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson


ENGINEERING_CONTRACT_MAJOR = 1
ENGINEERING_CONTRACT_MINOR = 0
MAX_ENVELOPE_BYTES = 1024 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = re.compile(
    r"(?:^|[_-])(password|passwd|secret|token|credential|api[_-]?key|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


def _text(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise AnalysisContractError(f"{field} must be non-empty.")
    return clean


def _version(major: int, minor: int) -> None:
    if major != ENGINEERING_CONTRACT_MAJOR:
        raise AnalysisContractError(
            f"Unsupported engineering contract major version {major}."
        )
    if type(minor) is not int or minor < 0:
        raise AnalysisContractError("contract_minor must be a non-negative integer.")


def _tuple_of(value: Any, expected: type, field: str) -> tuple[Any, ...]:
    result = tuple(value)
    if any(not isinstance(item, expected) for item in result):
        raise AnalysisContractError(f"{field} contains an invalid value.")
    return result


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise AnalysisContractError(f"{field} IDs must be unique.")


def _reject_secrets(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SECRET_KEYS.search(str(key)):
                raise AnalysisContractError(f"Secret-bearing field is forbidden at {path}.")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str) and _ABSOLUTE_PATH.match(value):
        raise AnalysisContractError(f"Absolute paths are forbidden at {path}.")


def _canonical_payload(value: Any, field: str) -> CanonicalJson:
    _reject_secrets(value, field)
    payload = CanonicalJson.from_value(value)
    if len(payload.encoded.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise AnalysisContractError(f"{field} exceeds the bounded envelope size.")
    return payload


@dataclass(frozen=True, slots=True)
class EngineeringIdentity:
    namespace: str
    owner: str
    kind: str
    value: str
    version: str

    def __post_init__(self) -> None:
        for name in ("namespace", "owner", "kind", "value", "version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    @property
    def canonical(self) -> str:
        return f"{self.namespace}:{self.owner}:{self.kind}:{self.version}:{self.value}"

    def require_same_type(self, other: "EngineeringIdentity") -> None:
        if not isinstance(other, EngineeringIdentity) or (
            self.namespace, self.owner, self.kind, self.version
        ) != (other.namespace, other.owner, other.kind, other.version):
            raise AnalysisContractError("Engineering identity types are not substitutable.")

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in (
            "namespace", "owner", "kind", "value", "version"
        )}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EngineeringIdentity":
        return cls(**{name: value[name] for name in (
            "namespace", "owner", "kind", "value", "version"
        )})


@dataclass(frozen=True, slots=True)
class ContentDescriptor:
    media_type: str
    digest_algorithm: str
    digest: str
    byte_size: int
    semantic_role: str
    schema: str
    signature_reference: str = ""

    def __post_init__(self) -> None:
        for name in ("media_type", "digest_algorithm", "semantic_role", "schema"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        digest = str(self.digest or "").lower()
        if self.digest_algorithm != "sha256" or not _DIGEST.fullmatch(digest):
            raise AnalysisContractError("Content descriptors require a SHA-256 digest.")
        object.__setattr__(self, "digest", digest)
        if type(self.byte_size) is not int or self.byte_size < 0:
            raise AnalysisContractError("byte_size must be a non-negative integer.")
        object.__setattr__(self, "signature_reference", str(self.signature_reference or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "media_type", "digest_algorithm", "digest", "byte_size",
            "semantic_role", "schema", "signature_reference"
        )}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContentDescriptor":
        return cls(**{name: value.get(name, "") for name in (
            "media_type", "digest_algorithm", "digest", "byte_size",
            "semantic_role", "schema", "signature_reference"
        )})


@dataclass(frozen=True, slots=True)
class FindingEnvelope:
    finding_id: str
    rule_id: str
    source_id: str
    domain: str
    verdict: str
    severity: str
    code: str
    message: str
    affected: tuple[EngineeringIdentity, ...]
    evidence: tuple[ContentDescriptor, ...]
    remediation: str
    currentness: str
    claim_ceiling: str

    def __post_init__(self) -> None:
        for name in ("finding_id", "rule_id", "source_id", "domain", "verdict",
                     "severity", "code", "message", "currentness", "claim_ceiling"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "remediation", str(self.remediation or "").strip())
        object.__setattr__(self, "affected", _tuple_of(self.affected, EngineeringIdentity, "affected"))
        object.__setattr__(self, "evidence", _tuple_of(self.evidence, ContentDescriptor, "evidence"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id, "rule_id": self.rule_id,
            "source_id": self.source_id, "domain": self.domain,
            "verdict": self.verdict, "severity": self.severity, "code": self.code,
            "message": self.message, "affected": [item.to_dict() for item in self.affected],
            "evidence": [item.to_dict() for item in self.evidence],
            "remediation": self.remediation, "currentness": self.currentness,
            "claim_ceiling": self.claim_ceiling,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FindingEnvelope":
        data = dict(value)
        data["affected"] = tuple(EngineeringIdentity.from_dict(item) for item in value["affected"])
        data["evidence"] = tuple(ContentDescriptor.from_dict(item) for item in value["evidence"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ProvenanceNode:
    node_id: str
    node_type: str
    attributes: CanonicalJson

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _text(self.node_id, "node_id"))
        if self.node_type not in {"entity", "activity", "agent"}:
            raise AnalysisContractError("Unknown provenance node type.")
        if not isinstance(self.attributes, CanonicalJson):
            raise AnalysisContractError("attributes must be CanonicalJson.")
        _reject_secrets(self.attributes.to_value(), "provenance attributes")

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "node_type": self.node_type,
                "attributes": self.attributes.to_value()}


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    edge_id: str
    relation: str
    source_id: str
    target_id: str
    role: str = ""

    def __post_init__(self) -> None:
        for name in ("edge_id", "relation", "source_id", "target_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.relation not in {"used", "generated", "derived", "associated",
                                  "delegated", "invalidated"}:
            raise AnalysisContractError("Unknown provenance relation.")
        object.__setattr__(self, "role", str(self.role or "").strip())

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in (
            "edge_id", "relation", "source_id", "target_id", "role"
        )}


@dataclass(frozen=True, slots=True)
class ProvenanceGraph:
    graph_id: str
    nodes: tuple[ProvenanceNode, ...]
    edges: tuple[ProvenanceEdge, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "graph_id", _text(self.graph_id, "graph_id"))
        nodes = _tuple_of(self.nodes, ProvenanceNode, "nodes")
        edges = _tuple_of(self.edges, ProvenanceEdge, "edges")
        _unique(tuple(item.node_id for item in nodes), "provenance node")
        _unique(tuple(item.edge_id for item in edges), "provenance edge")
        node_ids = {item.node_id for item in nodes}
        if any(edge.source_id not in node_ids or edge.target_id not in node_ids for edge in edges):
            raise AnalysisContractError("Provenance edges must reference existing nodes.")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)

    def to_dict(self) -> dict[str, Any]:
        return {"graph_id": self.graph_id,
                "nodes": [item.to_dict() for item in self.nodes],
                "edges": [item.to_dict() for item in self.edges]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProvenanceGraph":
        return cls(
            graph_id=value["graph_id"],
            nodes=tuple(ProvenanceNode(
                node_id=item["node_id"], node_type=item["node_type"],
                attributes=_canonical_payload(item.get("attributes", {}), "attributes")
            ) for item in value["nodes"]),
            edges=tuple(ProvenanceEdge(**item) for item in value["edges"]),
        )


@dataclass(frozen=True, slots=True)
class EngineeringResultEnvelope:
    contract_major: int
    contract_minor: int
    result_id: EngineeringIdentity
    activity_id: EngineeringIdentity
    domain: str
    adapter_id: str
    provider_attempt_id: str
    execution_status: str
    verification_verdict: str
    currentness: str
    publication_state: str
    source_identity: EngineeringIdentity
    dependency_digest: str
    artifacts: tuple[ContentDescriptor, ...]
    summary_metrics: CanonicalJson
    findings: tuple[FindingEnvelope, ...]
    provenance: ProvenanceGraph
    domain_payload: CanonicalJson

    def __post_init__(self) -> None:
        _version(self.contract_major, self.contract_minor)
        for name in ("result_id", "activity_id", "source_identity"):
            if not isinstance(getattr(self, name), EngineeringIdentity):
                raise AnalysisContractError(f"{name} must be EngineeringIdentity.")
        for name in ("domain", "adapter_id", "provider_attempt_id", "execution_status",
                     "verification_verdict", "currentness", "publication_state"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        digest = str(self.dependency_digest or "").lower()
        if not _DIGEST.fullmatch(digest):
            raise AnalysisContractError("dependency_digest must be SHA-256.")
        object.__setattr__(self, "dependency_digest", digest)
        object.__setattr__(self, "artifacts", _tuple_of(self.artifacts, ContentDescriptor, "artifacts"))
        findings = _tuple_of(self.findings, FindingEnvelope, "findings")
        _unique(tuple(item.finding_id for item in findings), "finding")
        object.__setattr__(self, "findings", findings)
        if not isinstance(self.provenance, ProvenanceGraph):
            raise AnalysisContractError("provenance must be ProvenanceGraph.")
        for name in ("summary_metrics", "domain_payload"):
            value = getattr(self, name)
            if not isinstance(value, CanonicalJson):
                raise AnalysisContractError(f"{name} must be CanonicalJson.")
            _reject_secrets(value.to_value(), name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_major": self.contract_major, "contract_minor": self.contract_minor,
            "result_id": self.result_id.to_dict(), "activity_id": self.activity_id.to_dict(),
            "domain": self.domain, "adapter_id": self.adapter_id,
            "provider_attempt_id": self.provider_attempt_id,
            "execution_status": self.execution_status,
            "verification_verdict": self.verification_verdict,
            "currentness": self.currentness, "publication_state": self.publication_state,
            "source_identity": self.source_identity.to_dict(),
            "dependency_digest": self.dependency_digest,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "summary_metrics": self.summary_metrics.to_value(),
            "findings": [item.to_dict() for item in self.findings],
            "provenance": self.provenance.to_dict(),
            "domain_payload": self.domain_payload.to_value(),
        }

    def to_canonical_json(self) -> str:
        payload = _canonical_payload(self.to_dict(), "result envelope")
        return payload.encoded

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EngineeringResultEnvelope":
        _version(value["contract_major"], value["contract_minor"])
        return cls(
            contract_major=value["contract_major"], contract_minor=value["contract_minor"],
            result_id=EngineeringIdentity.from_dict(value["result_id"]),
            activity_id=EngineeringIdentity.from_dict(value["activity_id"]),
            domain=value["domain"], adapter_id=value["adapter_id"],
            provider_attempt_id=value["provider_attempt_id"],
            execution_status=value["execution_status"],
            verification_verdict=value["verification_verdict"],
            currentness=value["currentness"], publication_state=value["publication_state"],
            source_identity=EngineeringIdentity.from_dict(value["source_identity"]),
            dependency_digest=value["dependency_digest"],
            artifacts=tuple(ContentDescriptor.from_dict(item) for item in value["artifacts"]),
            summary_metrics=_canonical_payload(value["summary_metrics"], "summary_metrics"),
            findings=tuple(FindingEnvelope.from_dict(item) for item in value["findings"]),
            provenance=ProvenanceGraph.from_dict(value["provenance"]),
            domain_payload=_canonical_payload(value["domain_payload"], "domain_payload"),
        )

    @classmethod
    def from_canonical_json(cls, encoded: str) -> "EngineeringResultEnvelope":
        try:
            value = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AnalysisContractError("Invalid engineering result JSON.") from exc
        return cls.from_dict(value)


def canonical_payload(value: Any, field: str = "payload") -> CanonicalJson:
    """Create a bounded, secret-screened opaque domain payload."""

    return _canonical_payload(value, field)
