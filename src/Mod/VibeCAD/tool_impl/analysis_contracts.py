# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure, domain-neutral contracts for the VibeCAD Analysis Runtime migration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping
import uuid


ANALYSIS_SCHEMA_VERSION = 1
_SHA256_LENGTH = 64


class AnalysisContractError(ValueError):
    """A host Analysis contract contains invalid or non-serializable state."""


def _text(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise AnalysisContractError(f"{field} must be non-empty.")
    return clean


def _sha256(value: Any, field: str) -> str:
    clean = _text(value, field).lower()
    if len(clean) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in clean
    ):
        raise AnalysisContractError(
            f"{field} must be a lowercase SHA-256 hex digest."
        )
    return clean


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AnalysisContractError(
                "Analysis contract JSON cannot contain NaN or infinity."
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AnalysisContractError(
                    "Analysis contract JSON object keys must be strings."
                )
            result[key] = _json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise AnalysisContractError(
        "Analysis contracts may contain only JSON primitives, arrays, and objects."
    )


@dataclass(frozen=True, slots=True)
class CanonicalJson:
    """Immutable canonical JSON used for opaque host/domain contract payloads."""

    encoded: str

    @classmethod
    def from_value(cls, value: Any) -> "CanonicalJson":
        normalized = _json_value(value)
        return cls(
            json.dumps(
                normalized,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )

    def to_value(self) -> Any:
        return json.loads(self.encoded)

    def sha256(self) -> str:
        return hashlib.sha256(self.encoded.encode("utf-8")).hexdigest()


def json_sha256(value: Any) -> str:
    return CanonicalJson.from_value(value).sha256()


def environment_sha256(environment: Mapping[str, str]) -> str:
    """Fingerprint exact environment values without persisting the values."""

    if not isinstance(environment, Mapping):
        raise AnalysisContractError("environment must be a mapping.")
    pairs = [
        [str(key), str(value)]
        for key, value in sorted(environment.items())
    ]
    return json_sha256(pairs)


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    program: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "program", _text(self.program, "program"))
        object.__setattr__(
            self,
            "arguments",
            tuple(str(value) for value in self.arguments),
        )

    def as_tuple(self) -> tuple[str, tuple[str, ...]]:
        return self.program, self.arguments


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    key: str
    kind: str
    canonical_digest: str = ""
    stable_reference: str = ""
    human_summary: str = ""
    required_for_current_attachment: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "dependency key"))
        object.__setattr__(self, "kind", _text(self.kind, "dependency kind"))
        digest = str(self.canonical_digest or "").strip().lower()
        reference = str(self.stable_reference or "").strip()
        if not digest and not reference:
            raise AnalysisContractError(
                "A dependency record needs a canonical digest or stable reference."
            )
        if digest:
            digest = _sha256(digest, "canonical_digest")
        object.__setattr__(self, "canonical_digest", digest)
        object.__setattr__(self, "stable_reference", reference)
        object.__setattr__(
            self,
            "human_summary",
            str(self.human_summary or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    records: tuple[DependencyRecord, ...]

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if any(not isinstance(record, DependencyRecord) for record in records):
            raise AnalysisContractError(
                "dependency_snapshot must contain DependencyRecord values."
            )
        keys = tuple(record.key for record in records)
        if len(keys) != len(set(keys)):
            raise AnalysisContractError("dependency_snapshot keys must be unique.")
        object.__setattr__(self, "records", records)

    def by_key(self, key: str) -> DependencyRecord:
        clean = str(key or "").strip()
        for record in self.records:
            if record.key == clean:
                return record
        raise KeyError(clean)


@dataclass(frozen=True, slots=True)
class PreparedInputManifest:
    """Compatibility identity for sealed inputs before manifest extraction."""

    storage_reference: str
    sha256: str
    file_count: int
    manifest_version: str = "fem-compat-v1"
    digest_algorithm: str = "vibecad-fem-directory-sha256-v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "storage_reference",
            _text(self.storage_reference, "storage_reference"),
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        if type(self.file_count) is not int or self.file_count < 1:
            raise AnalysisContractError("file_count must be a positive integer.")
        object.__setattr__(
            self,
            "manifest_version",
            _text(self.manifest_version, "manifest_version"),
        )
        object.__setattr__(
            self,
            "digest_algorithm",
            _text(self.digest_algorithm, "digest_algorithm"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    """Provider-neutral launch identity; environment values remain ephemeral."""

    provider_id: str
    commands: tuple[AnalysisCommand, ...]
    timeout_seconds: int
    environment_keys: tuple[str, ...] = ()
    environment_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _text(self.provider_id, "provider_id"),
        )
        commands = tuple(self.commands)
        if any(not isinstance(command, AnalysisCommand) for command in commands):
            raise AnalysisContractError(
                "commands must contain AnalysisCommand values."
            )
        object.__setattr__(self, "commands", commands)
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise AnalysisContractError(
                "timeout_seconds must be a positive integer."
            )
        keys = tuple(sorted(str(value) for value in self.environment_keys))
        if len(keys) != len(set(keys)):
            raise AnalysisContractError("environment_keys must be unique.")
        object.__setattr__(self, "environment_keys", keys)
        digest = str(self.environment_sha256 or "").strip().lower()
        if digest:
            digest = _sha256(digest, "environment_sha256")
        object.__setattr__(self, "environment_sha256", digest)

    def command_tuples(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(command.as_tuple() for command in self.commands)


@dataclass(frozen=True, slots=True)
class PreparedAnalysis:
    """Immutable host-facing identity for one prepared computation."""

    analysis_id: str
    schema_version: int
    domain: str
    adapter_id: str
    adapter_version: str
    created_at: str
    source_document_uid: str
    source_summary: CanonicalJson
    dependency_snapshot: DependencySnapshot
    input_manifest: PreparedInputManifest
    execution_spec: ExecutionSpec
    expected_outputs: tuple[str, ...]
    publication_descriptor: CanonicalJson
    provenance: CanonicalJson

    def __post_init__(self) -> None:
        try:
            canonical_id = str(uuid.UUID(str(self.analysis_id)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise AnalysisContractError("analysis_id must be a UUID.") from exc
        object.__setattr__(self, "analysis_id", canonical_id)
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise AnalysisContractError(
                "schema_version must be a positive integer."
            )
        for field_name in (
            "domain",
            "adapter_id",
            "adapter_version",
            "created_at",
            "source_document_uid",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.source_summary, CanonicalJson):
            raise AnalysisContractError("source_summary must be CanonicalJson.")
        if not isinstance(self.dependency_snapshot, DependencySnapshot):
            raise AnalysisContractError(
                "dependency_snapshot must be DependencySnapshot."
            )
        if not isinstance(self.input_manifest, PreparedInputManifest):
            raise AnalysisContractError(
                "input_manifest must be PreparedInputManifest."
            )
        if not isinstance(self.execution_spec, ExecutionSpec):
            raise AnalysisContractError("execution_spec must be ExecutionSpec.")
        outputs = tuple(
            _text(value, "expected output") for value in self.expected_outputs
        )
        object.__setattr__(self, "expected_outputs", outputs)
        if not isinstance(self.publication_descriptor, CanonicalJson):
            raise AnalysisContractError(
                "publication_descriptor must be CanonicalJson."
            )
        if not isinstance(self.provenance, CanonicalJson):
            raise AnalysisContractError("provenance must be CanonicalJson.")

    @classmethod
    def create(
        cls,
        *,
        domain: str,
        adapter_id: str,
        adapter_version: str,
        source_document_uid: str,
        source_summary: Mapping[str, Any],
        dependency_snapshot: DependencySnapshot,
        input_manifest: PreparedInputManifest,
        execution_spec: ExecutionSpec,
        expected_outputs: tuple[str, ...],
        publication_descriptor: Mapping[str, Any],
        provenance: Mapping[str, Any],
        analysis_id: str | None = None,
        created_at: str | None = None,
    ) -> "PreparedAnalysis":
        timestamp = created_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        return cls(
            analysis_id=analysis_id or str(uuid.uuid4()),
            schema_version=ANALYSIS_SCHEMA_VERSION,
            domain=domain,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            created_at=timestamp,
            source_document_uid=source_document_uid,
            source_summary=CanonicalJson.from_value(source_summary),
            dependency_snapshot=dependency_snapshot,
            input_manifest=input_manifest,
            execution_spec=execution_spec,
            expected_outputs=expected_outputs,
            publication_descriptor=CanonicalJson.from_value(
                publication_descriptor
            ),
            provenance=CanonicalJson.from_value(provenance),
        )


@dataclass(frozen=True, slots=True)
class CurrentnessReport:
    current: bool
    changed_dependency_keys: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        keys = tuple(
            str(value).strip()
            for value in self.changed_dependency_keys
            if str(value).strip()
        )
        object.__setattr__(self, "changed_dependency_keys", keys)
        object.__setattr__(self, "message", str(self.message or "").strip())
