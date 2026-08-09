# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact-call dispatcher for one frozen Native assistant turn."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import threading
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeState import NativeCallTicket, NativeDocumentStateStore
from VibeCADNativeTargets import document_uid
from VibeCADNativeTurn import NativeTurnSnapshot


MAX_NATIVE_CALLS_PER_TURN = 256
MAX_NATIVE_PROVIDER_CALL_ID_CHARACTERS = 256
MAX_NATIVE_ARGUMENTS_JSON_BYTES = 64 * 1024
MAX_NATIVE_RESULT_JSON_BYTES = 64 * 1024
MAX_NATIVE_FAILURE_TEXT_CHARACTERS = 512


class NativeDispatchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(str(message))
        self.code = str(code)

    def failure(self) -> dict[str, Any]:
        return {"error_code": self.code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class NativeCapabilityCall:
    arguments: Mapping[str, Any]
    ticket: NativeCallTicket
    runtime: Any


@dataclass(slots=True)
class _CallRecord:
    tool_name: str
    arguments_json: str
    ticket: NativeCallTicket | None = None
    result_json: str | None = None


def _canonical_json(value: Any, *, label: str, byte_limit: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise NativeDispatchError(
            "NATIVE_RESULT_INVALID",
            f"Native {label} is not bounded JSON.",
        ) from exc
    if len(encoded.encode("utf-8")) > byte_limit:
        raise NativeDispatchError(
            "NATIVE_RESULT_TOO_LARGE",
            f"Native {label} exceeds its deterministic byte limit.",
        )
    return encoded


def _schema_error(error: Any) -> str:
    path = ".".join(str(value) for value in error.absolute_path)
    location = f" at {path}" if path else ""
    message = " ".join(str(error.message or "").split())
    return f"Native tool arguments are invalid{location}: {message}"[
        :MAX_NATIVE_FAILURE_TEXT_CHARACTERS
    ]


def _failure_payload(exc: BaseException) -> dict[str, Any]:
    failure = getattr(exc, "failure", None)
    raw = failure() if callable(failure) else None
    details = dict(raw) if isinstance(raw, Mapping) else {}
    code = str(
        details.pop("error_code", "")
        or getattr(exc, "code", "")
        or "NATIVE_CALL_FAILED"
    )
    message = str(
        details.pop("message", "")
        or (
            "Native capability execution failed."
            if raw is None
            else str(exc)
        )
        or "Native call failed."
    )
    result: dict[str, Any] = {
        "ok": False,
        "error_code": code[:96],
        "error": " ".join(message.split())[:MAX_NATIVE_FAILURE_TEXT_CHARACTERS],
    }
    for name in (
        "current_revision",
        "current_surface",
        "repair",
        "retry_same_call",
    ):
        if name in details:
            result[name] = details[name]
    return result


class NativeTurnDispatcher:
    """Execute only definitions frozen for one exact human-selected turn."""

    def __init__(
        self,
        *,
        document: Any,
        state: NativeDocumentStateStore,
        registry: NativeCapabilityRegistry,
        turn: NativeTurnSnapshot,
        runtimes: Mapping[str, Any],
        reauthorize_turn: Callable[[], Any],
        active_document: Callable[[], Any],
        debug_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(state, NativeDocumentStateStore):
            raise TypeError("state must be a NativeDocumentStateStore")
        if not isinstance(registry, NativeCapabilityRegistry):
            raise TypeError("registry must be a NativeCapabilityRegistry")
        if not isinstance(turn, NativeTurnSnapshot):
            raise TypeError("turn must be a NativeTurnSnapshot")
        if not callable(reauthorize_turn) or not callable(active_document):
            raise TypeError("Native dispatcher guards must be callable")
        if debug_sink is not None and not callable(debug_sink):
            raise TypeError("debug_sink must be callable")
        runtime_map = dict(runtimes)
        if set(runtime_map) != set(turn.tool_names):
            raise NativeDispatchError(
                "NATIVE_RUNTIME_INCOMPLETE",
                "Native runtime bindings do not match the frozen tool surface.",
            )
        schema_map = {
            str(schema.get("name") or ""): dict(schema)
            for schema in turn.provider_schemas
        }
        if set(schema_map) != set(turn.tool_names):
            raise NativeDispatchError(
                "NATIVE_SCHEMA_INCOMPLETE",
                "Native schemas do not match the frozen tool surface.",
            )
        for name in turn.tool_names:
            if (
                registry.definition(name) is None
                or registry.implementation(name) is None
            ):
                raise NativeDispatchError(
                    "NATIVE_IMPLEMENTATION_MISSING",
                    "A frozen Native capability has no exact implementation.",
                )
        self._document = document
        self._document_uid = document_uid(document)
        self._state = state
        self._registry = registry
        self._turn = turn
        self._runtimes = runtime_map
        self._schemas = schema_map
        self._reauthorize_turn = reauthorize_turn
        self._active_document = active_document
        self._debug_sink = debug_sink
        self._calls: OrderedDict[str, _CallRecord] = OrderedDict()
        self._lock = threading.RLock()

    def _guard_document(self) -> None:
        active = self._active_document()
        if active is not self._document or document_uid(active) != self._document_uid:
            raise NativeDispatchError(
                "NATIVE_DOCUMENT_CHANGED",
                "The exact Native document is no longer active.",
            )

    def _guard(self) -> None:
        self._reauthorize_turn()
        self._guard_document()

    def _guard_after_call(self, variant: Any, payload: Mapping[str, Any]) -> None:
        if getattr(variant, "transaction_behavior", "") != "edit_control":
            self._guard()
            return

        self._guard_document()
        if (
            payload.get("next_turn_required") is not True
            or not str(payload.get("next_surface") or "").strip()
        ):
            raise NativeDispatchError(
                "NATIVE_EDIT_CONTROL_FAILED",
                "Native edit control did not publish its required surface transition.",
            )
        try:
            self._reauthorize_turn()
        except Exception as exc:
            failure = getattr(exc, "failure", None)
            details = failure() if callable(failure) else None
            if (
                isinstance(details, Mapping)
                and details.get("error_code") == "NATIVE_SURFACE_CHANGED"
                and str(details.get("current_surface") or "")
                == str(payload["next_surface"])
            ):
                return
            raise
        raise NativeDispatchError(
            "NATIVE_EDIT_CONTROL_FAILED",
            "Native edit control did not invalidate the frozen turn.",
        )

    def _debug(self, tool_name: str, exc: BaseException) -> None:
        if self._debug_sink is None:
            return
        try:
            causes = []
            cause = exc.__cause__
            while cause is not None and len(causes) < 8:
                causes.append(
                    {
                        "exception_type": type(cause).__name__,
                        "diagnostic": str(cause)[:2048],
                    }
                )
                cause = cause.__cause__
            self._debug_sink(
                {
                    "event": "native_call_failed",
                    "tool_name": tool_name,
                    "exception_type": type(exc).__name__,
                    "diagnostic": str(exc),
                    "causes": causes,
                }
            )
        except Exception:
            pass

    @staticmethod
    def _call_id(value: Any) -> str:
        call_id = str(value or "").strip()
        if (
            not call_id
            or len(call_id) > MAX_NATIVE_PROVIDER_CALL_ID_CHARACTERS
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E
                for character in call_id
            )
        ):
            raise NativeDispatchError(
                "NATIVE_CALL_ID_INVALID",
                "Native tool execution requires one bounded provider call ID.",
            )
        return call_id

    def _parse_arguments(
        self,
        arguments_json: Any,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(arguments_json, str):
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                "Native tool arguments must be a JSON object.",
            )
        if len(arguments_json.encode("utf-8")) > MAX_NATIVE_ARGUMENTS_JSON_BYTES:
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_TOO_LARGE",
                "Native tool arguments exceed their deterministic byte limit.",
            )
        try:
            arguments = json.loads(arguments_json or "{}")
        except (TypeError, ValueError) as exc:
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                "Native tool arguments are not valid JSON.",
            ) from exc
        if not isinstance(arguments, dict):
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                "Native tool arguments must be a JSON object.",
            )
        canonical = _canonical_json(
            arguments,
            label="arguments",
            byte_limit=MAX_NATIVE_ARGUMENTS_JSON_BYTES,
        )
        return arguments, canonical

    def _validate_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        validator = Draft202012Validator(self._schemas[tool_name]["parameters"])
        error = next(iter(validator.iter_errors(arguments)), None)
        if error is not None:
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                _schema_error(error),
            )
        definition = self._registry.definition(tool_name)
        operation = arguments.get("operation")
        variant = (
            next(
                (
                    item
                    for item in definition.variants
                    if item.operation == operation
                ),
                None,
            )
            if definition is not None
            else None
        )
        if variant is None:
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                "Native tool arguments name an unavailable operation.",
            )
        exact_validator = Draft202012Validator(variant.provider_parameters())
        exact_error = next(iter(exact_validator.iter_errors(arguments)), None)
        if exact_error is not None:
            raise NativeDispatchError(
                "NATIVE_ARGUMENTS_INVALID",
                _schema_error(exact_error),
            )
        return variant

    def call(
        self,
        tool_name: str,
        arguments_json: str,
        provider_call_id: str,
    ) -> dict[str, Any]:
        name = str(tool_name or "").strip()
        try:
            call_id = self._call_id(provider_call_id)
        except Exception as exc:
            self._debug(name, exc)
            return _failure_payload(exc)

        with self._lock:
            try:
                existing = self._calls.get(call_id)
                if existing is not None and existing.tool_name != name:
                    raise NativeDispatchError(
                        "NATIVE_CALL_ID_REUSED",
                        "A provider call ID cannot identify a different Native call.",
                    )
                try:
                    arguments, canonical = self._parse_arguments(arguments_json)
                except Exception:
                    canonical = "!raw:" + str(arguments_json or "")
                    if existing is not None:
                        if existing.arguments_json != canonical:
                            raise NativeDispatchError(
                                "NATIVE_CALL_ID_REUSED",
                                "A provider call ID cannot identify a different Native call.",
                            )
                        if existing.result_json is not None:
                            return json.loads(existing.result_json)
                    raise
                if existing is not None:
                    if existing.arguments_json != canonical:
                        raise NativeDispatchError(
                            "NATIVE_CALL_ID_REUSED",
                            "A provider call ID cannot identify a different Native call.",
                        )
                    if existing.result_json is None:
                        raise NativeDispatchError(
                            "NATIVE_CALL_IN_PROGRESS",
                            "The exact Native call is already running.",
                        )
                    return json.loads(existing.result_json)
                if name not in self._turn.tool_names:
                    raise NativeDispatchError(
                        "NATIVE_TOOL_UNAVAILABLE",
                        "That capability is not on the frozen Native ribbon surface.",
                    )
                variant = self._validate_arguments(name, arguments)
                if len(self._calls) >= MAX_NATIVE_CALLS_PER_TURN:
                    raise NativeDispatchError(
                        "NATIVE_TURN_CALL_LIMIT",
                        "This Native turn reached its deterministic call limit.",
                    )

                self._guard()
                ticket = self._state.begin_call(self._document_uid, name)
                record = _CallRecord(name, canonical, ticket=ticket)
                self._calls[call_id] = record
                implementation = self._registry.implementation(name)
                if implementation is None:
                    raise NativeDispatchError(
                        "NATIVE_IMPLEMENTATION_MISSING",
                        "The frozen Native capability has no implementation.",
                    )
                payload = implementation.handler(
                    NativeCapabilityCall(arguments, ticket, self._runtimes[name])
                )
                if not isinstance(payload, Mapping) or "ok" in payload:
                    raise NativeDispatchError(
                        "NATIVE_RESULT_INVALID",
                        "A Native capability returned an invalid result contract.",
                    )
                self._guard_after_call(variant, payload)
                response = {"ok": True, **dict(payload)}
                record.result_json = _canonical_json(
                    response,
                    label="result",
                    byte_limit=MAX_NATIVE_RESULT_JSON_BYTES,
                )
                return json.loads(record.result_json)
            except Exception as exc:
                self._debug(name, exc)
                response = _failure_payload(exc)
                encoded = _canonical_json(
                    response,
                    label="failure",
                    byte_limit=MAX_NATIVE_RESULT_JSON_BYTES,
                )
                record = self._calls.get(call_id)
                if record is None and len(self._calls) < MAX_NATIVE_CALLS_PER_TURN:
                    try:
                        _arguments, canonical = self._parse_arguments(arguments_json)
                    except Exception:
                        canonical = "!raw:" + str(arguments_json or "")
                    record = _CallRecord(name, canonical)
                    self._calls[call_id] = record
                if record is not None:
                    record.result_json = encoded
                return json.loads(encoded)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._calls)
