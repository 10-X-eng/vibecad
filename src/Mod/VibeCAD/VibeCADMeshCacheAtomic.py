# SPDX-License-Identifier: LGPL-2.1-or-later

"""Path-budgeted atomic publication helpers for retained Mesh caches."""

from __future__ import annotations

from pathlib import Path
import string
from typing import Any


_ROLE_CHARACTERS = frozenset(string.ascii_lowercase + string.digits + "-")
_TOKEN_CHARACTERS = frozenset(string.hexdigits.lower())


def atomic_cache_temporary_path(
    directory: Any,
    *,
    role: str,
    token: str,
) -> Path:
    """Return a unique same-directory temp path with a bounded basename."""

    clean_role = str(role or "").strip()
    clean_token = str(token or "").strip().lower()
    if (
        not 1 <= len(clean_role) <= 20
        or any(character not in _ROLE_CHARACTERS for character in clean_role)
    ):
        raise ValueError("A Mesh cache temporary role must be bounded lowercase ASCII.")
    if (
        len(clean_token) != 16
        or any(character not in _TOKEN_CHARACTERS for character in clean_token)
    ):
        raise ValueError("A Mesh cache temporary token must be 16 hexadecimal characters.")
    return Path(directory) / f".{clean_role}-{clean_token}.tmp"
