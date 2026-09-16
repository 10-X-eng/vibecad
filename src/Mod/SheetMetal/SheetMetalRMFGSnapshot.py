# SPDX-License-Identifier: LGPL-2.1-or-later
"""Immutable export and quote inputs shared by ribbon and native workflows.

These records contain detached data, not document authority. The GUI owner must
validate/capture folded geometry before export, check the current revision before
queuing work, and check it again before publishing a result or enabling checkout.
No document access or geometry work occurs in this module.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
import uuid

from SheetMetalRMFGClient import MAX_STEP_BYTES


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _revision(value):
    names = {"document_uid", "document_session", "object_name", "structural_revision", "input_hash"}
    if not isinstance(value, Mapping) or set(value) != names:
        raise ValueError("Use the exact prepared sheet revision")
    revision = value["structural_revision"]
    if type(revision) is not int or not 0 <= revision <= 9_007_199_254_740_991:
        raise ValueError("The structural revision must be a nonnegative integer")
    for name in names - {"structural_revision"}:
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"The sheet revision requires {name}")
    if not re.fullmatch(r"[0-9a-f]{64}", value["input_hash"]):
        raise ValueError("The sheet revision requires a prepared input hash")
    return _canonical(dict(value))


@dataclass(frozen=True, init=False)
class ExportSnapshot:
    _revision_json: str = field(repr=False)
    step_bytes: bytes = field(repr=False)
    step_sha256: str

    def __init__(self, revision, step_bytes):
        if not isinstance(step_bytes, bytes) or not 0 < len(step_bytes) <= MAX_STEP_BYTES:
            raise ValueError("The detached export must contain at most 50 MiB of STEP bytes")
        object.__setattr__(self, "_revision_json", _revision(revision))
        object.__setattr__(self, "step_bytes", step_bytes)
        object.__setattr__(self, "step_sha256", hashlib.sha256(step_bytes).hexdigest())

    @property
    def revision(self):
        return json.loads(self._revision_json)

    def matches_revision(self, current):
        try:
            return _revision(current) == self._revision_json
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True, init=False)
class QuoteRequest:
    export: ExportSnapshot
    design_id: str
    _configuration_json: str = field(repr=False)
    quantity: int
    operation_key: str
    fingerprint: str

    def __init__(self, export, design_id, configuration, quantity):
        if not isinstance(export, ExportSnapshot):
            raise TypeError("A quote requires its exact detached export")
        if not isinstance(design_id, str) or not design_id.strip():
            raise ValueError("Use the design ID returned for this export")
        if not isinstance(configuration, Mapping):
            raise TypeError("Use the selected manufacturing configuration")
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise ValueError("Quantity must be 1 to 1000000 completed design units")
        encoded = _canonical(dict(configuration))
        binding = _canonical({"revision": export.revision, "step_sha256": export.step_sha256,
                              "design_id": design_id, "configuration": json.loads(encoded),
                              "quantity": quantity})
        object.__setattr__(self, "export", export)
        object.__setattr__(self, "design_id", design_id)
        object.__setattr__(self, "_configuration_json", encoded)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "operation_key", uuid.uuid4().hex)
        object.__setattr__(self, "fingerprint", hashlib.sha256(binding.encode("utf-8")).hexdigest())

    def items(self):
        # Quantity is completed designs. RMFG applies each part's instance count;
        # multiplying it locally would quote the wrong number of parts.
        return [{"design_id": self.design_id, "quantity": self.quantity,
                 "configuration": json.loads(self._configuration_json)}]

    def is_current(self, revision, design_id, configuration, quantity):
        try:
            return (self.export.matches_revision(revision) and design_id == self.design_id
                    and type(quantity) is int and quantity == self.quantity
                    and isinstance(configuration, Mapping)
                    and _canonical(dict(configuration)) == self._configuration_json)
        except (TypeError, ValueError):
            return False

    def submit(self, client):
        """Execute detached worker work; the GUI must validate result currency."""
        return client.create_quote(self.items(), operation_key=self.operation_key)
