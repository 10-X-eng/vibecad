# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed FEM calculator expressions compiled to the native VTK filter."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzePost import (
    _copy_none_field_color,
    _label,
    _owning_post_pipeline,
    _post_parent_group,
)
from VibeCADNativeAnalyzePostSampling import post_point_fields
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_EXPRESSION_TOKENS = 64
MAX_REPAIR_FIELDS = 16
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]{0,79}$")

_RESERVED_RESULT_NAMES = frozenset(
    {
        "coords",
        "coordsX",
        "coordsY",
        "coordsZ",
        "iHat",
        "jHat",
        "kHat",
    }
)

_SCALAR_UNARY = {
    "absolute": "abs",
    "cosine": "cos",
    "sine": "sin",
    "tangent": "tan",
    "exponential": "exp",
    "natural_log": "log",
    "square_root": "sqrt",
}

_BINARY_SYMBOLS = {
    "add": "+",
    "subtract": "-",
    "multiply": "*",
    "divide": "/",
}


@dataclass(frozen=True, slots=True)
class PreparedPostCalculator:
    boundary: AnalyzeCreationBoundary
    source: PreparedResultTarget
    parent_group: Any
    pipeline: Any
    label: str
    result_field: str
    result_unit: str
    expression: str
    expression_type: str
    token_count: int
    referenced_fields: tuple[tuple[str, int], ...]
    invalid_mode: str
    replacement_value: float
    source_point_count: int
    source_was_visible: bool


def _expression_error(message: str, *, token_index: int, **repair: Any):
    raise NativeAnalyzeError(
        message,
        error_code="NATIVE_ANALYZE_CALCULATOR_EXPRESSION_INVALID",
        repair={"token_index": token_index, **repair},
    )


def _field_alias(name: str) -> str:
    return name.replace(" ", "_")


