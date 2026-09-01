# SPDX-License-Identifier: LGPL-2.1-or-later

"""Core VibeCAD context and read-only FreeCAD tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Mapping, Sequence
import uuid

from VibeCADAuth import (
    AuthState,
    provider_spec,
    resolve_auth_credential,
    resolve_auth_state,
)
from VibeCADEditState import active_edit_object, active_edit_state
from VibeCADPreferences import (
    configured_dotenv_path,
    load_debug_settings,
    load_settings,
)
from VibeCADIntentMemory import (
    active_memory_context,
    apply_memory_update,
    empty_memory,
    uncovered_turns,
)
from VibeCADModelingSurface import resolve_modeling_surface
from VibeCADNativeBackground import NativeBackgroundManager
from VibeCADNativeAnalyzeContext import AnalyzeContextCoordinator
from VibeCADNativeDrawingContext import DrawingSourceCatalogCoordinator
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger
from VibeCADNativeStatePersistence import (
    native_state_path,
    read_native_state,
    write_native_state,
)
from VibeCADProject import (
    AUTHORING_MODE_META_KEY,
    DEFAULT_CONVERSATION_TITLE,
    NATIVE_AUTHORITY_META_KEY,
    VibeCADConversationStore,
    VibeCADProjectStore,
    project_root_for_document_file,
    project_root_for_unsaved_document,
    vibecad_data_dir,
)
from VibeCADTools import ToolRegistry
import VibeCADVibeScriptDomains as vibescript_domains
from tool_impl import service as service_tools


MAX_CONTEXT_OBJECTS = 25
MAX_PROVIDER_PART_OBJECTS = 40
MAX_PROVIDER_SELECTION_BYTES = 4096
MAX_PROVIDER_SELECTION_ITEMS = 32
MAX_PROVIDER_SELECTION_SUBELEMENTS = 64
MAX_PROVIDER_SELECTION_FIELD_CHARS = 1024
REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCE_IMAGES = 6
REFERENCE_IMAGE_MAX_EDGE = 1568
REFERENCE_IMAGE_MAX_BYTES = 2_000_000
_REFERENCE_IMAGE_MIN_EDGE = 512
_REFERENCE_ENCODE_FORMATS = {
    "jpg": "JPG",
    "jpeg": "JPG",
    "png": "PNG",
    "webp": "WEBP",
}
_PRIVATE_SCRIPTED_ROLES = frozenset(
    {"implementation", "publication_target", "parameters"}
)
_CONVERSATION_WRITE_LOCK = threading.RLock()
_MAX_VIBESCRIPT_REFERENCE_CACHE_ENTRIES = 8
_MAX_VIBESCRIPT_REFERENCE_CACHE_BYTES = 256 * 1024 * 1024


def _slug_filename(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return slug[:64] or "reference"


def _load_qt_modules() -> tuple[Any, Any] | None:
    """Return (QtCore, QtGui) via FreeCAD PySide or PySide6, else None."""
    try:
        from PySide import QtCore, QtGui

        return QtCore, QtGui
    except Exception:
        try:
            from PySide6 import QtCore, QtGui

            return QtCore, QtGui
        except Exception:
            return None


def downscale_reference_image(
    path: str | Path,
    max_edge: int = REFERENCE_IMAGE_MAX_EDGE,
    max_bytes: int = REFERENCE_IMAGE_MAX_BYTES,
) -> dict[str, Any]:
    """Downscale/re-encode an image file in place when it exceeds limits.

    Pure function of the file at ``path``: reads it, optionally rewrites it,
    and returns metadata (``size_bytes``, ``image_size``, ``downscaled``,
    ``qt_available``). Gracefully no-ops when Qt bindings are unavailable
    and never raises for bad input.
    """
    target = Path(str(path or "")).expanduser()
    result: dict[str, Any] = {
        "path": str(target),
        "downscaled": False,
        "qt_available": False,
        "size_bytes": None,
        "image_size": None,
    }
    try:
        original_bytes = int(target.stat().st_size)
    except OSError as exc:
        result["error"] = f"Reference image not readable: {exc}"
        return result
    result["size_bytes"] = original_bytes

    qt_modules = _load_qt_modules()
    if qt_modules is None:
        return result
    qt_core, qt_gui = qt_modules
    result["qt_available"] = True

    try:
        image = qt_gui.QImage(str(target))
        if image.isNull():
            result["error"] = "Image could not be decoded for downscaling."
            return result
        width = int(image.width())
        height = int(image.height())
        result["image_size"] = [width, height]
        long_edge = max(width, height)
        # The project store owns the user's reference, not a provider-specific
        # thumbnail.  Keep a compact already-small-edge source byte-for-byte;
        # re-encoding an optimized line drawing as a full-color PNG can make
        # it much larger while destroying the small dimensions the model
        # needs to read. Compact files whose long edge exceeds max_edge still
        # need a resize so later provider payloads stay within limits.
        if original_bytes <= max_bytes and long_edge <= max_edge:
            return result

        encode_format = _REFERENCE_ENCODE_FORMATS.get(
            target.suffix.lower().lstrip("."), "PNG"
        )
        edge = min(long_edge, max_edge)
        quality = 90
        best_payload: bytes | None = None
        for _attempt in range(8):
            scaled = image
            if max(width, height) > edge:
                scaled = image.scaled(
                    edge,
                    edge,
                    qt_core.Qt.KeepAspectRatio,
                    qt_core.Qt.SmoothTransformation,
                )
            buffer = qt_core.QBuffer()
            buffer.open(qt_core.QIODevice.WriteOnly)
            saved = scaled.save(buffer, encode_format, quality)
            payload = bytes(buffer.data())
            buffer.close()
            if saved and payload:
                if best_payload is None or len(payload) < len(best_payload):
                    best_payload = payload
                if len(payload) <= max_bytes:
                    break
            if encode_format in ("JPG", "WEBP") and quality > 40:
                quality -= 15
            elif edge > _REFERENCE_IMAGE_MIN_EDGE:
                edge = max(_REFERENCE_IMAGE_MIN_EDGE, int(edge * 0.75))
            else:
                break

        if best_payload is None:
            result["error"] = "Image could not be re-encoded for downscaling."
            return result
        if len(best_payload) >= original_bytes and long_edge <= max_edge:
            # Re-encoding gained nothing and dimensions were already fine.
            return result
        target.write_bytes(best_payload)
        final = qt_gui.QImage(str(target))
        result["downscaled"] = True
        result["size_bytes"] = len(best_payload)
        if not final.isNull():
            result["image_size"] = [int(final.width()), int(final.height())]
        return result
    except Exception as exc:
        result["error"] = f"Downscale failed: {exc}"
        return result


class VibeCADService:
    """Shared state for existing workbench integrations."""

    def __init__(self, dotenv_path: Path | None = None) -> None:
        self.dotenv_path = dotenv_path
        self._registry = ToolRegistry()
        self._last_view_screenshot: dict[str, Any] | None = None
        self._reference_images: list[dict[str, Any]] = []
        self._pending_reference_image_ids: list[str] = []
        self._reference_cache_key: str | None = None
        self._reference_cache_document_uid: str | None = None
        self._conversation_cache: list[dict[str, Any]] = []
        self._conversation_cache_key: str | None = None
        self._conversation_cache_document_uid: str | None = None
        self._local_session_id = uuid.uuid4().hex
        self._tool_shape_feedback: list[dict[str, Any]] = []
        self._provider_working_object_names: list[str] = []
        self._provider_working_document_uid: str | None = None
        self._project_store = VibeCADProjectStore(self._local_session_id)
        self._native_document_states = NativeDocumentStateStore()
        self._native_assistant_undo = NativeAssistantUndoLedger()
        self._native_background_jobs = NativeBackgroundManager()
        self._native_analyze_contexts = AnalyzeContextCoordinator()
        self._native_drawing_source_contexts = DrawingSourceCatalogCoordinator()
        self._native_state_restores: set[tuple[str, str]] = set()
        self._native_state_restore_errors: dict[str, str] = {}
        self._new_document_authoring_pending: set[str] = set()
        self._steering_messages: list[dict[str, Any]] = []
        self._steering_sequence = 0
        self._vibescript_reference_cache_lock = threading.RLock()
        self._vibescript_reference_snapshots: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._register_core_tools()

    @staticmethod
    def _vibescript_object_identity(obj: Any) -> tuple[str, str]:
        document = getattr(obj, "Document", None)
        return (
            str(getattr(document, "Uid", "") or ""),
            str(getattr(obj, "Name", "") or ""),
        )

    @classmethod
    def _vibescript_reference_dependencies(
        cls, obj: Any
    ) -> frozenset[tuple[str, str]]:
        dependencies: list[Any] = [obj]
        dependencies.extend(list(getattr(obj, "OutListRecursive", []) or []))
        linked = getattr(obj, "LinkedObject", None)
        if linked is not None:
            dependencies.append(linked)
            dependencies.extend(
                list(getattr(linked, "OutListRecursive", []) or [])
            )
        return frozenset(
            identity
            for identity in (
                cls._vibescript_object_identity(candidate)
                for candidate in dependencies
            )
            if all(identity)
        )

    def capture_vibescript_reference_shape(self, obj: Any) -> dict[str, Any]:
        """Detach or reuse one exact source Shape for an isolated build.

        Cache validity is event-driven: any change to the source or one of its
        native dependencies invalidates the entry. No geometric fingerprints or
        approximate comparisons decide whether reuse is safe.
        """

        identity = self._vibescript_object_identity(obj)
        if not all(identity):
            raise RuntimeError("A VibeScript reference has no stable document identity.")
        with self._vibescript_reference_cache_lock:
            cached = self._vibescript_reference_snapshots.pop(identity, None)
            if cached is not None:
                self._vibescript_reference_snapshots[identity] = cached
                return {
                    "detached_shape": cached["detached_shape"],
                    "cache_token": str(cached["cache_token"]),
                    "artifact": cached.get("artifact"),
                    "cache_hit": True,
                }

        shape = getattr(obj, "Shape", None)
        if shape is None:
            raise RuntimeError("A VibeScript reference does not expose a Shape.")
        # Capture an independent topology wrapper over immutable authoritative
        # BREP geometry. Sharing the geometry avoids a costly deep copy of
        # thousands of imported surfaces; display triangulation is not copied
        # into the worker artifact and never participates in its identity.
        detached = shape.copy(False, False)
        entry = {
            "detached_shape": detached,
            "cache_token": uuid.uuid4().hex,
            "dependencies": self._vibescript_reference_dependencies(obj),
            "artifact": None,
        }
        with self._vibescript_reference_cache_lock:
            self._vibescript_reference_snapshots[identity] = entry
            while (
                len(self._vibescript_reference_snapshots)
                > _MAX_VIBESCRIPT_REFERENCE_CACHE_ENTRIES
            ):
                oldest = next(iter(self._vibescript_reference_snapshots))
                self._vibescript_reference_snapshots.pop(oldest, None)
        return {
            "detached_shape": detached,
            "cache_token": str(entry["cache_token"]),
            "artifact": None,
            "cache_hit": False,
        }

    def store_vibescript_reference_artifact(
        self, cache_token: str, artifact: dict[str, Any]
    ) -> None:
        """Attach exact serialized BREP evidence to its live snapshot token."""

        payload = artifact.get("brep_bytes")
        if not isinstance(payload, bytes) or not payload:
            return
        if len(payload) > _MAX_VIBESCRIPT_REFERENCE_CACHE_BYTES:
            return
        with self._vibescript_reference_cache_lock:
            target = next(
                (
                    entry
                    for entry in self._vibescript_reference_snapshots.values()
                    if str(entry.get("cache_token") or "") == cache_token
                ),
                None,
            )
            if target is None:
                return
            while True:
                retained_bytes = 0
                for entry in self._vibescript_reference_snapshots.values():
                    if entry is target:
                        continue
                    cached = entry.get("artifact")
                    cached_bytes = (
                        cached.get("brep_bytes")
                        if isinstance(cached, dict)
                        else None
                    )
                    if isinstance(cached_bytes, bytes):
                        retained_bytes += len(cached_bytes)
                if (
                    retained_bytes + len(payload)
                    <= _MAX_VIBESCRIPT_REFERENCE_CACHE_BYTES
                ):
                    break
                evicted = False
                for entry in self._vibescript_reference_snapshots.values():
                    if entry is target or entry.get("artifact") is None:
                        continue
                    entry["artifact"] = None
                    evicted = True
                    break
                if not evicted:
                    # payload alone was bounded above, so this is defensive.
                    if len(payload) <= _MAX_VIBESCRIPT_REFERENCE_CACHE_BYTES:
                        break
            target["artifact"] = dict(artifact)

    def invalidate_vibescript_reference_snapshots(self, obj: Any) -> None:
        """Invalidate every cached source that depends on ``obj`` exactly."""

        identity = self._vibescript_object_identity(obj)
        if not all(identity):
            return
        with self._vibescript_reference_cache_lock:
            stale = [
                key
                for key, entry in self._vibescript_reference_snapshots.items()
                if identity in entry.get("dependencies", ())
            ]
            for key in stale:
                self._vibescript_reference_snapshots.pop(key, None)

    def clear_vibescript_reference_snapshots(self, document_uid: str) -> None:
        clean_uid = str(document_uid or "")
        with self._vibescript_reference_cache_lock:
            stale = [
                key
                for key in self._vibescript_reference_snapshots
                if not clean_uid or key[0] == clean_uid
            ]
            for key in stale:
                self._vibescript_reference_snapshots.pop(key, None)

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def auth_state(self) -> AuthState:
        return resolve_auth_state(
            dotenv_path=self._dotenv_path(), provider=self.provider_name()
        )

    def _dotenv_path(self) -> Path | None:
        if self.dotenv_path is not None:
            return self.dotenv_path
        return configured_dotenv_path()

    def provider_name(self) -> str:
        name = load_settings().provider
        provider_spec(name)
        return name

    def provider_api_key(self) -> str | None:
        credential = resolve_auth_credential(
            dotenv_path=self._dotenv_path(), provider=self.provider_name()
        )
        return credential.value if credential is not None else None

    def provider_model(self) -> str:
        return load_settings().active_model

    def provider_base_url(self) -> str | None:
        """Base URL override for the selected provider, or None for official."""
        return load_settings().base_url_for(self.provider_name())

    def provider_reasoning_effort(self) -> str:
        return load_settings().reasoning_effort

    def web_search_enabled(self) -> bool:
        return bool(load_settings().web_search_enabled)

    def design_review_enabled(self) -> bool:
        return bool(load_settings().design_review_enabled)

    def codex_skills_enabled(self) -> bool:
        return bool(load_settings().codex_skills_enabled)

    def intent_memory_enabled(self) -> bool:
        return load_settings().intent_memory_enabled

    def intent_memory_model(self) -> str:
        settings = load_settings()
        return settings.intent_memory_model_for(self.provider_name())

    def modeling_engine(self) -> str:
        """Return the human-selected project/document authoring mode."""

        return self._project_store.modeling_engine()

    def _pending_authoring_documents(self) -> set[str]:
        pending = getattr(self, "_new_document_authoring_pending", None)
        if pending is None:
            pending = set()
            self._new_document_authoring_pending = pending
        return pending

    def new_document_authoring_mode_required(
        self,
        document_uid: str | None = None,
    ) -> bool:
        uid = str(document_uid or self._active_document_uid() or "").strip()
        return bool(uid and uid in self._pending_authoring_documents())

    def initialize_new_document_authoring_mode(
        self,
        document: Any,
    ) -> dict[str, Any]:
        """Apply the user default once to a genuinely new unsaved document."""

        uid = str(getattr(document, "Uid", "") or "").strip()
        if not uid or uid != str(self._active_document_uid() or ""):
            return {"required": False, "applied": False}
        metadata = dict(getattr(document, "Meta", {}) or {})
        embedded_mode = str(metadata.get(AUTHORING_MODE_META_KEY) or "").strip()
        if str(getattr(document, "FileName", "") or "").strip() or embedded_mode:
            self._pending_authoring_documents().discard(uid)
            return {"required": False, "applied": False}
        if self.new_document_authoring_mode_required(uid):
            return {"required": True, "applied": False}

        preference = load_settings().new_document_authoring_mode
        self._pending_authoring_documents().add(uid)
        if preference == "ask":
            return {"required": True, "applied": False}
        result = self.select_modeling_engine(preference)
        return {
            "required": False,
            "applied": True,
            "mode": result["mode"],
        }

    def select_modeling_engine(self, mode: str) -> dict[str, str]:
        """Select document authority and embed the portable choice in the FCStd."""

        from VibeCADAuthoringMode import normalize_authoring_mode

        selected = normalize_authoring_mode(mode)
        current = self.modeling_engine()
        uid = str(self._active_document_uid() or "")
        if not uid:
            raise RuntimeError("Select an authoring mode with an active document.")
        document = self._active_document()
        previous_metadata = dict(getattr(document, "Meta", {}) or {})
        pending_choice = self.new_document_authoring_mode_required(uid)
        if selected == current:
            if pending_choice:
                self._write_active_authoring_metadata(selected)
                self._pending_authoring_documents().discard(uid)
                return {"mode": current, "persistence": "session"}
            embedded = str(
                previous_metadata.get(AUTHORING_MODE_META_KEY) or ""
            ).strip()
            if embedded != selected:
                self._write_active_authoring_metadata(selected)
            return {"mode": current, "persistence": "unchanged"}
        self.ensure_native_document_state(uid)
        restore_error = self._native_state_restore_errors.get(uid)
        if restore_error:
            raise RuntimeError(
                f"Native authority state is unavailable: {restore_error}"
            )
        if selected == "native":
            self._native_document_states.begin_native_authority(uid)
            try:
                result = self._project_store.select_modeling_engine(selected)
                self._write_active_authoring_metadata(selected)
                self._sync_active_native_authority_metadata()
                self._persist_active_native_state()
            except Exception:
                try:
                    self._project_store.select_modeling_engine(current)
                finally:
                    document.Meta = previous_metadata
                    self._native_document_states.end_native_authority(uid)
                raise
            self._pending_authoring_documents().discard(uid)
            return result

        self._native_document_states.require_vibescript_return_safe(uid)
        try:
            result = self._project_store.select_modeling_engine(selected)
            self._write_active_authoring_metadata(selected)
            self._native_document_states.end_native_authority(uid)
        except Exception:
            try:
                self._project_store.select_modeling_engine(current)
            finally:
                document.Meta = previous_metadata
            raise
        self._pending_authoring_documents().discard(uid)
        return result

    def _write_active_authoring_metadata(self, mode: str) -> None:
        document = self._active_document()
        if document is None:
            raise RuntimeError("VibeCAD has no active document authority to persist.")
        metadata = dict(getattr(document, "Meta", {}) or {})
        metadata[AUTHORING_MODE_META_KEY] = str(mode)
        document.Meta = metadata

    def _sync_active_native_authority_metadata(self) -> None:
        document = self._active_document()
        if document is None:
            return
        uid = str(getattr(document, "Uid", "") or "").strip()
        if not uid:
            return
        exported = self._native_document_states.export_document(uid)
        exported["receipts"] = []
        metadata = dict(getattr(document, "Meta", {}) or {})
        metadata[NATIVE_AUTHORITY_META_KEY] = json.dumps(
            exported,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        document.Meta = metadata

    def _sync_native_authority_metadata_if_active(self, document_uid: str) -> None:
        uid = str(document_uid or "").strip()
        if uid != str(self._active_document_uid() or ""):
            return
        authority = self._native_document_states.snapshot(uid)["native_authority"]
        if authority.get("active"):
            self._sync_active_native_authority_metadata()

    @staticmethod
    def _embedded_native_authority_state(document: Any) -> dict[str, Any] | None:
        metadata = dict(getattr(document, "Meta", {}) or {})
        raw = str(metadata.get(NATIVE_AUTHORITY_META_KEY) or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"Embedded Native authority state is invalid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Embedded Native authority state is not an object.")
        return payload

    def persist_modeling_engine_after_save(
        self,
        document_identity: str,
    ) -> dict[str, str] | None:
        """Promote the active document's session choice after its first save."""

        if str(document_identity or "").strip() != str(
            self._active_document_uid() or ""
        ):
            return None
        selection = self._project_store.persist_modeling_engine_after_save()
        if selection.get("mode") == "native":
            if not self._native_document_states.has_document(document_identity):
                self.ensure_native_document_state(document_identity)
            self._native_document_states.begin_native_authority(document_identity)
            self._persist_active_native_state()
        return selection

    def prepare_document_for_save(self, document: Any) -> None:
        """Synchronize portable authority metadata before FCStd serialization."""

        uid = str(getattr(document, "Uid", "") or "").strip()
        if not uid or uid != str(self._active_document_uid() or ""):
            return
        mode = self.modeling_engine()
        self._write_active_authoring_metadata(mode)
        if mode == "native":
            self._sync_active_native_authority_metadata()

    def discard_session_modeling_engine(self, document_identity: str) -> None:
        self._project_store.discard_session_modeling_engine(document_identity)
        self._pending_authoring_documents().discard(
            str(document_identity or "").strip()
        )

    @staticmethod
    def _object_document_uid(obj: Any) -> str:
        document = getattr(obj, "Document", None)
        return str(getattr(document, "Uid", "") or "").strip()

    def ensure_native_document_state(self, document_uid: str) -> int | None:
        uid = str(document_uid or "").strip()
        if not uid:
            return None
        revision = self._native_document_states.ensure_document(uid)
        if uid != str(self._active_document_uid() or ""):
            return revision
        scope = self._project_store.project_scope()
        if not bool(scope.get("document_saved")) or self.modeling_engine() != "native":
            return revision
        path = native_state_path(scope)
        restore_key = (uid, str(path))
        if restore_key in self._native_state_restores:
            return self._native_document_states.current_revision(uid)
        try:
            payload = read_native_state(path)
            if payload is None:
                payload = self._embedded_native_authority_state(
                    self._active_document()
                )
            if payload is None:
                self._native_document_states.begin_native_authority(uid)
            else:
                self._native_document_states.restore_document(uid, payload)
        except Exception as exc:
            self._native_state_restore_errors[uid] = str(exc)
        else:
            self._native_state_restore_errors.pop(uid, None)
            self._native_state_restores.add(restore_key)
        return self._native_document_states.current_revision(uid)

    def _persist_active_native_state(self) -> str | None:
        uid = str(self._active_document_uid() or "")
        if not uid or self.modeling_engine() != "native":
            return None
        scope = self._project_store.project_scope()
        if not bool(scope.get("document_saved")):
            return None
        restore_error = self._native_state_restore_errors.get(uid)
        if restore_error:
            raise RuntimeError(
                f"Refusing to overwrite unreadable Native state: {restore_error}"
            )
        path = write_native_state(
            native_state_path(scope),
            self._native_document_states.export_document(uid),
        )
        self._native_state_restores.add((uid, str(path)))
        return str(path)

    def note_native_object_created(self, obj: Any) -> int | None:
        uid = self._object_document_uid(obj)
        revision = (
            self._native_document_states.note_structural_change(uid) if uid else None
        )
        if uid:
            self._invalidate_native_read_contexts(uid)
        self._sync_native_authority_metadata_if_active(uid)
        return revision

    def note_native_object_deleted(self, obj: Any) -> int | None:
        uid = self._object_document_uid(obj)
        revision = (
            self._native_document_states.note_structural_change(uid) if uid else None
        )
        if uid:
            self._invalidate_native_read_contexts(uid)
        self._sync_native_authority_metadata_if_active(uid)
        return revision

    def note_native_object_property_change(
        self,
        obj: Any,
        property_name: str,
    ) -> int | None:
        uid = self._object_document_uid(obj)
        if not uid:
            return None
        if str(property_name or "") in {"Placement", "LinkPlacement"}:
            try:
                from VibeCADNativeAssemblyPlayback import (
                    is_native_assembly_playback_presentation_change,
                )

                if is_native_assembly_playback_presentation_change(
                    obj,
                    property_name,
                ):
                    return self._native_document_states.current_revision(uid)
            except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
                pass
        previous_revision = self._native_document_states.current_revision(uid)
        revision = self._native_document_states.note_object_property_change(
            uid,
            property_name,
        )
        revision_changed = revision != previous_revision
        if str(property_name or "") == "Visibility" or revision_changed:
            self._invalidate_native_read_contexts(uid)
        if revision_changed:
            self._sync_native_authority_metadata_if_active(uid)
        return revision

    def _invalidate_native_read_contexts(self, document_uid: str) -> None:
        """Invalidate revision-scoped detached read state for one document."""

        uid = str(document_uid or "").strip()
        if not uid:
            return
        self._native_analyze_contexts.invalidate_document(uid)
        self._native_drawing_source_contexts.invalidate_document(uid)
        from VibeCADNativeDrawingSourceCatalog import (
            invalidate_drawing_source_catalog_cache,
        )

        invalidate_drawing_source_catalog_cache(uid)

    def native_document_state(self) -> dict[str, Any]:
        uid = str(self._active_document_uid() or "")
        if not uid:
            return {
                "document_uid": "",
                "structural_revision": 0,
                "recent_receipts": [],
            }
        result = self._native_document_states.snapshot(uid)
        restore_error = self._native_state_restore_errors.get(uid)
        if restore_error:
            result["restore_error"] = restore_error
        return result

    def native_active_snapshot(self) -> dict[str, Any]:
        """Build only the live state for the human-selected Native surface."""

        document = self._active_document()
        if document is None:
            raise RuntimeError("No active document is available.")
        from VibeCADNativeSnapshot import build_active_snapshot
        from VibeCADModelingSurface import provider_engine_for_ribbon
        from VibeCADRibbonSurface import read_active_ribbon_surface

        surface = read_active_ribbon_surface()
        if (
            provider_engine_for_ribbon(
                self.modeling_engine(),
                surface.surface_id,
            )
            != "native"
        ):
            raise RuntimeError("The active ribbon has no Native provider surface.")
        background_job = None
        if surface.surface_id == "analyze":
            jobs = self._native_background_jobs.document_snapshots(
                str(document.Uid),
                capability_prefix="analyze.",
                active_only=False,
            )
            background_job = (
                jobs[0]
                if len(jobs) == 1
                else jobs
                if jobs
                else None
            )
        elif surface.surface_id == "manufacture":
            background_job = self._native_background_jobs.document_snapshots(
                str(document.Uid),
                capability_prefix="manufacture.",
                active_only=True,
            )
        return build_active_snapshot(
            document,
            surface.surface_id,
            self.native_document_state(),
            background_job=background_job,
        )

    def begin_native_analyze_context_request(self) -> dict[str, Any] | None:
        """Capture one detached, visibility-scoped Analyze context request."""

        document = self._active_document()
        if document is None:
            return None
        from VibeCADModelingSurface import provider_engine_for_ribbon
        from VibeCADNativeAnalyzeSnapshot import (
            begin_analyze_snapshot_capture,
            capture_analyze_clipping,
            capture_analyze_snapshot_batch,
        )
        from VibeCADNativeGeometrySources import drawing_analysis_artifact_names
        from VibeCADNativeSnapshot import capture_active_snapshot_base
        from VibeCADRibbonSurface import read_active_ribbon_surface

        surface = read_active_ribbon_surface()
        if surface.surface_id != "analyze" or provider_engine_for_ribbon(
            self.modeling_engine(),
            surface.surface_id,
        ) != "native":
            return None
        native_state = self.native_document_state()
        jobs = self._native_background_jobs.document_snapshots(
            str(document.Uid),
            capability_prefix="analyze.",
            active_only=False,
        )
        background_job = (
            jobs[0]
            if len(jobs) == 1
            else jobs
            if jobs
            else None
        )
        analysis_artifacts = drawing_analysis_artifact_names(document)
        base = capture_active_snapshot_base(
            document,
            "analyze",
            native_state,
            analysis_artifact_names=analysis_artifacts,
        )
        details = begin_analyze_snapshot_capture(
            document,
            background_job=background_job,
            validate_brep=False,
            analysis_artifact_names=analysis_artifacts,
        )
        revision = int(native_state.get("structural_revision", 0) or 0)
        cacheable = (
            all(bool(getattr(job, "terminal", False)) for job in background_job)
            if isinstance(background_job, tuple)
            else bool(
                background_job is None
                or bool(getattr(background_job, "terminal", False))
            )
        )
        cache_hit = bool(
            cacheable
            and self._native_analyze_contexts.has_cached(
                str(document.Uid),
                revision,
                variant=str(details.get("active_analysis_name") or ""),
            )
        )
        detached_clipping = capture_analyze_clipping(document, details)
        request = {
            **details,
            "detached_clipping": detached_clipping,
            "structural_revision": revision,
            "base_snapshot": base,
            "cacheable": cacheable,
        }
        if not cache_hit:
            request["detached_parts"] = [
                capture_analyze_snapshot_batch(
                    document,
                    details,
                    list(details["object_names"]),
                )
            ]
        return request

    def _validate_native_analyze_context_request(
        self,
        request: Mapping[str, Any],
    ) -> Any:
        from VibeCADModelingSurface import provider_engine_for_ribbon
        from VibeCADNativeAnalyzeContext import AnalyzeContextStale
        from VibeCADRibbonSurface import read_active_ribbon_surface

        document = self._active_document()
        uid = str(request.get("document_uid") or "")
        revision = int(request.get("structural_revision", -1) or 0)
        current_uid = str(getattr(document, "Uid", "") or "")
        current_revision = int(
            self.native_document_state().get("structural_revision", 0) or 0
        )
        surface = read_active_ribbon_surface()
        if (
            document is None
            or current_uid != uid
            or current_revision != revision
            or surface.surface_id != "analyze"
            or provider_engine_for_ribbon(
                self.modeling_engine(),
                surface.surface_id,
            )
            != "native"
        ):
            raise AnalyzeContextStale(
                "The active Analyze document changed during context capture."
            )
        return document

    def capture_native_analyze_context_batch(
        self,
        request: Mapping[str, Any],
        object_names: Sequence[str],
    ) -> dict[str, Any]:
        from VibeCADNativeAnalyzeSnapshot import capture_analyze_snapshot_batch

        document = self._validate_native_analyze_context_request(request)
        return capture_analyze_snapshot_batch(document, request, object_names)

    def capture_native_analyze_context_clipping(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        from VibeCADNativeAnalyzeSnapshot import capture_analyze_clipping

        document = self._validate_native_analyze_context_request(request)
        return capture_analyze_clipping(document, request)

    def native_analyze_context_coordinator(self) -> AnalyzeContextCoordinator:
        """Return the read-only Analyze context lane; never a provider tool."""

        return self._native_analyze_contexts

    def begin_native_drawing_context_request(self) -> dict[str, Any] | None:
        """Capture one detached, visibility-scoped Drawing context request."""

        document = self._active_document()
        if document is None:
            return None
        from VibeCADModelingSurface import provider_engine_for_ribbon
        from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
        from VibeCADNativeDrawingSourceCatalog import (
            cached_drawing_source_catalog_state,
            capture_drawing_source_catalog_inventory,
        )
        from VibeCADNativeGeometrySources import drawing_analysis_artifact_names
        from VibeCADNativeSnapshot import (
            capture_active_snapshot_base,
            complete_active_snapshot,
        )
        from VibeCADRibbonSurface import read_active_ribbon_surface

        surface = read_active_ribbon_surface()
        if surface.surface_id != "drawing" or provider_engine_for_ribbon(
            self.modeling_engine(),
            surface.surface_id,
        ) != "native":
            return None
        native_state = self.native_document_state()
        revision = int(native_state.get("structural_revision", 0) or 0)
        sources = cached_drawing_source_catalog_state(
            str(document.Uid),
            revision,
        )
        if sources is None:
            inventory = capture_drawing_source_catalog_inventory(document)
            sources = [dict(source) for source in inventory["sources"]]
            object_names = list(inventory["object_names"])
            analysis_artifacts = frozenset(inventory["analysis_artifact_names"])
        else:
            object_names = [str(source["object_name"]) for source in sources]
            analysis_artifacts = drawing_analysis_artifact_names(document)
        base = capture_active_snapshot_base(
            document,
            "drawing",
            native_state,
            analysis_artifact_names=analysis_artifacts,
        )
        selection = base.get("_selection")
        domain = build_drawing_snapshot(
            document,
            selection=selection if isinstance(selection, Mapping) else None,
            structural_revision=revision,
            detached_sources=sources,
        )
        return {
            "document_uid": str(document.Uid),
            "structural_revision": revision,
            "object_names": object_names,
            "detached_sources": sources,
            "base_snapshot": base,
            "completed_snapshot": complete_active_snapshot(base, domain),
        }

    def _validate_native_drawing_context_request(
        self,
        request: Mapping[str, Any],
    ) -> Any:
        from VibeCADModelingSurface import provider_engine_for_ribbon
        from VibeCADNativeDrawingContext import DrawingContextStale
        from VibeCADRibbonSurface import read_active_ribbon_surface

        document = self._active_document()
        uid = str(request.get("document_uid") or "")
        revision = int(request.get("structural_revision", -1) or 0)
        current_uid = str(getattr(document, "Uid", "") or "")
        current_revision = int(
            self.native_document_state().get("structural_revision", 0) or 0
        )
        surface = read_active_ribbon_surface()
        if (
            document is None
            or current_uid != uid
            or current_revision != revision
            or surface.surface_id != "drawing"
            or provider_engine_for_ribbon(
                self.modeling_engine(),
                surface.surface_id,
            )
            != "native"
        ):
            raise DrawingContextStale(
                "The active Drawing document changed during source capture."
            )
        return document

    def capture_native_drawing_context_batch(
        self,
        request: Mapping[str, Any],
        object_names: Sequence[str],
    ) -> dict[str, Any]:
        """Capture one small, live Drawing source batch on the document thread."""

        from VibeCADNativeDrawingViewState import drawing_source_catalog_identity_state
        from VibeCADNativeGeometrySources import is_potential_design_geometry_source

        document = self._validate_native_drawing_context_request(request)
        sources = []
        for value in object_names:
            name = str(value or "")
            if not name:
                raise ValueError("Drawing context batch contains an invalid object name.")
            obj = document.getObject(name)
            if obj is None or not is_potential_design_geometry_source(document, obj):
                continue
            try:
                sources.append(drawing_source_catalog_identity_state(obj))
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                continue
        return {"sources": sources}

    def finish_native_drawing_context_request(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the bounded Drawing snapshot after its source cache is ready."""

        from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
        from VibeCADNativeSnapshot import complete_active_snapshot

        document = self._validate_native_drawing_context_request(request)
        base = request.get("base_snapshot")
        if not isinstance(base, Mapping):
            raise ValueError("Drawing context request has no base snapshot.")
        selection = base.get("_selection")
        domain = build_drawing_snapshot(
            document,
            selection=selection if isinstance(selection, Mapping) else None,
            structural_revision=int(request["structural_revision"]),
        )
        return complete_active_snapshot(base, domain)

    def native_drawing_context_coordinator(
        self,
    ) -> DrawingSourceCatalogCoordinator:
        """Return the read-only Drawing source lane; never a provider tool."""

        return self._native_drawing_source_contexts

    def close_native_document_state(self, document_uid: str) -> None:
        uid = str(document_uid or "").strip()
        self._native_background_jobs.cancel_document(uid)
        self._native_analyze_contexts.close_document(uid)
        self._native_drawing_source_contexts.close_document(uid)
        from VibeCADNativeDrawingSourceCatalog import (
            invalidate_drawing_source_catalog_cache,
        )

        invalidate_drawing_source_catalog_cache(uid)
        if uid:
            self._native_assistant_undo.close_document(uid)
        self._native_document_states.close_document(uid)
        self._native_state_restore_errors.pop(uid, None)
        self._native_state_restores = {
            item for item in self._native_state_restores if item[0] != uid
        }

    def native_background_manager(self) -> NativeBackgroundManager:
        """Return the host-owned manager; this is never a provider tool."""

        return self._native_background_jobs

    def native_document_state_store(self) -> NativeDocumentStateStore:
        """Return host-owned Native state; this is never a provider tool."""

        return self._native_document_states

    def native_assistant_undo_ledger(self) -> NativeAssistantUndoLedger:
        """Return document-lifetime assistant undo provenance."""

        return self._native_assistant_undo

    def provider_debug_config(self) -> dict[str, Any]:
        settings = load_debug_settings()
        return {
            "enabled": settings.context_debug_enabled,
            "capture_directory": str(settings.resolved_capture_directory),
        }

    def use_online_provider_by_default(self) -> bool:
        return load_settings().use_online_provider

    def active_workbench_name(self) -> str | None:
        try:
            import FreeCADGui as Gui

            workbench = Gui.activeWorkbench()
            if workbench:
                return workbench.name()
        except Exception:
            return None
        return None

    def document_summary(self) -> dict[str, Any]:
        try:
            import FreeCAD as App
        except Exception:
            return {"document": None, "objects": []}

        doc = App.ActiveDocument
        if doc is None:
            return {"document": None, "objects": []}
        objects = [self._document_object_summary(obj) for obj in doc.Objects]
        visible_objects, bounds = self._bounded_items(objects, MAX_CONTEXT_OBJECTS)
        return {
            "document": doc.Name,
            "label": getattr(doc, "Label", doc.Name),
            "object_count": len(doc.Objects),
            "object_limit": bounds["limit"],
            "objects_truncated": bounds["truncated"],
            "objects_omitted": bounds["omitted"],
            "objects": visible_objects,
            "aero": self.aero_summary(),
        }

    def aero_summary(self) -> dict[str, Any]:
        """Bounded AeroConfig / AeroReport facts for the active document."""

        from VibeCADAeroContext import document_aero_summary

        return document_aero_summary(self._active_document())

    def provider_document_summary(self) -> dict[str, Any]:
        """Return exact object identity without duplicating domain state."""
        doc = self._active_document()
        if doc is None:
            return {"document": None, "object_count": 0, "objects": []}
        visible_objects: list[dict[str, Any]] = []
        object_count = 0
        for obj in doc.Objects:
            if self._is_private_scripted_object(obj):
                continue
            object_count += 1
            if len(visible_objects) < MAX_CONTEXT_OBJECTS:
                visible_objects.append(self._object_summary(obj))
        return {
            "document": doc.Name,
            "label": getattr(doc, "Label", doc.Name),
            "file_name": str(getattr(doc, "FileName", "") or ""),
            "object_count": object_count,
            "objects_truncated": object_count > len(visible_objects),
            "objects_omitted": max(0, object_count - len(visible_objects)),
            "objects": visible_objects,
        }

    def provider_edit_object_summary(self) -> dict[str, Any] | None:
        """Return only the exact object currently owned by FreeCAD edit mode."""

        state = active_edit_state()
        if state.document_object is not None:
            return self._object_summary(state.document_object)
        if not state.active:
            return None
        return {
            "name": "",
            "label": "",
            "type": type(state.view_provider).__name__,
            "resolved": False,
        }

    def provider_turn_document_summary(self) -> dict[str, Any]:
        """Constant-size document identity for a model turn."""

        doc = self._active_document()
        if doc is None:
            return {
                "name": None,
                "uid": None,
                "object_count": 0,
                "edit_object": None,
            }
        return {
            "name": str(getattr(doc, "Name", "") or ""),
            "uid": str(getattr(doc, "Uid", "") or ""),
            "object_count": len(getattr(doc, "Objects", []) or []),
            "edit_object": self.provider_edit_object_summary(),
        }

    def provider_turn_selection_summary(self) -> dict[str, Any]:
        """Return the exact explicit selection or one deterministic omission."""

        try:
            import FreeCADGui as Gui

            selected = list(Gui.Selection.getSelectionEx() or [])
        except Exception as exc:
            return {"selection_count": 0, "selection": [], "error": str(exc)}

        def omitted(reason: str) -> dict[str, Any]:
            return {
                "selection_count": len(selected),
                "selection_omitted": True,
                "selection_item_limit": MAX_PROVIDER_SELECTION_ITEMS,
                "subelement_limit_per_item": MAX_PROVIDER_SELECTION_SUBELEMENTS,
                "selection_json_byte_limit": MAX_PROVIDER_SELECTION_BYTES,
                "reason": (
                    f"{reason} Reduce the explicit selection and retry so its "
                    "exact objects and subelements fit in turn-start context."
                ),
            }

        if len(selected) > MAX_PROVIDER_SELECTION_ITEMS:
            return omitted(
                "The explicit selection exceeds the bounded turn-start item limit."
            )
        document = self._active_document()
        document_uid = str(getattr(document, "Uid", "") or "")
        items = []
        for item in selected:
            try:
                raw_subelements = getattr(item, "SubElementNames", None)
                subelements: list[str] = []
                if raw_subelements is not None:
                    for name in raw_subelements:
                        if len(subelements) >= MAX_PROVIDER_SELECTION_SUBELEMENTS:
                            return omitted(
                                "A selected object exceeds the bounded subelement limit."
                            )
                        clean_name = str(name)
                        if len(clean_name) > MAX_PROVIDER_SELECTION_FIELD_CHARS:
                            return omitted(
                                "A selected subelement name exceeds the bounded field limit."
                            )
                        subelements.append(clean_name)
                obj = item.Object
                fields = {
                    "object": str(getattr(obj, "Name", "") or ""),
                    "label": str(getattr(obj, "Label", "") or ""),
                    "type": str(getattr(obj, "TypeId", "") or ""),
                }
                if any(
                    len(value) > MAX_PROVIDER_SELECTION_FIELD_CHARS
                    for value in fields.values()
                ):
                    return omitted(
                        "A selected object identity exceeds the bounded field limit."
                    )
                items.append(
                    {
                        **fields,
                        **(
                            {
                                "reference": {
                                    "document_uid": document_uid,
                                    "object_name": fields["object"],
                                }
                            }
                            if document_uid and fields["object"]
                            else {}
                        ),
                        "subelements": subelements,
                    }
                )
            except Exception:
                continue
            result = {"selection_count": len(selected), "selection": items}
            encoded = json.dumps(
                result,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_PROVIDER_SELECTION_BYTES:
                return omitted(
                    "The explicit selection exceeds the bounded turn-start JSON limit."
                )
        return {"selection_count": len(selected), "selection": items}

    def structural_document_revision(self) -> str:
        """Hash native object content and shape identity for the live document."""
        doc = self._active_document()
        if doc is None:
            return hashlib.sha256(b"no-document").hexdigest()
        objects: list[dict[str, Any]] = []
        for obj in list(getattr(doc, "Objects", []) or []):
            item: dict[str, Any] = {
                "name": str(getattr(obj, "Name", "") or ""),
                "type": str(getattr(obj, "TypeId", "") or ""),
                "label": str(getattr(obj, "Label", "") or ""),
            }
            shape = getattr(obj, "Shape", None)
            shape_summary: dict[str, Any] = {}
            if shape is not None:
                shape_summary = self._shape_topology_summary(shape)
                item["shape"] = shape_summary
            placement = self._placement_summary(getattr(obj, "Placement", None))
            if placement:
                item["placement"] = placement
            group = list(getattr(obj, "Group", []) or [])
            if group:
                item["group"] = [getattr(child, "Name", None) for child in group]
            tip = getattr(obj, "Tip", None)
            if tip is not None:
                item["tip"] = getattr(tip, "Name", None)
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject":
                sketch_payload = {
                    "geometry": [str(value) for value in list(obj.Geometry)],
                    "constraints": [str(value) for value in list(obj.Constraints)],
                    "support": str(getattr(obj, "Support", "")),
                    "map_mode": str(getattr(obj, "MapMode", "")),
                }
                item["sketch_sha256"] = hashlib.sha256(
                    json.dumps(
                        sketch_payload,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            elif not shape_summary or shape_summary.get("is_null"):
                try:
                    content = str(getattr(obj, "Content", "") or "")
                except Exception:
                    content = ""
                item["content_sha256"] = hashlib.sha256(
                    content.encode("utf-8", errors="replace")
                ).hexdigest()
            objects.append(item)
        payload = {
            "document_uid": str(getattr(doc, "Uid", "") or ""),
            "objects": objects,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def provider_document_revision(
        self,
        native_diagnostics: dict[str, Any] | None = None,
        *,
        object_count: int | None = None,
    ) -> str:
        """Return a topology-free turn token from bounded document metadata."""

        return self.provider_document_revision_for(
            self._active_document(),
            native_diagnostics=native_diagnostics,
            object_count=object_count,
        )

    def provider_document_revision_for(
        self,
        doc: Any,
        native_diagnostics: dict[str, Any] | None = None,
        *,
        object_count: int | None = None,
    ) -> str:
        """Return the provider revision token for one exact open document.

        Universal VibeScript source tools can operate on a referenced component's
        owning document while the Assembly document remains active.  Keeping the
        document argument explicit prevents those operations from accidentally
        validating or publishing against whichever GUI document happens to be
        active.
        """

        if doc is None:
            return hashlib.sha256(b"no-document").hexdigest()
        booked_transaction = getattr(doc, "getBookedTransactionID", None)
        payload = {
            "document_uid": str(getattr(doc, "Uid", "") or ""),
            "object_count": (
                int(object_count)
                if object_count is not None
                else len(getattr(doc, "Objects", []) or [])
            ),
            "undo_count": int(getattr(doc, "UndoCount", 0) or 0),
            "redo_count": int(getattr(doc, "RedoCount", 0) or 0),
            "booked_transaction_id": (
                int(booked_transaction()) if callable(booked_transaction) else 0
            ),
            "has_pending_transaction": bool(
                getattr(doc, "HasPendingTransaction", False)
            ),
            "recomputing": bool(getattr(doc, "Recomputing", False)),
            "recompute_pending": bool(getattr(doc, "RecomputePending", False)),
            "recompute_generation": (
                native_diagnostics.get("generation")
                if isinstance(native_diagnostics, dict)
                else None
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def note_provider_tool_targets(
        self,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Remember exact live objects referenced or returned by a successful call."""
        if not result.get("ok"):
            return
        doc = self._active_document()
        if doc is None:
            return
        document_uid = str(getattr(doc, "Uid", "") or "") or None
        if document_uid != self._provider_working_document_uid:
            self._provider_working_object_names = []
            self._provider_working_document_uid = document_uid
        candidates: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key in {
                        "name",
                        "object",
                        "body",
                        "body_name",
                        "sketch",
                        "sketch_name",
                        "feature",
                        "feature_name",
                        "profile",
                        "profile_name",
                        "object_name",
                        "object_names",
                    }:
                        collect(item)

        collect(arguments)
        collect(result)
        resolved: list[str] = []
        for value in candidates:
            obj = doc.getObject(value)
            if obj is None:
                matches = [
                    item
                    for item in doc.Objects
                    if str(getattr(item, "Label", "") or "") == value
                ]
                obj = matches[0] if len(matches) == 1 else None
            if obj is not None and obj.Name not in resolved:
                resolved.append(obj.Name)
        combined = resolved + self._provider_working_object_names
        self._provider_working_object_names = list(dict.fromkeys(combined))[:12]

    def provider_working_set(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {"target_count": 0, "targets": [], "body_headers": []}
        document_uid = str(getattr(doc, "Uid", "") or "") or None
        if document_uid != self._provider_working_document_uid:
            self._provider_working_object_names = []
            self._provider_working_document_uid = document_uid
        names = list(self._provider_working_object_names)
        try:
            for selected in self.selection_summary().get("selection") or []:
                name = str(selected.get("object") or "")
                if name and name not in names:
                    names.insert(0, name)
        except Exception:
            pass
        task = self.task_panel_summary()
        sketch_name = str(task.get("active_sketch") or "")
        if sketch_name and sketch_name not in names:
            names.insert(0, sketch_name)
        bodies = self._partdesign_bodies()
        if not names and len(bodies) == 1:
            names.append(bodies[0].Name)

        expanded_names: list[str] = []
        for name in names:
            obj = doc.getObject(name)
            if obj is None:
                continue
            if obj.Name not in expanded_names:
                expanded_names.append(obj.Name)
            body = self._partdesign_body_for_feature(obj)
            if body is not None and body.Name not in expanded_names:
                expanded_names.append(body.Name)
        targets = [
            self._document_object_summary(doc.getObject(name))
            for name in expanded_names[:12]
            if doc.getObject(name) is not None
        ]
        body_headers = [self._partdesign_body_header(body) for body in bodies[:80]]
        return {
            "target_count": len(targets),
            "targets": targets,
            "body_count": len(bodies),
            "body_headers": body_headers,
            "body_headers_truncated": len(bodies) > len(body_headers),
        }

    def provider_partdesign_summary(self) -> dict[str, Any]:
        working = self.provider_working_set()
        target_body_names: list[str] = []
        doc = self._active_document()
        if doc is not None:
            for target in working.get("targets") or []:
                obj = doc.getObject(str(target.get("name") or ""))
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    body = obj
                else:
                    body = self._partdesign_body_for_feature(obj)
                if body is not None and body.Name not in target_body_names:
                    target_body_names.append(body.Name)
        return {
            "document": getattr(doc, "Name", None) if doc else None,
            "body_count": len(self._partdesign_bodies()),
            "body_headers": working.get("body_headers") or [],
            "target_bodies": [
                self._partdesign_body_summary(self._get_partdesign_body(name))
                for name in target_body_names
                if self._get_partdesign_body(name) is not None
            ],
        }

    def _active_document(self):
        try:
            import FreeCAD as App

            return App.ActiveDocument
        except Exception:
            return None

    def _active_document_uid(self) -> str | None:
        doc = self._active_document()
        if doc is None:
            return None
        uid = str(getattr(doc, "Uid", "") or "").strip()
        return uid or None

    def document_persistence_state(self) -> dict[str, Any]:
        """Return whether the active document has a durable file identity."""
        doc = self._active_document()
        if doc is None:
            return {
                "enabled": False,
                "turn_enabled": False,
                "authoring_mode_required": False,
                "reason": "no_document",
                "document": None,
                "file_path": None,
                "message": "Create and save a VibeCAD document to enable VibeCAD.",
            }
        file_path = str(getattr(doc, "FileName", "") or "").strip()
        if not file_path:
            return {
                "enabled": False,
                "reason": "document_not_saved",
                "document": str(getattr(doc, "Name", "") or ""),
                "file_path": None,
                "message": "Save this VibeCAD document to enable VibeCAD.",
            }
        return {
            "enabled": True,
            "reason": "ready",
            "document": str(getattr(doc, "Name", "") or ""),
            "file_path": file_path,
            "message": "",
        }

    def assistant_document_state(self) -> dict[str, Any]:
        """Return whether chat may operate on the active document.

        Saving is not a prerequisite: an unsaved document receives an isolated
        temporary project scope that is relocated on first save.
        """

        doc = self._active_document()
        if doc is None:
            return {
                "enabled": False,
                "turn_enabled": False,
                "authoring_mode_required": False,
                "reason": "no_document",
                "document": None,
                "document_uid": None,
                "file_path": None,
                "saved": False,
                "message": "Create or open a document to use VibeCAD.",
            }
        uid = str(getattr(doc, "Uid", "") or "").strip()
        if not uid:
            return {
                "enabled": False,
                "turn_enabled": False,
                "authoring_mode_required": False,
                "reason": "document_identity_unavailable",
                "document": str(getattr(doc, "Name", "") or ""),
                "document_uid": None,
                "file_path": None,
                "saved": False,
                "message": "The active document has no stable identity.",
            }
        file_path = str(getattr(doc, "FileName", "") or "").strip()
        mode_required = self.new_document_authoring_mode_required(uid)
        return {
            "enabled": True,
            "turn_enabled": not mode_required,
            "authoring_mode_required": mode_required,
            "reason": "authoring_mode_required" if mode_required else "ready",
            "document": str(getattr(doc, "Name", "") or ""),
            "document_uid": uid,
            "file_path": file_path or None,
            "saved": bool(file_path),
            "message": (
                "Choose Native or VibeScript to begin."
                if mode_required
                else ""
            ),
        }

    @staticmethod
    def _short_value(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        text = str(value)
        return text if len(text) <= 160 else text[:157] + "..."

    @staticmethod
    def _object_summary(obj: Any) -> dict[str, Any]:
        summary = {
            "name": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": obj.TypeId,
        }
        role = str(getattr(obj, "VibeCADScriptedRole", "") or "")
        if role:
            summary["scripted_role"] = role
            summary["scripted_engine"] = str(
                getattr(obj, "VibeCADScriptedEngine", "") or ""
            )
            summary["scripted_model_id"] = str(
                getattr(obj, "VibeCADScriptedModelId", "") or ""
            )
            output_key = str(
                getattr(obj, "VibeCADScriptedOutputKey", "") or ""
            )
            if output_key:
                summary["published_output_key"] = output_key
        return summary

    @staticmethod
    def _is_private_scripted_object(obj: Any) -> bool:
        return (
            str(getattr(obj, "VibeCADScriptedRole", "") or "")
            in _PRIVATE_SCRIPTED_ROLES
        )

    @staticmethod
    def _linked_object_summary(obj: Any) -> dict[str, Any] | None:
        if obj is None or not hasattr(obj, "Name"):
            return None
        return {
            "name": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": getattr(obj, "TypeId", type(obj).__name__),
        }

    @staticmethod
    def _shape_topology_summary(shape: Any) -> dict[str, Any]:
        """Return cheap shape identity/topology without exact mass properties."""
        summary: dict[str, Any] = {}
        try:
            summary["is_null"] = bool(shape.isNull())
        except Exception:
            return summary
        if summary["is_null"]:
            summary.update(
                {
                    "shape_hash": None,
                    "solids": 0,
                    "faces": 0,
                    "edges": 0,
                    "vertices": 0,
                }
            )
            return summary
        try:
            summary["shape_hash"] = int(shape.hashCode())
        except Exception:
            pass
        for name, attribute in (
            ("solids", "Solids"),
            ("faces", "Faces"),
            ("edges", "Edges"),
            ("vertices", "Vertexes"),
        ):
            try:
                summary[name] = len(getattr(shape, attribute, []) or [])
            except Exception:
                continue
        return summary

    @classmethod
    def _document_object_summary(cls, obj: Any) -> dict[str, Any]:
        item = cls._object_summary(obj)
        placement = cls._placement_summary(getattr(obj, "Placement", None))
        if placement:
            item["placement"] = placement
        for property_name in ("Base", "Tool", "Source", "BaseFeature"):
            try:
                value = getattr(obj, property_name, None)
            except Exception:
                continue
            if isinstance(value, (list, tuple)):
                linked = [
                    summary
                    for summary in (
                        cls._linked_object_summary(child) for child in value[:20]
                    )
                    if summary
                ]
                if linked:
                    item[property_name.lower()] = linked
                continue
            linked = cls._linked_object_summary(value)
            if linked:
                item[property_name.lower()] = linked
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = cls._shape_topology_summary(shape)
                bound_box = cls._bound_box_summary(getattr(shape, "BoundBox", None))
                if bound_box:
                    item["bound_box"] = bound_box
            except Exception:
                pass
        material = getattr(obj, "ShapeMaterial", None)
        if material is not None:
            material_summary: dict[str, Any] = {
                "name": cls._short_value(getattr(material, "Name", "")),
                "uuid": cls._short_value(getattr(material, "UUID", "")),
            }
            try:
                if material.hasAppearanceProperty("DiffuseColor"):
                    material_summary["diffuse_color"] = cls._short_value(
                        material.getAppearanceValue("DiffuseColor")
                    )
            except Exception:
                pass
            try:
                if material.hasAppearanceProperty("Transparency"):
                    material_summary["transparency"] = float(
                        material.getAppearanceValue("Transparency")
                    )
            except Exception:
                pass
            item["material"] = material_summary
        return item

    @staticmethod
    def _bounded_items(
        items: list[Any], limit: int
    ) -> tuple[list[Any], dict[str, Any]]:
        safe_limit = max(0, int(limit))
        total = len(items)
        visible = items[:safe_limit]
        return visible, {
            "total": total,
            "limit": safe_limit,
            "returned": len(visible),
            "truncated": total > len(visible),
            "omitted": max(0, total - len(visible)),
        }

    def selection_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            selection_api = Gui.Selection
        except Exception:
            return {"selection": []}

        selection = []
        try:
            selected_items = selection_api.getSelectionEx()
        except Exception:
            selected_items = []
        for item in selected_items:
            try:
                obj = item.Object
                subelements = list(item.SubElementNames)
                selection.append(
                    {
                        "object": obj.Name,
                        "label": getattr(obj, "Label", obj.Name),
                        "type": obj.TypeId,
                        "subelement_count": len(subelements),
                        "subelements": subelements[:24],
                        "subelements_truncated": len(subelements) > 24,
                    }
                )
            except Exception:
                continue
        return {"selection": selection}

    def view_state(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui

            view = Gui.ActiveDocument.ActiveView
            width, height = view.getSize()
            return {
                "size": [width, height],
                "camera_type": view.getCameraType(),
                "workbench": self.active_workbench_name(),
            }
        except Exception:
            return {
                "size": None,
                "camera_type": None,
                "workbench": self.active_workbench_name(),
            }

    def task_panel_summary(self) -> dict[str, Any]:
        try:
            import FreeCADGui as Gui
        except Exception as exc:
            return {"available": False, "reason": str(exc), "widgets": []}

        try:
            active_dialog = bool(Gui.Control.activeDialog())
            result: dict[str, Any] = {
                "available": True,
                "active_dialog": active_dialog,
                "widget_count": 0,
                "widgets": [],
            }
            edit_state = active_edit_state(getattr(Gui, "ActiveDocument", None))
            edit_object = edit_state.document_object
            if edit_state.active:
                result["edit_mode"] = True
                if edit_object is not None:
                    result["edit_object"] = self._object_summary(edit_object)
                    if getattr(edit_object, "TypeId", "") == "Sketcher::SketchObject":
                        result["active_sketch"] = getattr(edit_object, "Name", "")
                        result["profile_status"] = self._sketch_profile_status(
                            edit_object
                        )
                else:
                    result["edit_state_reason"] = (
                        edit_state.error
                        or "The active GUI view provider has no document-object binding."
                    )
            else:
                result["edit_mode"] = False
                if edit_state.error:
                    result["edit_state_reason"] = edit_state.error
            return result
        except Exception as exc:
            return {"available": False, "reason": str(exc), "widgets": []}

    def capture_view_screenshot(
        self,
        camera: dict[str, Any] | None = None,
        frame: str = "auto",
        object_names: list[str] | None = None,
        sketch_annotations: str = "clean",
        page_name: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "camera": {"mode": "auto"} if camera is None else camera,
            "frame": frame,
            "sketch_annotations": sketch_annotations,
        }
        if object_names is not None:
            arguments["object_names"] = object_names
        if page_name is not None:
            arguments["page_name"] = page_name
        return self._registry.call("core.capture_view_screenshot", **arguments)

    def set_view(
        self,
        camera: dict[str, Any] | None = None,
        frame: str = "none",
        object_names: list[str] | None = None,
        zoom_steps: int = 0,
        sketch_annotations: str = "unchanged",
        show_objects: list[str] | None = None,
        hide_objects: list[str] | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "camera": {"mode": "unchanged"} if camera is None else camera,
            "frame": frame,
            "zoom_steps": zoom_steps,
            "sketch_annotations": sketch_annotations,
        }
        for key, value in (
            ("object_names", object_names),
            ("show_objects", show_objects),
            ("hide_objects", hide_objects),
        ):
            if value is not None:
                arguments[key] = value
        return self._registry.call("core.set_view", **arguments)

    @staticmethod
    def _screenshot_visual_observation(
        path: Path,
        *,
        background_path: Path | None = None,
    ) -> dict[str, Any]:
        try:
            try:
                from PySide import QtGui
            except Exception:
                from PySide6 import QtGui
            image = QtGui.QImage(str(path))
            if image.isNull():
                return {
                    "available": False,
                    "error": "Screenshot image could not be loaded.",
                }
            width = int(image.width())
            height = int(image.height())
            if width <= 0 or height <= 0:
                return {"available": False, "error": "Screenshot image has no pixels."}

            background_image = None
            if background_path is not None:
                background_image = QtGui.QImage(str(background_path))
                if background_image.isNull():
                    return {
                        "available": False,
                        "error": "Empty-view reference image could not be loaded.",
                    }
                if (
                    int(background_image.width()) != width
                    or int(background_image.height()) != height
                ):
                    return {
                        "available": False,
                        "error": "Empty-view reference dimensions do not match the screenshot.",
                    }

            corner_points = [
                (0, 0),
                (0, max(0, height - 1)),
                (max(0, width - 1), max(0, height - 1)),
            ]
            corners = []
            for x, y in corner_points:
                color = QtGui.QColor(image.pixel(x, y))
                corners.append((color.red(), color.green(), color.blue()))
            background = tuple(
                int(sum(color[index] for color in corners) / len(corners))
                for index in range(3)
            )

            x_steps = min(96, width)
            y_steps = min(96, height)
            sampled = 0
            foreground = 0
            foreground_grid = [[False for _ in range(x_steps)] for _ in range(y_steps)]
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            red_total = 0
            green_total = 0
            blue_total = 0
            threshold = 38
            for y_index in range(y_steps):
                y = int(round(y_index * (height - 1) / max(1, y_steps - 1)))
                for x_index in range(x_steps):
                    # The navigation cube is a viewport overlay, not model
                    # evidence. It occupies this same normalized corner in the
                    # framebuffer and otherwise makes an empty capture look
                    # non-empty.
                    if x_index >= int(0.79 * x_steps) and y_index <= int(0.26 * y_steps):
                        continue
                    x = int(round(x_index * (width - 1) / max(1, x_steps - 1)))
                    color = QtGui.QColor(image.pixel(x, y))
                    red = color.red()
                    green = color.green()
                    blue = color.blue()
                    red_total += red
                    green_total += green
                    blue_total += blue
                    sampled += 1
                    if background_image is not None:
                        reference = QtGui.QColor(background_image.pixel(x, y))
                        distance = (
                            abs(red - reference.red())
                            + abs(green - reference.green())
                            + abs(blue - reference.blue())
                        )
                    else:
                        distance = (
                            abs(red - background[0])
                            + abs(green - background[1])
                            + abs(blue - background[2])
                        )
                    if distance > threshold:
                        foreground += 1
                        foreground_grid[y_index][x_index] = True
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)

            foreground_ratio = float(foreground / sampled) if sampled else 0.0
            bbox = None
            center_offset = None
            if foreground:
                bbox = [int(min_x), int(min_y), int(max_x), int(max_y)]
                bbox_center_x = (min_x + max_x) / 2.0
                bbox_center_y = (min_y + max_y) / 2.0
                center_offset = [
                    round((bbox_center_x - width / 2.0) / max(1.0, width / 2.0), 3),
                    round((bbox_center_y - height / 2.0) / max(1.0, height / 2.0), 3),
                ]
            bbox_coverage = 0.0
            if bbox:
                bbox_width = max(0, bbox[2] - bbox[0] + 1)
                bbox_height = max(0, bbox[3] - bbox[1] + 1)
                bbox_coverage = (bbox_width * bbox_height) / max(1, width * height)

            visited = [[False for _ in range(x_steps)] for _ in range(y_steps)]
            component_sizes: list[int] = []
            for grid_y in range(y_steps):
                for grid_x in range(x_steps):
                    if visited[grid_y][grid_x] or not foreground_grid[grid_y][grid_x]:
                        continue
                    stack = [(grid_x, grid_y)]
                    visited[grid_y][grid_x] = True
                    component_size = 0
                    while stack:
                        current_x, current_y = stack.pop()
                        component_size += 1
                        for next_x, next_y in (
                            (current_x - 1, current_y),
                            (current_x + 1, current_y),
                            (current_x, current_y - 1),
                            (current_x, current_y + 1),
                        ):
                            if (
                                0 <= next_x < x_steps
                                and 0 <= next_y < y_steps
                                and not visited[next_y][next_x]
                                and foreground_grid[next_y][next_x]
                            ):
                                visited[next_y][next_x] = True
                                stack.append((next_x, next_y))
                    component_sizes.append(component_size)
            component_sizes.sort(reverse=True)
            component_count = len(component_sizes)
            largest_component_ratio = (
                component_sizes[0] / max(1, foreground) if component_sizes else 0.0
            )
            average_rgb = [
                int(red_total / sampled) if sampled else 0,
                int(green_total / sampled) if sampled else 0,
                int(blue_total / sampled) if sampled else 0,
            ]
            mostly_blank = foreground_ratio < 0.003
            attention_flags = []
            if mostly_blank:
                attention_flags.append("mostly_blank")
            elif foreground_ratio < 0.01:
                attention_flags.append("tiny_visible_model")
            if center_offset and (
                abs(float(center_offset[0])) > 0.45
                or abs(float(center_offset[1])) > 0.45
            ):
                attention_flags.append("off_center_model")
            if (
                component_count > 4
                and largest_component_ratio < 0.75
                and foreground_ratio > 0.01
            ):
                attention_flags.append("fragmented_view")
            if bbox and bbox_coverage > 0.92:
                attention_flags.append("model_fills_view_edges")
            layout_summary = (
                "No visible model layout detected."
                if mostly_blank
                else (
                    f"Visible model covers {foreground_ratio:.1%} of sampled pixels, "
                    f"bbox covers {bbox_coverage:.1%} of the viewport, "
                    f"with {component_count} foreground component"
                    f"{'' if component_count == 1 else 's'}."
                )
            )
            return {
                "available": True,
                "image_size": [width, height],
                "sampled_pixels": sampled,
                "background_rgb": list(background),
                "background_comparison": (
                    "empty_view_reference"
                    if background_image is not None
                    else "corner_color"
                ),
                "average_rgb": average_rgb,
                "foreground_pixel_ratio": round(foreground_ratio, 5),
                "foreground_bbox": bbox,
                "foreground_bbox_coverage": round(bbox_coverage, 5),
                "foreground_center_offset": center_offset,
                "foreground_component_count": component_count,
                "largest_component_pixel_ratio": round(largest_component_ratio, 5),
                "attention_flags": attention_flags,
                "mostly_blank": mostly_blank,
                "layout_summary": layout_summary,
                "inspection_summary": (
                    "No visible non-background model content detected."
                    if mostly_blank
                    else "Visible non-background model content detected in the viewport screenshot."
                ),
            }
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def view_screenshot_summary(self) -> dict[str, Any]:
        if self._last_view_screenshot is None:
            return {"captured": False, "path": None}
        return dict(self._last_view_screenshot)

    def consume_view_screenshot_attachment(
        self, screenshot: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Consume exactly the captured view handed to one provider message.

        Matching by artifact path prevents an older provider snapshot from
        clearing a newer capture made while a run is active.  The caller keeps
        its detached summary, so clearing service state cannot remove the image
        from the request already being assembled.
        """

        expected = screenshot if isinstance(screenshot, dict) else {}
        current = self._last_view_screenshot
        if not isinstance(current, dict) or not expected.get("captured"):
            return {"consumed": False, "reason": "no_pending_attachment"}
        current_path = str(current.get("path") or "")
        expected_path = str(expected.get("path") or "")
        if not current_path or current_path != expected_path:
            return {
                "consumed": False,
                "reason": "attachment_changed",
                "expected_path": expected_path,
                "current_path": current_path,
            }
        self._last_view_screenshot = None
        return {"consumed": True, "path": current_path}

    def _reference_artifact_dir(self) -> Path:
        """Reference-image folder inside the per-document project directory.

        Project roots always live under the central VibeCAD data dir, so
        references are never written next to the CAD file. Without a project
        context the default path still lands inside ``vibecad_data_dir()``.
        """
        try:
            project_context = self.project_context()
        except Exception:
            project_context = {}
        root = (
            project_context.get("root") if isinstance(project_context, dict) else None
        )
        if root:
            return Path(str(root)).expanduser() / "references"
        return vibecad_data_dir() / "references"

    def _reference_state_path(self) -> Path:
        return self._reference_artifact_dir().parent / "references.json"

    @staticmethod
    def reference_state_path_for_document_file(file_path: str | Path) -> Path:
        return project_root_for_document_file(file_path) / "references.json"

    @staticmethod
    def _reference_artifact_dir_for_document_file(file_path: str | Path) -> Path:
        return project_root_for_document_file(file_path) / "references"

    @staticmethod
    def _clean_reference_entry(entry: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "id",
            "name",
            "label",
            "path",
            "size_bytes",
            "image_size",
            "downscaled",
            "format",
            "artifact_role",
            "attached_at",
            "provider_delivery",
            "visual_brief",
            "visual_brief_updated_at",
            "downscale_error",
        }
        clean = {key: entry.get(key) for key in allowed if key in entry}
        clean["id"] = str(clean.get("id") or uuid.uuid4().hex[:12])
        clean["name"] = str(clean.get("name") or clean.get("id") or "reference")
        clean["label"] = str(clean.get("label") or "").strip()
        clean["path"] = str(clean.get("path") or "").strip()
        clean["artifact_role"] = "user_reference"
        return clean

    def _load_reference_images_for_active_project(self) -> None:
        try:
            path = self._reference_state_path()
        except Exception:
            path = vibecad_data_dir() / "references.json"
        key = str(path)
        document_uid = self._active_document_uid()
        if (
            key == self._reference_cache_key
            and document_uid == self._reference_cache_document_uid
        ):
            return
        loaded: list[dict[str, Any]] = []
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw_images = (
                    data.get("reference_images", []) if isinstance(data, dict) else data
                )
                if isinstance(raw_images, list):
                    loaded = [
                        self._clean_reference_entry(item)
                        for item in raw_images
                        if isinstance(item, dict)
                    ][:MAX_REFERENCE_IMAGES]
        except Exception:
            loaded = []
        if document_uid != self._reference_cache_document_uid:
            self._pending_reference_image_ids = []
        self._reference_cache_key = key
        self._reference_cache_document_uid = document_uid
        self._reference_images = loaded

    def _write_reference_images(self) -> None:
        try:
            path = self._reference_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": "VibeCAD reference images",
                "version": 1,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reference_images": [
                    self._clean_reference_entry(item)
                    for item in self._reference_images
                    if isinstance(item, dict)
                ][-MAX_REFERENCE_IMAGES:],
            }
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            tmp.replace(path)
            self._reference_cache_key = str(path)
            self._reference_cache_document_uid = self._active_document_uid()
        except Exception as exc:
            raise RuntimeError(
                f"VibeCAD references could not be written: {exc}"
            ) from exc

    @staticmethod
    def _reference_delivery_status(entry: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            return {"available": False, "reason": "reference image path is empty"}
        path = Path(raw_path).expanduser()
        try:
            if not path.is_file():
                return {
                    "available": False,
                    "reason": f"reference image file not found: {path}",
                }
            size = int(path.stat().st_size)
        except OSError as exc:
            return {"available": False, "reason": str(exc)}
        if size <= 0:
            return {"available": False, "reason": "reference image file is empty"}
        if size > REFERENCE_IMAGE_MAX_BYTES:
            return {
                "available": False,
                "reason": (
                    f"reference image is {size} bytes; provider limit is "
                    f"{REFERENCE_IMAGE_MAX_BYTES} bytes"
                ),
                "size_bytes": size,
            }
        return {"available": True, "size_bytes": size}

    def _refresh_reference_delivery_status(self) -> None:
        for entry in self._reference_images:
            if isinstance(entry, dict):
                entry["provider_delivery"] = self._reference_delivery_status(entry)

    def attach_reference_image(
        self, source_path: str, label: str = ""
    ) -> dict[str, Any]:
        """Copy a user-supplied reference image into the project artifact store.

        Returns ``{"ok": True, "reference": {...}}`` on success or a structured
        ``{"ok": False, "error": ...}`` result; never raises for bad input.
        """
        raw = str(source_path or "").strip()
        if not raw:
            return {"ok": False, "error": "Reference image path cannot be empty."}
        self._load_reference_images_for_active_project()
        source = Path(raw).expanduser()
        suffix = source.suffix.lower()
        if suffix not in REFERENCE_IMAGE_EXTENSIONS:
            supported = ", ".join(
                sorted(ext.lstrip(".") for ext in REFERENCE_IMAGE_EXTENSIONS)
            )
            return {
                "ok": False,
                "error": (
                    f"Unsupported reference image type '{suffix or source.name}'. "
                    f"Supported formats: {supported}."
                ),
            }
        if not source.is_file():
            return {"ok": False, "error": f"Reference image not found: {source}"}
        if len(self._reference_images) >= MAX_REFERENCE_IMAGES:
            return {
                "ok": False,
                "error": (
                    f"At most {MAX_REFERENCE_IMAGES} reference images may be "
                    "attached; remove one first."
                ),
            }
        try:
            target_dir = self._reference_artifact_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            reference_id = uuid.uuid4().hex[:12]
            safe_name = _slug_filename(source.name)
            target = target_dir / f"{reference_id}-{safe_name}"
            shutil.copyfile(source, target)
            size_bytes = target.stat().st_size
        except OSError as exc:
            return {"ok": False, "error": f"Could not copy reference image: {exc}"}
        downscale = downscale_reference_image(target)
        if downscale.get("size_bytes"):
            size_bytes = int(downscale["size_bytes"])
        entry: dict[str, Any] = {
            "id": reference_id,
            "name": source.name,
            "label": str(label or "").strip(),
            "path": str(target),
            "size_bytes": size_bytes,
            "image_size": downscale.get("image_size"),
            "downscaled": bool(downscale.get("downscaled")),
            "format": suffix.lstrip("."),
            "artifact_role": "user_reference",
            "attached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if downscale.get("error"):
            entry["downscale_error"] = str(downscale.get("error"))
        entry["provider_delivery"] = self._reference_delivery_status(entry)
        self._reference_images.append(entry)
        self._pending_reference_image_ids.append(reference_id)
        self._write_reference_images()
        return {
            "ok": True,
            "reference": dict(entry),
            "count": len(self._reference_images),
        }

    def remove_reference_image(self, reference_id: str) -> dict[str, Any]:
        ident = str(reference_id or "").strip()
        if not ident:
            return {"ok": False, "error": "Reference image id cannot be empty."}
        self._load_reference_images_for_active_project()
        for index, entry in enumerate(self._reference_images):
            if entry.get("id") == ident or entry.get("name") == ident:
                removed = self._reference_images.pop(index)
                removed_id = str(removed.get("id") or "")
                self._pending_reference_image_ids = [
                    image_id
                    for image_id in self._pending_reference_image_ids
                    if image_id != removed_id
                ]
                self._write_reference_images()
                return {
                    "ok": True,
                    "removed": dict(removed),
                    "count": len(self._reference_images),
                }
        return {"ok": False, "error": f"No attached reference image matches '{ident}'."}

    def clear_reference_images(self) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        cleared = len(self._reference_images)
        self._reference_images = []
        self._pending_reference_image_ids = []
        self._write_reference_images()
        return {"ok": True, "cleared": cleared}

    def reference_images_summary(self) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        self._refresh_reference_delivery_status()
        return {
            "count": len(self._reference_images),
            "images": [dict(entry) for entry in self._reference_images],
        }

    def pending_reference_image_attachments(self) -> dict[str, Any]:
        """Return only images attached by the human since their last delivery.

        This intentionally does not load the persisted reference manifest. A UI
        attachment populates the in-memory queue, while old project images stay
        stored with the project without being resent automatically.
        """

        if self._reference_cache_document_uid != self._active_document_uid():
            return {"count": 0, "images": []}
        pending = set(self._pending_reference_image_ids)
        images = [
            dict(entry)
            for entry in self._reference_images
            if isinstance(entry, dict) and str(entry.get("id") or "") in pending
        ]
        return {"count": len(images), "images": images}

    def provider_reference_image_attachments(self) -> dict[str, Any]:
        """Return every human-attached reference for each provider turn.

        References are durable project context.  Unlike a captured viewport,
        they remain attached until the human explicitly removes or clears
        them, so every provider turn must receive the actual image again.
        """

        return self.reference_images_summary()

    def consume_reference_image_attachments(
        self, references: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Consume exactly the copied image ids without touching artifact files."""

        raw = references.get("images") if isinstance(references, dict) else None
        expected = {
            str(item.get("id") or "")
            for item in list(raw or [])
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        if not expected:
            return {"consumed": False, "ids": []}
        consumed = [
            image_id
            for image_id in self._pending_reference_image_ids
            if image_id in expected
        ]
        self._pending_reference_image_ids = [
            image_id
            for image_id in self._pending_reference_image_ids
            if image_id not in expected
        ]
        return {"consumed": bool(consumed), "ids": consumed}

    def reference_images_snapshot_for_save(self, doc: Any) -> dict[str, Any]:
        document_uid = str(getattr(doc, "Uid", "") or "").strip()
        if not document_uid:
            raise RuntimeError("FreeCAD document has no stable Uid.")
        if self._reference_cache_document_uid != document_uid:
            return {"path": "", "references": []}
        return {
            "path": str(self._reference_cache_key or ""),
            "references": [dict(item) for item in self._reference_images],
        }

    @staticmethod
    def _normalize_reference_visual_brief(raw: dict[str, Any]) -> dict[str, Any]:
        def clean_text(value: Any, limit: int = 160) -> str:
            text = str(value or "").strip()
            return text[:limit]

        def clean_list(value: Any, limit: int = 8) -> list[str]:
            if isinstance(value, str):
                items = [value]
            elif isinstance(value, list):
                items = value
            else:
                items = []
            cleaned = [clean_text(item) for item in items]
            return [item for item in cleaned if item][:limit]

        brief = {
            "object_type": clean_text(raw.get("object_type"), 80),
            "must_preserve": clean_list(raw.get("must_preserve")),
            "counts_patterns": clean_list(raw.get("counts_patterns")),
            "proportion_notes": clean_list(raw.get("proportion_notes")),
            "unknown_dimensions": clean_list(raw.get("unknown_dimensions")),
            "do_not_simplify": clean_list(raw.get("do_not_simplify")),
        }
        summary = clean_text(raw.get("summary"), 240)
        if summary:
            brief["summary"] = summary
        return {key: value for key, value in brief.items() if bool(value)}

    def update_reference_visual_brief(
        self, reference_ids: list[str] | None, brief: dict[str, Any]
    ) -> dict[str, Any]:
        self._load_reference_images_for_active_project()
        normalized = self._normalize_reference_visual_brief(brief)
        if not normalized:
            return {"ok": False, "error": "Reference brief is empty."}
        requested = {
            str(item).strip() for item in (reference_ids or []) if str(item).strip()
        }
        changed = 0
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for entry in self._reference_images:
            if not isinstance(entry, dict):
                continue
            if requested and str(entry.get("id") or "") not in requested:
                continue
            entry["visual_brief"] = dict(normalized)
            entry["visual_brief_updated_at"] = timestamp
            changed += 1
        if changed:
            self._write_reference_images()
        return {
            "ok": bool(changed),
            "updated": changed,
            "brief": dict(normalized),
        }

    def write_references_for_document_file(
        self, file_path: str | Path, references: list[dict[str, Any]]
    ) -> dict[str, Any]:
        target_dir = self._reference_artifact_dir_for_document_file(file_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, Any]] = []
        for raw_entry in references:
            if not isinstance(raw_entry, dict):
                continue
            entry = self._clean_reference_entry(raw_entry)
            source_path = Path(str(entry.get("path") or "")).expanduser()
            if source_path.is_file() and source_path.parent != target_dir:
                target_name = f"{entry['id']}-{_slug_filename(entry['name'])}"
                target_path = target_dir / target_name
                try:
                    shutil.copyfile(source_path, target_path)
                    entry["path"] = str(target_path)
                    entry["size_bytes"] = target_path.stat().st_size
                except OSError:
                    pass
            entry["provider_delivery"] = self._reference_delivery_status(entry)
            saved.append(entry)
        saved = saved[-MAX_REFERENCE_IMAGES:]
        path = self.reference_state_path_for_document_file(file_path)
        payload = {
            "format": "VibeCAD reference images",
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reference_images": saved,
        }
        tmp = path.with_name(f"{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        self._reference_images = [dict(item) for item in saved]
        self._reference_cache_key = str(path)
        self._reference_cache_document_uid = self._active_document_uid()
        return {"path": str(path), "count": len(saved), "reference_images": saved}

    def object_property_summary(self, object_name: str) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "found": False,
                "error": "No active document.",
                "object": object_name,
            }
        obj = doc.getObject(object_name)
        if obj is None:
            return {
                "found": False,
                "error": f"Object not found: {object_name}",
                "object": object_name,
            }

        properties = {}
        for property_name in getattr(obj, "PropertiesList", []):
            try:
                properties[property_name] = self._short_value(
                    getattr(obj, property_name)
                )
            except Exception as exc:
                properties[property_name] = f"<unreadable: {exc}>"
        return {
            "found": True,
            "object": self._object_summary(obj),
            "properties": properties,
        }

    def _get_document_object(self, object_name: str | None):
        doc = self._active_document()
        if doc is None or not object_name:
            return None
        return doc.getObject(str(object_name))

    def _sketch_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
        ]

    def _get_sketch(self, sketch_name: str | None = None):
        if sketch_name:
            doc = self._active_document()
            if doc is None:
                return None
            sketch = doc.getObject(str(sketch_name))
            if getattr(sketch, "TypeId", "") == "Sketcher::SketchObject":
                return sketch
            return None
        edit_object = active_edit_object()
        if getattr(edit_object, "TypeId", "") != "Sketcher::SketchObject":
            return None
        active_document = self._active_document()
        if getattr(edit_object, "Document", None) is not active_document:
            return None
        return edit_object

    @staticmethod
    def _geometry_construction_state(
        geometry: Any,
        index: int,
        sketch: Any | None = None,
    ) -> bool:
        if sketch is not None:
            return bool(sketch.getConstruction(index))
        return bool(getattr(geometry, "Construction", False))

    @staticmethod
    def _geometry_summary(
        geometry: Any,
        index: int,
        sketch: Any | None = None,
    ) -> dict[str, Any]:
        item = {
            "index": index,
            "index_handle": f"geometry:{index}",
            "type": geometry.__class__.__name__,
            "construction": VibeCADService._geometry_construction_state(
                geometry,
                index,
                sketch,
            ),
        }
        if sketch is None:
            raise RuntimeError("Sketch geometry summaries require their owning sketch.")
        facades = list(getattr(sketch, "GeometryFacadeList", []) or [])
        if index < 0 or index >= len(facades):
            raise RuntimeError(
                f"Sketch geometry {index} has no matching GeometryFacade."
            )
        facade = facades[index]
        tag = str(getattr(facade, "Tag", "") or "").strip()
        if not tag:
            raise RuntimeError(f"Sketch geometry {index} has no native stable tag.")
        item["handle"] = f"tag:{tag}"
        item["geometry_tag"] = tag
        item["geometry_id"] = int(getattr(facade, "Id"))
        start = getattr(geometry, "StartPoint", None)
        end = getattr(geometry, "EndPoint", None)
        center = getattr(geometry, "Center", None)
        radius = getattr(geometry, "Radius", None)
        major_radius = getattr(geometry, "MajorRadius", None)
        minor_radius = getattr(geometry, "MinorRadius", None)
        if start is not None:
            item["start"] = [float(start.x), float(start.y), float(start.z)]
        if end is not None:
            item["end"] = [float(end.x), float(end.y), float(end.z)]
        if center is not None:
            item["center"] = [float(center.x), float(center.y), float(center.z)]
        if radius is not None:
            item["radius"] = float(radius)
            if abs(float(radius)) <= 1e-9:
                item["degenerate"] = True
                item["degenerate_reason"] = "zero_radius"
        if major_radius is not None:
            item["major_radius"] = float(major_radius)
        if minor_radius is not None:
            item["minor_radius"] = float(minor_radius)
        poles = None
        if hasattr(geometry, "getPoles"):
            try:
                poles = list(geometry.getPoles())
            except Exception:
                poles = None
        elif hasattr(geometry, "Poles"):
            try:
                poles = list(getattr(geometry, "Poles"))
            except Exception:
                poles = None
        if poles:
            item["pole_count"] = len(poles)
            item["poles"] = [
                [float(point.x), float(point.y), float(point.z)]
                for point in list(poles)[:20]
            ]
        if hasattr(geometry, "Degree"):
            try:
                item["degree"] = int(getattr(geometry, "Degree"))
            except Exception:
                pass
        for method_name, output_key in (
            ("getKnots", "knots"),
            ("getMultiplicities", "multiplicities"),
            ("getWeights", "weights"),
        ):
            if not hasattr(geometry, method_name):
                continue
            try:
                values = list(getattr(geometry, method_name)())
            except Exception:
                continue
            item[output_key] = [float(value) for value in values[:20]]
            item[f"{output_key}_count"] = len(values)
        for method_name, output_key in (
            ("isPeriodic", "periodic"),
            ("isRational", "rational"),
        ):
            if not hasattr(geometry, method_name):
                continue
            try:
                item[output_key] = bool(getattr(geometry, method_name)())
            except Exception:
                pass
        if hasattr(geometry, "length"):
            try:
                curve_length = float(
                    geometry.length(
                        float(getattr(geometry, "FirstParameter")),
                        float(getattr(geometry, "LastParameter")),
                    )
                )
            except Exception:
                curve_length = None
            if curve_length is not None:
                item["curve_length"] = curve_length
                import Part

                if curve_length < float(Part.Precision.confusion()):
                    item["degenerate"] = True
                    item["degenerate_reason"] = "curve_length_below_freecad_tolerance"
        for attribute, output_key in (
            ("FirstParameter", "first_parameter"),
            ("LastParameter", "last_parameter"),
        ):
            if hasattr(geometry, attribute):
                try:
                    item[output_key] = float(getattr(geometry, attribute))
                except Exception:
                    pass
        if "first_parameter" in item and "last_parameter" in item:
            item["parameter_span"] = item["last_parameter"] - item["first_parameter"]
        shape_diagnostics = VibeCADService._geometry_shape_diagnostics(geometry)
        if shape_diagnostics:
            item.update(shape_diagnostics)
        return item

    @staticmethod
    def _geometry_shape_diagnostics(geometry: Any) -> dict[str, Any]:
        if not hasattr(geometry, "toShape"):
            return {}
        try:
            shape = geometry.toShape()
        except Exception as exc:
            return {"shape_diagnostics_error": str(exc)}
        if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
            return {"shape_diagnostics_error": "Geometry produced a null shape."}

        result: dict[str, Any] = {
            "actual_bounds": VibeCADService._curve_bound_box_summary(shape.BoundBox),
        }
        edges = list(getattr(shape, "Edges", []) or [])
        edge = shape if getattr(shape, "ShapeType", "") == "Edge" else None
        if edge is None and len(edges) == 1:
            edge = edges[0]
        if edge is None:
            return result

        parameters = {
            "start": float(edge.FirstParameter),
            "end": float(edge.LastParameter),
        }
        for role, parameter in parameters.items():
            try:
                tangent = edge.tangentAt(parameter)
                length = float(tangent.Length)
                if length > 1e-12:
                    result[f"{role}_tangent"] = [
                        float(tangent.x) / length,
                        float(tangent.y) / length,
                        float(tangent.z) / length,
                    ]
            except Exception as exc:
                result[f"{role}_tangent_error"] = str(exc)
            try:
                curvature = float(edge.curvatureAt(parameter))
                result[f"{role}_curvature"] = curvature
                if abs(curvature) > 1e-12:
                    result[f"{role}_curvature_radius"] = 1.0 / abs(curvature)
            except Exception as exc:
                result[f"{role}_curvature_error"] = str(exc)
        return result

    @staticmethod
    def _curve_bound_box_summary(bounds: Any) -> dict[str, Any]:
        return {
            "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
            "size": [
                float(bounds.XLength),
                float(bounds.YLength),
                float(bounds.ZLength),
            ],
            "center": [
                float(bounds.Center.x),
                float(bounds.Center.y),
                float(bounds.Center.z),
            ],
        }

    @staticmethod
    def _constraint_summary(constraint: Any, index: int) -> dict[str, Any]:
        item = {
            "index": index,
            "handle": f"constraint:{index}",
            "type": getattr(constraint, "Type", constraint.__class__.__name__),
            "name": getattr(constraint, "Name", ""),
            "driving": getattr(
                constraint, "Driving", getattr(constraint, "isDriving", None)
            ),
        }
        for attribute in (
            "First",
            "FirstPos",
            "Second",
            "SecondPos",
            "Third",
            "ThirdPos",
            "Value",
        ):
            if hasattr(constraint, attribute):
                try:
                    item[attribute.lower()] = getattr(constraint, attribute)
                except Exception:
                    continue
        return item

    def sketcher_summary(self, sketch_name: str | None = None) -> dict[str, Any]:
        sketch = self._get_sketch(sketch_name)
        sketches = self._sketch_objects()
        if sketch is None:
            return {
                "found": False,
                "requested": sketch_name,
                "sketch_count": len(sketches),
                "sketches": [self._object_summary(item) for item in sketches],
            }
        geometry = list(getattr(sketch, "Geometry", []))
        constraints = list(getattr(sketch, "Constraints", []))
        expressions = {}
        try:
            expressions = {}
            for path, expression in sketch.ExpressionEngine:
                raw_path = str(path)
                expressions[raw_path] = str(expression)
                expressions[raw_path.lstrip(".")] = str(expression)
        except Exception:
            expressions = {}
        constraint_summaries = [
            self._constraint_summary(item, index)
            for index, item in enumerate(constraints)
        ]
        for item in constraint_summaries:
            index = item["index"]
            name = item.get("name")
            expression = expressions.get(f"Constraints[{index}]") or expressions.get(
                f".Constraints[{index}]"
            )
            if name:
                expression = (
                    expressions.get(f"Constraints.{name}")
                    or expressions.get(f".Constraints.{name}")
                    or expression
                )
            if expression is not None:
                item["expression"] = expression
        geometry_summaries = [
            self._geometry_summary(item, index, sketch)
            for index, item in enumerate(geometry)
        ]
        native_geometry_diagnostics = self._sketch_native_geometry_diagnostics(
            sketch,
            geometry,
            geometry_summaries,
        )
        return {
            "found": True,
            "sketch": self._object_summary(sketch),
            "geometry_count": len(geometry),
            "constraint_count": len(constraints),
            "geometry": geometry_summaries,
            "geometry_bounds": self._combined_geometry_bounds(geometry_summaries),
            "junction_diagnostics": self._sketch_junction_diagnostics(
                geometry_summaries,
                constraint_summaries,
            ),
            "constraints": constraint_summaries,
            "native_geometry_diagnostics": native_geometry_diagnostics,
            "profile_status": self._sketch_profile_status(sketch),
        }

    @staticmethod
    def _combined_geometry_bounds(
        geometry_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bounds = [
            item.get("actual_bounds")
            for item in geometry_summaries
            if not item.get("construction")
            and isinstance(item.get("actual_bounds"), dict)
        ]
        if not bounds:
            return {"available": False}
        minimum = [
            min(float(item["min"][axis]) for item in bounds) for axis in range(3)
        ]
        maximum = [
            max(float(item["max"][axis]) for item in bounds) for axis in range(3)
        ]
        size = [maximum[axis] - minimum[axis] for axis in range(3)]
        return {
            "available": True,
            "min": minimum,
            "max": maximum,
            "size": size,
            "center": [(minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)],
            "source": "actual_curve_shapes",
        }

    @staticmethod
    def _sketch_junction_diagnostics(
        geometry_summaries: list[dict[str, Any]],
        constraint_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import itertools
        import math

        endpoint_groups: dict[tuple[float, float, float], list[dict[str, Any]]] = {}
        for geometry in geometry_summaries:
            if geometry.get("construction"):
                continue
            for role in ("start", "end"):
                point = geometry.get(role)
                tangent = geometry.get(f"{role}_tangent")
                if not isinstance(point, list) or len(point) < 3:
                    continue
                endpoint = {
                    "geometry_index": int(geometry["index"]),
                    "geometry_handle": geometry.get("handle"),
                    "geometry_type": geometry.get("type"),
                    "role": role,
                    "point": [float(value) for value in point[:3]],
                    "tangent": tangent,
                    "curvature": geometry.get(f"{role}_curvature"),
                }
                key = VibeCADService._rounded_endpoint_key(endpoint["point"])
                endpoint_groups.setdefault(key, []).append(endpoint)

        explicit_tangent_pairs: set[frozenset[int]] = set()
        explicit_coincident_pairs: set[frozenset[int]] = set()
        for constraint in constraint_summaries:
            try:
                first = int(constraint.get("first"))
                second = int(constraint.get("second"))
            except (TypeError, ValueError):
                continue
            if first < 0 or second < 0 or first == second:
                continue
            pair = frozenset((first, second))
            constraint_type = str(constraint.get("type") or "").lower()
            if constraint_type == "tangent":
                explicit_tangent_pairs.add(pair)
            elif constraint_type == "coincident":
                explicit_coincident_pairs.add(pair)

        junctions: list[dict[str, Any]] = []
        for endpoints in endpoint_groups.values():
            if len(endpoints) < 2:
                continue
            for first, second in itertools.combinations(endpoints, 2):
                if first["geometry_index"] == second["geometry_index"]:
                    continue
                pair = frozenset((first["geometry_index"], second["geometry_index"]))
                item: dict[str, Any] = {
                    "point": first["point"],
                    "first": {
                        key: first[key]
                        for key in (
                            "geometry_index",
                            "geometry_handle",
                            "geometry_type",
                            "role",
                            "curvature",
                        )
                        if first.get(key) is not None
                    },
                    "second": {
                        key: second[key]
                        for key in (
                            "geometry_index",
                            "geometry_handle",
                            "geometry_type",
                            "role",
                            "curvature",
                        )
                        if second.get(key) is not None
                    },
                    "explicit_coincident_constraint": pair in explicit_coincident_pairs,
                    "explicit_tangent_constraint": pair in explicit_tangent_pairs,
                }
                first_tangent = VibeCADService._outward_endpoint_tangent(first)
                second_tangent = VibeCADService._outward_endpoint_tangent(second)
                if first_tangent is not None and second_tangent is not None:
                    dot = sum(
                        first_tangent[axis] * second_tangent[axis] for axis in range(3)
                    )
                    included_angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
                    deviation = abs(180.0 - included_angle)
                    item["included_angle_degrees"] = included_angle
                    item["tangent_deviation_degrees"] = deviation
                    item["continuity"] = (
                        "G1"
                        if deviation <= 0.5
                        else "near_G1"
                        if deviation <= 3.0
                        else "G0"
                    )
                first_curvature = first.get("curvature")
                second_curvature = second.get("curvature")
                if first_curvature is not None and second_curvature is not None:
                    item["curvature_magnitude_delta"] = abs(
                        float(first_curvature) - float(second_curvature)
                    )
                junctions.append(item)

        return {
            "junction_count": len(junctions),
            "non_tangent_junction_count": sum(
                item.get("continuity") == "G0" for item in junctions
            ),
            "junctions": junctions,
            "tangent_tolerance_degrees": 0.5,
            "near_tangent_tolerance_degrees": 3.0,
        }

    @staticmethod
    def _outward_endpoint_tangent(endpoint: dict[str, Any]) -> list[float] | None:
        tangent = endpoint.get("tangent")
        if not isinstance(tangent, list) or len(tangent) < 3:
            return None
        multiplier = 1.0 if endpoint.get("role") == "start" else -1.0
        vector = [multiplier * float(value) for value in tangent[:3]]
        length = sum(value * value for value in vector) ** 0.5
        if length <= 1e-12:
            return None
        return [value / length for value in vector]

    @staticmethod
    def _sketch_native_geometry_diagnostics(
        sketch: Any,
        geometry: list[Any] | None = None,
        geometry_summaries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        import Part

        geometry = list(geometry or getattr(sketch, "Geometry", []) or [])
        try:
            facades = list(getattr(sketch, "GeometryFacadeList", []) or [])
        except Exception:
            facades = []
        dependent_parameters: list[dict[str, Any]] = []
        if hasattr(sketch, "getGeometryWithDependentParameters"):
            try:
                raw_dependencies = list(
                    sketch.getGeometryWithDependentParameters() or []
                )
            except Exception:
                raw_dependencies = []
            for item in raw_dependencies[:80]:
                try:
                    dependent_parameters.append(
                        {
                            "geometry_index": int(item[0]),
                            "parameter_index": int(item[1]),
                        }
                    )
                except Exception:
                    dependent_parameters.append({"raw": str(item)})
        tolerance = float(Part.Precision.confusion())
        degenerate_count = int(sketch.detectDegeneratedGeometries(tolerance))
        summaries = geometry_summaries
        if summaries is None:
            summaries = [
                VibeCADService._geometry_summary(item, index, sketch)
                for index, item in enumerate(geometry)
            ]
        visible_degenerate_geometry = [
            summary for summary in summaries if summary.get("degenerate")
        ]
        return {
            "geometry_count": len(geometry),
            "geometry_facade_count": len(facades),
            "solver_dependent_parameter_count": len(dependent_parameters),
            "solver_dependent_parameters": dependent_parameters,
            "degeneracy_tolerance": tolerance,
            "native_degenerate_geometry_count": degenerate_count,
            "visible_degenerate_geometry": visible_degenerate_geometry,
        }

    def cad_state_summary(
        self,
        native_diagnostics: dict[str, Any] | None = None,
        task_panel: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the live CAD editor state that every provider turn needs.

        This is not another model-facing tool. It is the state packet attached
        to provider context and tool results so the model does not have to
        remember whether FreeCAD is editing a sketch, whether the profile is
        closed, or why the next feature is blocked.
        """
        doc = self._active_document()
        task = task_panel if isinstance(task_panel, dict) else self.task_panel_summary()
        state: dict[str, Any] = {
            "active_workbench": self.active_workbench_name(),
            "document": {
                "name": getattr(doc, "Name", None) if doc is not None else None,
                "label": getattr(doc, "Label", getattr(doc, "Name", None))
                if doc is not None
                else None,
                "object_count": len(getattr(doc, "Objects", []) or [])
                if doc is not None
                else 0,
            },
            "edit_mode": bool(task.get("edit_mode"))
            if isinstance(task, dict)
            else False,
            "task_panel_available": bool(task.get("available"))
            if isinstance(task, dict)
            else False,
        }
        if isinstance(task, dict) and task.get("edit_object"):
            state["edit_object"] = task.get("edit_object")

        active_sketch_name = ""
        if isinstance(task, dict):
            active_sketch_name = str(task.get("active_sketch") or "").strip()
        if active_sketch_name:
            sketch = self._get_sketch(active_sketch_name)
            state["active_sketch"] = self._cad_state_sketch_summary(
                sketch,
                edit_mode=bool(state["edit_mode"]),
            )
        if native_diagnostics is not None:
            diagnostic_state = self._cad_state_native_diagnostics(native_diagnostics)
            if diagnostic_state and (
                int(diagnostic_state.get("error_count") or 0) > 0
                or diagnostic_state.get("captured") is False
            ):
                state["native_diagnostics"] = diagnostic_state
        return {
            key: value
            for key, value in state.items()
            if value not in (None, "", [], {})
        }

    def _cad_state_sketch_summary(
        self,
        sketch: Any | None,
        *,
        edit_mode: bool,
    ) -> dict[str, Any]:
        if sketch is None:
            return {
                "found": False,
                "is_open": edit_mode,
                "error": "Active edit sketch was reported by FreeCAD but not found in the document.",
            }
        profile = self._sketch_profile_status(sketch)
        native = self.sketcher_summary(getattr(sketch, "Name", None))
        debt = self._sketch_constraint_debt(
            sketch,
            geometry_summaries=native.get("geometry", []),
            native_diagnostics=native.get("native_geometry_diagnostics", {}),
        )
        owner = None
        parent_getter = getattr(sketch, "getParentGeoFeatureGroup", None)
        if callable(parent_getter):
            owner = parent_getter()
        support = []
        for item in list(getattr(sketch, "Support", []) or []):
            if isinstance(item, (tuple, list)) and item:
                source = item[0]
                subelements = item[1] if len(item) > 1 else []
                support.append(
                    {
                        "object": getattr(source, "Name", None),
                        "subelements": list(subelements)
                        if isinstance(subelements, (tuple, list))
                        else [str(subelements)],
                    }
                )
        summary: dict[str, Any] = {
            "found": True,
            "name": getattr(sketch, "Name", None),
            "label": getattr(sketch, "Label", getattr(sketch, "Name", None)),
            "is_open": edit_mode,
            "owner_body": getattr(owner, "Name", None)
            if getattr(owner, "TypeId", "") == "PartDesign::Body"
            else None,
            "map_mode": str(getattr(sketch, "MapMode", "")),
            "support": support,
            "profile_status": profile,
            "geometry": native.get("geometry", []),
            "geometry_bounds": native.get("geometry_bounds", {}),
            "junction_diagnostics": native.get("junction_diagnostics", {}),
            "constraints": native.get("constraints", []),
            "native_geometry_diagnostics": native.get(
                "native_geometry_diagnostics",
                {},
            ),
            "constraint_debt": debt,
        }
        return {
            key: value
            for key, value in summary.items()
            if value not in (None, "", [], {})
        }

    def _sketch_constraint_debt(
        self,
        sketch: Any | None,
        *,
        geometry_summaries: list[dict[str, Any]] | None = None,
        native_diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if sketch is None:
            return {"found": False}
        geometry = list(getattr(sketch, "Geometry", []) or [])
        constraints = list(getattr(sketch, "Constraints", []) or [])
        referenced_geometry: dict[int, int] = {
            index: 0 for index in range(len(geometry))
        }
        for constraint in constraints:
            for attr in ("First", "Second", "Third"):
                if not hasattr(constraint, attr):
                    continue
                try:
                    index = int(getattr(constraint, attr))
                except Exception:
                    continue
                if index >= 0 and index in referenced_geometry:
                    referenced_geometry[index] += 1
        summaries_by_index = {
            int(item["index"]): item
            for item in list(geometry_summaries or [])
            if isinstance(item, dict) and "index" in item
        }
        unconstrained = []
        for index, item in enumerate(geometry):
            if self._geometry_construction_state(item, index, sketch):
                continue
            if referenced_geometry.get(index, 0) > 0:
                continue
            summary = summaries_by_index.get(index)
            if summary is None:
                summary = self._geometry_summary(item, index, sketch)
            unconstrained.append(self._compact_geometry_debt_summary(summary))

        if native_diagnostics is None:
            native_diagnostics = self._sketch_native_geometry_diagnostics(
                sketch,
                geometry,
                geometry_summaries,
            )
        open_endpoints = self._sketch_open_endpoints(geometry, sketch)
        conflicting = self._sketch_constraint_index_list(
            sketch, "ConflictingConstraints"
        )
        redundant = self._sketch_constraint_index_list(sketch, "RedundantConstraints")
        debt = {
            "open_endpoint_count": len(open_endpoints),
            "open_endpoints": open_endpoints[:12],
            "unconstrained_geometry_count": len(unconstrained),
            "unconstrained_geometry": unconstrained[:12],
            "conflicting_constraint_indices": conflicting,
            "redundant_constraint_indices": redundant,
            "native_degenerate_geometry_count": native_diagnostics.get(
                "native_degenerate_geometry_count",
                0,
            ),
            "visible_degenerate_geometry": native_diagnostics.get(
                "visible_degenerate_geometry",
                [],
            )[:12],
        }
        return {
            key: value
            for key, value in debt.items()
            if value not in (None, "", [], {}, False)
        }

    @staticmethod
    def _compact_geometry_debt_summary(
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        result = {
            "index": summary.get("index"),
            "handle": summary.get("handle"),
            "type": summary.get("type"),
        }
        for key in ("start", "end", "center", "radius"):
            if key in summary:
                result[key] = summary[key]
        return result

    @staticmethod
    def _sketch_constraint_index_list(sketch: Any, attr: str) -> list[int]:
        try:
            values = getattr(sketch, attr)
        except Exception:
            return []
        result = []
        for value in values or []:
            try:
                result.append(int(value))
            except Exception:
                continue
        return result

    def _sketch_open_endpoints(
        self,
        geometry: list[Any],
        sketch: Any | None,
    ) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        for index, item in enumerate(geometry):
            if self._geometry_construction_state(item, index, sketch):
                continue
            for role, attr in (("start", "StartPoint"), ("end", "EndPoint")):
                point = getattr(item, attr, None)
                if point is None:
                    continue
                xyz = [float(point.x), float(point.y), float(point.z)]
                endpoints.append(
                    {
                        "geometry_index": index,
                        "geometry_handle": self._geometry_summary(
                            item,
                            index,
                            sketch,
                        )["handle"],
                        "role": role,
                        "point": xyz,
                        "key": self._rounded_endpoint_key(xyz),
                    }
                )
        counts: dict[tuple[float, float, float], int] = {}
        for endpoint in endpoints:
            key = endpoint["key"]
            counts[key] = counts.get(key, 0) + 1
        open_items = [
            {key: value for key, value in endpoint.items() if key != "key"}
            for endpoint in endpoints
            if counts.get(endpoint["key"], 0) == 1
        ]
        self._annotate_nearest_endpoint_gaps(open_items)
        return open_items

    @staticmethod
    def _rounded_endpoint_key(point: list[float]) -> tuple[float, float, float]:
        return (
            round(float(point[0]), 6),
            round(float(point[1]), 6),
            round(float(point[2]), 6),
        )

    @staticmethod
    def _annotate_nearest_endpoint_gaps(endpoints: list[dict[str, Any]]) -> None:
        for index, item in enumerate(endpoints):
            point = item.get("point")
            if not isinstance(point, list) or len(point) < 3:
                continue
            nearest_distance = None
            nearest_index = None
            for other_index, other in enumerate(endpoints):
                if other_index == index:
                    continue
                other_point = other.get("point")
                if not isinstance(other_point, list) or len(other_point) < 3:
                    continue
                dx = float(point[0]) - float(other_point[0])
                dy = float(point[1]) - float(other_point[1])
                dz = float(point[2]) - float(other_point[2])
                distance = (dx * dx + dy * dy + dz * dz) ** 0.5
                if nearest_distance is None or distance < nearest_distance:
                    nearest_distance = distance
                    nearest_index = other_index
            if nearest_distance is not None:
                item["nearest_open_endpoint_distance"] = nearest_distance
                item["nearest_open_endpoint_index"] = nearest_index

    @staticmethod
    def _cad_state_native_diagnostics(
        native_diagnostics: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(native_diagnostics, dict):
            return {}
        diagnostics = [
            dict(item)
            for item in list(native_diagnostics.get("diagnostics") or [])
            if isinstance(item, dict)
        ]
        errors = [
            item
            for item in diagnostics
            if str(item.get("severity") or "").lower() == "error"
        ]
        return {
            "captured": native_diagnostics.get("captured"),
            "generation": native_diagnostics.get("generation"),
            "error_count": len(errors),
            "latest": errors[-4:],
            "source": native_diagnostics.get("source"),
            "reason": native_diagnostics.get("reason"),
        }

    def _sketch_profile_status(self, sketch: Any | None) -> dict[str, Any]:
        if sketch is None:
            return {
                "found": False,
                "ready_for_pad": False,
                "reason": "No active sketch.",
            }
        native_getter = getattr(sketch, "getProfileDiagnostics", None)
        if not callable(native_getter):
            return {
                "found": False,
                "sketch": getattr(sketch, "Name", ""),
                "ready_for_pad": False,
                "failure_code": "NATIVE_PROFILE_DIAGNOSTICS_UNAVAILABLE",
                "reason": (
                    "This FreeCAD build does not expose native Sketcher profile "
                    "diagnostics; profile-driven features cannot be preflighted."
                ),
            }
        try:
            native = native_getter()
        except Exception as exc:
            return {
                "found": False,
                "sketch": getattr(sketch, "Name", ""),
                "ready_for_pad": False,
                "failure_code": "NATIVE_PROFILE_DIAGNOSTICS_FAILED",
                "reason": str(exc),
            }
        if not isinstance(native, dict):
            return {
                "found": False,
                "sketch": getattr(sketch, "Name", ""),
                "ready_for_pad": False,
                "failure_code": "NATIVE_PROFILE_DIAGNOSTICS_INVALID",
                "reason": "FreeCAD returned a non-object profile diagnostic result.",
            }
        geometry = list(getattr(sketch, "Geometry", []) or [])
        constraints = list(getattr(sketch, "Constraints", []) or [])
        try:
            degrees_of_freedom = int(getattr(sketch, "DoF"))
        except Exception:
            degrees_of_freedom = None
        construction_count = 0
        drawable_geometry: list[tuple[int, Any]] = []
        for index, item in enumerate(geometry):
            if self._geometry_construction_state(item, index, sketch):
                construction_count += 1
                continue
            drawable_geometry.append((index, item))

        wire_details = [
            dict(item)
            for item in list(native.get("wires") or [])
            if isinstance(item, dict)
        ]
        wire_count = int(native.get("wire_count") or 0)
        closed_wire_count = int(native.get("closed_wire_count") or 0)
        face_count = int(native.get("face_count") or 0)
        face_maker_succeeded = bool(native.get("face_maker_succeeded"))
        face_build_errors = [
            {
                "wire_index": item.get("wire_index"),
                "brep_status_codes": item.get("brep_status_codes", []),
            }
            for item in wire_details
            if item.get("closed") and not item.get("brep_valid")
        ]

        conflicting: list[int] = []
        redundant: list[int] = []
        constraint_type_counts: dict[str, int] = {}
        for constraint in constraints:
            constraint_type = str(
                getattr(constraint, "Type", constraint.__class__.__name__)
            )
            constraint_type_counts[constraint_type] = (
                constraint_type_counts.get(constraint_type, 0) + 1
            )
        for attribute, target in (
            ("ConflictingConstraints", conflicting),
            ("RedundantConstraints", redundant),
        ):
            try:
                target.extend(int(value) for value in getattr(sketch, attribute) or [])
            except Exception:
                continue

        all_wires_closed = wire_count > 0 and closed_wire_count == wire_count
        closed_profile = (
            bool(drawable_geometry)
            and all_wires_closed
            and face_maker_succeeded
            and face_count > 0
            and not conflicting
        )
        has_geometry = bool(geometry)
        fully_constrained = bool(
            has_geometry and degrees_of_freedom is not None and degrees_of_freedom == 0
        )
        under_constrained = bool(
            has_geometry and degrees_of_freedom is not None and degrees_of_freedom > 0
        )
        geometry_types = [item.__class__.__name__ for _index, item in drawable_geometry]
        hole_center_types = {"Circle"}
        ready_for_hole_centers = (
            bool(geometry_types)
            and all(
                geometry_type in hole_center_types for geometry_type in geometry_types
            )
            and not conflicting
        )
        ready_for_path = wire_count == 1 and not conflicting

        if closed_profile and fully_constrained:
            reason = (
                "The native Sketcher wires are closed, face-buildable, and fully "
                "constrained."
            )
        elif closed_profile:
            if degrees_of_freedom is None:
                reason = (
                    "The native Sketcher wires are closed and face-buildable; "
                    "constraint completeness is unavailable."
                )
            else:
                reason = (
                    "The native Sketcher wires are closed and face-buildable. "
                    f"The sketch remains under-constrained ({degrees_of_freedom} DoF), "
                    "which is permitted for features but should reflect intentional dimensions."
                )
        elif conflicting:
            reason = (
                "The Sketcher solver reports conflicting constraints: "
                + ", ".join(str(index) for index in conflicting)
            )
        elif not drawable_geometry:
            reason = "The sketch has no non-construction geometry."
        elif not wire_count:
            reason = (
                "FreeCAD produced no native wire from the non-construction geometry. "
                "Repair degenerate, unsupported, or disconnected geometry."
            )
        elif not all_wires_closed:
            reason = f"{wire_count - closed_wire_count} of {wire_count} native wires are open."
        else:
            reason = (
                "FreeCAD's native FaceMaker did not produce a face from the closed "
                f"wires (status={native.get('face_maker_status')})."
            )
        return {
            "found": True,
            "sketch": getattr(sketch, "Name", ""),
            "sketch_label": getattr(sketch, "Label", getattr(sketch, "Name", "")),
            "geometry_count": len(geometry),
            "constraint_count": len(constraints),
            "degrees_of_freedom": degrees_of_freedom,
            "constraint_state": (
                "empty"
                if not has_geometry
                else "fully_constrained"
                if fully_constrained
                else "under_constrained"
                if under_constrained
                else "unknown"
            ),
            "fully_constrained": fully_constrained,
            "under_constrained": under_constrained,
            "construction_geometry_count": construction_count,
            "edge_count": sum(
                int(item.get("edge_count") or 0) for item in wire_details
            ),
            "wire_count": wire_count,
            "closed_wire_count": closed_wire_count,
            "open_wire_count": wire_count - closed_wire_count,
            "wires": wire_details,
            "faces": native.get("faces", []),
            "face_count": face_count,
            "face_maker_status": native.get("face_maker_status"),
            "face_maker_succeeded": face_maker_succeeded,
            "support_plane": native.get("support_plane"),
            "face_build_errors": face_build_errors,
            "conflicting_constraint_indices": conflicting,
            "redundant_constraint_indices": redundant,
            "constraint_type_counts": constraint_type_counts,
            "block_constraint_count": int(constraint_type_counts.get("Block", 0)),
            "closed_edge_loop": all_wires_closed,
            "closed_profile": closed_profile,
            "ready_for_closed_profile_feature": closed_profile,
            "ready_for_pad": closed_profile,
            "ready_for_pocket": closed_profile,
            "ready_for_revolve": closed_profile,
            "ready_for_loft_section": closed_profile,
            "ready_for_hole_centers": ready_for_hole_centers,
            "ready_for_path": ready_for_path,
            "ready_for_layout": bool(drawable_geometry) and not conflicting,
            "geometry_types": geometry_types,
            "native_profile_diagnostics": native,
            "reason": reason,
        }

    @staticmethod
    def _cell_name(column_index: int, row: int) -> str:
        value = int(column_index)
        column = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            column = chr(ord("A") + remainder) + column
        return f"{column}{row}"

    @staticmethod
    def _valid_cell(cell: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,5}", cell.upper()))

    def _get_spreadsheet(self, sheet_name: str | None = None):
        sheets = [
            obj
            for obj in (
                self._active_document().Objects if self._active_document() else []
            )
            if getattr(obj, "TypeId", "") == "Spreadsheet::Sheet"
        ]
        if sheet_name:
            for sheet in sheets:
                if (
                    sheet.Name == sheet_name
                    or getattr(sheet, "Label", None) == sheet_name
                ):
                    return sheet
            return None
        return sheets[0] if sheets else None

    def _spreadsheet_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Spreadsheet::Sheet"
        ]

    def spreadsheet_summary(
        self,
        sheet_name: str | None = None,
        max_columns: int = 8,
        max_rows: int = 20,
    ) -> dict[str, Any]:
        sheet = self._get_spreadsheet(sheet_name)
        sheets = self._spreadsheet_objects()
        if sheet is None:
            return {
                "found": False,
                "requested": sheet_name,
                "sheet_count": len(sheets),
                "sheets": [self._object_summary(item) for item in sheets],
            }

        safe_columns = max(1, min(int(max_columns), 26))
        safe_rows = max(1, min(int(max_rows), 200))
        cells = []
        for column_index in range(1, safe_columns + 1):
            for row in range(1, safe_rows + 1):
                cell = self._cell_name(column_index, row)
                try:
                    contents = sheet.getContents(cell)
                except Exception:
                    contents = ""
                if contents in ("", None):
                    continue
                try:
                    value = sheet.get(cell)
                except Exception as exc:
                    value = f"<error: {exc}>"
                cells.append(
                    {
                        "cell": cell,
                        "contents": self._short_value(contents),
                        "value": self._short_value(value),
                    }
                )
        return {
            "found": True,
            "sheet": self._object_summary(sheet),
            "scanned_columns": safe_columns,
            "scanned_rows": safe_rows,
            "non_empty_count": len(cells),
            "cells": cells,
        }

    def _draft_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_draft_object(obj)]

    @staticmethod
    def _is_draft_object(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        proxy_type = getattr(getattr(obj, "Proxy", None), "Type", "")
        draft_proxy_types = {
            "Array",
            "BSpline",
            "BezCurve",
            "Circle",
            "Clone",
            "Dimension",
            "Ellipse",
            "Facebinder",
            "Label",
            "Line",
            "PathArray",
            "Point",
            "Polygon",
            "Rectangle",
            "ShapeString",
            "Text",
            "Wire",
        }
        return (
            type_id.startswith("Part::Part2DObject")
            or (type_id == "Part::FeaturePython" and proxy_type in draft_proxy_types)
            or proxy_type.startswith("Draft")
        )

    @staticmethod
    def _vector_summary(value: Any) -> list[float] | None:
        if value is None:
            return None
        return [float(value.x), float(value.y), float(value.z)]

    def _draft_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        item["proxy_type"] = getattr(getattr(obj, "Proxy", None), "Type", None)
        for property_name in ("Start", "End", "Length", "Height", "Radius"):
            if hasattr(obj, property_name):
                try:
                    value = getattr(obj, property_name)
                    if (
                        hasattr(value, "x")
                        and hasattr(value, "y")
                        and hasattr(value, "z")
                    ):
                        item[property_name.lower()] = self._vector_summary(value)
                    else:
                        item[property_name.lower()] = self._short_value(value)
                except Exception:
                    continue
        return item

    def draft_summary(self) -> dict[str, Any]:
        objects = [self._draft_object_summary(obj) for obj in self._draft_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects,
        }

    def _partdesign_bodies(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "PartDesign::Body"
        ]

    def _get_partdesign_body(self, body_name: str | None = None):
        if not str(body_name or "").strip():
            return None
        doc = self._active_document()
        if doc is None:
            return None
        body = doc.getObject(str(body_name))
        return body if getattr(body, "TypeId", "") == "PartDesign::Body" else None

    @staticmethod
    def _partdesign_origin_feature(body: Any, role: str):
        origin = getattr(body, "Origin", None)
        for item in list(getattr(origin, "OriginFeatures", []) or []):
            if (
                getattr(item, "Role", "") == role
                or getattr(item, "Name", "") == role
                or getattr(item, "Label", "").replace("-", "_") == role
            ):
                return item
        return None

    def _partdesign_body_for_feature(self, feature: Any):
        if feature is None:
            return None
        parent_getter = getattr(feature, "getParentGeoFeatureGroup", None)
        if callable(parent_getter):
            parent = parent_getter()
            if getattr(parent, "TypeId", "") == "PartDesign::Body":
                return parent
        owners = [
            candidate
            for candidate in self._partdesign_bodies()
            if feature in list(getattr(candidate, "Group", []) or [])
        ]
        return owners[0] if len(owners) == 1 else None

    def _partdesign_body_summary(self, body: Any) -> dict[str, Any]:
        tip = getattr(body, "Tip", None)
        features = [
            self._partdesign_feature_summary(item)
            for item in list(getattr(body, "Group", []))
        ]
        item = self._object_summary(body)
        item["feature_count"] = len(getattr(body, "Group", []))
        item["features"] = features
        item["tip"] = self._object_summary(tip) if tip else None
        base_feature = getattr(body, "BaseFeature", None)
        item["base_feature"] = (
            self._object_summary(base_feature) if base_feature else None
        )
        return item

    def _partdesign_body_header(self, body: Any) -> dict[str, Any]:
        tip = getattr(body, "Tip", None)
        group = list(getattr(body, "Group", []) or [])
        return {
            **self._object_summary(body),
            "feature_count": len(group),
            "tip": self._object_summary(tip) if tip else None,
            "recent_features": [self._object_summary(item) for item in group[-8:]],
        }

    def _partdesign_feature_summary(
        self,
        feature: Any,
    ) -> dict[str, Any]:
        item = self._document_object_summary(feature)
        state = []
        try:
            state = [str(value) for value in list(getattr(feature, "State", []) or [])]
        except Exception:
            state = []
        if state:
            item["state"] = state
        for property_name in (
            "Profile",
            "BaseFeature",
            "OriginalFeature",
            "Tool",
            "Base",
        ):
            linked = self._linked_object_summary(getattr(feature, property_name, None))
            if linked:
                item[property_name.lower()] = linked
        return item

    def partdesign_summary(self, body_name: str | None = None) -> dict[str, Any]:
        bodies = self._partdesign_bodies()
        selected = self._get_partdesign_body(body_name)
        return {
            "document": self._active_document().Name
            if self._active_document()
            else None,
            "body_count": len(bodies),
            "requested": body_name,
            "selected": self._partdesign_body_summary(selected) if selected else None,
            "bodies": [self._partdesign_body_summary(body) for body in bodies],
        }

    def _techdraw_pages(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        pages = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("TechDraw::DrawPage"):
                    pages.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "TechDraw::DrawPage":
                    pages.append(obj)
        return pages

    def _get_techdraw_page(self, page_name: str | None = None):
        pages = self._techdraw_pages()
        if page_name:
            for page in pages:
                if page.Name == page_name or getattr(page, "Label", None) == page_name:
                    return page
            return None
        return pages[0] if pages else None

    def _techdraw_view_summary(self, view: Any) -> dict[str, Any]:
        item = self._object_summary(view)
        for property_name in ("X", "Y", "Scale"):
            if hasattr(view, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(view, property_name)
                    )
                except Exception:
                    continue
        sources = []
        for source in list(getattr(view, "Source", []) or [])[:20]:
            sources.append(self._object_summary(source))
        item["source_count"] = len(getattr(view, "Source", []) or [])
        item["sources"] = sources
        return item

    def _techdraw_page_summary(self, page: Any) -> dict[str, Any]:
        item = self._object_summary(page)
        template = getattr(page, "Template", None)
        views = list(getattr(page, "Views", []) or [])
        item["template"] = self._object_summary(template) if template else None
        item["view_count"] = len(views)
        item["views"] = [self._techdraw_view_summary(view) for view in views[:50]]
        if hasattr(page, "Scale"):
            try:
                item["scale"] = self._short_value(getattr(page, "Scale"))
            except Exception:
                pass
        return item

    def techdraw_summary(self, page_name: str | None = None) -> dict[str, Any]:
        pages = self._techdraw_pages()
        selected = self._get_techdraw_page(page_name)
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "page_count": len(pages),
            "requested": page_name,
            "selected": self._techdraw_page_summary(selected) if selected else None,
            "pages": [self._techdraw_page_summary(page) for page in pages[:20]],
        }

    def _fem_analyses(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        analyses = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Fem::FemAnalysis"):
                    analyses.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "Fem::FemAnalysis":
                    analyses.append(obj)
        return analyses

    def _get_fem_analysis(self, analysis_name: str | None = None):
        analyses = self._fem_analyses()
        clean_name = str(analysis_name or "").strip()
        if not clean_name:
            return None
        return next(
            (analysis for analysis in analyses if str(analysis.Name) == clean_name),
            None,
        )

    @staticmethod
    def _fem_member_category(obj: Any) -> str:
        type_id = getattr(obj, "TypeId", "")
        if "Solver" in type_id:
            return "solver"
        if "FemMesh" in type_id or "Mesh" in type_id:
            return "mesh"
        if "Material" in type_id:
            return "material"
        if "Constraint" in type_id:
            return "constraint"
        if "Result" in type_id:
            return "result"
        return "member"

    def _fem_analysis_summary(self, analysis: Any) -> dict[str, Any]:
        members = list(getattr(analysis, "Group", []) or [])
        item = self._object_summary(analysis)
        item["member_count"] = len(members)
        item["members"] = [
            {
                **self._object_summary(member),
                "category": self._fem_member_category(member),
            }
            for member in members[:80]
        ]
        counts: dict[str, int] = {}
        for member in members:
            category = self._fem_member_category(member)
            counts[category] = counts.get(category, 0) + 1
        item["member_categories"] = counts
        return item

    def fem_summary(
        self,
        analysis_name: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        analyses = self._fem_analyses()
        selected = self._get_fem_analysis(analysis_name)
        doc = self._active_document()
        if type(offset) is not int or offset < 0:
            raise ValueError("FEM analysis offset must be a non-negative integer.")
        if type(limit) is not int or not 1 <= limit <= 80:
            raise ValueError("FEM analysis limit must be an integer from 1 through 80.")
        page = analyses[offset : offset + limit]
        return {
            "document": doc.Name if doc else None,
            "analysis_count": len(analyses),
            "returned_count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(analyses),
            "requested": analysis_name,
            "selected": self._fem_analysis_summary(selected) if selected else None,
            "analyses": [
                self._fem_analysis_summary(analysis) for analysis in page
            ],
        }

    @staticmethod
    def _is_cam_job(obj: Any) -> bool:
        proxy = getattr(obj, "Proxy", None)
        return (
            getattr(obj, "TypeId", "") == "Path::FeaturePython"
            and proxy is not None
            and proxy.__class__.__name__ == "ObjectJob"
            and proxy.__class__.__module__ == "Path.Main.Job"
        )

    def _cam_jobs(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_cam_job(obj)]

    def _get_cam_job(self, job_name: str | None = None):
        jobs = self._cam_jobs()
        if job_name:
            for job in jobs:
                if job.Name == job_name or getattr(job, "Label", None) == job_name:
                    return job
            return None
        return jobs[0] if jobs else None

    def _cam_group_summary(self, group: Any) -> dict[str, Any] | None:
        if group is None:
            return None
        objects = list(getattr(group, "Group", []) or [])
        item = self._object_summary(group)
        item["object_count"] = len(objects)
        item["objects"] = [self._object_summary(obj) for obj in objects[:80]]
        return item

    def _cam_job_summary(self, job: Any) -> dict[str, Any]:
        item = self._object_summary(job)
        item["machine"] = str(getattr(job, "Machine", "") or "") or None
        item["postprocessor"] = str(getattr(job, "PostProcessor", "") or "") or None
        for property_name in ("Model", "Operations", "Tools"):
            item[property_name.lower()] = self._cam_group_summary(
                getattr(job, property_name, None)
            )
        for property_name in ("Stock", "SetupSheet"):
            value = getattr(job, property_name, None)
            item[property_name.lower()] = self._object_summary(value) if value else None
        return item

    def cam_summary(self, job_name: str | None = None) -> dict[str, Any]:
        jobs = self._cam_jobs()
        selected = self._get_cam_job(job_name)
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "job_count": len(jobs),
            "requested": job_name,
            "selected": self._cam_job_summary(selected) if selected else None,
            "jobs": [self._cam_job_summary(job) for job in jobs[:20]],
        }

    def _assembly_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Assembly::AssemblyObject"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "") == "Assembly::AssemblyObject":
                    objects.append(obj)
        return objects

    def _get_assembly(self, assembly_name: str | None = None):
        assemblies = self._assembly_objects()
        if assembly_name:
            for assembly in assemblies:
                if (
                    assembly.Name == assembly_name
                    or getattr(assembly, "Label", None) == assembly_name
                ):
                    return assembly
            return None
        return assemblies[0] if assemblies else None

    @staticmethod
    def _is_grounded_joint(obj: Any) -> bool:
        return getattr(obj, "ObjectToGround", None) is not None

    @staticmethod
    def _assembly_joint_objects(assembly: Any) -> list[Any]:
        joints: list[Any] = []
        for child in list(getattr(assembly, "Group", []) or []):
            if getattr(child, "TypeId", "") == "Assembly::JointGroup":
                joints.extend(list(getattr(child, "Group", []) or []))
        return joints

    def _assembly_component_objects(self, assembly: Any) -> list[Any]:
        children = list(getattr(assembly, "Group", []) or [])
        joint_names = {
            getattr(joint, "Name", None)
            for joint in self._assembly_joint_objects(assembly)
        }
        linked_target_ids = {
            id(target)
            for child in children
            for target in [getattr(child, "LinkedObject", None)]
            if target is not None
        }
        resource_output_types = {
            "bom",
            "check",
            "dependency_anchor",
            "exploded_view",
            "joint",
            "measurement",
            "motion",
            "simulation",
            "solver_diagnostics",
        }
        components = []
        for child in children:
            type_id = str(getattr(child, "TypeId", "") or "")
            if type_id in {
                "Assembly::JointGroup",
                "Assembly::BomGroup",
                "Assembly::ViewGroup",
                "Assembly::SimulationGroup",
            }:
                continue
            if getattr(child, "Name", None) in joint_names:
                continue
            if id(child) in linked_target_ids:
                # FreeCAD may expose a linked definition beside its occurrence
                # when walking an Assembly group. The occurrence is the one
                # independently placed mechanism component.
                continue
            output_type = str(
                getattr(child, "VibeCADVibeScriptOutputType", "") or ""
            ).strip()
            if output_type in resource_output_types:
                continue
            components.append(child)
        return components

    def _assembly_child_counts(self, assembly: Any) -> dict[str, int]:
        counts = {
            "components": 0,
            "joints": 0,
            "grounded_count": 0,
            "joint_groups": 0,
            "bom_groups": 0,
            "view_groups": 0,
            "simulation_groups": 0,
        }
        for child in list(getattr(assembly, "Group", []) or []):
            type_id = getattr(child, "TypeId", "")
            if type_id == "Assembly::JointGroup":
                counts["joint_groups"] += 1
                for joint in list(getattr(child, "Group", []) or []):
                    if self._is_grounded_joint(joint):
                        counts["grounded_count"] += 1
                    else:
                        counts["joints"] += 1
            elif type_id == "Assembly::BomGroup":
                counts["bom_groups"] += 1
            elif type_id == "Assembly::ViewGroup":
                counts["view_groups"] += 1
            elif type_id == "Assembly::SimulationGroup":
                counts["simulation_groups"] += 1
        counts["components"] = len(self._assembly_component_objects(assembly))
        return counts

    def _assembly_component_children(self, assembly: Any) -> list[dict[str, Any]]:
        return [
            self._object_summary(child)
            for child in self._assembly_component_objects(assembly)
        ]

    @staticmethod
    def _joint_reference_summary(reference: Any) -> dict[str, Any] | None:
        if not reference:
            return None
        try:
            obj = reference[0]
            subelements = [str(sub) for sub in (reference[1] or []) if sub]
        except (TypeError, IndexError):
            return None
        return {
            "object": getattr(obj, "Name", None),
            "label": getattr(obj, "Label", None),
            "elements": subelements,
        }

    def _joint_summary(self, joint: Any) -> dict[str, Any]:
        item = self._object_summary(joint)
        if self._is_grounded_joint(joint):
            grounded_obj = getattr(joint, "ObjectToGround", None)
            item["grounded"] = True
            item["object_to_ground"] = getattr(grounded_obj, "Name", None)
            return item
        item["grounded"] = False
        item["joint_type"] = getattr(joint, "JointType", None)
        item["reference1"] = self._joint_reference_summary(
            getattr(joint, "Reference1", None)
        )
        item["reference2"] = self._joint_reference_summary(
            getattr(joint, "Reference2", None)
        )
        return item

    def _assembly_summary(self, assembly: Any) -> dict[str, Any]:
        item = self._object_summary(assembly)
        item["type_property"] = getattr(assembly, "Type", None)
        item["child_count"] = len(list(getattr(assembly, "Group", []) or []))
        item.update(self._assembly_child_counts(assembly))
        item["component_children"] = self._assembly_component_children(assembly)[:60]
        item["joint_children"] = [
            self._joint_summary(joint)
            for joint in self._assembly_joint_objects(assembly)[:40]
        ]
        item["children"] = [
            self._object_summary(child)
            for child in list(getattr(assembly, "Group", []) or [])[:40]
        ]
        return item

    def assembly_summary(self) -> dict[str, Any]:
        assemblies = self._assembly_objects()
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "assembly_count": len(assemblies),
            "assemblies": [self._assembly_summary(item) for item in assemblies[:40]],
        }

    @staticmethod
    def _is_inspection_geometry(obj: Any) -> bool:
        for type_name in ("Mesh::Feature", "Points::Feature", "Part::Feature"):
            try:
                if obj.isDerivedFrom(type_name):
                    return True
            except Exception:
                continue
        type_id = getattr(obj, "TypeId", "")
        return (
            type_id.startswith("Mesh::")
            or type_id.startswith("Points::")
            or type_id.startswith("Part::")
        )

    def _inspection_features(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "")
            in {"Inspection::Feature", "Inspection::Group"}
        ]

    def _inspection_candidates(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [obj for obj in doc.Objects if self._is_inspection_geometry(obj)]

    @staticmethod
    def _inspection_distances_count(feature: Any) -> int:
        distances = getattr(feature, "Distances", None)
        if distances is None:
            return 0
        try:
            return len(distances)
        except Exception:
            try:
                return int(distances.getSize())
            except Exception:
                return 0

    def _inspection_feature_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        if getattr(obj, "TypeId", "") == "Inspection::Group":
            group = list(getattr(obj, "Group", []) or [])
            item["feature_count"] = len(
                [
                    child
                    for child in group
                    if getattr(child, "TypeId", "") == "Inspection::Feature"
                ]
            )
            item["children"] = [self._object_summary(child) for child in group[:40]]
            return item

        actual = getattr(obj, "Actual", None)
        nominals = list(getattr(obj, "Nominals", []) or [])
        item["actual"] = self._object_summary(actual) if actual else None
        item["nominal_count"] = len(nominals)
        item["nominals"] = [self._object_summary(nominal) for nominal in nominals[:20]]
        item["search_radius"] = self._short_value(getattr(obj, "SearchRadius", None))
        item["thickness"] = self._short_value(getattr(obj, "Thickness", None))
        item["distance_count"] = self._inspection_distances_count(obj)
        return item

    def inspection_summary(self) -> dict[str, Any]:
        objects = self._inspection_features()
        candidates = self._inspection_candidates()
        groups = [
            obj for obj in objects if getattr(obj, "TypeId", "") == "Inspection::Group"
        ]
        features = [
            obj
            for obj in objects
            if getattr(obj, "TypeId", "") == "Inspection::Feature"
        ]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "group_count": len(groups),
            "feature_count": len(features),
            "candidate_count": len(candidates),
            "groups": [self._inspection_feature_summary(obj) for obj in groups[:40]],
            "features": [
                self._inspection_feature_summary(obj) for obj in features[:80]
            ],
            "candidates": [self._object_summary(obj) for obj in candidates[:80]],
        }

    def project_context(self) -> dict[str, Any]:
        return self._project_store.context()

    def project_scope_snapshot(self) -> dict[str, Any]:
        """Return document-derived project identity without artifact I/O."""

        return self._project_store.project_scope()

    def _point_artifact_project_root(
        self,
        project_root: str | Path | None = None,
    ) -> str:
        root = str(
            project_root
            if project_root is not None
            else self.project_scope_snapshot().get("root") or ""
        ).strip()
        if not root:
            raise RuntimeError(
                "Save or initialize the active VibeCAD project before managing "
                "point artifacts."
            )
        return root

    def approve_point_artifact(
        self,
        source_path: str | Path,
        *,
        label: str = "",
        project_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Approve a human-selected point file for stable-id VibeScript use."""

        from VibeCADPointArtifacts import approve_point_artifact

        root = self._point_artifact_project_root(project_root)
        artifact = approve_point_artifact(root, source_path, label=label)
        return {"ok": True, "artifact": artifact}

    def point_artifacts(
        self,
        *,
        project_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """List provider-safe point artifact metadata for the active project."""

        from VibeCADPointArtifacts import point_artifacts_summary

        root = self._point_artifact_project_root(project_root)
        return {"ok": True, **point_artifacts_summary(root)}

    def remove_point_artifact(
        self,
        artifact_id: str,
        *,
        project_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Remove one unreferenced point approval and its project-local copy."""

        from VibeCADPointArtifacts import remove_point_artifact

        root = self._point_artifact_project_root(project_root)
        return {"ok": True, **remove_point_artifact(root, artifact_id)}

    def intent_memory(self) -> dict[str, Any]:
        return self._project_store.intent_memory()

    def intent_memory_snapshot(self) -> dict[str, Any]:
        memory = self.intent_memory()
        histories = self._project_store.conversation_histories()
        pending = uncovered_turns(memory, histories)
        legacy = self._project_store.design_document()
        return {
            "enabled": self.intent_memory_enabled(),
            "memory": memory,
            "active": active_memory_context(memory),
            "uncovered_turns": pending,
            "legacy_design_markdown": (
                str(legacy.get("content") or "")
                if legacy.get("exists") and not memory.get("exists")
                else ""
            ),
        }

    def apply_intent_memory_update(self, update: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.intent_memory_snapshot()
        memory = snapshot["memory"]
        pending = snapshot["uncovered_turns"]
        known_turn_ids = {
            str(turn.get("turn_id") or "")
            for history in self._project_store.conversation_histories()
            for turn in history.get("conversation") or []
        }
        updated = apply_memory_update(
            memory,
            update,
            expected_turns=pending,
            known_turn_ids=known_turn_ids,
        )
        return self._project_store.write_intent_memory(updated)

    def intent_memory_rebuild_snapshot(self) -> dict[str, Any]:
        current = self.intent_memory()
        histories = self._project_store.conversation_histories()
        blank = empty_memory(str(current["project_id"]))
        blank["exists"] = False
        return {
            "current_revision": str(current["revision"]),
            "memory": blank,
            "uncovered_turns": uncovered_turns(blank, histories),
        }

    def apply_intent_memory_rebuild(
        self,
        update: dict[str, Any],
        *,
        expected_current_revision: str,
    ) -> dict[str, Any]:
        snapshot = self.intent_memory_rebuild_snapshot()
        if snapshot["current_revision"] != str(expected_current_revision):
            raise RuntimeError(
                "Intent Memory changed while the rebuild was running; the existing "
                "memory was preserved."
            )
        histories = self._project_store.conversation_histories()
        known_turn_ids = {
            str(turn.get("turn_id") or "")
            for history in histories
            for turn in history.get("conversation") or []
        }
        rebuilt = apply_memory_update(
            snapshot["memory"],
            update,
            expected_turns=snapshot["uncovered_turns"],
            known_turn_ids=known_turn_ids,
        )
        return self._project_store.write_intent_memory(rebuilt)

    def update_project_summary(
        self, *, title: str = "", summary: str = ""
    ) -> dict[str, Any]:
        return self._project_store.update_summary(title=title, summary=summary)

    def queue_steering_message(self, text: str, source: str = "user") -> dict[str, Any]:
        clean = str(text or "").strip()
        if not clean:
            return {"ok": False, "error": "Steering message cannot be empty."}
        self._steering_sequence += 1
        entry = {
            "id": self._steering_sequence,
            "source": str(source or "user"),
            "text": clean,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "consumed": False,
        }
        self._steering_messages.append(entry)
        self._steering_messages = self._steering_messages[-40:]
        return {"ok": True, "message": entry}

    def consume_steering_messages(self) -> list[dict[str, Any]]:
        pending = [
            item
            for item in self._steering_messages
            if isinstance(item, dict) and not item.get("consumed")
        ]
        for item in pending:
            item["consumed"] = True
            item["consumed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return [dict(item) for item in pending]

    def steering_state(self) -> dict[str, Any]:
        return {
            "queued_count": len(
                [item for item in self._steering_messages if not item.get("consumed")]
            ),
            "messages": [dict(item) for item in self._steering_messages[-20:]],
        }

    def clear_steering_messages(self) -> dict[str, Any]:
        cleared_count = len(self._steering_messages)
        self._steering_messages.clear()
        return {"ok": True, "cleared_count": cleared_count}

    def _conversation_scope(self) -> dict[str, Any]:
        """Where the active conversation lives.

        Conversations are stored alongside the project manifest in the
        per-document project folder under the central VibeCAD data dir —
        never next to the CAD file.
        """
        project = self._project_store.project_scope()
        store = self._project_store.conversation_store()
        doc_info = project.get("document") or {}
        document_name = str(doc_info.get("document") or "")
        file_path = doc_info.get("file_path")
        kind = (
            "saved_document"
            if file_path
            else "unsaved_document"
            if document_name
            else "no_document"
        )
        base = {
            "kind": kind,
            "document": document_name or None,
            "file_path": str(file_path) if file_path else None,
            "store_path": str(store.directory),
            "persistent": bool(file_path),
        }
        if not file_path and not (
            store.index_path.is_file() or store.legacy_path.is_file()
        ):
            return {
                **base,
                "path": "",
                "conversation_id": None,
                "conversation_title": DEFAULT_CONVERSATION_TITLE,
                "conversation_created_at": "",
                "conversation_updated_at": "",
                "document_saved": False,
            }

        catalog = store.catalog()
        conversation_id = str(catalog["active_conversation_id"])
        metadata = next(
            item for item in catalog["conversations"] if item["id"] == conversation_id
        )
        path = store.thread_path(conversation_id)
        common = {
            "path": str(path),
            "conversation_id": conversation_id,
            "conversation_title": str(metadata["title"]),
            "conversation_created_at": str(metadata.get("created_at") or ""),
            "conversation_updated_at": str(metadata.get("updated_at") or ""),
            "persistent": True,
        }
        if file_path:
            return {**base, **common}
        return {
            **base,
            **common,
            "document_saved": False,
        }

    def prepare_conversation_history_read(self) -> dict[str, Any]:
        """Capture the selected thread identity without reading its artifacts."""

        scope = self.project_scope_snapshot()
        project_root = str(scope.get("root") or "")
        document_uid = str(self._active_document_uid() or "")
        cache_key = str(self._conversation_cache_key or "")
        conversation_id: str | None = None
        cached_conversation: list[dict[str, Any]] | None = None

        if (
            project_root
            and document_uid == self._conversation_cache_document_uid
            and cache_key
        ):
            cached_path = Path(cache_key)
            if cached_path.parent.parent == Path(project_root):
                candidate = cached_path.stem.lower()
                if re.fullmatch(r"[0-9a-f]{32}", candidate):
                    conversation_id = candidate
                    cached_conversation = [
                        dict(item)
                        for item in self._conversation_cache
                        if isinstance(item, dict)
                    ]

        return {
            "project_root": project_root,
            "document_uid": document_uid,
            "conversation_id": conversation_id,
            "cache_key": cache_key,
            "cached_conversation": cached_conversation,
        }

    @staticmethod
    def complete_conversation_history_read(
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        """Load a captured conversation without consulting the live document."""

        project_root = str(prepared.get("project_root") or "").strip()
        if not project_root:
            return {"conversation_id": None, "conversation": []}

        store = VibeCADConversationStore(project_root)
        conversation_id = str(prepared.get("conversation_id") or "").strip()
        cached = prepared.get("cached_conversation")
        if conversation_id and isinstance(cached, list):
            return {
                "path": str(store.thread_path(conversation_id)),
                "store_path": str(store.directory),
                "conversation_id": conversation_id,
                "conversation": [
                    dict(item) for item in cached if isinstance(item, dict)
                ],
            }
        return (
            store.history(conversation_id)
            if conversation_id
            else store.active_history()
        )

    def accept_conversation_history_read(
        self,
        prepared: dict[str, Any],
        history: dict[str, Any],
    ) -> dict[str, Any]:
        """Accept an off-thread read only while the same project/thread is selected."""

        scope = self.project_scope_snapshot()
        project_root = str(scope.get("root") or "")
        document_uid = str(self._active_document_uid() or "")
        if (
            project_root != str(prepared.get("project_root") or "")
            or document_uid != str(prepared.get("document_uid") or "")
        ):
            return {"accepted": False, "reason": "active_conversation_changed"}

        prepared_id = str(prepared.get("conversation_id") or "").lower()
        loaded_id = str(history.get("conversation_id") or "").lower()
        if prepared_id and loaded_id != prepared_id:
            return {"accepted": False, "reason": "active_conversation_changed"}

        current_id = ""
        cache_key = str(self._conversation_cache_key or "")
        if cache_key:
            cache_path = Path(cache_key)
            candidate = cache_path.stem.lower()
            if (
                self._conversation_cache_document_uid != document_uid
                or cache_path.parent.parent != Path(project_root)
                or re.fullmatch(r"[0-9a-f]{32}", candidate) is None
            ):
                return {"accepted": False, "reason": "active_conversation_changed"}
            current_id = candidate

        expected_id = prepared_id or loaded_id
        if (prepared_id and current_id != prepared_id) or (
            not prepared_id and current_id and current_id != expected_id
        ):
            return {"accepted": False, "reason": "active_conversation_changed"}
        self._set_conversation_cache(history)
        return {
            "accepted": True,
            "conversation_id": history.get("conversation_id"),
            "turn_count": len(history.get("conversation") or []),
        }

    def _load_conversation_for_active_document(
        self,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        scope = self._conversation_scope()
        if not scope.get("conversation_id"):
            self._conversation_cache_key = None
            self._conversation_cache_document_uid = self._active_document_uid()
            self._conversation_cache = []
            return scope, []
        key = str(scope["path"])
        document_uid = self._active_document_uid()
        if (
            key == self._conversation_cache_key
            and document_uid == self._conversation_cache_document_uid
        ):
            return scope, [dict(item) for item in self._conversation_cache]

        store = self._project_store.conversation_store()
        history = store.history(str(scope["conversation_id"]))
        loaded = [dict(item) for item in history["conversation"]]
        self._conversation_cache_key = key
        self._conversation_cache_document_uid = document_uid
        self._conversation_cache = loaded
        return scope, [dict(item) for item in self._conversation_cache]

    def _set_conversation_cache(self, history: dict[str, Any]) -> None:
        self._conversation_cache = [
            dict(item)
            for item in history.get("conversation", [])
            if isinstance(item, dict)
        ]
        self._conversation_cache_key = str(history.get("path") or "")
        self._conversation_cache_document_uid = self._active_document_uid()

    def _invalidate_conversation_cache(self) -> None:
        self._conversation_cache = []
        self._conversation_cache_key = None
        self._conversation_cache_document_uid = None

    def conversation_snapshot_for_save(self, doc: Any) -> dict[str, Any]:
        document_uid = str(getattr(doc, "Uid", "") or "").strip()
        if not document_uid:
            raise RuntimeError("FreeCAD document has no stable Uid.")
        if self._active_document_uid() != document_uid:
            return {"store_path": ""}

        store: VibeCADConversationStore | None = None
        if (
            self._conversation_cache_document_uid == document_uid
            and self._conversation_cache_key
        ):
            cached_thread_path = Path(self._conversation_cache_key)
            cached_directory = cached_thread_path.parent
            if cached_directory.is_dir():
                store = VibeCADConversationStore(cached_directory.parent)
        if store is None:
            current_file = str(getattr(doc, "FileName", "") or "").strip()
            if current_file:
                current_root = project_root_for_document_file(current_file)
                current_store = VibeCADConversationStore(current_root)
                if (
                    current_store.directory.is_dir()
                    or current_store.legacy_path.is_file()
                ):
                    store = current_store
        if store is None:
            current_store = self._project_store.conversation_store()
            if (
                not current_store.directory.is_dir()
                and not current_store.legacy_path.is_file()
            ):
                return {"store_path": ""}
            store = current_store
        catalog = store.catalog()
        return {
            "store_path": str(catalog["store_path"]),
            "active_conversation_id": catalog["active_conversation_id"],
            "conversation_count": catalog["conversation_count"],
        }

    def conversation_history(self) -> dict[str, Any]:
        scope, conversation = self._load_conversation_for_active_document()
        return {
            "path": str(scope["path"]),
            "store_path": str(scope["store_path"]),
            "scope": scope,
            "conversation_id": scope["conversation_id"],
            "title": scope["conversation_title"],
            "created_at": scope["conversation_created_at"],
            "updated_at": scope["conversation_updated_at"],
            "turn_count": len(conversation),
            "conversation": conversation,
        }

    def conversation_catalog(self) -> dict[str, Any]:
        scope = self._conversation_scope()
        if not scope.get("conversation_id"):
            return {
                "path": "",
                "store_path": scope["store_path"],
                "active_conversation_id": None,
                "conversation_count": 0,
                "conversations": [],
                "scope": scope,
            }
        catalog = self._project_store.conversation_store().catalog()
        return {**catalog, "scope": scope}

    def create_conversation(self) -> dict[str, Any]:
        state = self.assistant_document_state()
        if not state.get("enabled"):
            raise RuntimeError(str(state.get("message") or "Open a document first."))
        result = self._project_store.conversation_store().create_conversation()
        self._set_conversation_cache(result)
        return result

    def activate_conversation(self, conversation_id: str) -> dict[str, Any]:
        state = self.assistant_document_state()
        if not state.get("enabled"):
            raise RuntimeError(str(state.get("message") or "Open a document first."))
        result = self._project_store.conversation_store().activate_conversation(
            conversation_id
        )
        self._set_conversation_cache(result)
        return result

    def prepare_session_recovery(
        self,
        phase: str,
        prompt: str,
        *,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture recovery identity on the document thread without artifact I/O."""

        scope = self.project_scope_snapshot()
        project_root = str(scope.get("root") or "").strip()
        document = scope.get("document")
        document_info = document if isinstance(document, dict) else {}
        document_uid = str(
            document_info.get("uid") or self._active_document_uid() or ""
        ).strip()
        if not project_root or not document_uid:
            raise RuntimeError("Open a CAD document before saving VibeCAD recovery.")

        conversation_id: str | None = None
        cache_key = str(self._conversation_cache_key or "").strip()
        if cache_key:
            cached_path = Path(cache_key)
            candidate = cached_path.stem.lower()
            if (
                cached_path.parent.parent == Path(project_root)
                and re.fullmatch(r"[0-9a-f]{32}", candidate)
            ):
                conversation_id = candidate
        prepared = {
            "project_root": project_root,
            "phase": str(phase or "").strip().lower(),
            "prompt": str(prompt or ""),
            "document_uid": document_uid,
            "document_name": str(document_info.get("document") or "").strip(),
            "file_path": str(document_info.get("file_path") or "").strip(),
            "conversation_id": conversation_id,
        }
        clean_instance_id = str(instance_id or "").strip()
        if clean_instance_id:
            prepared["instance_id"] = clean_instance_id
        return prepared

    @staticmethod
    def persist_prepared_session_recovery(
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist one document-thread recovery capture."""

        from VibeCADSessionRecovery import SessionRecoveryStore

        project_root = str(prepared.get("project_root") or "").strip()
        if not project_root:
            raise RuntimeError("Prepared VibeCAD recovery has no project root.")
        return SessionRecoveryStore(project_root).write(prepared)

    def session_recovery(self) -> dict[str, Any]:
        """Load a recovery snapshot only for the active document and thread."""

        from VibeCADSessionRecovery import SessionRecoveryStore

        scope = self.project_scope_snapshot()
        project_root = str(scope.get("root") or "").strip()
        conversation_id: str | None = None
        cache_key = str(self._conversation_cache_key or "").strip()
        if cache_key:
            cached_path = Path(cache_key)
            candidate = cached_path.stem.lower()
            if (
                cached_path.parent.parent == Path(project_root)
                and re.fullmatch(r"[0-9a-f]{32}", candidate)
            ):
                conversation_id = candidate
        return SessionRecoveryStore(project_root).load(
            document_uid=str(self._active_document_uid() or ""),
            conversation_id=conversation_id,
        )

    def discard_session_recovery(self) -> dict[str, Any]:
        """Discard only the active project's crash-recovery snapshot."""

        from VibeCADSessionRecovery import SessionRecoveryStore

        project_root = str(self.project_scope_snapshot().get("root") or "").strip()
        if not project_root:
            return {"written": False, "discarded": False, "path": ""}
        return SessionRecoveryStore(project_root).discard()

    def relocate_conversation_store_for_document_file(
        self,
        file_path: str | Path,
        source_store_path: str | Path,
    ) -> dict[str, Any]:
        source = str(source_store_path or "").strip()
        if not source:
            raise RuntimeError(
                "No VibeCAD conversation store was captured before save."
            )
        result = VibeCADConversationStore.relocate_directory(
            source,
            project_root_for_document_file(file_path),
        )
        self._invalidate_conversation_cache()
        return result

    @staticmethod
    def discard_temporary_project_root(project_root: str | Path) -> bool:
        """Delete one validated unsaved-document artifact root."""

        root = Path(str(project_root)).expanduser().resolve()
        projects = (vibecad_data_dir() / "projects").expanduser().resolve()
        if root.parent != projects or not root.name.startswith("unsaved-"):
            raise RuntimeError(
                "Refusing to discard a project folder outside VibeCAD temporary scope."
            )
        if not root.exists():
            return False
        shutil.rmtree(root)
        return True

    @staticmethod
    def relocate_temporary_project_artifacts_for_document_file(
        project_root: str | Path,
        file_path: str | Path,
    ) -> dict[str, Any]:
        """Move non-conversation artifacts from first-save scope without overwrite."""

        source = Path(str(project_root)).expanduser().resolve()
        projects = (vibecad_data_dir() / "projects").expanduser().resolve()
        destination = project_root_for_document_file(file_path).resolve()
        if (
            source.parent != projects
            or not source.name.startswith("unsaved-")
            or destination.parent != projects
        ):
            raise RuntimeError("VibeCAD project relocation escaped its artifact scope.")
        if source == destination or not source.exists():
            return {"moved": False, "source": str(source), "path": str(destination)}

        destination.mkdir(parents=True, exist_ok=True)
        handled_separately = {
            "conversations",
            "conversation.json",
            "native-state.json",
            "project.vibecad.json",
            "references",
            "references.json",
        }

        def move_without_overwrite(source_path: Path, target_path: Path) -> None:
            if not target_path.exists():
                source_path.replace(target_path)
                return
            if source_path.is_dir() and target_path.is_dir():
                for child in list(source_path.iterdir()):
                    move_without_overwrite(child, target_path / child.name)
                source_path.rmdir()
                return
            if source_path.is_file() and target_path.is_file():
                if source_path.read_bytes() != target_path.read_bytes():
                    raise RuntimeError(
                        "Cannot merge VibeCAD project artifacts because "
                        f"{target_path.name!r} differs at the destination."
                    )
                source_path.unlink()
                return
            raise RuntimeError(
                "Cannot merge VibeCAD project artifacts with incompatible paths: "
                f"{source_path.name!r}."
            )

        moved: list[str] = []
        for child in list(source.iterdir()):
            if child.name in handled_separately:
                continue
            move_without_overwrite(child, destination / child.name)
            moved.append(child.name)
        return {
            "moved": bool(moved),
            "source": str(source),
            "path": str(destination),
            "artifacts": moved,
        }

    def discard_unsaved_document_project(self, document: Any) -> bool:
        """Discard local artifacts when an unsaved document is closed."""

        if document is None or str(getattr(document, "FileName", "") or "").strip():
            return False
        uid = str(getattr(document, "Uid", "") or "").strip()
        if not uid:
            return False
        root = project_root_for_unsaved_document(uid)
        removed = self.discard_temporary_project_root(root)
        if self._conversation_cache_document_uid == uid:
            self._invalidate_conversation_cache()
        if self._reference_cache_document_uid == uid:
            self._reference_images = []
            self._pending_reference_image_ids = []
            self._reference_cache_key = None
            self._reference_cache_document_uid = None
        return removed

    @staticmethod
    def temporary_project_root_for_document(document: Any) -> str:
        """Return an existing UID-scoped temporary root, even during first save."""

        uid = str(getattr(document, "Uid", "") or "").strip()
        if not uid:
            return ""
        root = project_root_for_unsaved_document(uid)
        return str(root) if root.exists() else ""

    def prepare_conversation_turn(
        self,
        role: str,
        content: str,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture document identity and text only; perform no artifact I/O."""

        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported conversation role: {role}")
        state = self.assistant_document_state()
        if not state.get("enabled"):
            raise RuntimeError(str(state.get("message") or "Open a document first."))
        clean_content = str(content).strip()
        if not clean_content:
            raise ValueError("Conversation content cannot be empty.")
        entry: dict[str, Any] = {
            "role": role,
            "content": clean_content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if provider:
            entry["provider"] = provider
        if metadata:
            entry["metadata"] = dict(metadata)
        requested_id = str(conversation_id or "").strip().lower()
        if requested_id and not re.fullmatch(r"[0-9a-f]{32}", requested_id):
            raise ValueError("Conversation id must be 32 lowercase hexadecimal characters.")
        if not requested_id and (
            self._conversation_cache_document_uid == self._active_document_uid()
            and self._conversation_cache_key
        ):
            cached_id = Path(self._conversation_cache_key).stem.lower()
            if re.fullmatch(r"[0-9a-f]{32}", cached_id):
                requested_id = cached_id
        return {
            "project_root": str(self.project_scope_snapshot().get("root") or ""),
            "document_uid": str(self._active_document_uid() or ""),
            "conversation_id": requested_id or None,
            "entry": entry,
        }

    @staticmethod
    def _conversation_has_equivalent_turn(
        conversation: list[dict[str, Any]], entry: dict[str, Any]
    ) -> bool:
        role = str(entry.get("role") or "")
        content = str(entry.get("content") or "").strip()
        if not conversation:
            return False
        if role == "user":
            for previous in reversed(conversation):
                previous_role = previous.get("role")
                if previous_role == "assistant":
                    break
                if (
                    previous_role == "user"
                    and str(previous.get("content", "")).strip() == content
                ):
                    return True
        latest = conversation[-1]
        return latest.get("role") == role and str(
            latest.get("content", "")
        ).strip() == content

    @staticmethod
    def persist_prepared_conversation_turn(prepared: dict[str, Any]) -> dict[str, Any]:
        """Read and atomically rewrite one conversation off the document thread."""

        project_root = str(prepared.get("project_root") or "").strip()
        entry = prepared.get("entry")
        if not project_root or not isinstance(entry, dict):
            raise RuntimeError("Prepared conversation turn is incomplete.")
        with _CONVERSATION_WRITE_LOCK:
            store = VibeCADConversationStore(project_root)
            conversation_id = str(prepared.get("conversation_id") or "").strip()
            history = (
                store.history(conversation_id)
                if conversation_id
                else store.active_history()
            )
            conversation = [dict(item) for item in history.get("conversation") or []]
            if VibeCADService._conversation_has_equivalent_turn(conversation, entry):
                return {**history, "written": False}
            conversation.append(dict(entry))
            written = store.write_conversation(
                str(history["conversation_id"]), conversation
            )
            return {**written, "written": True}

    def accept_persisted_conversation_turn(
        self,
        history: dict[str, Any],
        prepared: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish an off-thread write to the in-memory cache if still current."""

        expected_uid = str(prepared.get("document_uid") or "")
        active_uid = str(self._active_document_uid() or "")
        if expected_uid and expected_uid != active_uid:
            return {
                "accepted": False,
                "reason": "active_document_changed",
                "conversation_id": history.get("conversation_id"),
            }
        self._set_conversation_cache(history)
        return {
            "accepted": True,
            "written": bool(history.get("written")),
            "conversation_id": history.get("conversation_id"),
        }

    def recompute_diagnostics(self) -> dict[str, Any]:
        from VibeCADTransactions import recompute_diagnostic_summary

        return recompute_diagnostic_summary(self._active_document())

    def provider_tool_surface(self, workbench: str | None = None) -> dict[str, Any]:
        from VibeCADSession import provider_tool_schemas

        active = workbench or self.active_workbench_name()
        tools = provider_tool_schemas(self, active)
        return {
            "active_workbench": active,
            "tool_count": len(tools),
            "tools": tools,
        }

    def is_provider_tool_available(
        self,
        tool_name: str,
        workbench: str | None = None,
    ) -> bool:
        from VibeCADSession import is_provider_safe_tool

        return is_provider_safe_tool(
            self, tool_name, workbench or self.active_workbench_name()
        )

    def _surface_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "").startswith("Surface::")
        ]

    @staticmethod
    def _link_sub_count(value: Any) -> int:
        try:
            return len(value)
        except Exception:
            pass
        try:
            return len(value.getSubValues())
        except Exception:
            return 0

    def _surface_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = {
                    "faces": len(getattr(shape, "Faces", [])),
                    "edges": len(getattr(shape, "Edges", [])),
                    "vertices": len(getattr(shape, "Vertexes", [])),
                }
            except Exception:
                pass
        for property_name in (
            "BoundaryEdges",
            "BoundaryList",
            "NSections",
            "UnboundEdges",
            "FreeFaces",
            "Points",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._link_sub_count(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        for property_name in (
            "FillType",
            "Degree",
            "PointsOnCurve",
            "Iterations",
            "MaximumDegree",
            "MaximumSegments",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        return item

    def surface_summary(self) -> dict[str, Any]:
        objects = [self._surface_object_summary(obj) for obj in self._surface_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
            "feature_types": sorted({item["type"] for item in objects}),
        }

    @staticmethod
    def _placement_summary(value: Any) -> dict[str, Any] | None:
        try:
            base = value.Base
            rotation = value.Rotation
            return {
                "base": [float(base.x), float(base.y), float(base.z)],
                "rotation_euler": [float(item) for item in rotation.toEuler()],
            }
        except Exception:
            return None

    @staticmethod
    def _waypoint_summary(waypoint: Any, index: int) -> dict[str, Any]:
        item = {
            "index": index,
            "name": getattr(waypoint, "Name", ""),
            "type": getattr(waypoint, "Type", ""),
            "velocity": getattr(waypoint, "Velocity", None),
            "acceleration": getattr(waypoint, "Acceleration", None),
            "continuous": bool(getattr(waypoint, "Cont", False)),
        }
        placement = getattr(waypoint, "Pos", None)
        if placement is not None:
            item["placement"] = VibeCADService._placement_summary(placement)
        return item

    def _robot_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        if getattr(obj, "TypeId", "") == "Robot::RobotObject":
            item["axes"] = {}
            for axis in range(1, 7):
                property_name = f"Axis{axis}"
                try:
                    item["axes"][property_name.lower()] = float(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
            item["tcp"] = self._placement_summary(getattr(obj, "Tcp", None))
            item["base"] = self._placement_summary(getattr(obj, "Base", None))
            item["tool"] = self._placement_summary(getattr(obj, "Tool", None))
            for property_name in ("RobotVrmlFile", "RobotKinematicFile", "Error"):
                if hasattr(obj, property_name):
                    try:
                        item[property_name.lower()] = self._short_value(
                            getattr(obj, property_name)
                        )
                    except Exception:
                        continue
        if getattr(obj, "TypeId", "") == "Robot::TrajectoryObject":
            trajectory = getattr(obj, "Trajectory", None)
            waypoints = (
                list(getattr(trajectory, "Waypoints", []) or []) if trajectory else []
            )
            item["waypoint_count"] = len(waypoints)
            try:
                item["duration"] = float(getattr(trajectory, "Duration", 0.0))
            except Exception:
                pass
            try:
                item["length"] = float(getattr(trajectory, "Length", 0.0))
            except Exception:
                pass
            item["waypoints"] = [
                self._waypoint_summary(waypoint, index)
                for index, waypoint in enumerate(waypoints[:20])
            ]
        return item

    def robot_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "robot_count": 0,
                "trajectory_count": 0,
                "robots": [],
                "trajectories": [],
            }
        robots = [
            self._robot_object_summary(obj)
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Robot::RobotObject"
        ]
        trajectories = [
            self._robot_object_summary(obj)
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "Robot::TrajectoryObject"
        ]
        return {
            "document": doc.Name,
            "robot_count": len(robots),
            "trajectory_count": len(trajectories),
            "robots": robots[:40],
            "trajectories": trajectories[:40],
        }

    @staticmethod
    def _is_meshpart_part_candidate(obj: Any) -> bool:
        type_id = getattr(obj, "TypeId", "")
        return type_id.startswith("Part::") and hasattr(obj, "Shape")

    @staticmethod
    def _is_meshpart_mesh_output(obj: Any) -> bool:
        return getattr(obj, "TypeId", "").startswith("Mesh::")

    def meshpart_summary(self) -> dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {
                "document": None,
                "part_candidate_count": 0,
                "mesh_count": 0,
                "part_candidates": [],
                "meshes": [],
            }
        part_candidates = [
            self._part_object_summary(obj)
            for obj in doc.Objects
            if self._is_meshpart_part_candidate(obj)
        ]
        meshes = [
            self._mesh_object_summary(obj)
            for obj in doc.Objects
            if self._is_meshpart_mesh_output(obj)
        ]
        return {
            "document": doc.Name,
            "part_candidate_count": len(part_candidates),
            "mesh_count": len(meshes),
            "part_candidates": part_candidates[:80],
            "meshes": meshes[:80],
        }

    def _part_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        return [
            obj
            for obj in doc.Objects
            if not self._is_private_scripted_object(obj)
            and (
                getattr(obj, "TypeId", "").startswith("Part::")
                or str(getattr(obj, "VibeCADScriptedRole", "") or "")
                == "publication"
            )
        ]

    def _get_document_object(self, object_name: str | None):
        doc = self._active_document()
        if doc is None or not object_name:
            return None
        return doc.getObject(str(object_name))

    def _part_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        for property_name in (
            "Length",
            "Width",
            "Height",
            "Radius",
            "Angle",
            "Placement",
        ):
            if hasattr(obj, property_name):
                try:
                    item[property_name.lower()] = self._short_value(
                        getattr(obj, property_name)
                    )
                except Exception:
                    continue
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                item["shape"] = self._shape_topology_summary(shape)
            except Exception:
                pass
        return item

    def part_summary(self) -> dict[str, Any]:
        objects = [self._part_object_summary(obj) for obj in self._part_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def provider_part_summary(self) -> dict[str, Any]:
        """Return bounded Part identity without broad owner-thread topology work."""

        objects = self._part_objects()
        visible = objects[:MAX_PROVIDER_PART_OBJECTS]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "object_limit": MAX_PROVIDER_PART_OBJECTS,
            "objects_truncated": len(objects) > len(visible),
            "objects_omitted": max(0, len(objects) - len(visible)),
            "objects": [self._object_summary(obj) for obj in visible],
        }

    def _mesh_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Mesh::Feature"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "").startswith("Mesh::"):
                    objects.append(obj)
        return objects

    @staticmethod
    def _bound_box_summary(bound_box: Any) -> dict[str, float] | None:
        if bound_box is None:
            return None
        try:
            return {
                "xmin": float(bound_box.XMin),
                "ymin": float(bound_box.YMin),
                "zmin": float(bound_box.ZMin),
                "xmax": float(bound_box.XMax),
                "ymax": float(bound_box.YMax),
                "zmax": float(bound_box.ZMax),
            }
        except Exception:
            return None

    def _mesh_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None:
            item["mesh"] = {
                "points": int(getattr(mesh, "CountPoints", 0)),
                "edges": int(getattr(mesh, "CountEdges", 0)),
                "facets": int(getattr(mesh, "CountFacets", 0)),
                "bound_box": self._bound_box_summary(getattr(mesh, "BoundBox", None)),
            }
        return item

    def mesh_summary(self) -> dict[str, Any]:
        objects = [self._mesh_object_summary(obj) for obj in self._mesh_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def _points_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            try:
                if obj.isDerivedFrom("Points::Feature"):
                    objects.append(obj)
            except Exception:
                if getattr(obj, "TypeId", "").startswith("Points::"):
                    objects.append(obj)
        return objects

    def _points_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        points = getattr(obj, "Points", None)
        count = int(getattr(points, "CountPoints", 0) or 0) if points is not None else 0
        point_list = (
            list(points.fromSegment(range(min(8, count))).Points)
            if points is not None and count
            else []
        )
        item["point_count"] = count
        item["bound_box"] = self._bound_box_summary(getattr(points, "BoundBox", None))
        item["sample"] = [
            [float(point.x), float(point.y), float(point.z)]
            for point in point_list[:8]
            if hasattr(point, "x") and hasattr(point, "y") and hasattr(point, "z")
        ]
        return item

    def points_summary(self) -> dict[str, Any]:
        objects = [self._points_object_summary(obj) for obj in self._points_objects()]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    def _material_capable_objects(self) -> list[Any]:
        doc = self._active_document()
        if doc is None:
            return []
        objects = []
        for obj in doc.Objects:
            if hasattr(obj, "ShapeMaterial"):
                objects.append(obj)
                continue
            try:
                view_object = getattr(obj, "ViewObject", None)
                if view_object is not None and hasattr(view_object, "ShapeAppearance"):
                    objects.append(obj)
            except Exception:
                continue
        return objects

    def _material_object_summary(self, obj: Any) -> dict[str, Any]:
        item = self._object_summary(obj)
        material = getattr(obj, "ShapeMaterial", None)
        if material is not None:
            item["material_name"] = self._short_value(getattr(material, "Name", ""))
            item["material_uuid"] = self._short_value(getattr(material, "UUID", ""))
            for property_name in (
                "DiffuseColor",
                "AmbientColor",
                "SpecularColor",
                "EmissiveColor",
                "Transparency",
            ):
                try:
                    if material.hasAppearanceProperty(property_name):
                        item[property_name.lower()] = self._short_value(
                            material.getAppearanceValue(property_name)
                        )
                except Exception:
                    properties = getattr(material, "AppearanceProperties", {}) or {}
                    if property_name in properties:
                        item[property_name.lower()] = self._short_value(
                            properties[property_name]
                        )
        try:
            view_object = getattr(obj, "ViewObject", None)
            appearances = (
                list(getattr(view_object, "ShapeAppearance", []) or [])
                if view_object
                else []
            )
            item["shape_appearance_count"] = len(appearances)
            if appearances:
                appearance = appearances[0]
                item["first_shape_diffuse_color"] = self._short_value(
                    tuple(getattr(appearance, "DiffuseColor", ()))
                )
                item["first_shape_transparency"] = self._short_value(
                    getattr(appearance, "Transparency", None)
                )
        except Exception:
            item["shape_appearance_count"] = None
        return item

    def material_summary(self) -> dict[str, Any]:
        objects = [
            self._material_object_summary(obj)
            for obj in self._material_capable_objects()
        ]
        doc = self._active_document()
        return {
            "document": doc.Name if doc else None,
            "object_count": len(objects),
            "objects": objects[:80],
        }

    @staticmethod
    def _coerce_rgb(color: Any) -> tuple[float, float, float]:
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("diffuse_color must be [r, g, b].")
        values = tuple(float(component) for component in color)
        if any(component < 0.0 or component > 1.0 for component in values):
            raise ValueError("diffuse_color components must be between 0.0 and 1.0.")
        return values

    @staticmethod
    def _coerce_points(points: Any) -> list[list[float]]:
        if points is None:
            return [[0.0, 0.0, 0.0]]
        if not isinstance(points, list):
            raise ValueError("points must be a list of [x, y, z] coordinates.")
        if not points:
            raise ValueError("points must contain at least one coordinate.")
        if len(points) > 1000:
            raise ValueError("points is limited to 1000 coordinates.")
        coerced = []
        for index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError(f"Point {index} must be [x, y, z].")
            coerced.append([float(point[0]), float(point[1]), float(point[2])])
        return coerced

    def _select_default_sketch_plane(self) -> dict[str, Any]:
        try:
            import FreeCAD as App
            import FreeCADGui as Gui
        except Exception as exc:
            return {"selected": False, "error": str(exc)}

        doc = App.ActiveDocument
        if doc is None:
            return {"selected": False, "error": "No active document."}
        try:
            if Gui.Selection.getSelectionEx():
                return {"selected": False, "reason": "Existing selection preserved."}
        except Exception:
            pass
        bodies = [
            obj
            for obj in doc.Objects
            if getattr(obj, "TypeId", "") == "PartDesign::Body"
        ]
        if not bodies:
            return {"selected": False, "error": "No PartDesign body."}
        body = bodies[0]
        origin = getattr(body, "Origin", None)
        features = list(getattr(origin, "OriginFeatures", []) or [])
        plane = next(
            (
                item
                for item in features
                if getattr(item, "Name", "") == "XY_Plane"
                or getattr(item, "Label", "") in {"XY-plane", "XY_Plane", "XY plane"}
            ),
            features[3] if len(features) > 3 else None,
        )
        if plane is None:
            return {"selected": False, "error": "XY plane not found."}
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, plane.Name)
            return {
                "selected": True,
                "object": plane.Name,
                "label": getattr(plane, "Label", plane.Name),
            }
        except Exception as exc:
            return {"selected": False, "error": str(exc)}

    def context_summary(self) -> dict[str, Any]:
        auth = self.auth_state()
        task_panel = self.task_panel_summary()
        context = {
            "auth": {
                "status": auth.status.value,
                "source": auth.source,
                "configured": auth.can_call_provider,
            },
            "provider": {
                "model": self.provider_model(),
                "reasoning_effort": self.provider_reasoning_effort(),
                "use_online_by_default": self.use_online_provider_by_default(),
            },
            "workbench": self.active_workbench_name(),
            "vibecad_project": self.project_context(),
            "human_steering": self.steering_state(),
            "document": self.document_summary(),
            "selection": self.selection_summary(),
            "view": self.view_state(),
            "task_panel": task_panel,
            "view_screenshot": self.view_screenshot_summary(),
            "reference_images": self.reference_images_summary(),
            "part": self.part_summary(),
            "mesh": self.mesh_summary(),
            "points": self.points_summary(),
            "material": self.material_summary(),
            "sketcher": self.sketcher_summary(),
            "spreadsheet": self.spreadsheet_summary(),
            "draft": self.draft_summary(),
            "partdesign": self.partdesign_summary(),
            "techdraw": self.techdraw_summary(),
            "fem": self.fem_summary(),
            "cam": self.cam_summary(),
            "assembly": self.assembly_summary(),
            "inspection": self.inspection_summary(),
            "surface": self.surface_summary(),
            "robot": self.robot_summary(),
            "meshpart": self.meshpart_summary(),
            "aero": self.aero_summary(),
            "provider_tool_surface": self.provider_tool_surface(),
            "conversation": self.conversation_history(),
        }
        native_diagnostics = self.recompute_diagnostics()
        context["cad_state"] = self.cad_state_summary(
            native_diagnostics=native_diagnostics,
            task_panel=task_panel,
        )
        return context

    def provider_context_summary(self) -> dict[str, Any]:
        """Return only deterministic turn-start facts needed by the model.

        Broad workbench summaries remain available to explicit UI and debugger
        callers through ``context_summary``. They are never traversed merely
        because an AI turn starts.
        """
        active_workbench = self.active_workbench_name()
        modeling_surface = resolve_modeling_surface(active_workbench, self.modeling_engine())
        surface = {
            "workbench": str(modeling_surface.workbench or ""),
            "engine": modeling_surface.engine,
            "domain": modeling_surface.domain,
            "surface_id": modeling_surface.surface_id,
            "available": modeling_surface.available,
        }
        if not modeling_surface.available:
            surface["unavailable_reason"] = modeling_surface.unavailable_reason
        return {
            "workbench": active_workbench,
            "modeling_surface": surface,
            "document": self.provider_turn_document_summary(),
            "selection": self.provider_turn_selection_summary(),
            "view_screenshot": self.view_screenshot_summary(),
            "reference_images": self.provider_reference_image_attachments(),
            "aero": self.aero_summary(),
        }

    def _register_core_tools(self) -> None:
        service_tools.register_tools(self._registry, self)
        vibescript_domains.register_domain_tools(self._registry, self)


_service: VibeCADService | None = None


def get_service() -> VibeCADService:
    global _service
    if _service is None:
        _service = VibeCADService()
    return _service
