# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, deterministic workflow DAG contracts for durable Analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Mapping

from .analysis_contracts import AnalysisContractError, CanonicalJson


WORKFLOW_SCHEMA_VERSION = 1
MAX_WORKFLOW_NODES = 128
MAX_WORKFLOW_EDGES = 512
MAX_NODE_FAN_OUT = 32
NODE_STATES = frozenset({"pending", "running", "cancel_requested", "succeeded", "failed", "cancelled", "skipped", "interrupted"})
NODE_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "skipped", "interrupted"})
FAILURE_POLICIES = frozenset({"fail_workflow", "skip_downstream", "continue"})
CANCELLATION_POLICIES = frozenset({"cancel_pending", "finish_running"})
PUBLICATION_POLICIES = frozenset({"not_required", "require_published", "publish_once"})


def _text(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise AnalysisContractError(f"{field} must be non-empty.")
    return clean


@dataclass(frozen=True, slots=True)
class WorkflowRequirement:
    output: str
    execution_states: tuple[str, ...] = ("succeeded",)
    currentness_states: tuple[str, ...] = ("current",)
    publication_states: tuple[str, ...] = ("published", "not_required")

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _text(self.output, "output"))
        for name in ("execution_states", "currentness_states", "publication_states"):
            values = tuple(_text(item, name) for item in getattr(self, name))
            if not values or len(values) != len(set(values)):
                raise AnalysisContractError(f"{name} must be non-empty and unique.")
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    node_id: str
    domain: str
    adapter_id: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    requirements: tuple[WorkflowRequirement, ...] = ()
    condition: CanonicalJson = CanonicalJson("{}")
    failure_policy: str = "fail_workflow"
    cancellation_policy: str = "cancel_pending"
    retry_limit: int = 0
    retention_policy: str = "workflow"
    publication_policy: str = "not_required"
    resource_class: str = "local_cpu"
    concurrency_group: str = "default"
    max_fan_out: int = 1

    def __post_init__(self) -> None:
        for name in ("node_id", "domain", "adapter_id", "failure_policy",
                     "cancellation_policy", "retention_policy", "publication_policy",
                     "resource_class", "concurrency_group"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("inputs", "outputs"):
            values = tuple(_text(item, name) for item in getattr(self, name))
            if len(values) != len(set(values)):
                raise AnalysisContractError(f"{name} must be unique.")
            object.__setattr__(self, name, values)
        requirements = tuple(self.requirements)
        if any(not isinstance(item, WorkflowRequirement) for item in requirements):
            raise AnalysisContractError("requirements contains an invalid value.")
        object.__setattr__(self, "requirements", requirements)
        if not isinstance(self.condition, CanonicalJson):
            raise AnalysisContractError("condition must be deterministic CanonicalJson data.")
        if self.failure_policy not in FAILURE_POLICIES:
            raise AnalysisContractError("Unknown workflow failure policy.")
        if self.cancellation_policy not in CANCELLATION_POLICIES:
            raise AnalysisContractError("Unknown workflow cancellation policy.")
        if self.publication_policy not in PUBLICATION_POLICIES:
            raise AnalysisContractError("Unknown workflow publication policy.")
        if type(self.retry_limit) is not int or not 0 <= self.retry_limit <= 16:
            raise AnalysisContractError("retry_limit is outside the bounded range.")
        if type(self.max_fan_out) is not int or not 1 <= self.max_fan_out <= MAX_NODE_FAN_OUT:
            raise AnalysisContractError("max_fan_out is outside the bounded range.")


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    source_node: str
    target_node: str
    output: str
    input_name: str

    def __post_init__(self) -> None:
        for name in ("source_node", "target_node", "output", "input_name"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.source_node == self.target_node:
            raise AnalysisContractError("A workflow node cannot depend on itself.")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _text(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "version", _text(self.version, "version"))
        nodes, edges = tuple(self.nodes), tuple(self.edges)
        if not nodes or len(nodes) > MAX_WORKFLOW_NODES or len(edges) > MAX_WORKFLOW_EDGES:
            raise AnalysisContractError("Workflow graph exceeds its bounded size.")
        if any(not isinstance(item, WorkflowNode) for item in nodes) or any(not isinstance(item, WorkflowEdge) for item in edges):
            raise AnalysisContractError("Workflow graph contains an invalid value.")
        node_map = {item.node_id: item for item in nodes}
        if len(node_map) != len(nodes):
            raise AnalysisContractError("Workflow node IDs must be unique.")
        edge_keys = {(e.source_node, e.target_node, e.output, e.input_name) for e in edges}
        if len(edge_keys) != len(edges):
            raise AnalysisContractError("Workflow edges must be unique.")
        fan_out = {name: 0 for name in node_map}
        for edge in edges:
            if edge.source_node not in node_map or edge.target_node not in node_map:
                raise AnalysisContractError("Workflow edge references a missing node.")
            if edge.output not in node_map[edge.source_node].outputs:
                raise AnalysisContractError("Workflow edge references an undeclared output.")
            if edge.input_name not in node_map[edge.target_node].inputs:
                raise AnalysisContractError("Workflow edge references an undeclared input.")
            fan_out[edge.source_node] += 1
        if any(fan_out[n.node_id] > n.max_fan_out for n in nodes):
            raise AnalysisContractError("Workflow node fan-out exceeds its declared bound.")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)
        self.topological_order()

    def topological_order(self) -> tuple[str, ...]:
        incoming = {node.node_id: 0 for node in self.nodes}
        outgoing = {node.node_id: [] for node in self.nodes}
        for edge in self.edges:
            incoming[edge.target_node] += 1
            outgoing[edge.source_node].append(edge.target_node)
        ready = sorted(name for name, count in incoming.items() if count == 0)
        result = []
        while ready:
            name = ready.pop(0)
            result.append(name)
            for target in sorted(outgoing[name]):
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
                    ready.sort()
        if len(result) != len(self.nodes):
            raise AnalysisContractError("Workflow graph contains a cycle.")
        return tuple(result)

    def ready_nodes(self, states: Mapping[str, str]) -> tuple[str, ...]:
        complete = {name for name, state in states.items() if state in {"succeeded", "skipped"}}
        predecessors = {node.node_id: set() for node in self.nodes}
        for edge in self.edges:
            predecessors[edge.target_node].add(edge.source_node)
        return tuple(name for name in self.topological_order()
                     if states.get(name, "pending") == "pending" and predecessors[name] <= complete)

    @staticmethod
    def condition_allows(node: WorkflowNode, context: Mapping[str, Any]) -> bool:
        value = node.condition.to_value()
        clauses = value.get("all", []) if isinstance(value, Mapping) else None
        if not isinstance(clauses, list) or len(clauses) > 32:
            raise AnalysisContractError("Workflow condition must be a bounded all-clause.")
        for clause in clauses:
            if not isinstance(clause, Mapping) or set(clause) != {"key", "equals"}:
                raise AnalysisContractError("Workflow condition clause is invalid.")
            if context.get(str(clause["key"])) != clause["equals"]:
                return False
        return True

    def sha256(self) -> str:
        def node(item: WorkflowNode) -> dict[str, Any]:
            value = {name: (getattr(item, name).to_value() if name == "condition" else getattr(item, name))
                     for name in item.__dataclass_fields__}
            value["requirements"] = [
                {name: getattr(requirement, name) for name in requirement.__dataclass_fields__}
                for requirement in item.requirements
            ]
            return value
        value = {"schema_version": WORKFLOW_SCHEMA_VERSION, "workflow_id": self.workflow_id,
                 "version": self.version, "nodes": [node(n) for n in self.nodes],
                 "edges": [{name: getattr(e, name) for name in e.__dataclass_fields__} for e in self.edges]}
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class WorkflowRunError(RuntimeError):
    """A durable workflow run violates identity or lifecycle policy."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: Any, field: str) -> str:
    clean = _text(value, field)
    if clean in {".", ".."} or any(mark in clean for mark in "/\\:"):
        raise WorkflowRunError(f"{field} is not a safe identifier.")
    return clean


class WorkflowRunStore:
    """Atomic durable run records; definitions and child artifacts remain referenced."""

    def __init__(self, root: str | Path, *, fault_injector=None) -> None:
        self.root = Path(root)
        self.records = self.root / "workflow-runs"
        self.lock_path = self.root / "workflow-writer.lock"
        if fault_injector is not None and not callable(fault_injector):
            raise TypeError("fault_injector must be callable")
        self.fault_injector = fault_injector

    def _path(self, run_id: str) -> Path:
        return self.records / f"{_safe_id(run_id, 'run_id')}.json"

    def _fault(self, point: str, record: Mapping[str, Any]) -> None:
        if self.fault_injector:
            self.fault_injector(point, dict(record))

    @contextmanager
    def _writer(self):
        self.root.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise WorkflowRunError("Another process owns workflow writes.") from exc
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
        value = json.loads(json.dumps(dict(record), allow_nan=False))
        if value.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
            raise WorkflowRunError("Unsupported workflow run schema version.")
        _safe_id(value.get("run_id"), "run_id")
        digest = str(value.get("definition_sha256") or "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise WorkflowRunError("definition_sha256 must be SHA-256.")
        nodes = value.get("nodes")
        if not isinstance(nodes, dict) or not nodes or len(nodes) > MAX_WORKFLOW_NODES:
            raise WorkflowRunError("Workflow run nodes are invalid.")
        for node_id, node in nodes.items():
            _safe_id(node_id, "node_id")
            if not isinstance(node, dict) or node.get("state") not in NODE_STATES:
                raise WorkflowRunError("Workflow node state is invalid.")
            attempts = node.get("attempts")
            retry_limit = node.get("retry_limit")
            if type(retry_limit) is not int or not 0 <= retry_limit <= 16:
                raise WorkflowRunError("Workflow node retry policy is invalid.")
            if not isinstance(attempts, list) or len(attempts) > retry_limit + 1:
                raise WorkflowRunError("Workflow node attempts are invalid.")
            if node.get("publication_policy") not in PUBLICATION_POLICIES:
                raise WorkflowRunError("Workflow node publication policy is invalid.")
        events = value.get("events")
        if not isinstance(events, list) or len(events) > 8192:
            raise WorkflowRunError("Workflow event history is invalid.")
        return value

    def _write(self, path: Path, record: Mapping[str, Any]) -> None:
        value = self._validate(record)
        self.records.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        self._fault("before_stage", value)
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            self._fault("after_stage", value)
            self._fault("before_replace", value)
            os.replace(temporary, path)
            self._fault("after_replace", value)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, definition: WorkflowDefinition, run_id: str) -> dict[str, Any]:
        if not isinstance(definition, WorkflowDefinition):
            raise TypeError("definition must be WorkflowDefinition")
        now = _now()
        record = {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "run_id": _safe_id(run_id, "run_id"),
            "workflow_id": definition.workflow_id,
            "workflow_version": definition.version,
            "definition_sha256": definition.sha256(),
            "state": "running",
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "nodes": {node.node_id: {"state": "pending", "attempts": [], "analysis_id": None,
                                      "outcome": None, "publication_receipt_id": None,
                                      "retry_limit": node.retry_limit,
                                      "publication_policy": node.publication_policy}
                      for node in definition.nodes},
            "events": [{"sequence": 1, "at": now, "kind": "run_created"}],
        }
        path = self._path(run_id)
        with self._writer():
            if path.exists():
                raise WorkflowRunError("Workflow run already exists.")
            self._write(path, record)
        return record

    def load(self, run_id: str) -> dict[str, Any]:
        try:
            return self._validate(json.loads(self._path(run_id).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise WorkflowRunError("Workflow run is missing or corrupt.") from exc

    def list_records(self) -> tuple[dict[str, Any], ...]:
        """Read every bounded workflow record without changing recovery state."""

        if not self.records.exists():
            return ()
        paths = tuple(sorted(self.records.glob("*.json"), key=lambda path: path.name))
        if len(paths) > 4096:
            raise WorkflowRunError("Workflow discovery exceeds its bounded record limit.")
        records = []
        for path in paths:
            try:
                record = self._validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, WorkflowRunError) as exc:
                raise WorkflowRunError(
                    f"Workflow discovery found an invalid record: {path.name}"
                ) from exc
            if path.stem != record["run_id"]:
                raise WorkflowRunError(
                    f"Workflow filename does not match its identity: {path.name}"
                )
            records.append(record)
        return tuple(records)

    def find_by_analysis_ids(
        self, analysis_ids: tuple[str, ...] | list[str]
    ) -> tuple[dict[str, Any], ...]:
        """Find runs whose node history explicitly references an Analysis identity."""

        if not isinstance(analysis_ids, (tuple, list)) or len(analysis_ids) > 4096:
            raise WorkflowRunError("analysis_ids exceed their bounded discovery limit.")
        identities = frozenset(_safe_id(value, "analysis_id") for value in analysis_ids)
        if not identities:
            return ()
        matches = []
        for record in self.list_records():
            references = set()
            for node in record["nodes"].values():
                if node.get("analysis_id"):
                    references.add(_safe_id(node["analysis_id"], "analysis_id"))
                for attempt in node.get("attempts", ()):
                    if not isinstance(attempt, Mapping):
                        raise WorkflowRunError(
                            "Workflow discovery found an invalid node attempt."
                        )
                    if attempt.get("analysis_id"):
                        references.add(
                            _safe_id(attempt["analysis_id"], "analysis_id")
                        )
            if identities & references:
                matches.append(record)
        return tuple(matches)

    def _update(self, run_id: str, mutator) -> dict[str, Any]:
        with self._writer():
            record = self.load(run_id)
            mutator(record)
            states = {node["state"] for node in record["nodes"].values()}
            if states <= NODE_TERMINAL_STATES:
                if "failed" in states or "interrupted" in states:
                    record["state"] = "failed"
                elif "cancelled" in states:
                    record["state"] = "cancelled"
                else:
                    record["state"] = "succeeded"
            record["updated_at"] = _now()
            record["events"].append({"sequence": len(record["events"]) + 1,
                                     "at": record["updated_at"], "kind": "state_updated"})
            self._write(self._path(run_id), record)
            return self._validate(record)

    def start_node(self, run_id: str, node_id: str, *, analysis_id: str) -> dict[str, Any]:
        def mutate(record):
            node = record["nodes"].get(node_id)
            if not node or node["state"] not in {"pending", "failed", "interrupted"}:
                raise WorkflowRunError("Workflow node cannot start from its current state.")
            if record["cancel_requested"]:
                raise WorkflowRunError("Cancelled workflow cannot launch downstream work.")
            attempt = len(node["attempts"]) + 1
            if attempt > node["retry_limit"] + 1:
                raise WorkflowRunError("Workflow node retry limit is exhausted.")
            node.update(state="running", analysis_id=_safe_id(analysis_id, "analysis_id"))
            node["attempts"].append({"attempt": attempt, "analysis_id": node["analysis_id"], "started_at": _now()})
        return self._update(run_id, mutate)

    def finish_node(self, run_id: str, node_id: str, *, state: str,
                    outcome: Mapping[str, Any], publication_receipt_id: str = "") -> dict[str, Any]:
        if state not in NODE_TERMINAL_STATES:
            raise WorkflowRunError("Node finish state must be terminal.")
        def mutate(record):
            node = record["nodes"].get(node_id)
            if not node:
                raise WorkflowRunError("Unknown workflow node.")
            if node["state"] in {"cancelled", "skipped"}:
                return  # Late provider completion is evidence, not authority to reopen.
            if node["state"] not in {"running", "cancel_requested"}:
                raise WorkflowRunError("Workflow node is not running.")
            node["state"] = "cancelled" if node["state"] == "cancel_requested" else state
            node["outcome"] = json.loads(CanonicalJson.from_value(outcome).encoded)
            receipt = str(publication_receipt_id or "").strip()
            if state == "succeeded" and node["publication_policy"] == "publish_once" and not receipt:
                raise WorkflowRunError("Successful publish-once node requires its receipt.")
            if node["publication_receipt_id"] and receipt != node["publication_receipt_id"]:
                raise WorkflowRunError("Workflow node cannot publish twice.")
            node["publication_receipt_id"] = receipt or node["publication_receipt_id"]
        return self._update(run_id, mutate)

    def cancel(self, run_id: str) -> dict[str, Any]:
        def mutate(record):
            record["cancel_requested"] = True
            record["state"] = "cancelled"
            for node in record["nodes"].values():
                if node["state"] == "pending":
                    node["state"] = "cancelled"
                elif node["state"] == "running":
                    node["state"] = "cancel_requested"
        return self._update(run_id, mutate)

    def skip_node(self, run_id: str, node_id: str, *, reason: str) -> dict[str, Any]:
        def mutate(record):
            node = record["nodes"].get(node_id)
            if not node or node["state"] != "pending":
                raise WorkflowRunError("Only a pending workflow node can be skipped.")
            node["state"] = "skipped"
            node["outcome"] = {"skip_reason": _text(reason, "reason")}
        return self._update(run_id, mutate)

    def recover(self, run_id: str) -> dict[str, Any]:
        def mutate(record):
            for node in record["nodes"].values():
                if node["state"] == "running":
                    node["state"] = "interrupted"
                elif node["state"] == "cancel_requested":
                    node["state"] = "cancelled"
        return self._update(run_id, mutate)

    def eligible_ready_nodes(self, definition: WorkflowDefinition, run_id: str) -> tuple[str, ...]:
        record = self.load(run_id)
        if record["definition_sha256"] != definition.sha256() or record["cancel_requested"]:
            return ()
        states = {name: node["state"] for name, node in record["nodes"].items()}
        candidates = definition.ready_nodes(states)
        incoming = {node.node_id: [] for node in definition.nodes}
        for edge in definition.edges:
            incoming[edge.target_node].append(edge.source_node)
        node_map = {node.node_id: node for node in definition.nodes}
        eligible = []
        for node_id in candidates:
            outcomes = [record["nodes"][source].get("outcome") or {} for source in incoming[node_id]]
            requirements = node_map[node_id].requirements
            if all(any(
                outcome.get("output") == requirement.output
                and outcome.get("execution_state") in requirement.execution_states
                and outcome.get("currentness") in requirement.currentness_states
                and outcome.get("publication_state") in requirement.publication_states
                for outcome in outcomes
            ) for requirement in requirements):
                eligible.append(node_id)
        return tuple(eligible)

    def summary(self, run_id: str) -> dict[str, Any]:
        record = self.load(run_id)
        return {"run_id": record["run_id"], "workflow_id": record["workflow_id"],
                "definition_sha256": record["definition_sha256"], "state": record["state"],
                "nodes": {name: {"state": node["state"], "analysis_id": node["analysis_id"],
                                  "publication_receipt_id": node["publication_receipt_id"]}
                          for name, node in sorted(record["nodes"].items())}}
