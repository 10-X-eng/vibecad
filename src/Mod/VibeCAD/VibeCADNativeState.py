# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host-owned structural revision and Native operation memory.

The store is independent of FreeCAD and provider code. GUI/document observers
report structural events; future mutation runners freeze and verify call
tickets. This module never executes tools or activates UI state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import secrets
import threading
from typing import Any, Mapping


DEFAULT_RECEIPT_LIMIT = 32
MAX_VERIFIED_RESULT_JSON_BYTES = 32 * 1024
NATIVE_REVISION_CONFLICT = "NATIVE_REVISION_CONFLICT"
NATIVE_AUTHORITY_CONFLICT = "NATIVE_AUTHORITY_CONFLICT"
NATIVE_PREVIEW_MISSING = "NATIVE_PREVIEW_MISSING"
NATIVE_PREVIEW_CONSUMED = "NATIVE_PREVIEW_CONSUMED"
NATIVE_PREVIEW_FAMILIES = frozenset(
    {
        "model.extrude",
        "model.revolve",
        "model.helix",
        "model.loft",
        "model.sweep",
        "model.dressup",
        "model.boolean",
        "model.transform",
        "model.hole",
        "model.history",
    }
)
NATIVE_STATE_SCHEMA = "vibecad-native-state-v1"

PRESENTATION_PROPERTY_NAMES = frozenset(
    {
        "DisplayMode",
        "LineColor",
        "LineWidth",
        "PointColor",
        "PointSize",
        "SelectionStyle",
        "ShapeAppearance",
        "ShapeColor",
        "Transparency",
        "Visibility",
        "VisibilityAtEnd",
    }
)
NON_STRUCTURAL_INTERNAL_PROPERTIES = frozenset(
    {
        "_LinkTouched",
        "RecomputePending",
        "Touched",
        "_GroupTouched",
        "PrecomputedDimensionFlags",
        "PrecomputedDimensionScalars",
        "PrecomputedDimensionVectors",
        "VibeCADVibeScriptEditorDraft",
    }
)


class NativeStateError(RuntimeError):
    """Native state or receipt data violates the host contract."""


class NativeRevisionConflict(NativeStateError):
    """The document changed after the call ticket was frozen."""

    def __init__(self, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            "The document changed after this operation was prepared. "
            "Read its current state and retry."
        )
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": NATIVE_REVISION_CONFLICT,
            "message": str(self),
            "current_revision": self.current_revision,
            "repair": {"retry_from_current_state": True},
        }


class NativeAuthorityConflict(NativeStateError):
    """Native changes prevent a silent return to VibeScript authority."""

    def __init__(self, baseline_revision: int, current_revision: int) -> None:
        super().__init__(
            "This document changed after Native authority began. Discard the "
            "Native epoch or create a new VibeScript source before returning "
            "to VibeScript authority."
        )
        self.baseline_revision = int(baseline_revision)
        self.current_revision = int(current_revision)

    def failure(self) -> dict[str, Any]:
        return {
            "error_code": NATIVE_AUTHORITY_CONFLICT,
            "message": str(self),
            "current_revision": self.current_revision,
            "repair": {"requires_explicit_authority_reset": True},
        }


def is_structural_property(property_name: str) -> bool:
    name = str(property_name or "").strip()
    return bool(
        name
        and name not in PRESENTATION_PROPERTY_NAMES
        and name not in NON_STRUCTURAL_INTERNAL_PROPERTIES
    )


def _required_text(value: Any, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise NativeStateError(f"Native {label} must not be empty.")
    return clean


@dataclass(frozen=True, slots=True, order=True)
class NativeObjectIdentity:
    document_uid: str
    object_name: str
    type_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "document_uid",
            _required_text(self.document_uid, "document UID"),
        )
        object.__setattr__(
            self,
            "object_name",
            _required_text(self.object_name, "object name"),
        )
        object.__setattr__(
            self,
            "type_id",
            _required_text(self.type_id, "object type"),
        )

    def summary(self) -> dict[str, str]:
        return {
            "document_uid": self.document_uid,
            "object_name": self.object_name,
            "type_id": self.type_id,
        }


