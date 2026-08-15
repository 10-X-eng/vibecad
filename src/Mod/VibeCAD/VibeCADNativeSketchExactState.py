# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical bounded records used by exact Native Sketch mutations."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping


def canonical_sketch_record(record: Mapping[str, object]) -> str:
    if not isinstance(record, Mapping):
        raise TypeError("A canonical Sketch record must be a mapping.")
    return json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sketch_records(
    records: Iterable[Mapping[str, object]],
) -> tuple[str, ...]:
    return tuple(canonical_sketch_record(record) for record in records)


def sketch_records_sha256(records: Iterable[Mapping[str, object]]) -> str:
    return canonical_sketch_records_sha256(canonical_sketch_records(records))


def canonical_sketch_records_sha256(records: Iterable[str]) -> str:
    """Digest records that have already been canonicalized exactly once."""

    digest = hashlib.sha256()
    for encoded in records:
        if not isinstance(encoded, str):
            raise TypeError("A canonical Sketch record must be a string.")
        value = encoded.encode("utf-8")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()
