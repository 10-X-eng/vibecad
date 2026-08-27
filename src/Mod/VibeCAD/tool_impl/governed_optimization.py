# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic, bounded optimization contracts without document authority."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson


OPTIMIZATION_SCHEMA_VERSION = 1
MAX_VARIABLES = 32
MAX_CANDIDATES = 4096
MAX_DISCOVERABLE_OPTIMIZATION_RUNS = 4096
MAX_VALUES_PER_VARIABLE = 256
VARIABLE_KINDS = frozenset({"continuous", "integer", "discrete"})
OBJECTIVE_DIRECTIONS = frozenset({"minimize", "maximize"})
CONSTRAINT_OPERATORS = frozenset({"<=", ">=", "=="})
EVALUATION_STATES = frozenset(
    {"pending", "running", "succeeded", "failed", "cancelled", "interrupted"}
)
CURRENTNESS_STATES = frozenset({"current", "stale", "indeterminate"})
TREATMENTS = frozenset({"exclude", "rank_last"})


class OptimizationError(RuntimeError):
    """Optimization identity, lifecycle, budget, or publication policy failed."""


def _text(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise AnalysisContractError(f"{field} must be non-empty.")
    return clean


def _digest(value: Any, field: str) -> str:
    clean = _text(value, field).lower()
    if len(clean) != 64 or any(character not in "0123456789abcdef" for character in clean):
        raise AnalysisContractError(f"{field} must be a lowercase SHA-256 digest.")
    return clean


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AnalysisContractError(f"{field} must be an exact decimal value.") from exc
    if not result.is_finite():
        raise AnalysisContractError(f"{field} must be finite.")
    return result.normalize()


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class DesignVariable:
    name: str
    kind: str
    unit: str
    mutation_owner: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("name", "kind", "unit", "mutation_owner"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if self.kind not in VARIABLE_KINDS:
            raise AnalysisContractError("Unknown design-variable kind.")
        raw_values = tuple(self.values)
        if not raw_values or len(raw_values) > MAX_VALUES_PER_VARIABLE:
            raise AnalysisContractError("Design-variable values exceed their bounded size.")
        if self.kind == "discrete":
            values = tuple(_text(item, "value") for item in raw_values)
        else:
            decimals = tuple(_decimal(item, "value") for item in raw_values)
            if self.kind == "integer" and any(item != item.to_integral_value() for item in decimals):
                raise AnalysisContractError("Integer variables require integral values.")
            values = tuple(_decimal_text(item) for item in decimals)
        if len(values) != len(set(values)):
            raise AnalysisContractError("Design-variable values must be unique after normalization.")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class Objective:
    metric: str
    direction: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _text(self.metric, "metric"))
        object.__setattr__(self, "direction", _text(self.direction, "direction"))
        if self.direction not in OBJECTIVE_DIRECTIONS:
            raise AnalysisContractError("Unknown objective direction.")


@dataclass(frozen=True, slots=True)
class MetricConstraint:
    metric: str
    operator: str
    threshold: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _text(self.metric, "metric"))
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        if self.operator not in CONSTRAINT_OPERATORS:
            raise AnalysisContractError("Unknown constraint operator.")
        object.__setattr__(self, "threshold", _decimal_text(_decimal(self.threshold, "threshold")))


@dataclass(frozen=True, slots=True)
class OptimizationBudget:
    max_candidates: int
    max_workflow_runs: int
    max_wall_seconds: int
    max_cost_units: int
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        limits = {
            "max_candidates": MAX_CANDIDATES,
            "max_workflow_runs": MAX_CANDIDATES,
            "max_wall_seconds": 31_536_000,
            "max_cost_units": 1_000_000_000,
            "max_concurrency": 256,
        }
        for field, limit in limits.items():
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= limit:
                raise AnalysisContractError(f"{field} is outside its bounded range.")
        if self.max_workflow_runs > self.max_candidates:
            raise AnalysisContractError("Workflow-run budget cannot exceed candidate budget.")


