# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact state for CAM Jobs, operations, and model candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureToolState import tool_property_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeSnapshot import concise_object


MAX_JOB_OPERATIONS = 64
MAX_JOB_TOOLS = 32
MAX_JOB_MODELS = 32
_NON_SEMANTIC_PROPERTY_NAMES = frozenset(
    {
        "AreaParams",
        "CycleTime",
        "Label",
        "Label2",
        "OpFinalDepth",
        "OpStartDepth",
        "OpStockZMax",
        "OpStockZMin",
        "OpToolDiameter",
        "Path",
        "PathParams",
        "Placement",
        "Proxy",
        "Shape",
        "removalshape",
    }
)
_CONTROLLER_SETTINGS_EXCLUDED = _NON_SEMANTIC_PROPERTY_NAMES | {"Tool"}
_COPY_CONFIGURATION_EXCLUDED = _NON_SEMANTIC_PROPERTY_NAMES | {
    "Visibility",
    "VibeCADTimelineOwner",
    "VibeCADTimelineReplacedInputs",
    "VibeCADTimelineRole",
    "_ObjectUUID",
    "_SourceUUID",
}


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic(value: Any) -> Any:
    """Remove presentation and transient recompute state from exact hashes."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic(item)
            for key, item in value.items()
            if str(key) not in {"label", "state", "state_sha256"}
        }
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    return value


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(result, 9) if math.isfinite(result) else None


def _object_state(obj: Any) -> tuple[str, ...]:
    try:
        return tuple(str(value) for value in obj.State)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _restore_read_state(obj: Any, before: tuple[str, ...], noun: str) -> None:
    """Undo only a transient read-induced touch and reject any other drift."""

    after = _object_state(obj)
    if after != before and "Touched" not in before and "Touched" in after:
        try:
            obj.purgeTouched()
        except (AttributeError, ReferenceError, RuntimeError) as exc:
            raise NativeManufactureError(
                f"Reading {noun} changed its transient document state.",
                error_code="NATIVE_MANUFACTURE_STATE_INVALID",
            ) from exc
        after = _object_state(obj)
    if after != before:
        raise NativeManufactureError(
            f"Reading {noun} changed its transient document state.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        )


def _capture_document_read_state(
    document: Any,
    *,
    overrides: tuple[tuple[Any, tuple[str, ...]], ...] = (),
) -> tuple[tuple[Any, ...], tuple[tuple[Any, tuple[str, ...]], ...]]:
    objects = tuple(getattr(document, "Objects", ()) or ())
    states = {id(obj): (obj, _object_state(obj)) for obj in objects}
    for obj, state in overrides:
        states[id(obj)] = (obj, state)
    return objects, tuple(states.values())


def _restore_document_read_state(
    document: Any,
    objects_before: tuple[Any, ...],
    states_before: tuple[tuple[Any, tuple[str, ...]], ...],
    noun: str,
) -> None:
    if tuple(getattr(document, "Objects", ()) or ()) != objects_before:
        raise NativeManufactureError(
            f"Reading {noun} changed the CAM document graph.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        )
    for obj, state_before in states_before:
        _restore_read_state(obj, state_before, noun)


def _publish_frozen_state(result: dict[str, Any], state: tuple[str, ...]) -> None:
    values = sorted(str(value) for value in state)
    if values:
        result["state"] = values[:8]
    else:
        result.pop("state", None)


def _bounds(value: Any) -> dict[str, list[float]] | None:
    box = getattr(value, "BoundBox", None)
    if box is None or not bool(getattr(box, "isValid", lambda: False)()):
        return None
    minimum = [_finite(getattr(box, name, None)) for name in ("XMin", "YMin", "ZMin")]
    maximum = [_finite(getattr(box, name, None)) for name in ("XMax", "YMax", "ZMax")]
    if any(item is None for item in (*minimum, *maximum)):
        return None
    return {
        "minimum_mm": minimum,  # type: ignore[dict-item]
        "maximum_mm": maximum,  # type: ignore[dict-item]
    }


def _placement_state(obj: Any) -> dict[str, list[float]] | None:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    base = getattr(placement, "Base", None)
    translation = [_finite(getattr(base, axis, None)) for axis in ("x", "y", "z")]
    try:
        quaternion = [_finite(value) for value in placement.Rotation.Q]
    except Exception:
        return None
    if any(value is None for value in (*translation, *quaternion)):
        return None
    return {
        "translation_mm": translation,  # type: ignore[dict-item]
        "quaternion": quaternion,  # type: ignore[dict-item]
    }


def _path_sha256(commands: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for command in commands:
        try:
            encoded = str(command.toGCode()).encode("utf-8")
        except Exception as exc:
            raise NativeManufactureError(
                "The CAM operation contains an unreadable toolpath command.",
                error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            ) from exc
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _stable_property_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return _finite(value)
    if isinstance(value, Mapping):
        return {
            str(key): _stable_property_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_property_value(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes_sha256": hashlib.sha256(bytes(value)).hexdigest()}
    try:
        persistent_uuid = str(getattr(value, "UUID", "") or "").strip()
    except Exception:
        persistent_uuid = ""
    if persistent_uuid:
        # Native material properties expose a fresh Python wrapper on every
        # read.  Its repr contains a process address, while UUID is the exact
        # identity persisted in the FCStd document.
        return {"uuid": persistent_uuid}
    document = getattr(value, "Document", None)
    object_name = str(getattr(value, "Name", "") or "")
    if document is not None and object_name:
        return {"object_name": object_name}
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        vector = [_finite(getattr(value, axis)) for axis in ("x", "y", "z")]
        if all(item is not None for item in vector):
            return vector
    quantity = _finite(getattr(value, "Value", None))
    if quantity is not None:
        return quantity
    quaternion = getattr(value, "Q", None)
    if quaternion is not None:
        values = [_finite(item) for item in quaternion]
        if all(item is not None for item in values):
            return values
    matrix = getattr(value, "A", None)
    if matrix is not None:
        values = [_finite(item) for item in matrix]
        if all(item is not None for item in values):
            return values
    text = str(value)
    if " at 0x" not in text and len(text) <= 512:
        return text
    return {"value_type": f"{type(value).__module__}.{type(value).__name__}"}


def _copy_property_value(
    value: Any,
    canonical_object_names: Mapping[str, str],
    *,
    expression: bool = False,
) -> Any:
    """Normalize durable property data across an exact source/copy mapping."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        if not expression:
            return value
        normalized = value
        for object_name in sorted(canonical_object_names, key=len, reverse=True):
            normalized = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(object_name)}(?![A-Za-z0-9_])",
                canonical_object_names[object_name],
                normalized,
            )
        return normalized
    if isinstance(value, float):
        return _finite(value)
    if isinstance(value, Mapping):
        return {
            str(key): _copy_property_value(
                item,
                canonical_object_names,
                expression=expression,
            )
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [
            _copy_property_value(
                item,
                canonical_object_names,
                expression=expression,
            )
            for item in value
        ]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes_sha256": hashlib.sha256(bytes(value)).hexdigest()}
    document = getattr(value, "Document", None)
    object_name = str(getattr(value, "Name", "") or "")
    if document is not None and object_name:
        return {
            "object_name": canonical_object_names.get(object_name, object_name)
        }
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        vector = [_finite(getattr(value, axis)) for axis in ("x", "y", "z")]
        if all(item is not None for item in vector):
            return vector
    quantity = _finite(getattr(value, "Value", None))
    if quantity is not None:
        return quantity
    quaternion = getattr(value, "Q", None)
    if quaternion is not None:
        values = [_finite(item) for item in quaternion]
        if all(item is not None for item in values):
            return values
    matrix = getattr(value, "A", None)
    if matrix is not None:
        values = [_finite(item) for item in matrix]
        if all(item is not None for item in values):
            return values
    text = str(value)
    if " at 0x" not in text and len(text) <= 512:
        return text
    return {"value_type": f"{type(value).__module__}.{type(value).__name__}"}


def _property_state_sha256(
    obj: Any,
    *,
    excluded: frozenset[str] = _NON_SEMANTIC_PROPERTY_NAMES,
) -> str:
    """Fingerprint normalized persistent settings without reading path commands."""

    state_before = _object_state(obj)
    try:
        return _property_state_sha256_unchecked(obj, excluded=excluded)
    finally:
        _restore_read_state(obj, state_before, "persistent CAM properties")


def _property_state_sha256_unchecked(
    obj: Any,
    *,
    excluded: frozenset[str],
) -> str:
    """Build a persistent-property fingerprint within a guarded read."""

    return _digest(
        _persistent_property_state_unchecked(
            obj,
            excluded=excluded,
        )
    )


def _persistent_property_state_unchecked(
    obj: Any,
    *,
    excluded: frozenset[str],
) -> dict[str, Any]:
    """Normalize persistent properties while the caller guards read state."""

    expression_roots = {
        str(path).lstrip(".").split(".", 1)[0].split("[", 1)[0]
        for path, _expression in tuple(
            getattr(obj, "ExpressionEngine", ()) or ()
        )
    }
    values = {}
    for name in sorted(str(value) for value in getattr(obj, "PropertiesList", ()) or ()):
        if name in excluded:
            continue
        try:
            property_type = str(obj.getTypeIdOfProperty(name) or "")
            property_value = (
                None if name in expression_roots else obj.getPropertyByName(name)
            )
        except Exception as exc:
            raise NativeManufactureError(
                f"The persistent CAM property {name!r} could not be read.",
                error_code="NATIVE_MANUFACTURE_STATE_INVALID",
            ) from exc
        values[name] = {"type": property_type}
        if name in expression_roots:
            # ExpressionEngine below retains the authored formula.  Its
            # evaluated property value is a recompute output and may differ
            # by modeling tolerance after undo or FCStd restoration.
            values[name]["expression_bound"] = True
        else:
            values[name]["value"] = _stable_property_value(property_value)
    return values


def persistent_configuration_state(
    obj: Any,
    *,
    excluded_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return exact authored settings with explicit property exclusions."""

    excluded = tuple(str(name or "").strip() for name in excluded_names)
    if any(not name for name in excluded) or len(excluded) != len(set(excluded)):
        raise TypeError("excluded_names must contain distinct property names")
    state_before = _object_state(obj)
    try:
        return _persistent_property_state_unchecked(
            obj,
            excluded=_NON_SEMANTIC_PROPERTY_NAMES | frozenset(excluded),
        )
    finally:
        _restore_read_state(obj, state_before, "persistent CAM properties")


def persistent_configuration_sha256(
    obj: Any,
    *,
    excluded_names: tuple[str, ...] = (),
) -> str:
    """Fingerprint authored object settings with explicit property exclusions.

    Exact container operations sometimes change one relationship property while
    requiring every other authored setting to remain byte-for-byte semantic.
    This public reader keeps that proof on the same guarded serializer used by
    the rest of Manufacture state instead of duplicating property decoding.
    """

    excluded = tuple(str(name or "").strip() for name in excluded_names)
    if any(not name for name in excluded) or len(excluded) != len(set(excluded)):
        raise TypeError("excluded_names must contain distinct property names")
    return _property_state_sha256(
        obj,
        excluded=_NON_SEMANTIC_PROPERTY_NAMES | frozenset(excluded),
    )


def copy_configuration_state(
    obj: Any,
    canonical_object_names: Mapping[str, str],
) -> dict[str, Any]:
    """Normalize authored settings after exact graph-link canonicalization."""

    if not isinstance(canonical_object_names, Mapping) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        for key, value in canonical_object_names.items()
    ):
        raise TypeError("canonical_object_names must map stable names to stable tokens")
    state_before = _object_state(obj)
    try:
        expression_roots = {
            str(path).lstrip(".").split(".", 1)[0].split("[", 1)[0]
            for path, _expression in tuple(
                getattr(obj, "ExpressionEngine", ()) or ()
            )
        }
        values = {}
        for name in sorted(
            str(value) for value in getattr(obj, "PropertiesList", ()) or ()
        ):
            if name in _COPY_CONFIGURATION_EXCLUDED:
                continue
            try:
                property_type = str(obj.getTypeIdOfProperty(name) or "")
                property_value = (
                    None if name in expression_roots else obj.getPropertyByName(name)
                )
            except Exception as exc:
                raise NativeManufactureError(
                    f"The copied CAM property {name!r} could not be read.",
                    error_code="NATIVE_MANUFACTURE_STATE_INVALID",
                ) from exc
            values[name] = {"type": property_type}
            if name in expression_roots:
                values[name]["expression_bound"] = True
            else:
                values[name]["value"] = _copy_property_value(
                    property_value,
                    canonical_object_names,
                    expression=name == "ExpressionEngine",
                )
        return values
    finally:
        _restore_read_state(obj, state_before, "copied CAM configuration")


def copy_configuration_sha256(
    obj: Any,
    canonical_object_names: Mapping[str, str],
) -> str:
    """Fingerprint authored settings after exact graph-link canonicalization."""

    return _digest(copy_configuration_state(obj, canonical_object_names))


def is_job(obj: Any) -> bool:
    try:
        import Path.Main.Job as PathJob

        return isinstance(getattr(obj, "Proxy", None), PathJob.ObjectJob)
    except Exception:
        return False


def _controller_tool_reference(controller: Any, document: Any) -> Any | None:
    """Resolve a controller's ToolBit link without reading the linked proxy."""

    linked_objects = tuple(getattr(controller, "OutList", ()) or ())
    linked_states = tuple((linked, _object_state(linked)) for linked in linked_objects)
    try:
        candidates = []
        for linked in linked_objects:
            if getattr(linked, "Document", None) is not document:
                continue
            properties = frozenset(
                str(value) for value in getattr(linked, "PropertiesList", ()) or ()
            )
            if {"ShapeID", "ToolBitID"}.issubset(properties):
                candidates.append(linked)
        return candidates[0] if len(candidates) == 1 else None
    finally:
        for linked, state_before in linked_states:
            _restore_read_state(linked, state_before, "CAM controller link")


def _is_usable(obj: Any, document: Any) -> bool:
    try:
        from Path.CommandBoundary import is_timeline_input_usable

        return bool(is_timeline_input_usable(obj, document))
    except Exception:
        return False


def _validated_model_state(
    obj: Any,
    *,
    require_history_usable: bool,
) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    name = str(getattr(obj, "Name", "") or "")
    if (
        document is None
        or not name
        or document.getObject(name) is not obj
        or (require_history_usable and not _is_usable(obj, document))
    ):
        raise NativeManufactureError(
            "The CAM model candidate is not usable at the current History position.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    try:
        import Path.Base.Util as PathUtil
    except ImportError as exc:
        raise NativeManufactureError(
            "The CAM Job model validator is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc
    if not PathUtil.isValidBaseObject(obj):
        raise NativeManufactureError(
            "The object is not a valid CAM Job model.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    state = mesh_object_state(obj)
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeManufactureError(
            "The CAM model candidate has no valid current shape.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    state["shape_type"] = str(shape.ShapeType)
    digest_source = {
        key: value
        for key, value in state.items()
        if key not in {"label", "state_sha256"}
    }
    state["state_sha256"] = _digest(digest_source)
    return state


def candidate_model_state(obj: Any) -> dict[str, Any]:
    return _validated_model_state(obj, require_history_usable=True)


def _job_model_state(obj: Any) -> dict[str, Any]:
    """Fingerprint an exact Job-owned source after its History replacement."""

    return _validated_model_state(obj, require_history_usable=False)


def _operation_reference_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if document is None or not _is_usable(obj, document):
        raise NativeManufactureError(
            "The CAM operation is not usable at the current History position.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    derived = getattr(obj, "isDerivedFrom", None)
    if not callable(derived) or not derived("Path::Feature") or not hasattr(obj, "Path"):
        raise NativeManufactureError(
            "The target is not a CAM toolpath operation.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    path = obj.Path
    result = concise_object(obj)
    result.update(
        active=operation_active_state(obj),
        command_count=int(getattr(path, "Size", 0) or 0),
        configuration_sha256=_property_state_sha256(
            obj,
            excluded=_NON_SEMANTIC_PROPERTY_NAMES | {"Active"},
        ),
        settings_sha256=_property_state_sha256(obj),
    )
    controller = getattr(obj, "ToolController", None)
    if controller is not None:
        result["tool_controller"] = str(getattr(controller, "Name", "") or "")
    properties = set(str(value) for value in getattr(obj, "PropertiesList", ()) or ())
    if {"StartPoint", "UseStartPoint", "ClearanceHeight"} <= properties:
        point = obj.StartPoint
        point_values = {
            "x_mm": _finite(getattr(point, "x", None)),
            "y_mm": _finite(getattr(point, "y", None)),
            "z_mm": _finite(getattr(point, "z", None)),
        }
        clearance = _finite(getattr(obj.ClearanceHeight, "Value", None))
        if clearance is None or any(value is None for value in point_values.values()):
            raise NativeManufactureError(
                "The CAM operation has an unreadable start-point contract.",
                error_code="NATIVE_MANUFACTURE_STATE_INVALID",
            )
        result["start_point"] = {
            "enabled": bool(obj.UseStartPoint),
            "point_mm": point_values,
            "clearance_height_mm": clearance,
        }
    placement = _placement_state(obj)
    if placement is not None:
        result["placement"] = placement
    result["state_sha256"] = _digest(_semantic(result))
    return result


def operation_active_state(obj: Any) -> bool:
    """Return the effective Active state used by CAM operations and dress-ups."""

    visited: set[int] = set()
    current = obj
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if bool(getattr(current, "Suppressed", False)):
            return False
        if "Active" in tuple(getattr(current, "PropertiesList", ()) or ()):
            return bool(current.Active)
        base = getattr(current, "Base", None)
        current = base if hasattr(base, "TypeId") else None
    return True


def operation_reference_state(obj: Any) -> dict[str, Any]:
    """Return bounded persistent operation state without reading its command stream."""

    return _operation_reference_state(obj)


def operation_state(obj: Any) -> dict[str, Any]:
    result = _operation_reference_state(obj)
    path = obj.Path
    commands = tuple(getattr(path, "Commands", ()) or ())
    result.update(
        path_sha256=_path_sha256(commands),
        path_length_mm=_finite(getattr(path, "Length", 0.0)),
    )
    bounds = _bounds(path)
    if bounds is not None:
        result["bounds"] = bounds
    cycle_time = str(getattr(obj, "CycleTime", "") or "").strip()
    if cycle_time:
        result["cycle_time"] = cycle_time[:32]
    digest_source = _semantic(result)
    # These are useful measurements, but they are derived from the durable
    # command stream and can vary below modeling tolerance after FCStd reload.
    # The exact command fingerprint already captures the toolpath identity.
    for derived_name in ("path_length_mm", "bounds", "cycle_time"):
        digest_source.pop(derived_name, None)
    result["state_sha256"] = _digest(digest_source)
    return result


def persistent_resource_state(obj: Any) -> dict[str, Any]:
    """Fingerprint one live CAM History resource without publishing its payload.

    Operation copies must prove that their complete source closure was retained
    unchanged.  The returned state therefore includes persistent properties,
    placement, serialized Part geometry when present, and the full toolpath
    command fingerprint for Path features, while returning only hashes and
    bounded metadata to callers.
    """

    document = getattr(obj, "Document", None)
    name = str(getattr(obj, "Name", "") or "")
    if (
        document is None
        or not name
        or document.getObject(name) is not obj
    ):
        raise NativeManufactureError(
            "The CAM History resource is no longer in its exact document.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    result = concise_object(obj)
    # DocumentObject.State is transient recompute bookkeeping, not durable
    # resource authorship. The guarded readers above ensure inspection cannot
    # introduce touches; copy identity must not vary merely because an earlier
    # undo/redo left an otherwise-valid object pending recompute.
    result.pop("state", None)
    result["settings_sha256"] = _property_state_sha256(obj)
    placement = _placement_state(obj)
    if placement is not None:
        result["placement"] = placement

    shape = getattr(obj, "Shape", None)
    export_shape = getattr(shape, "exportBrepToString", None)
    if shape is not None and callable(export_shape):
        try:
            is_null = bool(shape.isNull())
        except Exception:
            is_null = True
        if not is_null:
            state_before = _object_state(obj)
            try:
                content = export_shape()
                encoded = (
                    content
                    if isinstance(content, bytes)
                    else str(content).encode("utf-8")
                )
                result["shape_sha256"] = hashlib.sha256(encoded).hexdigest()
            except Exception as exc:
                raise NativeManufactureError(
                    f"CAM History resource {name!r} has unreadable Part geometry.",
                    error_code="NATIVE_MANUFACTURE_STATE_INVALID",
                ) from exc
            finally:
                _restore_read_state(obj, state_before, "CAM History resource geometry")

    derived = getattr(obj, "isDerivedFrom", None)
    if callable(derived) and derived("Path::Feature") and hasattr(obj, "Path"):
        path = obj.Path
        commands = tuple(getattr(path, "Commands", ()) or ())
        result.update(
            command_count=len(commands),
            path_sha256=_path_sha256(commands),
            active=operation_active_state(obj),
        )
    result["state_sha256"] = _digest(_semantic(result))
    return result


def _public_model(job: Any, resource: Any) -> Any:
    try:
        return job.Proxy.baseObject(job, resource)
    except Exception:
        return resource


def _speed_mm_per_minute(value: Any) -> float | None:
    try:
        return _finite(value.getValueAs("mm/min"))
    except Exception:
        return None


def _tool_bit_reference_state(tool: Any) -> dict[str, Any]:
    state_before = _object_state(tool)
    try:
        return _tool_bit_reference_state_unchecked(tool)
    finally:
        _restore_read_state(tool, state_before, "CAM ToolBit state")


def _tool_bit_reference_state_unchecked(tool: Any) -> dict[str, Any]:
    result = concise_object(tool)
    result["settings_sha256"] = _property_state_sha256(tool)
    proxy = getattr(tool, "Proxy", None)
    if proxy is None or not callable(getattr(proxy, "to_dict", None)):
        raise NativeManufactureError(
            "The CAM ToolBit has no usable persistent definition.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        definition = proxy.to_dict()
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM ToolBit definition could not be read.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    shape_name = getattr(proxy, "get_shape_name", None)
    canonical_shape_type = (
        str(shape_name() or "").strip()
        if callable(shape_name)
        else str(getattr(tool, "ShapeType", "") or "").strip()
    )
    if not canonical_shape_type:
        canonical_shape_type = str(definition.get("shape-type") or "").strip()
    result.update(
        shape_type=canonical_shape_type[:80],
        editable_properties=tool_property_state(tool),
    )
    result["state_sha256"] = _digest(_semantic(result))
    return result


def _tool_bit_state_with_controllers(tool: Any, document: Any) -> dict[str, Any]:
    result = _tool_bit_reference_state(tool)
    result["controller_names"] = sorted(
        str(controller.Name)
        for job in document.Objects
        if is_job(job)
        for controller in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
        if _controller_tool_reference(controller, document) is tool
    )
    if not result["controller_names"]:
        raise NativeManufactureError(
            "The CAM ToolBit is not attached to a current Job controller.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    result["state_sha256"] = _digest(_semantic(result))
    return result


def tool_controller_state(controller: Any) -> dict[str, Any]:
    tool = getattr(controller, "Tool", None)
    tool_state_before = _object_state(tool) if tool is not None else ()
    controller_state_before = _object_state(controller)
    document = getattr(controller, "Document", None)
    if document is None:
        raise NativeManufactureError(
            "The target is not a current CAM tool controller.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    overrides = ((controller, controller_state_before),)
    if tool is not None:
        overrides += ((tool, tool_state_before),)
    objects_before, states_before = _capture_document_read_state(
        document,
        overrides=overrides,
    )
    frozen_states = {id(obj): state for obj, state in states_before}
    try:
        owner = next(
            (
                job
                for job in document.Objects
                if is_job(job)
                and controller
                in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
            ),
            None,
        )
        if owner is None:
            raise NativeManufactureError(
                "The CAM tool controller is not owned by a current Job.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        result = concise_object(controller)
        _publish_frozen_state(result, frozen_states[id(controller)])
        result["job_name"] = str(owner.Name)
        # The exact linked ToolBit is fingerprinted independently below. Reading
        # the FeaturePython link while hashing controller settings can transiently
        # touch that linked object and would also duplicate its semantic identity.
        result["settings_sha256"] = _property_state_sha256(
            controller,
            excluded=_CONTROLLER_SETTINGS_EXCLUDED,
        )
        result.update(
            tool_number=int(getattr(controller, "ToolNumber", 0) or 0),
            tool_length_offset=int(
                getattr(controller, "ToolLengthOffset", 0) or 0
            ),
            spindle_speed_rpm=_finite(getattr(controller, "SpindleSpeed", 0.0)),
            spindle_direction=str(getattr(controller, "SpindleDir", "") or ""),
        )
        for property_name, output_name in (
            ("HorizFeed", "horizontal_feed_mm_per_minute"),
            ("VertFeed", "vertical_feed_mm_per_minute"),
            ("RampFeed", "ramp_feed_mm_per_minute"),
            ("LeadInFeed", "lead_in_feed_mm_per_minute"),
            ("LeadOutFeed", "lead_out_feed_mm_per_minute"),
            ("HorizRapid", "horizontal_rapid_mm_per_minute"),
            ("VertRapid", "vertical_rapid_mm_per_minute"),
        ):
            value = _speed_mm_per_minute(getattr(controller, property_name, None))
            if value is not None:
                result[output_name] = value
        if tool is None or getattr(tool, "Document", None) is not document:
            raise NativeManufactureError(
                "The CAM tool controller has no valid attached ToolBit.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        result["tool"] = _tool_bit_state_with_controllers(tool, document)
        _publish_frozen_state(result["tool"], frozen_states[id(tool)])
        result["state_sha256"] = _digest(_semantic(result))
        return result
    finally:
        _restore_document_read_state(
            document,
            objects_before,
            states_before,
            "CAM tool-controller state",
        )


def tool_bit_state(tool: Any) -> dict[str, Any]:
    document = getattr(tool, "Document", None)
    if document is None:
        raise NativeManufactureError(
            "The target is not a current CAM ToolBit.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return _tool_bit_state_with_controllers(tool, document)


def _tool_state(controller: Any) -> dict[str, Any]:
    return tool_controller_state(controller)


def _fallback_resource_state(obj: Any) -> dict[str, Any]:
    result = concise_object(obj)
    result["state_sha256"] = _digest(_semantic(result))
    return result


def configured_machine_state(job: Any) -> dict[str, Any]:
    """Return the bounded machine state that can affect CAM generation."""

    machine_name = str(getattr(job, "Machine", "") or "").strip()
    result: dict[str, Any] = {
        "configured": bool(machine_name),
        "machine_name": machine_name[:160],
        "available": False,
        "rotary_axes": [],
    }
    proxy = getattr(job, "Proxy", None)
    reader = getattr(proxy, "getMachine", None)
    if not callable(reader):
        result["state_sha256"] = _digest(_semantic(result))
        return result
    try:
        machine = reader()
    except Exception:
        machine = None
    if machine is None:
        result["state_sha256"] = _digest(_semantic(result))
        return result

    axes = []
    for axis_name, axis in tuple(getattr(machine, "rotary_axes", {}).items()):
        vector = getattr(axis, "rotation_vector", None)
        direction = [
            _finite(getattr(vector, name, None)) for name in ("x", "y", "z")
        ]
        joint_origin = [
            _finite(value) for value in tuple(getattr(axis, "joint_origin", ()) or ())
        ]
        minimum = _finite(getattr(axis, "min_limit", None))
        maximum = _finite(getattr(axis, "max_limit", None))
        wrap = getattr(axis, "wrap_strategy", "unwound")
        axes.append(
            {
                "axis_name": str(axis_name)[:16],
                "command_letter": str(getattr(axis, "name", axis_name))[:16],
                "direction": direction,
                "joint_origin_mm": joint_origin,
                "minimum_degrees": minimum,
                "maximum_degrees": maximum,
                "wrap_strategy": str(getattr(wrap, "value", wrap))[:32],
            }
        )
    result.update(
        available=True,
        configuration_name=str(getattr(machine, "name", "") or "")[:160],
        rotary_axes=axes,
    )
    result["state_sha256"] = _digest(_semantic(result))
    return result


def job_state(
    job: Any,
    *,
    operation_limit: int = MAX_JOB_OPERATIONS,
    tool_limit: int = MAX_JOB_TOOLS,
    model_limit: int = MAX_JOB_MODELS,
) -> dict[str, Any]:
    document = getattr(job, "Document", None)
    if document is None or not is_job(job) or not _is_usable(job, document):
        raise NativeManufactureError(
            "The target is not a current CAM Job.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    model_group = tuple(getattr(getattr(job, "Model", None), "Group", ()) or ())
    tool_group = tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
    operation_group = tuple(
        getattr(getattr(job, "Operations", None), "Group", ()) or ()
    )
    models = []
    for resource in model_group:
        public = _public_model(job, resource)
        try:
            state = _job_model_state(public)
        except NativeManufactureError:
            state = _fallback_resource_state(public)
        models.append(
            {
                "object_name": state.get("object_name"),
                "label": state.get("label"),
                "type_id": state.get("type_id"),
                "state_sha256": state.get("state_sha256"),
                "resource_name": str(getattr(resource, "Name", "") or ""),
                "resource_placement": _placement_state(resource),
                "resource_state_sha256": _digest(
                    {
                        "source_state_sha256": state.get("state_sha256"),
                        "placement": _placement_state(resource),
                    }
                ),
            }
        )
    operation_states = []
    for operation in operation_group:
        try:
            operation_states.append(operation_reference_state(operation))
        except NativeManufactureError:
            operation_states.append(_fallback_resource_state(operation))
    tool_states = [_tool_state(value) for value in tool_group]
    result = concise_object(job)
    result["settings_sha256"] = _property_state_sha256(job)
    result["machine"] = configured_machine_state(job)
    try:
        from Path.Main.JobSetup import setup_configuration_state

        result["configuration"] = setup_configuration_state(job)
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM setup configuration could not be read.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    result.update(
        models=models[:model_limit],
        tools=tool_states[:tool_limit],
        operations=operation_states[:operation_limit],
        counts={
            "models": len(model_group),
            "tools": len(tool_group),
            "operations": len(operation_group),
            "active_operations": sum(
                1 for value in operation_group if operation_active_state(value)
            ),
        },
        models_truncated=len(model_group) > model_limit,
        operations_truncated=len(operation_group) > operation_limit,
        tools_truncated=len(tool_group) > tool_limit,
    )
    stock = getattr(job, "Stock", None)
    if stock is not None:
        try:
            from Path.Main.JobStock import stock_configuration_state

            result["stock"] = stock_configuration_state(job)
        except Exception as exc:
            raise NativeManufactureError(
                "The CAM stock configuration could not be read.",
                error_code="NATIVE_MANUFACTURE_STATE_INVALID",
            ) from exc
        stock_shape = getattr(stock, "Shape", None)
        bounds = _bounds(stock_shape) if stock_shape is not None else None
        if bounds is not None:
            result["stock"]["bounds"] = bounds
    postprocessor = str(getattr(job, "PostProcessor", "") or "").strip()
    if postprocessor:
        result["postprocessor"] = postprocessor[:160]
    digest_source = _semantic(result)
    for name in (
        "models",
        "tools",
        "operations",
        "models_truncated",
        "tools_truncated",
        "operations_truncated",
    ):
        digest_source.pop(name, None)
    digest_source["model_states"] = [
        value.get("resource_state_sha256") for value in models
    ]
    digest_source["tool_states"] = [
        value.get("state_sha256") for value in tool_states
    ]
    digest_source["operation_states"] = [
        value.get("state_sha256") for value in operation_states
    ]
    result["state_sha256"] = _digest(digest_source)
    return result


def capture_other_job_states(
    document: Any,
    owned_jobs: tuple[Any, ...],
) -> tuple[tuple[Any, str], ...]:
    """Freeze every CAM Job not owned by one exact operation boundary."""

    owned = tuple(owned_jobs)
    return tuple(
        (candidate, str(job_state(candidate)["state_sha256"]))
        for candidate in tuple(document.Objects)
        if is_job(candidate)
        and not any(candidate is owned_job for owned_job in owned)
    )


def other_job_states_are_current(
    document: Any,
    frozen: tuple[tuple[Any, str], ...],
) -> bool:
    """Return whether every frozen CAM Job retains identity and semantic state."""

    try:
        return all(
            getattr(job, "Document", None) is document
            and document.getObject(str(job.Name)) is job
            and job_state(job).get("state_sha256") == expected
            for job, expected in frozen
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _resolve_target(
    document: Any,
    value: Mapping[str, Any],
    *,
    state_reader,
    noun: str,
) -> tuple[Any, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeManufactureError(
            f"The exact {noun} target must contain object_name and expected_state_sha256.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    name = str(value.get("object_name") or "").strip()
    expected = str(value.get("expected_state_sha256") or "").strip()
    obj = document.getObject(name) if name else None
    if obj is None or getattr(obj, "Document", None) is not document:
        raise NativeManufactureError(
            f"The exact {noun} target no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    current = state_reader(obj)
    if current.get("state_sha256") != expected:
        current_target = {
            "object_name": name,
            "expected_state_sha256": current.get("state_sha256"),
        }
        raise NativeManufactureError(
            f"The exact {noun} target changed after turn start.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": name,
                "current_state_sha256": current.get("state_sha256"),
                "target": current_target,
            },
        )
    return obj, current


def resolve_job_target(document: Any, value: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    return _resolve_target(document, value, state_reader=job_state, noun="CAM Job")


def resolve_operation_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeManufactureError(
            "The exact CAM operation target must contain object_name and "
            "expected_state_sha256.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    name = str(value.get("object_name") or "").strip()
    expected = str(value.get("expected_state_sha256") or "").strip()
    operation = document.getObject(name) if name else None
    if operation is None or getattr(operation, "Document", None) is not document:
        raise NativeManufactureError(
            "The exact CAM operation target no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        )
    current = operation_state(operation)
    reference = operation_reference_state(operation)
    if expected not in {
        current.get("state_sha256"),
        reference.get("state_sha256"),
    }:
        current_target = {
            "object_name": name,
            "expected_state_sha256": current.get("state_sha256"),
        }
        raise NativeManufactureError(
            "The exact CAM operation target changed after turn start.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": name,
                "current_state_sha256": current.get("state_sha256"),
                "target": current_target,
            },
        )
    return operation, current


def resolve_model_target(document: Any, value: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    return _resolve_target(
        document,
        value,
        state_reader=candidate_model_state,
        noun="CAM model",
    )


def resolve_tool_controller_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    return _resolve_target(
        document,
        value,
        state_reader=tool_controller_state,
        noun="CAM tool controller",
    )


def resolve_tool_bit_target(
    document: Any,
    value: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    try:
        return _resolve_target(
            document,
            value,
            state_reader=tool_bit_state,
            noun="CAM ToolBit",
        )
    except NativeManufactureError as exc:
        name = str(value.get("object_name") or "") if isinstance(value, Mapping) else ""
        controller = document.getObject(name) if name else None
        tool = getattr(controller, "Tool", None)
        if (
            exc.error_code == "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID"
            and tool is not None
            and getattr(tool, "Document", None) is document
        ):
            current = tool_bit_state(tool)
            raise NativeManufactureError(
                "The exact ToolBit target is read_setup tools[].tool, not its controller.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                repair={
                    "target": {
                        "object_name": current["object_name"],
                        "expected_state_sha256": current["state_sha256"],
                    }
                },
            ) from exc
        raise