@dataclass(frozen=True, slots=True)
class NativeCallTicket:
    document_uid: str
    capability_name: str
    expected_revision: int
    idempotency_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class NativeMutationAuthorization:
    ticket: NativeCallTicket
    current_revision: int
    prior_verified_result: dict[str, Any] | None

    @property
    def duplicate(self) -> bool:
        return self.prior_verified_result is not None


@dataclass(frozen=True, slots=True)
class NativeOperationReceipt:
    idempotency_token: str
    capability_name: str
    revision_before: int
    revision_after: int
    created: tuple[NativeObjectIdentity, ...]
    changed: tuple[NativeObjectIdentity, ...]
    deleted: tuple[NativeObjectIdentity, ...]
    replaced: tuple[NativeObjectIdentity, ...]
    claim_ceiling: str = "geometry_applied"
    evidence_state: str = "pass"

    def summary(self) -> dict[str, Any]:
        return {
            "capability": self.capability_name,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "claim_ceiling": self.claim_ceiling,
            "evidence_state": self.evidence_state,
            "created": [item.summary() for item in self.created],
            "changed": [item.summary() for item in self.changed],
            "deleted": [item.summary() for item in self.deleted],
            "replaced": [item.summary() for item in self.replaced],
        }


@dataclass(frozen=True, slots=True)
class PreparedNativeMutation:
    ticket: NativeCallTicket
    verified_result_json: str = field(repr=False)
    created: tuple[NativeObjectIdentity, ...]
    changed: tuple[NativeObjectIdentity, ...]
    deleted: tuple[NativeObjectIdentity, ...]
    replaced: tuple[NativeObjectIdentity, ...]


@dataclass(slots=True)
class _DocumentRecord:
    revision: int = 0
    authority_baseline_revision: int | None = None
    receipts: OrderedDict[str, NativeOperationReceipt] = field(
        default_factory=OrderedDict
    )
    verified_results: OrderedDict[str, str] = field(default_factory=OrderedDict)
    authorized_tokens: set[str] = field(default_factory=set)
    mutation_observer_token: str | None = None
    mutation_observer_events: int = 0
    previews: dict[str, dict[str, Any]] = field(default_factory=dict)


