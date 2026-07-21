# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-approved, project-local point-cloud artifact registry.

The provider receives stable artifact ids and bounded metadata only.  Raw user
paths never enter a VibeScript program; candidates resolve and reauthenticate a
copy under the active project root before an isolated worker may parse it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any, Iterable, Mapping
import uuid


POINT_ARTIFACT_SCHEMA = "vibecad-point-artifacts-v1"
POINT_ARTIFACT_DIRECTORY = "point-artifacts"
POINT_ARTIFACT_MANIFEST = "manifest.json"
SUPPORTED_EXTENSIONS = frozenset({".asc", ".e57", ".pcd", ".ply", ".xyz"})
MAX_POINT_ARTIFACTS = 64
MAX_POINT_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_POINT_PROGRAMS_TO_SCAN = 4096
MAX_POINT_PROGRAM_MANIFEST_BYTES = 2 * 1024 * 1024
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_root(project_root: str | Path) -> Path:
    raw = str(project_root).strip()
    if not raw:
        raise ValueError("The active VibeCAD project has no artifact root.")
    root = Path(raw).expanduser()
    return root / POINT_ARTIFACT_DIRECTORY


def _manifest_path(project_root: str | Path) -> Path:
    return _artifact_root(project_root) / POINT_ARTIFACT_MANIFEST


def _safe_filename(name: str) -> str:
    clean = _SAFE_FILENAME.sub("_", Path(str(name or "")).name).strip("._")
    return clean[:120] or "point-cloud"


def _clean_label(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Point artifact label must be a string.")
    clean = value.strip()
    if len(clean) > 120 or "\0" in clean:
        raise ValueError(
            "Point artifact label must contain at most 120 characters without nulls."
        )
    return clean


def _clean_entry(raw: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"Point artifact entry {index} is not an object.")
    artifact_id = raw.get("artifact_id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError(f"Point artifact entry {index} has an invalid artifact_id.")
    name = raw.get("name")
    if (
        not isinstance(name, str)
        or not name
        or name != Path(name).name
        or len(name) > 160
        or "\0" in name
    ):
        raise ValueError(f"Point artifact entry {index} has an invalid name.")
    relative_path = raw.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError(f"Point artifact entry {index} has no relative_path.")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise ValueError(
            f"Point artifact entry {index} has an unsafe relative_path."
        )
    suffix = str(raw.get("format") or relative.suffix.lstrip(".")).lower()
    if f".{suffix}" not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Point artifact entry {index} has unsupported format {suffix!r}."
        )
    size_bytes = raw.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or type(size_bytes) is not int
        or not 1 <= size_bytes <= MAX_POINT_ARTIFACT_BYTES
    ):
        raise ValueError(f"Point artifact entry {index} has an invalid size_bytes.")
    sha256 = raw.get("sha256")
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ValueError(f"Point artifact entry {index} has an invalid SHA-256.")
    added_at = raw.get("added_at")
    if not isinstance(added_at, str) or not added_at:
        raise ValueError(f"Point artifact entry {index} has no added_at timestamp.")
    return {
        "artifact_id": artifact_id,
        "name": name,
        "label": _clean_label(raw.get("label") or ""),
        "relative_path": str(relative),
        "format": suffix,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "added_at": added_at,
    }


