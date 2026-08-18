# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider adapter for the compact Native Sketch tool surface."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBatchRuntime import NativeSketchBatchRuntime
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchControlRuntime import NativeSketchControlRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchInspectRuntime import NativeSketchInspectRuntime
from VibeCADNativeSketchInsertion import (
    sketch_constraint_refs,
    sketch_geometry_refs,
)
from VibeCADNativeSketchPresentationRuntime import NativeSketchPresentationRuntime
from VibeCADNativeSketchProviderSchema import (
    CONSTRAIN_OPERATIONS,
    DIMENSION_OPERATIONS,
    DRAW_OPERATIONS,
    EDIT_CONSTRAINT_OPERATIONS,
    EDIT_GEOMETRY_OPERATIONS,
    FOCUSED_CONSTRAINT_OPERATIONS,
    TRANSFORM_OPERATIONS,
    sketch_runtime_variant_parameters,
)
from VibeCADNativeSketchRevision import (
    NativeSketchRevisionConflict,
    require_active_sketch,
    require_sketch_revision,
    sketch_read_result,
    sketch_revision,
)
from VibeCADNativeSketchState import serialize_sketch_state
from VibeCADNativeState import NativeCallTicket


_GEOMETRY_OPERATIONS = frozenset(
    {
        *DRAW_OPERATIONS,
        *TRANSFORM_OPERATIONS,
        *EDIT_GEOMETRY_OPERATIONS,
        "create_fillet",
        "create_chamfer",
        "project_external_geometry",
        "intersect_external_geometry",
        "carbon_copy",
        "trim",
        "split",
        "extend",
        "delete_geometry",
    }
)
_CONSTRAINT_OPERATIONS = frozenset(
    {
        *CONSTRAIN_OPERATIONS,
        *FOCUSED_CONSTRAINT_OPERATIONS.values(),
        *DIMENSION_OPERATIONS,
        *EDIT_CONSTRAINT_OPERATIONS,
    }
)


