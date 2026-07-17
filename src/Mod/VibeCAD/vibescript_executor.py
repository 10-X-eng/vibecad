# SPDX-License-Identifier: LGPL-2.1-or-later

"""Transaction-scoped VibeScript executor for a caller-owned document.

Runs VibeScript model source against the document supplied by its caller,
wrapped in a transaction so any failure restores that document's prior state::

    openTransaction -> exec source -> enforce contract -> commitTransaction
                                   \\-> any failure     -> abortTransaction

This module never imports FreeCAD. Production calls supply the temporary
document owned by the isolated ``FreeCADCmd`` worker; tests may supply a stub.
The executor does not receive the user's live GUI document.

The execution budget is a trace-based guard: a runaway Python loop in model
source raises ``ExecutionBudgetExceeded`` instead of hanging its worker.
``ExecutionBudgetExceeded`` derives from ``BaseException`` so model-source
``except Exception`` blocks cannot swallow it, and the tracer's tripped flag
is re-checked after exec as a second line of defense against bare handlers.

Import policy is enforced statically (``VibeCADVibeScript.validate_source``
rejects disallowed imports before source ever reaches this module). The
execution namespace deliberately exposes the *real* ``__import__``: FreeCAD's
own machinery resolves ``__import__`` from the executing frame's builtins,
and a restricted runtime hook breaks ViewProvider attachment.

Script ``print()`` output is captured into a bounded per-execution buffer and
returned as ``stdout`` on both success and failure payloads, so model source
can report intermediate values without exception-driven probing.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from typing import Any

import VibeCADGeometry
import vibescript_api

__all__ = [
    "ALLOWED_IMPORT_ROOTS",
    "ContractViolation",
    "DEFAULT_MAX_OPERATIONS",
    "DEFAULT_MAX_SECONDS",
    "ExecutionBudgetExceeded",
    "MAX_STDOUT_CHARS",
    "SCRIPT_FILENAME",
    "TRANSACTION_NAME",
    "bop_check",
    "execute_model",
    "shape_facts",
]

SCRIPT_FILENAME = "<vibecad-vibescript>"
TRANSACTION_NAME = "VibeScript model"

DEFAULT_MAX_OPERATIONS = 5_000_000
# Wall-clock budget covers the whole transaction, including native FreeCAD
# recompute time (pads, booleans, patterns), which dominates on real parts.
# 30s proved too tight for multi-boolean models; 120s keeps runaway scripts
# bounded while leaving room for legitimate heavy recomputes.
DEFAULT_MAX_SECONDS = 120.0

MAX_STDOUT_CHARS = 65_536

ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "FreeCAD",
        "Part",
        "PartDesign",
        "Sketcher",
        "vibescript_api",
        "collections",
        "dataclasses",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "operator",
        "statistics",
        "typing",
    }
)


class ExecutionBudgetExceeded(BaseException):
    """The model source exceeded its execution budget.

    Derives from ``BaseException`` on purpose: model-source ``except
    Exception`` handlers must not be able to swallow a budget trip.
    """


class ContractViolation(vibescript_api.VibeScriptError):
    """The executed source did not satisfy the VibeScript output contract."""


# --------------------------------------------------------------------------
# Restricted execution namespace
# --------------------------------------------------------------------------


_BUILTIN_ALLOWLIST = (
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "ImportError",
    "Exception",
    "IndexError",
    "KeyError",
    "NameError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
    "__build_class__",
    "abs",
    "all",
    "any",
    "bool",
    "classmethod",
    "dict",
    "dir",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hasattr",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "map",
    "max",
    "min",
    "object",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "staticmethod",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
)

# Private frame-builtin entries required by native Python bindings. These are
# runtime protocol details, not names VibeScript source is allowed to use.
_FRAME_INTERNAL_BUILTINS = frozenset({"__orig_import__"})

#: Names resolvable inside VibeScript source without a policy hint on NameError.
_ALLOWED_BUILTIN_NAMES = frozenset(_BUILTIN_ALLOWLIST) | {"__import__"}


class _StdoutBuffer:
    """Bounded sink for script ``print()`` output.

    Stops accepting text after ``limit`` characters and reports the
    truncation in :meth:`getvalue` so payloads stay JSON-safe and small.
    """

    __slots__ = ("chunks", "length", "limit", "truncated")

    def __init__(self, limit: int = MAX_STDOUT_CHARS) -> None:
        self.chunks: list[str] = []
        self.length = 0
        self.limit = limit
        self.truncated = False

    def write(self, text: str) -> None:
        if self.truncated:
            return
        space = self.limit - self.length
        if len(text) > space:
            text = text[:space]
            self.truncated = True
        if text:
            self.chunks.append(text)
            self.length += len(text)

    def getvalue(self) -> str:
        value = "".join(self.chunks)
        if self.truncated:
            value += f"\n[stdout truncated at {self.limit} characters]"
        return value


def _sandbox_print(buffer: _StdoutBuffer) -> Callable[..., None]:
    """Return a ``print`` replacement that writes to ``buffer``.

    ``file`` and ``flush`` keywords are accepted for signature compatibility
    but ignored: script output always goes to the captured buffer.
    """

    def sandbox_print(
        *values: Any,
        sep: str | None = " ",
        end: str | None = "\n",
        file: Any = None,
        flush: bool = False,
    ) -> None:
        del file, flush
        joiner = " " if sep is None else str(sep)
        terminator = "\n" if end is None else str(end)
        buffer.write(joiner.join(str(value) for value in values) + terminator)

    return sandbox_print


def _restricted_builtins(stdout: _StdoutBuffer) -> dict[str, Any]:
    import builtins

    allowed = {name: getattr(builtins, name) for name in _BUILTIN_ALLOWLIST}
    allowed["print"] = _sandbox_print(stdout)
    # Import policy is enforced *statically* (AST validation before execution).
    # The runtime ``__import__`` must be the real one: FreeCAD's own machinery
    # (e.g. ViewProvider attachment during ``doc.addObject``) resolves
    # ``__import__`` from the executing frame's builtins, and a restricted
    # hook here vetoes FreeCAD importing its own Gui modules, leaving objects
    # without ViewProviders. Static validation already rejects imports the
    # script author writes; runtime imports triggered by FreeCAD internals
    # must succeed unconditionally.
    allowed["__import__"] = builtins.__import__
    # PySide/Shiboken's feature-aware importer fetches ``__orig_import__``
    # from the *executing frame's* builtins. Omitting it makes libshiboken
    # call Py_FatalError during an otherwise ordinary import, terminating the
    # whole FreeCAD process. Preserve the interpreter-installed callable in
    # this private frame dictionary; source validation prevents model code
    # from resolving the private name directly.
    orig_import = getattr(builtins, "__orig_import__", None)
    if callable(orig_import):
        allowed["__orig_import__"] = orig_import
    return allowed


def _build_namespace(
    document: Any,
    parameters: Mapping[str, Any] | vibescript_api.Params | None,
    environment: Mapping[str, Any] | None,
    stdout: _StdoutBuffer,
) -> dict[str, Any]:
    if isinstance(parameters, vibescript_api.Params):
        params = parameters
    else:
        params = vibescript_api.Params(**dict(parameters or {}))
    namespace: dict[str, Any] = {
        "__builtins__": _restricted_builtins(stdout),
        "__name__": "__vibecad_vibescript__",
        "doc": document,
        "params": params,
    }
    for name in vibescript_api.__all__:
        namespace[name] = getattr(vibescript_api, name)
    if environment:
        namespace.update(environment)
    return namespace


# --------------------------------------------------------------------------
# Execution budget
# --------------------------------------------------------------------------


class _BudgetTracer:
    """Trace function that bounds line-event count and wall-clock time."""

    __slots__ = ("deadline", "executed", "max_operations", "max_seconds", "tripped")

    def __init__(self, max_operations: int, max_seconds: float) -> None:
        self.max_operations = max_operations
        self.max_seconds = max_seconds
        self.deadline = time.monotonic() + max_seconds
        self.executed = 0
        self.tripped: str | None = None

    def _trip(self, reason: str) -> None:
        self.tripped = reason
        raise ExecutionBudgetExceeded(reason)

    def __call__(self, frame: Any, event: str, arg: Any) -> "_BudgetTracer":
        if self.tripped is not None:
            raise ExecutionBudgetExceeded(self.tripped)
        self.executed += 1
        if self.executed > self.max_operations:
            self._trip(
                "execution budget exceeded: more than "
                f"{self.max_operations} traced operations; remove unbounded "
                "loops from the model source."
            )
        if not (self.executed & 0x03FF) and time.monotonic() > self.deadline:
            self._trip(
                f"execution budget exceeded: ran longer than {self.max_seconds:g} "
                "seconds (wall clock, including native FreeCAD recompute time); "
                "simplify the model source or reduce recompute-heavy features."
            )
        return self


# --------------------------------------------------------------------------
# Shape facts (duck-typed over FreeCAD Part shapes)
# --------------------------------------------------------------------------


def shape_facts(shape: Any) -> dict[str, Any]:
    """Extract JSON-safe geometric facts from a FreeCAD-like shape."""
    facts: dict[str, Any] = {}
    is_valid = getattr(shape, "isValid", None)
    if callable(is_valid):
        facts["valid"] = bool(is_valid())
    for key, attribute in (
        ("solid_count", "Solids"),
        ("face_count", "Faces"),
        ("edge_count", "Edges"),
        ("vertex_count", "Vertexes"),
    ):
        items = getattr(shape, attribute, None)
        if items is not None:
            facts[key] = len(items)
    for key, attribute in (("volume_mm3", "Volume"), ("surface_area_mm2", "Area")):
        value = getattr(shape, attribute, None)
        if isinstance(value, Real) and not isinstance(value, bool):
            facts[key] = float(value)
    # Prefer the optimal (tight) bounding box: the default BoundBox is
    # computed from control points, so B-spline surfaces overshoot it and
    # scripts asserting on bounds see phantom oversize.
    bounds = None
    optimal = getattr(shape, "optimalBoundingBox", None)
    if callable(optimal):
        try:
            bounds = optimal()
        except (RuntimeError, TypeError, ValueError):
            # FreeCAD raises Base.FreeCADError (a RuntimeError) when the
            # tight box cannot be computed; fall back to the loose box.
            bounds = None
    if bounds is None:
        bounds = getattr(shape, "BoundBox", None)
    if bounds is not None:
        try:
            facts["bounds_mm"] = {
                "min": [
                    float(bounds.XMin),
                    float(bounds.YMin),
                    float(bounds.ZMin),
                ],
                "max": [
                    float(bounds.XMax),
                    float(bounds.YMax),
                    float(bounds.ZMax),
                ],
                "size": [
                    float(bounds.XLength),
                    float(bounds.YLength),
                    float(bounds.ZLength),
                ],
            }
        except (AttributeError, TypeError, ValueError):
            pass
    return facts


def _validation_defect_detail(result: Mapping[str, Any]) -> str | None:
    """Summarize structured worker diagnostics for contract error messages."""
    details: list[str] = []
    for stage in ("brep", "bop"):
        report = result.get(stage)
        if not isinstance(report, Mapping):
            continue
        defects = report.get("defects")
        if not isinstance(defects, Sequence) or isinstance(defects, (str, bytes)):
            continue
        for defect in defects:
            if not isinstance(defect, Mapping):
                details.append(f"{stage.upper()}: {defect!s}")
                continue
            status = str(defect.get("status") or "unknown defect")
            shape_type = str(defect.get("shape_type") or "shape")
            shape_index = defect.get("shape_index")
            location = (
                f"{shape_type} {shape_index}" if shape_index is not None else shape_type
            )
            details.append(f"{stage.upper()}: {status} ({location})")
    if details:
        return "; ".join(details)
    error = result.get("error")
    return str(error) if error else None


def bop_check(shape: Any) -> tuple[bool | None, str | None]:
    """Run deep BREP/BOP validation in the isolated geometry worker.

    Returns ``(True, None)`` for a valid shape and ``(False, detail)`` for
    reported defects or a native worker crash. Validation is unknown when the
    shape cannot be exported (including duck-typed test doubles), the worker
    is unavailable, or another infrastructure failure prevents a result.
    """
    result = VibeCADGeometry.validate_shape(shape)
    valid = result.get("valid")
    if valid is True:
        return True, None
    detail = _validation_defect_detail(result)
    if valid is False:
        return False, detail or "unspecified defect"
    if result.get("failure_code") == "GEOMETRY_WORKER_CRASHED":
        return False, detail or "the isolated geometry validator crashed"
    if result.get("failure_code") == "BREP_EXPORT_UNAVAILABLE":
        return None, None
    return None, detail


# --------------------------------------------------------------------------
# Contract enforcement
# --------------------------------------------------------------------------


def _object_names(document: Any) -> set[str]:
    objects = getattr(document, "Objects", None) or []
    return {str(getattr(item, "Name", "")) for item in objects}


def _rollback_snapshot(
    document: Any,
    *,
    shape_names: set[str] | None = None,
    copy_shapes: bool = True,
    detailed_shape_facts: bool = True,
    scoped_metadata: bool = False,
    only_names: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Capture rollback evidence, retaining geometry only inside the mutation scope.

    ``None`` preserves the executor's strict whole-document behavior.  A name
    set is used by isolated-engine commits, where untrusted source never sees
    the live document and trusted publication code has an explicit dependency
    closure. ``copy_shapes=False`` retains immutable TopoShape handles rather
    than deep-copying geometry. ``scoped_metadata=True`` records only identity
    outside that closure, which still detects unrelated additions/removals
    without walking every property in a large document. ``only_names`` avoids
    enumerating unrelated objects for trusted publication commits.
    """
    records: list[dict[str, Any]] = []
    objects = (
        list(getattr(document, "Objects", []) or [])
        if only_names is None
        else [
            obj
            for obj in (
                document.getObject(name) for name in sorted(only_names)
            )
            if obj is not None
        ]
    )
    for obj in objects:
        object_name = str(getattr(obj, "Name", "") or "")
        capture_shape = shape_names is None or object_name in shape_names
        capture_metadata = not scoped_metadata or capture_shape
        record: dict[str, Any] = {
            "name": object_name,
            "type": str(getattr(obj, "TypeId", "") or ""),
        }
        if capture_metadata:
            record.update(
                {
                    "label": str(getattr(obj, "Label", "") or ""),
                    "state": sorted(
                        str(value)
                        for value in list(getattr(obj, "State", []) or [])
                    ),
                }
            )
        shape = getattr(obj, "Shape", None)
        if shape is not None and capture_shape:
            try:
                if detailed_shape_facts:
                    is_null = bool(shape.isNull())
                    record["shape"] = {
                        "null": is_null,
                        "type": None if is_null else str(shape.ShapeType),
                        "solids": len(list(getattr(shape, "Solids", []) or [])),
                        "faces": len(list(getattr(shape, "Faces", []) or [])),
                        "edges": len(list(getattr(shape, "Edges", []) or [])),
                        "vertices": len(list(getattr(shape, "Vertexes", []) or [])),
                        "volume": round(float(getattr(shape, "Volume", 0.0)), 12),
                        "area": round(float(getattr(shape, "Area", 0.0)), 12),
                        "length": round(float(getattr(shape, "Length", 0.0)), 12),
                    }
                    if not is_null:
                        bounds = shape.BoundBox
                        record["shape"]["bounds"] = [
                            round(float(value), 12)
                            for value in (
                                bounds.XMin,
                                bounds.YMin,
                                bounds.ZMin,
                                bounds.XMax,
                                bounds.YMax,
                                bounds.ZMax,
                            )
                        ]
                record["_shape_restore"] = shape.copy() if copy_shapes else shape
            except Exception:
                record["shape"] = "unavailable"
        placement = getattr(obj, "Placement", None) if capture_metadata else None
        if placement is not None:
            try:
                matrix = placement.toMatrix()
                record["placement"] = [
                    round(float(getattr(matrix, name)), 12)
                    for name in (
                        "A11", "A12", "A13", "A14",
                        "A21", "A22", "A23", "A24",
                        "A31", "A32", "A33", "A34",
                    )
                ]
            except Exception:
                pass
        for property_name in (
            list(getattr(obj, "PropertiesList", []) or [])
            if capture_metadata
            else []
        ):
            if not str(property_name).startswith("VibeCAD"):
                continue
            try:
                value = getattr(obj, property_name)
            except Exception:
                continue
            if isinstance(value, (str, bool, int, float)):
                record.setdefault("vibecad_properties", {})[property_name] = value
        records.append(record)
    return {item["name"]: item for item in records}