def _available_operands(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for field in fields[:MAX_REPAIR_FIELDS]:
        components = int(field["components"])
        if components == 1:
            choices = ["scalar"]
        elif components == 3:
            choices = ["vector", "x", "y", "z"]
        elif components == 6:
            choices = ["xx", "yy", "zz", "xy", "yz", "zx"]
        else:
            choices = []
        result.append(
            {
                "name": field["name"],
                "components": components,
                "allowed_components": choices,
                **({"unit": field["unit"]} if field.get("unit") else {}),
            }
        )
    return result


def _field_operand(
    token: Mapping[str, Any],
    fields: list[dict[str, Any]],
    token_index: int,
) -> tuple[str, str, tuple[str, int]]:
    if set(token) != {"kind", "name", "component"}:
        _expression_error(
            "A field token must contain only kind, name, and component.",
            token_index=token_index,
        )
    name = str(token["name"] or "").strip()
    matches = [field for field in fields if field["name"] == name]
    if not matches:
        _expression_error(
            f"Field {name!r} is not available on the exact source.",
            token_index=token_index,
            available_fields=_available_operands(fields),
            available_fields_truncated=len(fields) > MAX_REPAIR_FIELDS,
        )
    if len(matches) != 1:
        _expression_error(
            f"Field {name!r} is ambiguous on the exact source.",
            token_index=token_index,
            matching_field_count=len(matches),
        )
    selected = matches[0]
    alias = _field_alias(name)
    if not _VARIABLE.fullmatch(alias):
        _expression_error(
            f"Field {name!r} has no valid native calculator variable name.",
            token_index=token_index,
            native_alias=alias,
        )
    alias_collisions = sorted(
        {
            str(field["name"])
            for field in fields
            if str(field["name"]) != name
            and _field_alias(str(field["name"])) == alias
        }
    )
    if alias_collisions:
        _expression_error(
            f"Field {name!r} shares its native calculator variable with another field.",
            token_index=token_index,
            native_alias=alias,
            conflicting_fields=alias_collisions[:MAX_REPAIR_FIELDS],
            conflicting_fields_truncated=len(alias_collisions) > MAX_REPAIR_FIELDS,
        )
    component = str(token["component"] or "").strip().lower()
    components = int(selected["components"])
    if components == 1:
        choices = {"scalar": (alias, "scalar")}
    elif components == 3:
        choices = {
            "vector": (alias, "vector"),
            "x": (alias + "_X", "scalar"),
            "y": (alias + "_Y", "scalar"),
            "z": (alias + "_Z", "scalar"),
        }
    elif components == 6:
        choices = {
            name.lower(): (alias + "_" + name, "scalar")
            for name in ("XX", "YY", "ZZ", "XY", "YZ", "ZX")
        }
    else:
        choices = {}
    if component not in choices:
        _expression_error(
            f"Component {component!r} is invalid for field {name!r}.",
            token_index=token_index,
            field_components=components,
            allowed_components=list(choices),
        )
    expression, value_type = choices[component]
    return expression, value_type, (name, components)


def _pop(stack: list[tuple[str, str]], count: int, index: int):
    if len(stack) < count:
        _expression_error(
            "The calculator expression does not provide enough operands.",
            token_index=index,
            required_operands=count,
            available_operands=len(stack),
        )
    result = stack[-count:]
    del stack[-count:]
    return result


def _apply_operator(
    stack: list[tuple[str, str]], operation: str, token_index: int
) -> None:
    if operation in _BINARY_SYMBOLS:
        (left_expression, left_type), (right_expression, right_type) = _pop(
            stack, 2, token_index
        )
        if operation in {"add", "subtract"}:
            if left_type != right_type:
                _expression_error(
                    f"{operation} requires two operands of the same type.",
                    token_index=token_index,
                    left_type=left_type,
                    right_type=right_type,
                )
            result_type = left_type
        elif operation == "multiply":
            if left_type == right_type == "scalar":
                result_type = "scalar"
            elif {left_type, right_type} == {"scalar", "vector"}:
                result_type = "vector"
            else:
                _expression_error(
                    "multiply accepts scalar × scalar or scalar × vector.",
                    token_index=token_index,
                    left_type=left_type,
                    right_type=right_type,
                )
        else:
            if right_type != "scalar" or left_type not in {"scalar", "vector"}:
                _expression_error(
                    "divide requires a scalar divisor.",
                    token_index=token_index,
                    left_type=left_type,
                    right_type=right_type,
                )
            result_type = left_type
        symbol = _BINARY_SYMBOLS[operation]
        stack.append((f"({left_expression}{symbol}{right_expression})", result_type))
        return
    if operation == "power":
        (left_expression, left_type), (right_expression, right_type) = _pop(
            stack, 2, token_index
        )
        if left_type != "scalar" or right_type != "scalar":
            _expression_error(
                "power requires two scalar operands.", token_index=token_index
            )
        stack.append((f"pow({left_expression},{right_expression})", "scalar"))
        return
    if operation in {"cross", "dot"}:
        (left_expression, left_type), (right_expression, right_type) = _pop(
            stack, 2, token_index
        )
        if left_type != "vector" or right_type != "vector":
            _expression_error(
                f"{operation} requires two vector operands.", token_index=token_index
            )
        result_type = "vector" if operation == "cross" else "scalar"
        stack.append((f"{operation}({left_expression},{right_expression})", result_type))
        return
    (expression, value_type), = _pop(stack, 1, token_index)
    if operation == "negate":
        stack.append((f"(-{expression})", value_type))
    elif operation in _SCALAR_UNARY:
        if value_type != "scalar":
            _expression_error(
                f"{operation} requires one scalar operand.", token_index=token_index
            )
        stack.append((f"{_SCALAR_UNARY[operation]}({expression})", "scalar"))
    elif operation in {"magnitude", "normalize"}:
        if value_type != "vector":
            _expression_error(
                f"{operation} requires one vector operand.", token_index=token_index
            )
        native = "mag" if operation == "magnitude" else "norm"
        result_type = "scalar" if operation == "magnitude" else "vector"
        stack.append((f"{native}({expression})", result_type))
    else:
        _expression_error(
            f"Calculator operator {operation!r} is not supported.",
            token_index=token_index,
        )


def _compile_expression(
    value: Any, fields: list[dict[str, Any]]
) -> tuple[str, str, int, tuple[tuple[str, int], ...]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_EXPRESSION_TOKENS:
        raise NativeAnalyzeError(
            "expression must contain 1 to 64 reverse-Polish tokens."
        )
    stack: list[tuple[str, str]] = []
    referenced: list[tuple[str, int]] = []
    for index, raw_token in enumerate(value):
        if not isinstance(raw_token, Mapping):
            _expression_error("Each expression token must be an object.", token_index=index)
        token = dict(raw_token)
        kind = str(token.get("kind") or "")
        if kind == "number":
            if set(token) != {"kind", "value"}:
                _expression_error(
                    "A number token must contain only kind and value.", token_index=index
                )
            raw_number = token["value"]
            if type(raw_number) not in {int, float} or not math.isfinite(float(raw_number)):
                _expression_error("A number token must be finite.", token_index=index)
            number = float(raw_number)
            if abs(number) > 1.0e100:
                _expression_error(
                    "A number token magnitude must not exceed 1e100.", token_index=index
                )
            stack.append((format(number, ".17g"), "scalar"))
        elif kind == "field":
            expression, value_type, reference = _field_operand(token, fields, index)
            stack.append((expression, value_type))
            if reference not in referenced:
                referenced.append(reference)
        elif kind == "coordinate":
            if set(token) != {"kind", "component"}:
                _expression_error(
                    "A coordinate token must contain only kind and component.",
                    token_index=index,
                )
            component = str(token["component"] or "").lower()
            choices = {
                "vector": ("coords", "vector"),
                "x": ("coordsX", "scalar"),
                "y": ("coordsY", "scalar"),
                "z": ("coordsZ", "scalar"),
            }
            if component not in choices:
                _expression_error(
                    "coordinate.component must be vector, x, y, or z.",
                    token_index=index,
                )
            stack.append(choices[component])
        elif kind == "basis_vector":
            if set(token) != {"kind", "axis"}:
                _expression_error(
                    "A basis-vector token must contain only kind and axis.",
                    token_index=index,
                )
            axis = str(token["axis"] or "").lower()
            if axis not in {"x", "y", "z"}:
                _expression_error("basis_vector.axis must be x, y, or z.", token_index=index)
            stack.append(({"x": "iHat", "y": "jHat", "z": "kHat"}[axis], "vector"))
        elif kind == "operator":
            if set(token) != {"kind", "operation"}:
                _expression_error(
                    "An operator token must contain only kind and operation.",
                    token_index=index,
                )
            _apply_operator(stack, str(token["operation"] or ""), index)
        else:
            _expression_error(
                f"Expression token kind {kind!r} is not supported.", token_index=index
            )
    if len(stack) != 1:
        _expression_error(
            "The calculator expression must reduce to exactly one result.",
            token_index=len(value),
            remaining_operands=len(stack),
        )
    expression, result_type = stack[0]
    if len(expression) > 4096:
        raise NativeAnalyzeError("The compiled calculator expression exceeds 4096 characters.")
    return expression, result_type, len(value), tuple(referenced)


def _unit(value: Any) -> str:
    unit = str(value or "").strip()
    if not unit or len(unit) > 32 or any(ord(character) < 0x20 for character in unit):
        raise NativeAnalyzeError("result_unit must contain 1 to 32 visible characters.")
    if unit == "1":
        return unit
    try:
        import FreeCAD as App

        App.Units.Quantity(f"1 {unit}")
    except Exception as exc:
        raise NativeAnalyzeError(
            f"result_unit {unit!r} is not a valid FreeCAD engineering unit."
        ) from exc
    return unit


def _invalid_policy(value: Any) -> tuple[str, float]:
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("invalid_values must be one typed policy object.")
    mode = str(value.get("mode") or "")
    if mode == "reject" and set(value) == {"mode"}:
        return mode, 0.0
    if mode == "replace" and set(value) == {"mode", "value"}:
        raw = value["value"]
        if type(raw) in {int, float} and math.isfinite(float(raw)):
            return mode, float(raw)
    raise NativeAnalyzeError(
        "invalid_values must be either {'mode':'reject'} or "
        "{'mode':'replace','value':<finite number>}."
    )


def prepare_post_calculator(
    document: Any,
    document_uid: str,
    *,
    source: Any,
    label: Any,
    result_field: Any,
    result_unit: Any,
    expression: Any,
    invalid_values: Any,
) -> PreparedPostCalculator:
    source_target = prepare_result_target(
        document,
        document_uid,
        source,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    parent_group = _post_parent_group(
        document, source_target.result, source_target.kind
    )
    pipeline = _owning_post_pipeline(document, parent_group)
    fields = post_point_fields(source_target.result)
    name = str(result_field or "").strip()
    if not _FIELD_NAME.fullmatch(name) or name in _RESERVED_RESULT_NAMES:
        raise NativeAnalyzeError(
            "result_field must begin with a letter, contain only letters, digits, "
            "spaces, or underscores, and not use a reserved calculator variable."
        )
    if name in {field["name"] for field in fields}:
        raise NativeAnalyzeError(
            f"result_field {name!r} already exists on the exact source.",
            error_code="NATIVE_ANALYZE_FIELD_NAME_CONFLICT",
        )
    compiled, result_type, token_count, referenced = _compile_expression(
        expression, fields
    )
    invalid_mode, replacement = _invalid_policy(invalid_values)
    source_state = result_state(source_target.result, include_ranges=False)
    source_point_count = int(source_state["point_count"])
    if not bool(source_state["data_available"]) or source_point_count < 1:
        raise NativeAnalyzeError(
            "The exact post-processing source has no points to calculate."
        )
    return PreparedPostCalculator(
        creation_boundary(document),
        source_target,
        parent_group,
        pipeline,
        _label(label),
        name,
        _unit(result_unit),
        compiled,
        result_type,
        token_count,
        referenced,
        invalid_mode,
        replacement,
        source_point_count,
        bool(source_target.result.ViewObject.Visibility),
    )


def _require_current_source(document: Any, prepared: PreparedPostCalculator) -> None:
    source = prepared.source.result
    source_state = result_state(source, include_ranges=False)
    if (
        not is_live(document, source)
        or not is_live(document, prepared.parent_group)
        or not is_live(document, prepared.pipeline)
        or _post_parent_group(document, source, prepared.source.kind)
        is not prepared.parent_group
        or _owning_post_pipeline(document, prepared.parent_group) is not prepared.pipeline
        or source_state["state_sha256"] != prepared.source.expected_state_sha256
    ):
        raise NativeAnalyzeError(
            "The exact post-processing graph changed after calculator preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    fields = {field["name"]: int(field["components"]) for field in post_point_fields(source)}
    if any(fields.get(name) != components for name, components in prepared.referenced_fields):
        raise NativeAnalyzeError(
            "A referenced calculator field changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.result_field in fields:
        raise NativeAnalyzeError(
            "The requested calculator result field appeared after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )


def create_post_calculator(
    document: Any, prepared: PreparedPostCalculator
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPostCalculator):
        raise TypeError("prepared must be a PreparedPostCalculator")
    require_boundary(document, prepared.boundary)
    _require_current_source(document, prepared)
    source = prepared.source.result
    try:
        calculator = document.addObject(
            "Fem::FemPostCalculatorFilter",
            document.getUniqueObjectName("Calculator"),
        )
        prepared = assign_prepared_label(calculator, prepared)
        prepared.parent_group.addObject(calculator)
        calculator.FieldName = prepared.result_field
        calculator.Function = prepared.expression
        calculator.ResultUnit = prepared.result_unit
        calculator.ReplaceInvalid = prepared.invalid_mode == "replace"
        calculator.ReplacementValue = prepared.replacement_value
        calculator.ViewObject.DisplayMode = "Surface"
        calculator.ViewObject.SelectionStyle = "BoundBox"
        _copy_none_field_color(source, calculator)
        replaced = (source,) if prepared.source_was_visible else ()
        publish_operation(
            document,
            prepared.boundary,
            calculator,
            replaced_inputs=replaced,
        )
        source.ViewObject.Visibility = False
        calculator.ViewObject.Visibility = True
    except NativeAnalyzeError:
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The FEM calculator filter could not be created: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    changed = tuple(dict.fromkeys((prepared.parent_group, prepared.pipeline)))
    return NativeMutationDraft(
        value={"prepared": prepared, "calculator": calculator, "replaced": replaced},
        recompute_targets=(calculator, prepared.parent_group, prepared.pipeline),
        created=(object_identity(calculator),),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _array_summary(calculator: Any, prepared: PreparedPostCalculator) -> dict[str, Any]:
    try:
        array = calculator.getDataSet().GetPointData().GetArray(prepared.result_field)
        tuple_count = int(array.GetNumberOfTuples())
        components = int(array.GetNumberOfComponents())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The calculator did not create its requested result field.",
            error_code="NATIVE_ANALYZE_CALCULATOR_EVALUATION_FAILED",
        ) from exc
    expected_components = 1 if prepared.expression_type == "scalar" else 3
    if components != expected_components or tuple_count != prepared.source_point_count:
        raise NativeAnalyzeError(
            "The calculator result shape does not match its typed expression and source.",
            error_code="NATIVE_ANALYZE_CALCULATOR_EVALUATION_FAILED",
            repair={
                "expected_components": expected_components,
                "actual_components": components,
                "expected_value_count": prepared.source_point_count,
                "actual_value_count": tuple_count,
            },
        )
    try:
        from vtk.util.numpy_support import vtk_to_numpy
        import numpy as np

        values = vtk_to_numpy(array)
        finite = np.isfinite(values)
        finite_count = int(np.count_nonzero(finite))
        value_count = int(values.size)
    except Exception:
        value_count = tuple_count * components
        finite_count = sum(
            math.isfinite(float(array.GetComponent(row, column)))
            for row in range(tuple_count)
            for column in range(components)
        )
    if finite_count != value_count:
        raise NativeAnalyzeError(
            "The calculator result contains non-finite values.",
            error_code="NATIVE_ANALYZE_CALCULATOR_NONFINITE_RESULT",
            repair={
                "invalid_value_policy": prepared.invalid_mode,
                "finite_component_values": finite_count,
                "total_component_values": value_count,
            },
        )
    raw_range = array.GetRange(-1 if components > 1 else 0)
    lower, upper = float(raw_range[0]), float(raw_range[1])
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise NativeAnalyzeError("The calculator result has no finite range.")
    return {
        "name": prepared.result_field,
        "value_type": prepared.expression_type,
        "components": components,
        "value_count": tuple_count,
        "unit": prepared.result_unit,
        "range": [float(format(lower, ".15g")), float(format(upper, ".15g"))],
        "range_component": "scalar" if components == 1 else "magnitude",
    }


def verify_post_calculator(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    calculator = draft.value["calculator"]
    replaced = draft.value["replaced"]
    source = prepared.source.result
    verify_operation_block(
        document,
        prepared.boundary,
        calculator,
        replaced_inputs=replaced,
    )
    field_summary = _array_summary(calculator, prepared)
    state = result_state(calculator)
    state_field = next(
        (field for field in state["fields"] if field["name"] == prepared.result_field),
        None,
    )
    checks = {
        "live calculator": is_live(document, calculator),
        "calculator type": str(calculator.TypeId) == "Fem::FemPostCalculatorFilter",
        "parent group membership": calculator in tuple(prepared.parent_group.Group or ()),
        "owning pipeline": _owning_post_pipeline(document, calculator) is prepared.pipeline,
        "source retained": is_live(document, source),
        "label": str(calculator.Label) == prepared.label,
        "field name": str(calculator.FieldName) == prepared.result_field,
        "function": str(calculator.Function) == prepared.expression,
        "result unit": str(calculator.ResultUnit) == prepared.result_unit,
        "replace invalid": bool(calculator.ReplaceInvalid)
        == (prepared.invalid_mode == "replace"),
        "replacement value": math.isclose(
            float(calculator.ReplacementValue),
            prepared.replacement_value,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ),
        "result field state": state_field is not None
        and state_field.get("unit") == prepared.result_unit,
        "calculator visible": bool(calculator.ViewObject.Visibility),
        "source hidden": not bool(source.ViewObject.Visibility),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The FEM calculator failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    return {
        "created_calculator": state,
        "result_field": field_summary,
        "expression": {
            "notation": "reverse_polish_tokens",
            "token_count": prepared.token_count,
            "result_type": prepared.expression_type,
            "referenced_fields": [name for name, _components in prepared.referenced_fields],
        },
        "invalid_values": (
            {"mode": "reject"}
            if prepared.invalid_mode == "reject"
            else {"mode": "replace", "value": prepared.replacement_value}
        ),
        "source": result_reference_state(source),
        "pipeline": result_reference_state(prepared.pipeline),
        "presentation": {
            "visible_object": str(calculator.Name),
            "hidden_source": str(source.Name),
        },
    }