def _canonical_verified_result(result: Mapping[str, Any]) -> str:
    if not isinstance(result, Mapping):
        raise NativeStateError("A verified Native result must be an object.")
    try:
        encoded = json.dumps(
            dict(result),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise NativeStateError(f"A verified Native result is not JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_VERIFIED_RESULT_JSON_BYTES:
        raise NativeStateError("A verified Native result exceeds the bounded size.")
    return encoded


def _exact_identities(
    values: tuple[NativeObjectIdentity, ...],
    label: str,
) -> tuple[NativeObjectIdentity, ...]:
    result = tuple(values)
    if any(not isinstance(item, NativeObjectIdentity) for item in result):
        raise NativeStateError(f"Native {label} identities are invalid.")
    if len(result) != len(set(result)):
        raise NativeStateError(f"Native {label} identities contain duplicates.")
    return tuple(sorted(result))


class NativeDocumentStateStore:
    """Thread-safe structural revisions and bounded verified call memory."""

    def __init__(self, *, receipt_limit: int = DEFAULT_RECEIPT_LIMIT) -> None:
        if type(receipt_limit) is not int or receipt_limit < 1:
            raise ValueError("receipt_limit must be a positive integer")
        self._receipt_limit = receipt_limit
        self._records: dict[str, _DocumentRecord] = {}
        self._lock = threading.RLock()

    def ensure_document(self, document_uid: str) -> int:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            return self._records.setdefault(uid, _DocumentRecord()).revision

    def has_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        with self._lock:
            return bool(uid and uid in self._records)

    def close_document(self, document_uid: str) -> None:
        uid = str(document_uid or "").strip()
        with self._lock:
            self._records.pop(uid, None)

    def note_structural_change(self, document_uid: str) -> int:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            if record.mutation_observer_token is not None:
                record.mutation_observer_events += 1
                return record.revision
            record.revision += 1
            return record.revision

    def begin_native_authority(self, document_uid: str) -> dict[str, Any]:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            if record.authority_baseline_revision is None:
                record.authority_baseline_revision = record.revision
                record.receipts.clear()
                record.verified_results.clear()
                record.authorized_tokens.clear()
            return self._authority_summary(uid, record)

    def require_vibescript_return_safe(self, document_uid: str) -> None:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            baseline = record.authority_baseline_revision
            if baseline is None:
                return
            if record.revision != baseline or record.receipts:
                raise NativeAuthorityConflict(baseline, record.revision)

    def end_native_authority(self, document_uid: str) -> dict[str, Any]:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            self.require_vibescript_return_safe(uid)
            record = self._records.setdefault(uid, _DocumentRecord())
            record.authority_baseline_revision = None
            record.receipts.clear()
            record.verified_results.clear()
            record.authorized_tokens.clear()
            return self._authority_summary(uid, record)

    @staticmethod
    def _authority_summary(uid: str, record: _DocumentRecord) -> dict[str, Any]:
        baseline = record.authority_baseline_revision
        return {
            "document_uid": uid,
            "active": baseline is not None,
            "baseline_revision": baseline,
            "current_revision": record.revision,
            "changed": bool(
                baseline is not None
                and (record.revision != baseline or record.receipts)
            ),
        }

    def note_object_property_change(
        self,
        document_uid: str,
        property_name: str,
    ) -> int:
        if not is_structural_property(property_name):
            return self.current_revision(document_uid)
        return self.note_structural_change(document_uid)

    def current_revision(self, document_uid: str) -> int:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            return self._records.setdefault(uid, _DocumentRecord()).revision

    def begin_call(
        self,
        document_uid: str,
        capability_name: str,
    ) -> NativeCallTicket:
        uid = _required_text(document_uid, "document UID")
        capability = _required_text(capability_name, "capability name")
        with self._lock:
            revision = self._records.setdefault(uid, _DocumentRecord()).revision
        return NativeCallTicket(
            document_uid=uid,
            capability_name=capability,
            expected_revision=revision,
            idempotency_token=secrets.token_hex(16),
        )

    def propose_mutation_preview(
        self,
        document_uid: str,
        *,
        capability_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Record one authorized preview-family proposal. Does not mutate CAD."""

        uid = _required_text(document_uid, "document UID")
        capability = _required_text(capability_name, "capability name")
        if capability not in NATIVE_PREVIEW_FAMILIES:
            raise NativeStateError(
                "Native preview is only authorized for "
                + ", ".join(sorted(NATIVE_PREVIEW_FAMILIES))
                + "."
            )
        if not isinstance(arguments, Mapping):
            raise NativeStateError("Native preview arguments must be an object.")
        payload = {
            name: value
            for name, value in arguments.items()
            if name not in {"stage", "preview_id"}
        }
        preview_id = secrets.token_hex(16)
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            record.previews[preview_id] = {
                "preview_id": preview_id,
                "capability_name": capability,
                "expected_revision": record.revision,
                "arguments": dict(payload),
                "consumed": False,
            }
            expected_revision = record.revision
        return {
            "preview_id": preview_id,
            "capability": capability,
            "expected_revision": expected_revision,
            "applied": False,
            "evidence_state": "evidence_waiting",
            "claim_ceiling": "geometry_applied",
        }

    def consume_mutation_preview(
        self,
        document_uid: str,
        preview_id: str,
        *,
        capability_name: str,
    ) -> dict[str, Any]:
        """Return stored arguments if revision still matches. One-shot."""

        uid = _required_text(document_uid, "document UID")
        token = _required_text(preview_id, "preview id")
        capability = _required_text(capability_name, "capability name")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            preview = record.previews.get(token)
            if preview is None:
                raise NativeStateError(NATIVE_PREVIEW_MISSING)
            if preview.get("consumed"):
                raise NativeStateError(NATIVE_PREVIEW_CONSUMED)
            if preview.get("capability_name") != capability:
                raise NativeStateError(NATIVE_PREVIEW_MISSING)
            expected = int(preview["expected_revision"])
            if record.revision != expected:
                raise NativeRevisionConflict(expected, record.revision)
            preview["consumed"] = True
            return dict(preview["arguments"])

    def list_mutation_previews(self, document_uid: str) -> list[dict[str, Any]]:
        """Return unconsumed allowlisted previews. Does not mutate CAD."""

        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            pending: list[dict[str, Any]] = []
            for preview in record.previews.values():
                if preview.get("consumed"):
                    continue
                arguments = dict(preview.get("arguments") or {})
                expected = int(preview["expected_revision"])
                pending.append(
                    {
                        "preview_id": str(preview["preview_id"]),
                        "capability": str(preview["capability_name"]),
                        "operation": str(arguments.get("operation") or ""),
                        "expected_revision": expected,
                        "current_revision": record.revision,
                        "applied": False,
                        "stale": record.revision != expected,
                    }
                )
            return pending

    def authorize_mutation(
        self,
        ticket: NativeCallTicket,
    ) -> NativeMutationAuthorization:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            prior = record.verified_results.get(ticket.idempotency_token)
            if prior is not None:
                return NativeMutationAuthorization(
                    ticket,
                    record.revision,
                    json.loads(prior),
                )
            if record.authority_baseline_revision is None:
                raise NativeStateError("Native mutation authority is not active.")
            if record.revision != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, record.revision)
            record.authorized_tokens.add(ticket.idempotency_token)
            return NativeMutationAuthorization(ticket, record.revision, None)

    def begin_mutation_observation(self, ticket: NativeCallTicket) -> None:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            if ticket.idempotency_token not in record.authorized_tokens:
                raise NativeStateError(
                    "A Native mutation must pass stale preflight before observation."
                )
            if record.mutation_observer_token is not None:
                raise NativeStateError(
                    "A Native document already has an observed mutation in progress."
                )
            record.mutation_observer_token = ticket.idempotency_token
            record.mutation_observer_events = 0

    def commit_mutation_observation(self, ticket: NativeCallTicket) -> int:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            if record.mutation_observer_token != ticket.idempotency_token:
                raise NativeStateError("Native mutation observation ownership changed.")
            if record.mutation_observer_events:
                record.revision += 1
            record.mutation_observer_token = None
            record.mutation_observer_events = 0
            return record.revision

    def cancel_mutation(self, ticket: NativeCallTicket) -> None:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            if record.mutation_observer_token == ticket.idempotency_token:
                record.mutation_observer_token = None
                record.mutation_observer_events = 0
            record.authorized_tokens.discard(ticket.idempotency_token)

    def prepare_mutation_completion(
        self,
        ticket: NativeCallTicket,
        verified_result: Mapping[str, Any],
        *,
        created: tuple[NativeObjectIdentity, ...] = (),
        changed: tuple[NativeObjectIdentity, ...] = (),
        deleted: tuple[NativeObjectIdentity, ...] = (),
        replaced: tuple[NativeObjectIdentity, ...] = (),
    ) -> PreparedNativeMutation:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        prepared = PreparedNativeMutation(
            ticket=ticket,
            verified_result_json=_canonical_verified_result(verified_result),
            created=_exact_identities(created, "created"),
            changed=_exact_identities(changed, "changed"),
            deleted=_exact_identities(deleted, "deleted"),
            replaced=_exact_identities(replaced, "replaced"),
        )
        if any(
            identity.document_uid != ticket.document_uid
            for identities in (
                prepared.created,
                prepared.changed,
                prepared.deleted,
                prepared.replaced,
            )
            for identity in identities
        ):
            raise NativeStateError(
                "Native mutation evidence belongs to another document."
            )
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            if (
                ticket.idempotency_token not in record.authorized_tokens
                and ticket.idempotency_token not in record.receipts
            ):
                raise NativeStateError(
                    "A Native mutation must pass stale preflight before completion."
                )
        return prepared

    def complete_prepared_mutation(
        self,
        prepared: PreparedNativeMutation,
    ) -> NativeOperationReceipt:
        if not isinstance(prepared, PreparedNativeMutation):
            raise TypeError("prepared must be a PreparedNativeMutation")
        ticket = prepared.ticket
        with self._lock:
            record = self._records.setdefault(ticket.document_uid, _DocumentRecord())
            existing = record.receipts.get(ticket.idempotency_token)
            if existing is not None:
                repeated = NativeOperationReceipt(
                    idempotency_token=ticket.idempotency_token,
                    capability_name=ticket.capability_name,
                    revision_before=ticket.expected_revision,
                    revision_after=existing.revision_after,
                    created=prepared.created,
                    changed=prepared.changed,
                    deleted=prepared.deleted,
                    replaced=prepared.replaced,
                )
                if (
                    record.verified_results[ticket.idempotency_token]
                    != prepared.verified_result_json
                    or repeated != existing
                ):
                    raise NativeStateError(
                        "A completed Native token cannot store different evidence."
                    )
                return existing
            if record.mutation_observer_token is not None:
                raise NativeStateError(
                    "Native mutation observation must close before completion."
                )
            if ticket.idempotency_token not in record.authorized_tokens:
                raise NativeStateError(
                    "A Native mutation must pass stale preflight before completion."
                )
            if record.revision < ticket.expected_revision:
                raise NativeStateError("Native document revision moved backwards.")
            receipt = NativeOperationReceipt(
                idempotency_token=ticket.idempotency_token,
                capability_name=ticket.capability_name,
                revision_before=ticket.expected_revision,
                revision_after=record.revision,
                created=prepared.created,
                changed=prepared.changed,
                deleted=prepared.deleted,
                replaced=prepared.replaced,
            )
            record.receipts[ticket.idempotency_token] = receipt
            record.verified_results[ticket.idempotency_token] = (
                prepared.verified_result_json
            )
            record.authorized_tokens.discard(ticket.idempotency_token)
            while len(record.receipts) > self._receipt_limit:
                oldest_token, _oldest = record.receipts.popitem(last=False)
                record.verified_results.pop(oldest_token, None)
            return receipt

    def complete_mutation(
        self,
        ticket: NativeCallTicket,
        verified_result: Mapping[str, Any],
        *,
        created: tuple[NativeObjectIdentity, ...] = (),
        changed: tuple[NativeObjectIdentity, ...] = (),
        deleted: tuple[NativeObjectIdentity, ...] = (),
        replaced: tuple[NativeObjectIdentity, ...] = (),
    ) -> NativeOperationReceipt:
        prepared = self.prepare_mutation_completion(
            ticket,
            verified_result,
            created=created,
            changed=changed,
            deleted=deleted,
            replaced=replaced,
        )
        return self.complete_prepared_mutation(prepared)

    def snapshot(self, document_uid: str) -> dict[str, Any]:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            return {
                "document_uid": uid,
                "structural_revision": record.revision,
                "native_authority": self._authority_summary(uid, record),
                "recent_receipts": [
                    receipt.summary() for receipt in record.receipts.values()
                ],
            }

    def export_document(self, document_uid: str) -> dict[str, Any]:
        uid = _required_text(document_uid, "document UID")
        with self._lock:
            record = self._records.setdefault(uid, _DocumentRecord())
            receipts = []
            for token, receipt in record.receipts.items():
                receipts.append(
                    {
                        "idempotency_token": token,
                        "capability_name": receipt.capability_name,
                        "revision_before": receipt.revision_before,
                        "revision_after": receipt.revision_after,
                        "created": [item.summary() for item in receipt.created],
                        "changed": [item.summary() for item in receipt.changed],
                        "deleted": [item.summary() for item in receipt.deleted],
                        "replaced": [item.summary() for item in receipt.replaced],
                        "verified_result": json.loads(
                            record.verified_results[token]
                        ),
                    }
                )
            return {
                "schema": NATIVE_STATE_SCHEMA,
                "version": 1,
                "document_uid": uid,
                "structural_revision": record.revision,
                "authority_baseline_revision": record.authority_baseline_revision,
                "receipts": receipts,
            }

    def restore_document(
        self,
        document_uid: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        uid = _required_text(document_uid, "document UID")
        if not isinstance(payload, Mapping):
            raise NativeStateError("Persisted Native state must be an object.")
        if payload.get("schema") != NATIVE_STATE_SCHEMA or payload.get("version") != 1:
            raise NativeStateError("Persisted Native state has an invalid schema.")
        if str(payload.get("document_uid") or "") != uid:
            raise NativeStateError("Persisted Native state belongs to another document.")
        revision = payload.get("structural_revision")
        baseline = payload.get("authority_baseline_revision")
        if type(revision) is not int or revision < 0:
            raise NativeStateError("Persisted Native revision is invalid.")
        if baseline is not None and (
            type(baseline) is not int or baseline < 0 or baseline > revision
        ):
            raise NativeStateError("Persisted Native authority baseline is invalid.")
        raw_receipts = payload.get("receipts")
        if not isinstance(raw_receipts, list) or len(raw_receipts) > self._receipt_limit:
            raise NativeStateError("Persisted Native receipts exceed their bound.")
        if raw_receipts and baseline is None:
            raise NativeStateError("Persisted Native receipts have no authority epoch.")

        restored = _DocumentRecord(
            revision=revision,
            authority_baseline_revision=baseline,
        )
        for raw in raw_receipts:
            if not isinstance(raw, Mapping):
                raise NativeStateError("Persisted Native receipt is not an object.")
            token = _required_text(raw.get("idempotency_token"), "receipt token")
            if len(token) != 32 or any(
                character not in "0123456789abcdef" for character in token
            ):
                raise NativeStateError("Persisted Native receipt token is invalid.")
            capability = _required_text(raw.get("capability_name"), "capability name")
            before = raw.get("revision_before")
            after = raw.get("revision_after")
            if (
                type(before) is not int
                or type(after) is not int
                or before < 0
                or after < before
                or after > revision
                or (baseline is not None and before < baseline)
            ):
                raise NativeStateError("Persisted Native receipt revision is invalid.")
            if token in restored.receipts:
                raise NativeStateError("Persisted Native receipt token is duplicated.")

            def identities(name: str) -> tuple[NativeObjectIdentity, ...]:
                raw_values = raw.get(name)
                if not isinstance(raw_values, list):
                    raise NativeStateError(
                        f"Persisted Native {name} identities are invalid."
                    )
                values = tuple(
                    NativeObjectIdentity(
                        str(value.get("document_uid") or ""),
                        str(value.get("object_name") or ""),
                        str(value.get("type_id") or ""),
                    )
                    for value in raw_values
                    if isinstance(value, Mapping)
                )
                if len(values) != len(raw_values) or any(
                    value.document_uid != uid for value in values
                ):
                    raise NativeStateError(
                        f"Persisted Native {name} identities belong elsewhere."
                    )
                return _exact_identities(values, name)

            receipt = NativeOperationReceipt(
                idempotency_token=token,
                capability_name=capability,
                revision_before=before,
                revision_after=after,
                created=identities("created"),
                changed=identities("changed"),
                deleted=identities("deleted"),
                replaced=identities("replaced"),
            )
            verified_result = raw.get("verified_result")
            if not isinstance(verified_result, Mapping):
                raise NativeStateError(
                    "Persisted Native verified result must be an object."
                )
            result_json = _canonical_verified_result(verified_result)
            restored.receipts[token] = receipt
            restored.verified_results[token] = result_json

        with self._lock:
            current = self._records.get(uid)
            if current is not None and (
                current.revision != 0
                or current.receipts
                or current.authority_baseline_revision is not None
            ):
                raise NativeStateError(
                    "Cannot restore Native state over live document changes."
                )
            self._records[uid] = restored
            return self.snapshot(uid)