class NativeSketchProviderFailure(NativeSketchError):
    def __init__(
        self,
        operation: str,
        message: str,
        revision: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.current_revision = revision
        self.details = dict(details or {})

    def failure(self) -> dict[str, Any]:
        if not self.details.get("repair"):
            return {
                "error_code": "NATIVE_SKETCH_OPERATION_INVALID",
                "message": str(self),
                "current_revision": self.current_revision,
                "repair": {
                    "tool": "sketch.inspect",
                    "arguments": {"operation": "read_state"},
                    "failed_operation": self.operation,
                },
                "retry_same_call": False,
            }
        result = dict(self.details)
        result["message"] = str(self)
        result["current_revision"] = self.current_revision
        result.setdefault("retry_same_call", False)
        repair = result.get("repair")
        if isinstance(repair, Mapping):
            repair = dict(repair)
            arguments = repair.get("arguments")
            if isinstance(arguments, Mapping):
                arguments = dict(arguments)
                arguments.setdefault("revision", self.current_revision)
                repair["arguments"] = arguments
            result["repair"] = repair
        return result


def _external_reference_count(sketch: Any) -> int:
    return len(list(getattr(sketch, "ExternalGeometry", []) or []))


def _external_geometry_count(sketch: Any) -> int:
    return max(0, len(list(getattr(sketch, "ExternalGeo", []) or [])) - 2)


def _compact_mutation_result(
    result: Mapping[str, Any],
    *,
    operation: str = "",
) -> dict[str, Any]:
    """Keep only state needed to choose the next Sketch operation."""

    compact = {
        name: value
        for name, value in result.items()
        if name not in {"receipt", "sketch", "profile", "solver"}
    }
    for source, target, converter in (
        ("geometry", "geometry_ref", sketch_geometry_refs),
        ("geometries", "geometry_refs", sketch_geometry_refs),
        ("constraints", "constraint_refs", sketch_constraint_refs),
    ):
        value = compact.pop(source, None)
        if isinstance(value, Mapping):
            references = converter((value,))
            compact[target] = references[0]
        elif isinstance(value, (list, tuple)):
            compact[target] = converter(value)

    compact.pop("internal_geometries", None)
    compact.pop("internal_constraints", None)
    compact.pop("internal_geometry_refs", None)
    compact.pop("internal_constraint_refs", None)

    if operation in DRAW_OPERATIONS:
        public_geometry = list(compact.get("geometry_refs") or [])
        single = compact.get("geometry_ref")
        if isinstance(single, Mapping):
            public_geometry.insert(0, dict(single))
        for name in ("lines", "arcs"):
            records = compact.pop(name, None)
            if isinstance(records, (list, tuple)):
                public_geometry.extend(sketch_geometry_refs(records))
        for source, target in (("spline", "geometry_ref"), ("handle", "handle_ref")):
            record = compact.pop(source, None)
            if isinstance(record, Mapping):
                compact[target] = sketch_geometry_refs((record,))[0]
        if public_geometry:
            seen: set[tuple[Any, Any]] = set()
            unique_geometry = []
            for reference in public_geometry:
                key = (
                    reference.get("geometry_id"),
                    reference.get("geometry_index"),
                )
                if key not in seen:
                    seen.add(key)
                    unique_geometry.append(reference)
            if len(unique_geometry) == 1 and "geometry_ref" in compact:
                compact["geometry_ref"] = unique_geometry[0]
                compact.pop("geometry_refs", None)
            else:
                compact["geometry_refs"] = unique_geometry
                compact.pop("geometry_ref", None)
        allowed = {
            "assistant_undo_available",
            "geometry_count",
            "constraint_count",
            "geometry_ref",
            "geometry_refs",
            "handle_ref",
        }
        compact = {name: value for name, value in compact.items() if name in allowed}

    profile = result.get("profile")
    if isinstance(profile, Mapping):
        compact["profile"] = {
            "closed": bool(profile.get("closed_profile")),
            "face_buildable": bool(profile.get("face_maker_succeeded")),
            "closed_wire_count": int(profile.get("closed_wire_count", 0) or 0),
            "open_wire_count": int(profile.get("open_wire_count", 0) or 0),
        }
    solver = result.get("solver")
    if isinstance(solver, Mapping):
        solver_status = {
            "degrees_of_freedom": int(solver.get("degrees_of_freedom", 0) or 0),
            "fully_constrained": bool(solver.get("fully_constrained")),
        }
        for name in (
            "conflicting_constraints",
            "redundant_constraints",
            "partially_redundant_constraints",
            "malformed_constraints",
        ):
            values = list(solver.get(name) or [])
            if values:
                solver_status[name] = values
        compact["solver"] = solver_status
    return compact


class NativeSketchProviderRuntime:
    """Supply host-owned targets to the existing exact operation runtimes."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._geometry = NativeSketchGeometryRuntime(context)
        self._constraint = NativeSketchConstraintRuntime(context)
        self._batch = NativeSketchBatchRuntime(context)
        self._inspect = NativeSketchInspectRuntime(context)
        self._presentation = NativeSketchPresentationRuntime(context)
        self._control = NativeSketchControlRuntime(context)

    def _source_state(self, values: Mapping[str, Any]) -> dict[str, int]:
        source_ref = values.get("source_sketch")
        if not isinstance(source_ref, Mapping):
            return {}
        object_name = str(source_ref.get("object_name") or "").strip()
        source = self._context.document.getObject(object_name) if object_name else None
        if (
            source is None
            or str(getattr(source, "TypeId", "") or "")
            != "Sketcher::SketchObject"
        ):
            return {}
        return {
            "expected_source_geometry_count": int(
                getattr(source, "GeometryCount", 0) or 0
            ),
            "expected_source_constraint_count": int(
                getattr(source, "ConstraintCount", 0) or 0
            ),
            "expected_source_external_reference_count": _external_reference_count(
                source
            ),
            "expected_source_external_geometry_count": _external_geometry_count(source),
        }

    def _runtime_arguments(
        self,
        sketch: Any,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        parameters = sketch_runtime_variant_parameters(operation)
        allowed = set(dict(parameters.get("properties") or {}))
        values = {
            name: value
            for name, value in arguments.items()
            if name not in {"operation", "revision"}
        }
        host = {
            "sketch": {"object_name": str(getattr(sketch, "Name", "") or "")},
            "expected_geometry_count": int(getattr(sketch, "GeometryCount", 0) or 0),
            "expected_constraint_count": int(
                getattr(sketch, "ConstraintCount", 0) or 0
            ),
            "expected_external_reference_count": _external_reference_count(sketch),
            "expected_external_geometry_count": _external_geometry_count(sketch),
            **self._source_state(values),
        }
        values.update({name: value for name, value in host.items() if name in allowed})
        return {"operation": operation, **values}

    @staticmethod
    def _with_revision(
        result: Mapping[str, Any],
        sketch: Any,
        operation: str,
    ) -> dict[str, Any]:
        return {
            **_compact_mutation_result(result, operation=operation),
            "revision": sketch_revision(sketch),
        }

    def execute(
        self,
        capability_name: str,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("A Sketch provider call requires argument data.")
        operation = str(arguments.get("operation") or "")
        sketch = require_active_sketch(self._context)
        if capability_name == "sketch.inspect" and operation == "read_state":
            try:
                requested_revision = arguments.get("revision")
                if requested_revision is not None:
                    require_sketch_revision(sketch, requested_revision)
                return sketch_read_result(
                    sketch,
                    {
                        name: value
                        for name, value in arguments.items()
                        if name not in {"operation", "revision"}
                    },
                )
            except NativeSketchError as exc:
                raise NativeSketchProviderFailure(
                    operation,
                    str(exc),
                    sketch_revision(sketch),
                ) from exc
        require_sketch_revision(sketch, arguments.get("revision"))
        runtime_arguments = self._runtime_arguments(sketch, operation, arguments)

        try:
            if operation in _GEOMETRY_OPERATIONS:
                result = self._geometry.mutate_geometry(runtime_arguments, ticket=ticket)
            elif operation in _CONSTRAINT_OPERATIONS:
                result = self._constraint.mutate_constraint(
                    runtime_arguments,
                    ticket=ticket,
                )
            elif capability_name == "sketch.batch":
                result = self._batch.create(runtime_arguments, ticket=ticket)
            elif capability_name == "sketch.inspect":
                result = self._inspect.inspect(runtime_arguments)
            elif capability_name == "sketch.presentation":
                result = self._presentation.present(runtime_arguments)
            elif capability_name in {"sketch.control", "sketch.finish"}:
                return self._control.control(runtime_arguments, ticket=ticket)
            else:
                raise RuntimeError(
                    f"Native Sketch capability {capability_name!r} has no exact route."
                )
        except NativeSketchRevisionConflict:
            raise
        except NativeSketchError as exc:
            details = exc.failure()
            raise NativeSketchProviderFailure(
                operation,
                str(exc),
                sketch_revision(sketch),
                details=details if details.get("repair") else None,
            ) from exc
        exact_sketch = require_active_sketch(self._context)
        if exact_sketch is not sketch:
            raise RuntimeError("The human-opened Sketch changed during the operation.")
        return self._with_revision(result, sketch, operation)

    def current_state(self) -> dict[str, Any]:
        sketch = require_active_sketch(self._context)
        return {
            "revision": sketch_revision(sketch),
            "state": serialize_sketch_state(sketch),
        }