@dataclass(frozen=True, slots=True)
class OptimizationDefinition:
    optimization_id: str
    source_document_uid: str
    source_revision: str
    source_sha256: str
    workflow_definition_sha256: str
    variables: tuple[DesignVariable, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[MetricConstraint, ...]
    budget: OptimizationBudget
    seed: int = 0
    algorithm: str = "enumerate-v1"
    algorithm_version: str = "1"
    failure_treatment: str = "exclude"
    stale_treatment: str = "exclude"
    indeterminate_treatment: str = "exclude"
    publication_policy: str = "human_authorized_publish_once"

    def __post_init__(self) -> None:
        for field in ("optimization_id", "source_document_uid", "source_revision", "algorithm",
                      "algorithm_version", "failure_treatment", "stale_treatment",
                      "indeterminate_treatment", "publication_policy"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "source_sha256", _digest(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "workflow_definition_sha256",
                           _digest(self.workflow_definition_sha256, "workflow_definition_sha256"))
        variables, objectives, constraints = tuple(self.variables), tuple(self.objectives), tuple(self.constraints)
        if not variables or len(variables) > MAX_VARIABLES or len({item.name for item in variables}) != len(variables):
            raise AnalysisContractError("Optimization variables must be non-empty, bounded, and unique.")
        if not objectives or len({item.metric for item in objectives}) != len(objectives):
            raise AnalysisContractError("Optimization objectives must be non-empty and unique.")
        if any(not isinstance(item, DesignVariable) for item in variables) or any(not isinstance(item, Objective) for item in objectives) or any(not isinstance(item, MetricConstraint) for item in constraints):
            raise AnalysisContractError("Optimization definition contains an invalid contract value.")
        if not isinstance(self.budget, OptimizationBudget):
            raise AnalysisContractError("budget must be OptimizationBudget.")
        if type(self.seed) is not int:
            raise AnalysisContractError("seed must be an integer.")
        if self.algorithm != "enumerate-v1":
            raise AnalysisContractError("Only the deterministic enumerate-v1 algorithm is supported.")
        for treatment in (self.failure_treatment, self.stale_treatment, self.indeterminate_treatment):
            if treatment not in TREATMENTS:
                raise AnalysisContractError("Unknown candidate treatment.")
        if self.publication_policy != "human_authorized_publish_once":
            raise AnalysisContractError("Optimization cannot acquire direct publication authority.")
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "objectives", objectives)
        object.__setattr__(self, "constraints", constraints)

    def canonical_value(self) -> dict[str, Any]:
        def fields(item: Any) -> dict[str, Any]:
            return {name: getattr(item, name) for name in item.__dataclass_fields__}
        return {
            "schema_version": OPTIMIZATION_SCHEMA_VERSION,
            "optimization_id": self.optimization_id,
            "source_document_uid": self.source_document_uid,
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
            "workflow_definition_sha256": self.workflow_definition_sha256,
            "variables": [fields(item) for item in self.variables],
            "objectives": [fields(item) for item in self.objectives],
            "constraints": [fields(item) for item in self.constraints],
            "budget": fields(self.budget),
            "seed": self.seed,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "failure_treatment": self.failure_treatment,
            "stale_treatment": self.stale_treatment,
            "indeterminate_treatment": self.indeterminate_treatment,
            "publication_policy": self.publication_policy,
        }

    def sha256(self) -> str:
        return CanonicalJson.from_value(self.canonical_value()).sha256()

    def candidates(self) -> tuple[dict[str, Any], ...]:
        count = 1
        for variable in self.variables:
            count *= len(variable.values)
            if count > self.budget.max_candidates:
                raise OptimizationError("Candidate search space exceeds the declared budget.")
        definition_sha = self.sha256()
        result = []
        for combination in itertools.product(*(item.values for item in self.variables)):
            values = {variable.name: value for variable, value in zip(self.variables, combination)}
            owner_values: dict[str, dict[str, str]] = {}
            for variable, value in zip(self.variables, combination):
                owner_values.setdefault(variable.mutation_owner, {})[variable.name] = value
            identity = CanonicalJson.from_value({"definition_sha256": definition_sha, "values": values}).sha256()
            result.append({"candidate_id": f"candidate-{identity[:24]}", "candidate_sha256": identity,
                           "values": values, "mutation_proposal": {"owner_values": owner_values}})
        return tuple(result)


def optimization_definition_from_value(
    value: Mapping[str, Any],
) -> OptimizationDefinition:
    """Reconstruct the exact persisted definition for owner-computed ranking."""

    if not isinstance(value, Mapping):
        raise OptimizationError("Optimization definition must be an object.")
    try:
        return OptimizationDefinition(
            optimization_id=value["optimization_id"],
            source_document_uid=value["source_document_uid"],
            source_revision=value["source_revision"],
            source_sha256=value["source_sha256"],
            workflow_definition_sha256=value["workflow_definition_sha256"],
            variables=tuple(DesignVariable(**item) for item in value["variables"]),
            objectives=tuple(Objective(**item) for item in value["objectives"]),
            constraints=tuple(MetricConstraint(**item) for item in value["constraints"]),
            budget=OptimizationBudget(**value["budget"]),
            seed=value["seed"],
            algorithm=value["algorithm"],
            algorithm_version=value["algorithm_version"],
            failure_treatment=value["failure_treatment"],
            stale_treatment=value["stale_treatment"],
            indeterminate_treatment=value["indeterminate_treatment"],
            publication_policy=value["publication_policy"],
        )
    except (KeyError, TypeError, AnalysisContractError) as exc:
        raise OptimizationError(
            "Persisted optimization definition is incomplete or invalid."
        ) from exc


def rank_candidates(definition: OptimizationDefinition, evaluations: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    candidates = {item["candidate_id"]: item for item in definition.candidates()}
    ranked = []
    for candidate_id, candidate in candidates.items():
        evaluation = dict(evaluations.get(candidate_id) or {})
        state = evaluation.get("state", "pending")
        currentness = evaluation.get("currentness", "indeterminate")
        metrics = evaluation.get("metrics") or {}
        treatment = None
        if state != "succeeded":
            treatment = definition.failure_treatment
        elif currentness == "stale":
            treatment = definition.stale_treatment
        elif currentness != "current":
            treatment = definition.indeterminate_treatment
        missing = any(item.metric not in metrics for item in (*definition.objectives, *definition.constraints))
        if missing and treatment is None:
            treatment = definition.indeterminate_treatment
        if treatment == "exclude":
            continue
        constraint_failures = []
        if treatment is None:
            for constraint in definition.constraints:
                metric, threshold = _decimal(metrics[constraint.metric], constraint.metric), Decimal(constraint.threshold)
                passed = {"<=": metric <= threshold, ">=": metric >= threshold, "==": metric == threshold}[constraint.operator]
                if not passed:
                    constraint_failures.append(constraint.metric)
        objective_key = []
        for objective in definition.objectives:
            value = _decimal(metrics.get(objective.metric, "0"), objective.metric)
            objective_key.append(value if objective.direction == "minimize" else -value)
        key = (1 if treatment == "rank_last" else 0, 1 if constraint_failures else 0,
               tuple(objective_key), candidate_id)
        ranked.append((key, candidate_id, candidate, evaluation, tuple(constraint_failures)))
    ranked.sort(key=lambda item: item[0])
    return tuple({"rank": index, "candidate_id": item[1], "candidate_sha256": item[2]["candidate_sha256"],
                  "values": item[2]["values"], "state": item[3].get("state", "pending"),
                  "currentness": item[3].get("currentness", "indeterminate"),
                  "constraint_failures": item[4]}
                 for index, item in enumerate(ranked, 1))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: Any, field: str) -> str:
    clean = _text(value, field)
    if clean in {".", ".."} or any(mark in clean for mark in "/\\:"):
        raise OptimizationError(f"{field} is not a safe identifier.")
    return clean


class OptimizationRunStore:
    """Atomic records for proposal evaluation and human-authorized selection."""

    def __init__(self, root: str | Path, *, fault_injector=None) -> None:
        self.root = Path(root)
        self.records = self.root / "optimization-runs"
        self.lock_path = self.root / "optimization-writer.lock"
        if fault_injector is not None and not callable(fault_injector):
            raise TypeError("fault_injector must be callable")
        self.fault_injector = fault_injector

    def _path(self, run_id: str) -> Path:
        return self.records / f"{_safe_id(run_id, 'run_id')}.json"

    @contextmanager
    def _writer(self):
        self.root.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise OptimizationError("Another process owns optimization writes.") from exc
        try:
            yield
        finally:
            try:
                stream.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _validate(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = json.loads(CanonicalJson.from_value(record).encoded)
        if value.get("schema_version") != OPTIMIZATION_SCHEMA_VERSION:
            raise OptimizationError("Unsupported optimization schema version.")
        _safe_id(value.get("run_id"), "run_id")
        if len(value.get("definition_sha256", "")) != 64:
            raise OptimizationError("Optimization definition identity is invalid.")
        candidates = value.get("candidates")
        if not isinstance(candidates, dict) or not candidates or len(candidates) > MAX_CANDIDATES:
            raise OptimizationError("Optimization candidates are invalid.")
        for candidate_id, item in candidates.items():
            _safe_id(candidate_id, "candidate_id")
            if item.get("state") not in EVALUATION_STATES or item.get("currentness") not in CURRENTNESS_STATES:
                raise OptimizationError("Candidate evaluation state is invalid.")
        return value

    def _write(self, path: Path, record: Mapping[str, Any]) -> None:
        value = self._validate(record)
        self.records.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        if self.fault_injector: self.fault_injector("before_stage", dict(value))
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
                stream.flush(); os.fsync(stream.fileno())
            if self.fault_injector: self.fault_injector("after_stage", dict(value))
            os.replace(temporary, path)
            if self.fault_injector: self.fault_injector("after_replace", dict(value))
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, definition: OptimizationDefinition, run_id: str) -> dict[str, Any]:
        now = _now()
        candidates = definition.candidates()
        record = {"schema_version": OPTIMIZATION_SCHEMA_VERSION, "run_id": _safe_id(run_id, "run_id"),
                  "definition_sha256": definition.sha256(), "definition": definition.canonical_value(),
                  "source_document_uid": definition.source_document_uid, "source_revision": definition.source_revision,
                  "source_sha256": definition.source_sha256, "workflow_definition_sha256": definition.workflow_definition_sha256,
                  "created_at": now, "updated_at": now, "selection": None, "publication": None,
                  "candidates": {item["candidate_id"]: {**item, "state": "pending", "currentness": "indeterminate",
                                                        "workflow_run_id": None, "workflow_run_ids": [],
                                                        "metrics": {}, "findings": []}
                                 for item in candidates}}
        path = self._path(run_id)
        with self._writer():
            if path.exists(): raise OptimizationError("Optimization run already exists.")
            self._write(path, record)
        return record

    def load(self, run_id: str) -> dict[str, Any]:
        try:
            return self._validate(json.loads(self._path(run_id).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise OptimizationError("Optimization run is missing or corrupt.") from exc

    def list_records(self) -> tuple[dict[str, Any], ...]:
        """Read all bounded run records without selection or publication authority."""

        if not self.records.exists():
            return ()
        paths = tuple(sorted(self.records.glob("*.json"), key=lambda path: path.name))
        if len(paths) > MAX_DISCOVERABLE_OPTIMIZATION_RUNS:
            raise OptimizationError(
                "Optimization discovery exceeds its bounded record limit."
            )
        records = []
        for path in paths:
            try:
                record = self._validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                definition = optimization_definition_from_value(record["definition"])
            except (OSError, ValueError, KeyError, OptimizationError) as exc:
                raise OptimizationError(
                    f"Optimization discovery found an invalid record: {path.name}"
                ) from exc
            if path.stem != record["run_id"]:
                raise OptimizationError(
                    f"Optimization filename does not match its identity: {path.name}"
                )
            if definition.sha256() != record["definition_sha256"]:
                raise OptimizationError(
                    f"Optimization definition identity does not match: {path.name}"
                )
            records.append(record)
        return tuple(records)

    def find_by_document_uid(
        self, document_uid: str
    ) -> tuple[dict[str, Any], ...]:
        """Find exact runs for one document identity without path/label inference."""

        identity = str(document_uid or "").strip()
        if not identity:
            raise OptimizationError("document_uid must be non-empty.")
        return tuple(
            record
            for record in self.list_records()
            if record["source_document_uid"] == identity
        )

    def _update(self, run_id: str, mutate) -> dict[str, Any]:
        with self._writer():
            record = self.load(run_id); mutate(record); record["updated_at"] = _now()
            self._write(self._path(run_id), record); return self._validate(record)

    def start_candidate(self, run_id: str, candidate_id: str, *, workflow_run_id: str) -> dict[str, Any]:
        def mutate(record):
            item = record["candidates"].get(candidate_id)
            if not item or item["state"] not in {"pending", "failed", "interrupted"}:
                raise OptimizationError("Candidate cannot start from its current state.")
            started = sum(len(candidate["workflow_run_ids"]) for candidate in record["candidates"].values())
            if started >= record["definition"]["budget"]["max_workflow_runs"]:
                raise OptimizationError("Workflow-run budget is exhausted.")
            workflow_id = _safe_id(workflow_run_id, "workflow_run_id")
            if workflow_id in item["workflow_run_ids"]:
                raise OptimizationError("Workflow-run identity cannot be reused.")
            item["workflow_run_ids"].append(workflow_id)
            item.update(state="running", currentness="indeterminate", workflow_run_id=workflow_id)
        return self._update(run_id, mutate)

    def finish_candidate(self, run_id: str, candidate_id: str, *, state: str, currentness: str,
                         metrics: Mapping[str, Any] | None = None, findings: tuple[Mapping[str, Any], ...] = ()) -> dict[str, Any]:
        if state not in EVALUATION_STATES - {"pending", "running"} or currentness not in CURRENTNESS_STATES:
            raise OptimizationError("Candidate terminal outcome is invalid.")
        clean_metrics = {str(key): _decimal_text(_decimal(value, str(key))) for key, value in (metrics or {}).items()}
        clean_findings = CanonicalJson.from_value(findings).to_value()
        def mutate(record):
            item = record["candidates"].get(candidate_id)
            if not item or item["state"] != "running":
                raise OptimizationError("Candidate is not running.")
            item.update(state=state, currentness=currentness, metrics=clean_metrics, findings=clean_findings)
        return self._update(run_id, mutate)

    def recover(self, run_id: str) -> dict[str, Any]:
        def mutate(record):
            for item in record["candidates"].values():
                if item["state"] == "running": item.update(state="interrupted", currentness="indeterminate")
        return self._update(run_id, mutate)

    def ranking(self, definition: OptimizationDefinition, run_id: str) -> tuple[dict[str, Any], ...]:
        record = self.load(run_id)
        if record["definition_sha256"] != definition.sha256():
            raise OptimizationError("Optimization definition changed after run creation.")
        return rank_candidates(definition, record["candidates"])

    def authorize_selection(self, definition: OptimizationDefinition, run_id: str, *, candidate_id: str,
                            human_authorization_id: str, observed_source_revision: str,
                            observed_source_sha256: str) -> dict[str, Any]:
        ranking = self.ranking(definition, run_id)
        eligible = {item["candidate_id"] for item in ranking if not item["constraint_failures"] and item["state"] == "succeeded" and item["currentness"] == "current"}
        def mutate(record):
            if candidate_id not in eligible:
                raise OptimizationError("Selected candidate is not a current, feasible successful evaluation.")
            if _text(observed_source_revision, "observed_source_revision") != record["source_revision"] or _digest(observed_source_sha256, "observed_source_sha256") != record["source_sha256"]:
                raise OptimizationError("Source design changed before selection authorization.")
            identity = {"candidate_id": candidate_id,
                        "candidate_sha256": record["candidates"][candidate_id]["candidate_sha256"],
                        "human_authorization_id": _safe_id(human_authorization_id, "human_authorization_id")}
            if record["selection"]:
                if any(record["selection"].get(key) != value for key, value in identity.items()):
                    raise OptimizationError("Optimization selection is already authorized.")
                return
            record["selection"] = {**identity, "authorized_at": _now()}
        return self._update(run_id, mutate)

    def publish_once(self, run_id: str, *, candidate_id: str, human_authorization_id: str,
                     publication_receipt_id: str) -> dict[str, Any]:
        def mutate(record):
            selection = record.get("selection")
            if not selection or selection["candidate_id"] != candidate_id or selection["human_authorization_id"] != human_authorization_id:
                raise OptimizationError("Publication does not match the authorized selection.")
            intent = {"candidate_id": candidate_id, "candidate_sha256": selection["candidate_sha256"],
                      "source_document_uid": record["source_document_uid"], "source_revision": record["source_revision"],
                      "human_authorization_id": human_authorization_id,
                      "publication_receipt_id": _safe_id(publication_receipt_id, "publication_receipt_id")}
            if record["publication"] and record["publication"] != intent:
                raise OptimizationError("Optimization selection cannot publish twice.")
            record["publication"] = intent
        return self._update(run_id, mutate)
