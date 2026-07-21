# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical immutable API for Inspection VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("comparison", "group", "measurement", "report")
_OUTPUT_TYPES = (
    "inspection_group",
    "inspection_feature",
    "measurement",
    "report",
)
_MEASUREMENT_METRICS = (
    "minimum",
    "maximum",
    "mean",
    "rms",
    "absolute_maximum",
    "within_tolerance_fraction",
    "measured_count",
    "unmeasured_count",
)
_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_LABEL_CHARS = 256
_MAX_NOMINALS = 16
_MAX_GROUP_MEMBERS = 64
_MAX_DISTANCE = 1.0e9
_MISSING = object()


class InspectionAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> InspectionAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return InspectionAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    result = float(value)
    if not math.isfinite(result):
        raise _error(operation, parameter, "must be finite", value)
    if result < minimum or (strict_minimum and result == minimum):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if result > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return result


def _label(operation: str, value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_LABEL_CHARS
        or "\0" in value
    ):
        raise _error(
            operation,
            "label",
            f"must be a string of at most {_MAX_LABEL_CHARS} characters without nulls",
            value,
        )
    return value


def _reference(operation: str, parameter: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            parameter,
            "must contain exactly document_uid and object_name",
            value,
        )
    document_uid = value.get("document_uid")
    object_name = value.get("object_name")
    if (
        not isinstance(document_uid, str)
        or not document_uid
        or document_uid != document_uid.strip()
        or len(document_uid) > 256
        or "\0" in document_uid
    ):
        raise _error(
            operation,
            f"{parameter}.document_uid",
            "must be a non-empty string of at most 256 characters without surrounding whitespace",
            document_uid,
        )
    if (
        not isinstance(object_name, str)
        or len(object_name) > 128
        or _OBJECT_NAME.fullmatch(object_name) is None
    ):
        raise _error(
            operation,
            f"{parameter}.object_name",
            "must be an exact FreeCAD internal object name",
            object_name,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _tolerance(value: Any) -> tuple[float, float]:
    if isinstance(value, bool):
        raise _error(
            "comparison",
            "tolerance",
            "must be a non-negative symmetric number or [lower, upper]",
            value,
        )
    if isinstance(value, (int, float)):
        magnitude = _number(
            "comparison",
            "tolerance",
            value,
            minimum=0.0,
            maximum=_MAX_DISTANCE,
        )
        return (-magnitude, magnitude)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error(
            "comparison",
            "tolerance",
            "must be a non-negative symmetric number or [lower, upper]",
            value,
        )
    lower = _number(
        "comparison",
        "tolerance[0]",
        value[0],
        minimum=-_MAX_DISTANCE,
        maximum=_MAX_DISTANCE,
    )
    upper = _number(
        "comparison",
        "tolerance[1]",
        value[1],
        minimum=-_MAX_DISTANCE,
        maximum=_MAX_DISTANCE,
    )
    if lower > upper:
        raise _error(
            "comparison",
            "tolerance",
            "lower bound must not exceed upper bound",
            value,
        )
    return (lower, upper)


def _domain_value(
    operation: str,
    parameter: str,
    value: Any,
    *,
    output_type: str,
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "inspection":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this Inspection api",
            type(value).__name__,
        )
    if value.output_type != output_type:
        raise _error(
            operation,
            parameter,
            f"must be an Inspection {output_type} value",
            value.output_type,
        )
    return value


def _definition_key(value: DomainValue) -> str:
    return json.dumps(
        value.to_payload(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _comparison_sequence(
    operation: str,
    value: Any,
) -> tuple[DomainValue, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(
            operation,
            "comparisons",
            "must be a non-empty sequence returned by api.comparison",
            value,
        )
    if not 1 <= len(value) <= _MAX_GROUP_MEMBERS:
        raise _error(
            operation,
            "comparisons",
            f"must contain 1-{_MAX_GROUP_MEMBERS} values",
        )
    result = tuple(
        _domain_value(
            operation,
            f"comparisons[{index}]",
            item,
            output_type="inspection_feature",
        )
        for index, item in enumerate(value)
    )
    keys = [_definition_key(item) for item in result]
    if len(keys) != len(set(keys)):
        raise _error(operation, "comparisons", "cannot contain duplicate definitions")
    return result


class InspectionDomainAPI:
    """Exact native-comparison, grouping, scalar-measurement, and report API."""

    __slots__ = ()

    domain = "inspection"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        declared_outputs = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_exports != _EXPORTS:
            raise RuntimeError(
                "Inspection pack exports do not match the production runtime contract: "
                f"expected {_EXPORTS!r}, received {declared_exports!r}."
            )
        if declared_outputs != _OUTPUT_TYPES:
            raise RuntimeError(
                "Inspection pack must publish exactly inspection_group, "
                "inspection_feature, measurement, and report."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="inspection",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def comparison(
        self,
        actual: Mapping[str, str],
        nominals: Sequence[Mapping[str, str]],
        *,
        search_radius: float,
        tolerance: float | Sequence[float],
        require_complete: bool = True,
        label: str = "",
    ) -> DomainValue:
        """Compare one actual shape/mesh/cloud against one or more nominals."""

        actual_reference = _reference("comparison", "actual", actual)
        if isinstance(nominals, (str, bytes)) or not isinstance(nominals, Sequence):
            raise _error(
                "comparison",
                "nominals",
                "must be a non-empty sequence of stable references",
                nominals,
            )
        if not 1 <= len(nominals) <= _MAX_NOMINALS:
            raise _error(
                "comparison",
                "nominals",
                f"must contain 1-{_MAX_NOMINALS} references",
            )
        nominal_references = tuple(
            _reference("comparison", f"nominals[{index}]", item)
            for index, item in enumerate(nominals)
        )
        identities = [
            (item["document_uid"], item["object_name"])
            for item in nominal_references
        ]
        if len(identities) != len(set(identities)):
            raise _error("comparison", "nominals", "cannot contain duplicates")
        if (
            actual_reference["document_uid"],
            actual_reference["object_name"],
        ) in set(identities):
            raise _error(
                "comparison",
                "actual",
                "cannot also appear in nominals",
            )
        radius = _number(
            "comparison",
            "search_radius",
            search_radius,
            minimum=0.0,
            maximum=_MAX_DISTANCE,
            strict_minimum=True,
        )
        bounds = _tolerance(tolerance)
        if max(abs(bounds[0]), abs(bounds[1])) > radius:
            raise _error(
                "comparison",
                "tolerance",
                "bounds must lie inside search_radius",
                tolerance,
            )
        if type(require_complete) is not bool:
            raise _error(
                "comparison",
                "require_complete",
                "must be true or false",
                require_complete,
            )
        return self._value(
            "comparison",
            "inspection_feature",
            actual_reference,
            nominal_references,
            search_radius=radius,
            tolerance=bounds,
            thickness=0.0,
            require_complete=require_complete,
            label=_label("comparison", label),
        )

    def group(
        self,
        comparisons: Sequence[DomainValue],
        *,
        label: str = "",
    ) -> DomainValue:
        """Collect exact returned comparisons in one native Inspection group."""

        return self._value(
            "group",
            "inspection_group",
            _comparison_sequence("group", comparisons),
            label=_label("group", label),
        )

    def measurement(
        self,
        comparison: DomainValue,
        *,
        metric: str,
        label: str = "",
    ) -> DomainValue:
        """Publish one typed scalar derived from a returned comparison."""

        if not isinstance(metric, str) or metric.strip().lower() not in _MEASUREMENT_METRICS:
            raise _error(
                "measurement",
                "metric",
                f"must be one of {list(_MEASUREMENT_METRICS)!r}",
                metric,
            )
        return self._value(
            "measurement",
            "measurement",
            _domain_value(
                "measurement",
                "comparison",
                comparison,
                output_type="inspection_feature",
            ),
            metric=metric.strip().lower(),
            label=_label("measurement", label),
        )

    def report(
        self,
        inspection_group: DomainValue,
        *,
        label: str = "",
    ) -> DomainValue:
        """Publish a stable aggregate pass/fail report for one returned group."""

        return self._value(
            "report",
            "report",
            _domain_value(
                "report",
                "inspection_group",
                inspection_group,
                output_type="inspection_group",
            ),
            label=_label("report", label),
        )