def _load_manifest(project_root: str | Path) -> dict[str, Any]:
    path = _manifest_path(project_root)
    if not path.is_file():
        return {
            "schema": POINT_ARTIFACT_SCHEMA,
            "updated_at": "",
            "artifacts": [],
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read point artifact manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != POINT_ARTIFACT_SCHEMA:
        raise RuntimeError(
            f"Point artifact manifest {path} does not use {POINT_ARTIFACT_SCHEMA}."
        )
    values = raw.get("artifacts")
    if not isinstance(values, list) or len(values) > MAX_POINT_ARTIFACTS:
        raise RuntimeError(
            f"Point artifact manifest {path} must contain at most "
            f"{MAX_POINT_ARTIFACTS} artifacts."
        )
    artifacts = [_clean_entry(value, index=index) for index, value in enumerate(values)]
    ids = [item["artifact_id"] for item in artifacts]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Point artifact manifest {path} contains duplicate ids.")
    return {
        "schema": POINT_ARTIFACT_SCHEMA,
        "updated_at": str(raw.get("updated_at") or ""),
        "artifacts": artifacts,
    }


def _write_manifest(project_root: str | Path, artifacts: list[dict[str, Any]]) -> None:
    path = _manifest_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": POINT_ARTIFACT_SCHEMA,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "artifacts": artifacts,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def approve_point_artifact(
    project_root: str | Path,
    source_path: str | Path,
    *,
    label: str = "",
) -> dict[str, Any]:
    """Copy one human-selected cloud into the project approval boundary."""

    source = Path(str(source_path)).expanduser()
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        formats = ", ".join(sorted(item.lstrip(".") for item in SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported point artifact format {suffix or source.name!r}; "
            f"supported formats are {formats}."
        )
    if not source.is_file():
        raise ValueError(f"Point artifact does not exist: {source}.")
    size_bytes = source.stat().st_size
    if not 1 <= size_bytes <= MAX_POINT_ARTIFACT_BYTES:
        raise ValueError(
            f"Point artifact size must be 1-{MAX_POINT_ARTIFACT_BYTES} bytes; "
            f"received {size_bytes}."
        )
    manifest = _load_manifest(project_root)
    artifacts = list(manifest["artifacts"])
    if len(artifacts) >= MAX_POINT_ARTIFACTS:
        raise ValueError(
            f"A project may approve at most {MAX_POINT_ARTIFACTS} point artifacts."
        )
    artifact_id = uuid.uuid4().hex
    name = source.name
    target_name = f"{artifact_id}-{_safe_filename(name)}"
    target_root = _artifact_root(project_root)
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / target_name
    try:
        shutil.copyfile(source, target)
        observed_size = target.stat().st_size
        if observed_size != size_bytes:
            raise RuntimeError(
                f"copied size changed from {size_bytes} to {observed_size} bytes"
            )
        entry = {
            "artifact_id": artifact_id,
            "name": name,
            "label": _clean_label(label),
            "relative_path": target_name,
            "format": suffix.lstrip("."),
            "size_bytes": size_bytes,
            "sha256": _sha256_file(target),
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        artifacts.append(entry)
        _write_manifest(project_root, artifacts)
    except Exception:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise
    return dict(entry)


def point_artifacts_summary(project_root: str | Path) -> dict[str, Any]:
    """Return bounded provider-safe metadata without exposing filesystem paths."""

    manifest = _load_manifest(project_root)
    root = _artifact_root(project_root).resolve()
    artifacts = []
    for entry in manifest["artifacts"]:
        path = (root / entry["relative_path"]).resolve()
        available = root in path.parents and path.is_file()
        observed_size = path.stat().st_size if available else 0
        artifacts.append(
            {
                key: entry[key]
                for key in (
                    "artifact_id",
                    "name",
                    "label",
                    "format",
                    "size_bytes",
                    "sha256",
                    "added_at",
                )
            }
            | {
                "available": bool(available and observed_size == entry["size_bytes"]),
                "observed_size_bytes": int(observed_size),
            }
        )
    return {
        "schema": POINT_ARTIFACT_SCHEMA,
        "artifact_count": len(artifacts),
        "artifact_limit": MAX_POINT_ARTIFACTS,
        "supported_formats": sorted(
            extension.lstrip(".") for extension in SUPPORTED_EXTENSIONS
        ),
        "maximum_artifact_bytes": MAX_POINT_ARTIFACT_BYTES,
        "artifacts": artifacts,
    }


def resolve_point_artifacts(
    project_root: str | Path,
    artifact_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Resolve and reauthenticate requested ids in deterministic request order."""

    requested = list(artifact_ids)
    if len(requested) > MAX_POINT_ARTIFACTS:
        raise ValueError(
            f"A candidate may reference at most {MAX_POINT_ARTIFACTS} point artifacts."
        )
    for index, artifact_id in enumerate(requested):
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError(f"point_artifact_ids[{index}] is not a 32-character id.")
    if len(requested) != len(set(requested)):
        raise ValueError("point_artifact_ids contains a duplicate id.")
    manifest = _load_manifest(project_root)
    by_id = {item["artifact_id"]: item for item in manifest["artifacts"]}
    root = _artifact_root(project_root).resolve()
    resolved = []
    for artifact_id in requested:
        entry = by_id.get(artifact_id)
        if entry is None:
            raise ValueError(
                f"Point artifact {artifact_id!r} is not approved for this project."
            )
        path = (root / entry["relative_path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(
                f"Approved point artifact {artifact_id!r} is missing from the project."
            )
        observed_size = path.stat().st_size
        if observed_size != entry["size_bytes"]:
            raise ValueError(
                f"Approved point artifact {artifact_id!r} changed size after approval."
            )
        observed_digest = _sha256_file(path)
        if observed_digest != entry["sha256"]:
            raise ValueError(
                f"Approved point artifact {artifact_id!r} changed content after approval."
            )
        resolved.append({**entry, "path": str(path)})
    return resolved


def _contains_artifact_reference(value: Any, artifact_id: str, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if isinstance(value, list):
        return any(
            _contains_artifact_reference(item, artifact_id, depth + 1)
            for item in value
        )
    if not isinstance(value, dict):
        return False
    if set(value) == {"artifact_id"} and value.get("artifact_id") == artifact_id:
        return True
    return any(
        _contains_artifact_reference(item, artifact_id, depth + 1)
        for item in value.values()
    )


def point_artifact_program_references(
    project_root: str | Path,
    artifact_id: str,
) -> list[dict[str, Any]]:
    """Return bounded persisted programs that still depend on one approval."""

    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError("Point artifact id must be 32 lowercase hexadecimal characters.")
    programs_root = Path(str(project_root)) / "vibescript" / "points"
    if not programs_root.is_dir():
        return []
    directories = sorted(
        (
            path
            for path in programs_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: path.name,
    )
    if len(directories) > MAX_POINT_PROGRAMS_TO_SCAN:
        raise RuntimeError(
            f"The Points program registry exceeds the safe scan limit of "
            f"{MAX_POINT_PROGRAMS_TO_SCAN}."
        )
    references = []
    for directory in directories:
        manifest_path = directory / "program.json"
        if not manifest_path.is_file():
            continue
        size = manifest_path.stat().st_size
        if not 1 <= size <= MAX_POINT_PROGRAM_MANIFEST_BYTES:
            raise RuntimeError(
                f"Points program {directory.name!r} has an invalid manifest size."
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Could not inspect Points program {directory.name!r}: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise RuntimeError(
                f"Points program {directory.name!r} manifest is not an object."
            )
        accepted = manifest.get("accepted_contract")
        accepted_inputs = (
            accepted.get("inputs") if isinstance(accepted, dict) else None
        )
        working_reference = _contains_artifact_reference(
            manifest.get("inputs"), artifact_id
        )
        accepted_reference = _contains_artifact_reference(
            accepted_inputs, artifact_id
        )
        if not working_reference and not accepted_reference:
            continue
        references.append(
            {
                "program_id": str(manifest.get("program_id") or directory.name),
                "label": str(manifest.get("label") or ""),
                "working_revision": str(manifest.get("working_revision") or ""),
                "accepted_revision": str(manifest.get("accepted_revision") or ""),
                "working_reference": working_reference,
                "accepted_reference": accepted_reference,
            }
        )
    return references


def remove_point_artifact(
    project_root: str | Path,
    artifact_id: str,
) -> dict[str, Any]:
    """Remove one explicitly selected approval and its project-local copy."""

    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError("Point artifact id must be 32 lowercase hexadecimal characters.")
    manifest = _load_manifest(project_root)
    artifacts = list(manifest["artifacts"])
    removed = next(
        (item for item in artifacts if item["artifact_id"] == artifact_id),
        None,
    )
    if removed is None:
        raise ValueError(f"No approved point artifact matches {artifact_id!r}.")
    references = point_artifact_program_references(project_root, artifact_id)
    if references:
        identities = [
            {
                "program_id": item["program_id"],
                "label": item["label"],
                "accepted_reference": item["accepted_reference"],
            }
            for item in references
        ]
        raise ValueError(
            "Cannot remove this approved point artifact while persisted Points "
            f"programs reference it: {json.dumps(identities, sort_keys=True)}"
        )
    remaining = [item for item in artifacts if item["artifact_id"] != artifact_id]
    root = _artifact_root(project_root).resolve()
    path = (root / removed["relative_path"]).resolve()
    if root not in path.parents:
        raise RuntimeError("The approved point artifact path escaped its project root.")
    tombstone = root / f".delete-{artifact_id}-{uuid.uuid4().hex}.tmp"
    deleted = False
    moved = False
    manifest_written = False
    try:
        if path.is_file():
            path.replace(tombstone)
            moved = True
        _write_manifest(project_root, remaining)
        manifest_written = True
        if moved:
            tombstone.unlink()
            deleted = True
    except Exception as exc:
        rollback_failures = []
        if manifest_written:
            try:
                _write_manifest(project_root, artifacts)
            except Exception as rollback_exc:
                rollback_failures.append(
                    f"manifest: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if tombstone.exists() and not path.exists():
            try:
                tombstone.replace(path)
            except Exception as rollback_exc:
                rollback_failures.append(
                    f"artifact: {type(rollback_exc).__name__}: {rollback_exc}"
                )
        if rollback_failures:
            raise RuntimeError(
                f"Point artifact removal failed ({type(exc).__name__}: {exc}) and "
                f"rollback was incomplete: {'; '.join(rollback_failures)}"
            ) from exc
        raise
    return {
        "artifact_id": artifact_id,
        "name": removed["name"],
        "artifact_copy_deleted": deleted,
        "remaining": len(remaining),
    }
