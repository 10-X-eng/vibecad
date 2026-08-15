# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded creation environment for Native CAM Jobs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError


MAX_JOB_TEMPLATES = 128
MAX_JOB_TEMPLATE_BYTES = 2 * 1024 * 1024


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


@dataclass(frozen=True, slots=True)
class JobTemplateRecord:
    template_id: str
    label: str
    description: str
    content_sha256: str
    content: bytes
    path: Path

    def summary(self, *, is_default: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "template_id": self.template_id,
            "label": self.label,
            "content_sha256": self.content_sha256,
            "is_default": bool(is_default),
        }
        if self.description:
            result["description"] = self.description
        return result


@dataclass(frozen=True, slots=True)
class JobCreationEnvironment:
    state_sha256: str
    templates: tuple[JobTemplateRecord, ...]
    default_template_id: str | None
    template_count: int
    templates_truncated: bool

    def summary(self) -> dict[str, Any]:
        visible = self.templates[:MAX_JOB_TEMPLATES]
        return {
            "state_sha256": self.state_sha256,
            "template_count": self.template_count,
            "templates": [
                record.summary(is_default=record.template_id == self.default_template_id)
                for record in visible
            ],
            "templates_truncated": self.templates_truncated,
            "default_template_id": self.default_template_id,
        }


@dataclass(frozen=True, slots=True)
class PreparedJobTemplate:
    kind: str
    template_id: str | None = None
    content_sha256: str | None = None
    content: bytes | None = None

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.template_id is not None:
            result.update(
                template_id=self.template_id,
                content_sha256=self.content_sha256,
            )
        return result


def _read_template(path: Path) -> tuple[bytes, Mapping[str, Any]] | None:
    try:
        before = path.stat()
        if not path.is_file() or before.st_size > MAX_JOB_TEMPLATE_BYTES:
            return None
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(content) != after.st_size
        ):
            return None
        decoded = json.loads(content.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, Mapping):
        return None
    try:
        import Path.Main.Job as PathJob

        version = int(decoded.get(PathJob.JobTemplate.Version, 0) or 0)
    except (ImportError, TypeError, ValueError):
        return None
    if version != 1:
        return None
    return content, decoded


def _template_records() -> tuple[JobTemplateRecord, ...]:
    try:
        import Path.Preferences as PathPreferences
        import Path.Main.Job as PathJob
    except ImportError as exc:
        raise NativeManufactureError(
            "The CAM Job template catalog is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc

    paths: list[Path] = []
    seen: set[str] = set()
    default_template = str(PathPreferences.defaultJobTemplate() or "").strip()
    if default_template:
        try:
            resolved_default = Path(default_template).resolve(strict=True)
        except OSError:
            resolved_default = None
        if resolved_default is not None and resolved_default.is_file():
            seen.add(str(resolved_default))
            paths.append(resolved_default)
    for directory in PathPreferences.searchPaths():
        try:
            candidates = sorted(Path(str(directory)).glob("job_*.json"))
        except (OSError, ValueError):
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            identity = str(resolved)
            if identity in seen:
                continue
            seen.add(identity)
            paths.append(resolved)

    records: list[JobTemplateRecord] = []
    labels: dict[str, int] = {}
    for path in paths:
        loaded = _read_template(path)
        if loaded is None:
            continue
        content, decoded = loaded
        base_label = path.stem[4:] or path.stem
        ordinal = labels.get(base_label, 0)
        labels[base_label] = ordinal + 1
        label = base_label if ordinal == 0 else f"{base_label} ({ordinal + 1})"
        path_identity = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
        records.append(
            JobTemplateRecord(
                template_id=f"cam-job-template-v1:{path_identity}",
                label=_text(label, 160),
                description=_text(
                    decoded.get(PathJob.JobTemplate.Description, ""),
                    320,
                ),
                content_sha256=hashlib.sha256(content).hexdigest(),
                content=content,
                path=path,
            )
        )
    return tuple(sorted(records, key=lambda item: (item.label.casefold(), item.template_id)))


def capture_job_creation_environment() -> JobCreationEnvironment:
    """Capture every preference and catalog identity consumed by Job creation."""

    try:
        import Path.Preferences as PathPreferences

        records = _template_records()
        default_path = str(PathPreferences.defaultJobTemplate() or "").strip()
        default_resolved = ""
        if default_path:
            try:
                default_resolved = str(Path(default_path).resolve(strict=True))
            except OSError:
                default_resolved = ""
        default_template_id = next(
            (
                record.template_id
                for record in records
                if str(record.path) == default_resolved
            ),
            None,
        )
        stock_template = str(PathPreferences.defaultStockTemplate() or "")
        settings = {
            "default_output_file_sha256": hashlib.sha256(
                str(PathPreferences.defaultOutputFile() or "").encode("utf-8")
            ).hexdigest(),
            "enabled_postprocessors": list(
                PathPreferences.allEnabledLegacyPostProcessors()
            ),
            "default_postprocessor": str(
                PathPreferences.defaultPostProcessor() or ""
            ),
            "default_postprocessor_args": str(
                PathPreferences.defaultPostProcessorArgs() or ""
            ),
            "default_geometry_tolerance_mm": float(
                PathPreferences.defaultGeometryTolerance()
            ),
            "default_stock_template_sha256": hashlib.sha256(
                stock_template.encode("utf-8")
            ).hexdigest(),
            "default_template_id": default_template_id,
            "templates": [
                {
                    "template_id": record.template_id,
                    "content_sha256": record.content_sha256,
                }
                for record in records
            ],
        }
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Job creation environment could not be read.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc
    return JobCreationEnvironment(
        state_sha256=_digest(settings),
        templates=records,
        default_template_id=default_template_id,
        template_count=len(records),
        templates_truncated=len(records) > MAX_JOB_TEMPLATES,
    )


def prepare_job_template(
    environment: JobCreationEnvironment,
    value: Mapping[str, Any],
) -> PreparedJobTemplate:
    if not isinstance(environment, JobCreationEnvironment):
        raise TypeError("environment must be a JobCreationEnvironment")
    if not isinstance(value, Mapping):
        raise NativeManufactureError(
            "Job template must be one typed object.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    kind = str(value.get("kind") or "")
    if kind == "none" and set(value) == {"kind"}:
        return PreparedJobTemplate("none")
    if kind != "catalog" or set(value) != {
        "kind",
        "template_id",
        "expected_content_sha256",
    }:
        raise NativeManufactureError(
            "Job template must be {kind:'none'} or one exact catalog template.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    template_id = str(value.get("template_id") or "").strip()
    expected = str(value.get("expected_content_sha256") or "").strip()
    record = next(
        (item for item in environment.templates if item.template_id == template_id),
        None,
    )
    if record is None:
        raise NativeManufactureError(
            "The selected CAM Job template is no longer in the host catalog.",
            error_code="NATIVE_MANUFACTURE_TEMPLATE_STALE",
        )
    if record.content_sha256 != expected:
        raise NativeManufactureError(
            "The selected CAM Job template changed after turn start.",
            error_code="NATIVE_MANUFACTURE_TEMPLATE_STALE",
            repair={"current_content_sha256": record.content_sha256},
        )
    return PreparedJobTemplate(
        "catalog",
        template_id=record.template_id,
        content_sha256=record.content_sha256,
        content=record.content,
    )