def _rollback_difference(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    before_names = set(before)
    after_names = set(after)
    return {
        "added": sorted(after_names - before_names),
        "removed": sorted(before_names - after_names),
        "changed": sorted(
            name
            for name in before_names & after_names
            if not _rollback_records_equal(before[name], after[name])
        ),
    }


def _rollback_records_equal(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_public = {
        key: value for key, value in before.items() if not key.startswith("_")
    }
    after_public = {
        key: value for key, value in after.items() if not key.startswith("_")
    }
    if before_public != after_public:
        return False
    if before_public.get("type") in {"App::Line", "App::Plane"}:
        # Origin axes and planes are infinite construction geometry. OCCT
        # boolean/sample equivalence is undefined for them, while their exact
        # native type, placement, label, and state were compared above.
        return True
    shape_facts = before_public.get("shape")
    if isinstance(shape_facts, dict) and any(
        abs(float(value)) >= 1.0e90
        for value in list(shape_facts.get("bounds") or [])
    ):
        # FreeCAD origin axes and planes are represented by artificial infinite
        # shapes. Boolean equivalence checks are undefined for those carriers;
        # their exact type, placement, topology, and bounds were compared above.
        return True
    before_shape = before.get("_shape_restore")
    after_shape = after.get("_shape_restore")
    if before_shape is None or after_shape is None:
        return before_shape is after_shape
    return _shapes_geometrically_equivalent(before_shape, after_shape)


def _shapes_geometrically_equivalent(before: Any, after: Any) -> bool:
    try:
        before_null = bool(before.isNull())
        after_null = bool(after.isNull())
        if before_null or after_null:
            return before_null == after_null
        if before.isSame(after):
            return True
        volume = max(abs(float(before.Volume)), abs(float(after.Volume)), 1.0)
        if list(getattr(before, "Solids", []) or []) or list(
            getattr(after, "Solids", []) or []
        ):
            tolerance = max(1.0e-7, volume * 1.0e-9)
            return (
                float(before.cut(after).Volume) <= tolerance
                and float(after.cut(before).Volume) <= tolerance
            )
        area = max(abs(float(before.Area)), abs(float(after.Area)), 1.0)
        if list(getattr(before, "Faces", []) or []) or list(
            getattr(after, "Faces", []) or []
        ):
            tolerance = max(1.0e-7, area * 1.0e-9)
            return (
                float(before.cut(after).Area) <= tolerance
                and float(after.cut(before).Area) <= tolerance
            )
        return _edge_samples_lie_on_shape(before, after) and _edge_samples_lie_on_shape(
            after, before
        )
    except Exception:
        return False


def _edge_samples_lie_on_shape(source: Any, target: Any) -> bool:
    import Part

    bounds = source.BoundBox
    tolerance = max(1.0e-7, float(bounds.DiagonalLength) * 1.0e-9)
    for edge in list(getattr(source, "Edges", []) or []):
        for point in list(edge.discretize(Number=9) or []):
            if float(Part.Vertex(point).distToShape(target)[0]) > tolerance:
                return False
    for vertex in list(getattr(source, "Vertexes", []) or []):
        if float(vertex.distToShape(target)[0]) > tolerance:
            return False
    return True


def _new_objects(document: Any, before_names: set[str]) -> list[Any]:
    objects = getattr(document, "Objects", None) or []
    return [
        item for item in objects if str(getattr(item, "Name", "")) not in before_names
    ]


def _check_new_sketches(new_objects: list[Any]) -> None:
    for item in new_objects:
        type_id = str(getattr(item, "TypeId", ""))
        if not type_id.startswith("Sketcher::"):
            continue
        try:
            vibescript_api.assert_fully_constrained(item)
        except vibescript_api.SketchValidationError as error:
            raise ContractViolation(str(error)) from error


def _enforce_contract(
    document: Any,
    namespace: dict[str, Any],
    expected_outputs: list[str],
    new_objects: list[Any],
) -> list[dict[str, Any]]:
    result = namespace.get("result")
    if not isinstance(result, dict) or not result:
        raise ContractViolation(
            "The VibeScript source must assign a non-empty dict mapping output "
            "names to document objects to `result`."
        )
    actual_outputs = [str(key) for key in result]
    if expected_outputs and actual_outputs != expected_outputs:
        raise ContractViolation(
            "result keys must exactly match expected_outputs in the same "
            f"order; expected {expected_outputs!r}, received {actual_outputs!r}."
        )

    recompute = getattr(document, "recompute", None)
    if callable(recompute):
        recompute()

    _check_new_sketches(new_objects)

    outputs: list[dict[str, Any]] = []
    for key, value in result.items():
        shape = getattr(value, "Shape", None)
        if value is None or shape is None:
            raise ContractViolation(
                f"result[{key!r}] is missing an output body: expected a "
                "document object exposing Shape, received "
                f"{type(value).__name__}."
            )
        is_valid = getattr(value, "isValid", None)
        if callable(is_valid) and not is_valid():
            raise ContractViolation(
                f"result[{key!r}] ({getattr(value, 'Name', key)!s}) failed to "
                "recompute cleanly."
            )
        facts = shape_facts(shape)
        if not facts.get("valid", True):
            raise ContractViolation(f"result[{key!r}] has an invalid shape.")
        solid_count = int(facts.get("solid_count", 0))
        if solid_count != 1:
            raise ContractViolation(
                f"result[{key!r}] must contain exactly one solid; received "
                f"{solid_count}. Return physical components as separate named "
                "outputs."
            )
        bop_ok, bop_detail = bop_check(shape)
        if bop_ok is False:
            raise ContractViolation(
                f"result[{key!r}] ({getattr(value, 'Name', key)!s}) passed "
                "recompute but OCCT's deep validity check (BOPCheck) reports "
                f"defects: {bop_detail or 'unspecified defect'}. Defective "
                "booleans usually come from tangent face contact or plane "
                "faces piercing spline/loft surfaces; overlap fused geometry "
                "by >=0.5mm or attach at a loft's own end-cap section."
            )
        outputs.append(
            {
                "key": key,
                "object_name": str(getattr(value, "Name", "")) or None,
                "label": str(getattr(value, "Label", "")) or None,
                "shape": facts,
            }
        )
    return outputs


def _declared_interfaces(
    namespace: dict[str, Any], result: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    raw = namespace.get("interfaces", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ContractViolation(
            "interfaces must be a dict mapping stable interface names to "
            "published-output selection contracts."
        )
    allowed_query_fields = {
        "type",
        "element_type",
        "expected_count",
        "geometry_type",
        "normal",
        "normal_tolerance_degrees",
        "direction",
        "direction_tolerance_degrees",
        "radius",
        "radius_tolerance",
        "min_area",
        "max_area",
        "min_length",
        "max_length",
        "near_point",
        "max_distance",
    }
    interfaces: dict[str, dict[str, Any]] = {}
    for raw_name, raw_definition in raw.items():
        name = str(raw_name or "").strip()
        if not name or not name.replace("_", "").isalnum() or not name[0].isalpha():
            raise ContractViolation(
                f"Interface name {raw_name!r} must start with a letter and "
                "contain only letters, numbers, and underscores."
            )
        if not isinstance(raw_definition, dict):
            raise ContractViolation(f"interfaces[{name!r}] must be an object.")
        unexpected = set(raw_definition) - {"output", "selection", "description"}
        if unexpected:
            raise ContractViolation(
                f"interfaces[{name!r}] has unsupported fields: "
                + ", ".join(sorted(unexpected))
            )
        output = str(raw_definition.get("output") or "")
        if output not in result:
            raise ContractViolation(
                f"interfaces[{name!r}].output must name one result output; "
                f"received {output!r}."
            )
        selection = raw_definition.get("selection")
        if not isinstance(selection, dict):
            raise ContractViolation(
                f"interfaces[{name!r}].selection must be an object."
            )
        mode = str(selection.get("type") or "")
        if mode == "origin":
            if set(selection) != {"type"}:
                raise ContractViolation(
                    f"interfaces[{name!r}] origin selection accepts only type."
                )
        elif mode == "query":
            unexpected_query = set(selection) - allowed_query_fields
            if unexpected_query:
                raise ContractViolation(
                    f"interfaces[{name!r}].selection has unsupported fields: "
                    + ", ".join(sorted(unexpected_query))
                )
            element_type = str(selection.get("element_type") or "")
            if element_type not in {"edge", "face"}:
                raise ContractViolation(
                    f"interfaces[{name!r}].selection.element_type must be "
                    "'edge' or 'face'."
                )
            count = selection.get("expected_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ContractViolation(
                    f"interfaces[{name!r}].selection.expected_count must be "
                    "an integer of at least 1."
                )
        else:
            raise ContractViolation(
                f"interfaces[{name!r}].selection.type must be 'origin' or "
                "the topology-stable 'query' mode. Exact FaceN/EdgeN names "
                "are not valid published interfaces."
            )
        description = str(raw_definition.get("description") or "").strip()
        interfaces[name] = {
            "output": output,
            "selection": dict(selection),
            **({"description": description} if description else {}),
        }
    return interfaces


# --------------------------------------------------------------------------
# Failure evidence
# --------------------------------------------------------------------------


def _feature_entry(item: Any) -> dict[str, Any]:
    """JSON-safe shape health facts for one document object."""
    entry: dict[str, Any] = {
        "object_name": str(getattr(item, "Name", "")) or None,
        "label": str(getattr(item, "Label", "")) or None,
        "type_id": str(getattr(item, "TypeId", "")) or None,
    }
    try:
        shape = getattr(item, "Shape", None)
    except (AttributeError, RuntimeError, TypeError):
        shape = None
    if shape is None:
        entry["has_shape"] = False
        entry["defective"] = False
        return entry
    entry["has_shape"] = True
    try:
        facts = shape_facts(shape)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        facts = {}
    entry["is_valid"] = facts.get("valid")
    for key in ("solid_count", "volume_mm3", "bounds_mm"):
        if key in facts:
            entry[key] = facts[key]
    bop_ok, bop_detail = bop_check(shape)
    entry["bop_ok"] = bop_ok
    if bop_detail:
        entry["bop_errors"] = bop_detail
    entry["defective"] = entry["is_valid"] is False or bop_ok is False
    return entry


def _collect_feature_report(
    document: Any, before_names: set[str]
) -> dict[str, Any] | None:
    """Shape health facts for every object this run created, in tree order.

    Runs on the failure path *before* the transaction is aborted, while the
    created objects still exist. Defective OCCT booleans compute
    "successfully" and only break the *next* feature, so the report flags
    the first defective feature — the true culprit — instead of leaving the
    agent chasing the downstream failure. Best-effort by design: it must
    never raise, because it runs while the original failure is being
    reported.
    """
    try:
        entries: list[dict[str, Any]] = []
        first_defective: str | None = None
        for item in _new_objects(document, before_names):
            entry = _feature_entry(item)
            if entry.get("defective") and first_defective is None:
                first_defective = entry.get("object_name") or entry.get("label")
            entries.append(entry)
        if not entries:
            return None
        return {"features": entries, "first_defective": first_defective}
    except Exception:  # noqa: BLE001 - must never mask the original failure
        return None


def _script_frames(exc: BaseException, source: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    frames: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        tb = current.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            if frame.f_code.co_filename == SCRIPT_FILENAME:
                item: dict[str, Any] = {
                    "line": int(tb.tb_lineno),
                    "function": str(frame.f_code.co_name),
                }
                if 0 < tb.tb_lineno <= len(lines):
                    item["source"] = lines[tb.tb_lineno - 1].strip()
                frames.append(item)
            tb = tb.tb_next
        if frames:
            break
        current = current.__cause__ or current.__context__
    return frames[-16:]


def _policy_hint_for(exc: BaseException) -> str | None:
    """Explain a NameError caused by a policy-excluded builtin.

    Returns a hint only when the missing name is a real Python builtin that
    the sandbox deliberately does not expose; ordinary typos get no hint.
    """
    if not isinstance(exc, NameError):
        return None
    name = getattr(exc, "name", None)
    if not isinstance(name, str) or not name or name in _ALLOWED_BUILTIN_NAMES:
        return None
    import builtins

    if not hasattr(builtins, name):
        return None
    return (
        f"{name!r} is a Python builtin that the VibeScript sandbox excludes "
        "by policy. Use the vibescript_api helpers and the allowed builtin "
        "subset (math, containers, iteration, introspection, print) instead."
    )


def _exception_kind(exc: BaseException) -> str:
    if isinstance(exc, ExecutionBudgetExceeded):
        return "execution_budget_exceeded"
    if isinstance(exc, ContractViolation):
        return "contract_violation"
    if isinstance(exc, vibescript_api.SketchValidationError):
        return "sketch_validation_failure"
    if isinstance(exc, vibescript_api.VibeScriptError):
        return "vibescript_api_failure"
    if isinstance(exc, SyntaxError):
        return "syntax_error"
    if isinstance(exc, AssertionError):
        return "design_assertion_failure"
    return "python_execution_failure"


def _failure_payload(
    exc: BaseException,
    source: str,
    *,
    opened: bool,
    aborted: bool,
    budget: dict[str, Any] | None = None,
    stdout: str = "",
    feature_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frames = _script_frames(exc, source)
    location: dict[str, Any] | None = frames[-1] if frames else None
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        location = {"line": int(exc.lineno), "source": (exc.text or "").strip()}
    hint = _policy_hint_for(exc)
    error = str(exc) if hint is None else f"{exc}. {hint}"
    payload: dict[str, Any] = {
        "ok": False,
        "error": error,
        "exception_type": type(exc).__name__,
        "exception_kind": _exception_kind(exc),
        "traceback": "".join(traceback.format_exception(exc, limit=16)),
        "script_frames": frames,
        "stdout": stdout,
        "transaction": {"opened": opened, "committed": False, "aborted": aborted},
    }
    if hint is not None:
        payload["policy_hint"] = hint
    if location is not None:
        payload["failure_location"] = location
    if budget is not None:
        payload["budget"] = budget
    if feature_report is not None:
        payload["feature_report"] = feature_report
    return payload


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def execute_model(
    document: Any,
    source: str,
    *,
    expected_outputs: Sequence[str],
    parameters: Mapping[str, Any] | vibescript_api.Params | None = None,
    environment: Mapping[str, Any] | None = None,
    max_operations: int = DEFAULT_MAX_OPERATIONS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    transaction_name: str = TRANSACTION_NAME,
    before_exec: Callable[[Any], None] | None = None,
    after_contract: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute VibeScript ``source`` against ``document`` atomically.

    Returns a JSON-safe report: ``{"ok": True, "outputs": [...], ...}`` on
    success, ``{"ok": False, "error": ..., "failure_location": ...}`` on any
    failure. Failure payloads additionally carry ``feature_report``: per-
    feature shape health facts (validity, solid count, volume, bounds,
    BOPCheck) for every object the run created, collected before the abort,
    with the first defective feature flagged. On failure the document
    transaction is aborted, leaving the document in its exact prior state. ``KeyboardInterrupt``/``SystemExit``
    still abort the transaction but propagate to the caller.

    ``before_exec(document)`` runs inside the transaction before the source
    executes (engines use it to delete objects owned by a prior revision).
    ``after_contract(context)`` runs inside the transaction after contract
    enforcement and immediately before commit; ``context`` carries ``result``
    (the output-name -> document-object mapping), ``new_objects``, and
    ``outputs`` (JSON-safe facts). Either hook may raise to abort atomically.
    """
    if max_operations <= 0:
        raise ValueError(f"max_operations must be positive, got {max_operations}.")
    if max_seconds <= 0:
        raise ValueError(f"max_seconds must be positive, got {max_seconds:g}.")

    expected = [str(item) for item in expected_outputs]
    stdout = _StdoutBuffer()
    try:
        compiled = compile(source, SCRIPT_FILENAME, "exec")
        namespace = _build_namespace(document, parameters, environment, stdout)
    except (SyntaxError, ValueError, TypeError, vibescript_api.VibeScriptError) as exc:
        return _failure_payload(
            exc, source, opened=False, aborted=False, stdout=stdout.getvalue()
        )

    tracer = _BudgetTracer(max_operations, max_seconds)

    def _budget() -> dict[str, Any]:
        return {
            "max_operations": max_operations,
            "max_seconds": max_seconds,
            "operations_used": tracer.executed,
        }

    before_names: set[str] | None = None
    rollback_before = _rollback_snapshot(document)
    original_undo_mode = getattr(document, "UndoMode", None)
    enabled_undo = False
    if isinstance(original_undo_mode, int) and original_undo_mode == 0:
        try:
            document.UndoMode = 1
            enabled_undo = True
        except Exception as exc:
            return _failure_payload(
                RuntimeError(
                    "VibeScript cannot guarantee atomic execution because "
                    f"FreeCAD undo transactions could not be enabled: {exc}"
                ),
                source,
                opened=False,
                aborted=False,
                stdout=stdout.getvalue(),
            )
    document.openTransaction(transaction_name)
    booked_transaction = getattr(document, "getBookedTransactionID", None)
    if callable(booked_transaction) and int(booked_transaction() or 0) == 0:
        if enabled_undo:
            document.UndoMode = original_undo_mode
        return _failure_payload(
            RuntimeError(
                "FreeCAD refused the VibeScript document transaction; no "
                "source was executed."
            ),
            source,
            opened=False,
            aborted=False,
            stdout=stdout.getvalue(),
        )
    try:
        if before_exec is not None:
            before_exec(document)
        before_names = _object_names(document)
        previous_trace = sys.gettrace()
        sys.settrace(tracer)
        try:
            exec(compiled, namespace)  # noqa: S102 - policy-validated source
        finally:
            sys.settrace(previous_trace)
        if tracer.tripped is not None:
            raise ExecutionBudgetExceeded(tracer.tripped)
        new_objects = _new_objects(document, before_names)
        created_object_names = [
            str(getattr(item, "Name", "")) for item in new_objects
        ]
        outputs = _enforce_contract(document, namespace, expected, new_objects)
        declared_interfaces = _declared_interfaces(namespace, namespace["result"])
        if after_contract is not None:
            after_contract(
                {
                    "result": dict(namespace["result"]),
                    "new_objects": list(new_objects),
                    "outputs": outputs,
                    "interfaces": declared_interfaces,
                }
            )
        document.commitTransaction()
        if enabled_undo:
            document.UndoMode = original_undo_mode
        return {
            "ok": True,
            "outputs": outputs,
            "interfaces": declared_interfaces,
            "created_objects": created_object_names,
            "stdout": stdout.getvalue(),
            "transaction": {"opened": True, "committed": True, "aborted": False},
            "budget": _budget(),
        }
    except (Exception, ExecutionBudgetExceeded) as exc:
        # Collect per-feature evidence while the created objects still
        # exist: abortTransaction destroys them.
        feature_report = (
            _collect_feature_report(document, before_names)
            if before_names is not None
            else None
        )
        rollback_error = None
        try:
            document.abortTransaction()
            document.recompute()
            rollback_after = _rollback_snapshot(document)
            difference = _rollback_difference(rollback_before, rollback_after)
            if any(difference.values()):
                rollback_error = (
                    "FreeCAD transaction rollback did not restore the pre-run "
                    "document state: "
                    f"added={difference['added']}, "
                    f"removed={difference['removed']}, "
                    f"changed={difference['changed']}."
                )
        except Exception as rollback_exc:
            rollback_error = f"FreeCAD transaction rollback raised: {rollback_exc}"
        finally:
            if enabled_undo:
                try:
                    document.UndoMode = original_undo_mode
                except Exception as mode_exc:
                    rollback_error = (
                        f"{rollback_error + '; ' if rollback_error else ''}"
                        f"undo mode restoration failed: {mode_exc}"
                    )
        payload = _failure_payload(
            exc,
            source,
            opened=True,
            aborted=rollback_error is None,
            budget=_budget(),
            stdout=stdout.getvalue(),
            feature_report=feature_report,
        )
        if rollback_error is not None:
            payload["rollback_error"] = rollback_error
            payload["error"] = (
                f"{payload['error']} Rollback failure: {rollback_error}"
            )
        return payload
    except BaseException:
        document.abortTransaction()
        if enabled_undo:
            document.UndoMode = original_undo_mode
        raise
