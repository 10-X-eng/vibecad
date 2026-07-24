# SPDX-License-Identifier: LGPL-2.1-or-later

"""Runtime values exposed to workbench-qualified VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, MethodType
from typing import Any, Iterable

_OPERATION_OUTPUT_TYPES: dict[str, str] = {
    "box": "solid",
    "cylinder": "solid",
    "sphere": "solid",
    "extrude": "solid",
    "revolve": "solid",
    "fuse": "solid",
    "cut": "solid",
    "common": "solid",
    "face": "face",
    "wire": "wire",
    "compound": "compound",
    "shell": "shell",
    "fill": "fill",
    "blend": "blend",
    "extend": "extension",
    "loft": "loft",
    "thicken": "solid",
    "sketch": "sketch",
    "sheet": "sheet",
    "assign": "material_assignment",
    "appearance": "appearance",
    "site": "site",
    "building": "building",
    "level": "level",
    "wall": "wall",
    "slab": "slab",
    "structure": "structure",
    "opening": "opening",
    "mesh": "mesh",
    "mesh_from_shape": "mesh",
    "shape_from_mesh": "solid",
    "points": "points",
    "point_cloud": "points",
    "inspection": "inspection_group",
    "measurement": "measurement",
    "report": "report",
    "robot": "robot",
    "waypoint": "waypoints",
    "trajectory": "trajectory",
    "dressup": "dressup",
    "simulate": "simulation",
    "analysis": "analysis",
    "solver": "solver",
    "material": "material",
    "constraint": "constraint",
    "load_case": "load_case",
    "solve": "result",
    "job": "job",
    "stock": "stock",
    "tool": "tool",
    "operation": "operation",
    "generate_toolpath": "toolpath",
    "postprocess": "toolpath",
    "page": "page",
    "template": "template",
    "view": "view",
    "projection": "projection",
    "dimension": "dimension",
    "annotation": "annotation",
    "assembly": "assembly",
    "component": "component_link",
    "joint": "joint",
    "connector": "joint",
}

_DOMAIN_OPERATION_OUTPUT_TYPES: dict[str, dict[str, str]] = {
    "mesh": {"from_object": "mesh"},
    "surface": {
        # Boundary builders are intermediate domain values; only the pack's
        # declared output types may appear at the top-level result contract.
        "wire": "wire",
        "face": "face",
    },
    "assembly": {
        "assembly": "assembly",
        "component": "component_link",
        "fastener": "component_link",
        "connector": "joint",
        "joint": "joint",
        "solve": "solver_diagnostics",
        "motion": "motion",
        "simulation": "simulation",
        "exploded_view": "exploded_view",
        "bill_of_materials": "bom",
    },
    "material": {
        "material": "material_assignment",
        "assign": "material_assignment",
        "appearance": "appearance",
    },
    "meshpart": {
        "mesh_from_shape": "mesh",
        "shape_from_mesh": "solid",
    },
    "points": {"point_cloud": "points"},
    "reverse_engineering": {
        "fit_curve": "curve",
        "fit_surface": "surface",
        "reconstruct": "mesh",
        "segment": "mesh",
        "fit_metrics": "fit_metrics",
    },
    "inspection": {
        "comparison": "inspection_feature",
        "group": "inspection_group",
        "measurement": "measurement",
        "report": "report",
    },
}


def _json_value(value: Any) -> Any:
    if isinstance(value, DomainValue):
        return value.to_payload()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise TypeError(
        "VibeScript domain API values must contain only bounded JSON or other "
        f"domain API values, not {type(value).__name__}."
    )


def _immutable_value(value: Any) -> Any:
    """Recursively detach and freeze one declarative graph value."""

    if isinstance(value, DomainValue):
        return value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _immutable_value(item) for key, item in value.items()}
        )
    raise TypeError(
        "VibeScript domain API values must contain only bounded JSON or other "
        f"domain API values, not {type(value).__name__}."
    )


@dataclass(frozen=True)
class DomainValue:
    domain: str
    operation: str
    output_type: str
    arguments: tuple[Any, ...]
    properties: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            tuple(_immutable_value(item) for item in self.arguments),
        )
        object.__setattr__(self, "properties", _immutable_value(self.properties))

    def to_payload(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "operation": self.operation,
            "output_type": self.output_type,
            "arguments": [_json_value(item) for item in self.arguments],
            "properties": _json_value(self.properties),
        }


class DomainAPI:
    """One immutable domain-qualified API object injected as ``api``."""

    def __init__(
        self,
        domain: str,
        exports: Iterable[str],
        output_types: Iterable[str],
    ) -> None:
        object.__setattr__(self, "_locked", False)
        object.__setattr__(self, "_domain", str(domain))
        object.__setattr__(
            self,
            "_output_types",
            frozenset(str(item) for item in output_types),
        )
        object.__setattr__(
            self,
            "_exports",
            tuple(dict.fromkeys(str(item) for item in exports)),
        )
        for operation in self._exports:
            if not operation.isidentifier() or operation.startswith("_"):
                raise ValueError(f"Invalid domain API export: {operation!r}.")
            object.__setattr__(
                self,
                operation,
                MethodType(self._builder(operation), self),
            )
        object.__setattr__(self, "_locked", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise TypeError("The VibeScript domain api is immutable.")
        object.__setattr__(self, name, value)

    @staticmethod
    def _builder(operation: str):
        def build(_self: "DomainAPI", *args: Any, **properties: Any) -> DomainValue:
            requested_type = str(properties.pop("output_type", "") or "")
            output_type = (
                requested_type
                or _DOMAIN_OPERATION_OUTPUT_TYPES.get(_self._domain, {}).get(operation)
                or _OPERATION_OUTPUT_TYPES.get(operation, operation)
            )
            intermediate = (
                output_type
                in _DOMAIN_OPERATION_OUTPUT_TYPES.get(_self._domain, {}).values()
            )
            if output_type not in _self._output_types and not intermediate:
                if len(_self._output_types) == 1:
                    output_type = next(iter(_self._output_types))
                else:
                    raise ValueError(
                        f"api.{operation} requires output_type to be one of "
                        f"{sorted(_self._output_types)}."
                    )
            return DomainValue(
                domain=_self._domain,
                operation=operation,
                output_type=output_type,
                arguments=tuple(args),
                properties={str(key): value for key, value in properties.items()},
            )

        build.__name__ = operation
        build.__qualname__ = f"DomainAPI.{operation}"
        build.__doc__ = (
            f"Build one {operation} definition for the active VibeScript domain."
        )
        return build

    def output(self, output_type: str, **properties: Any) -> DomainValue:
        """Build an explicitly typed output when no higher-level helper applies."""

        clean = str(output_type or "")
        if clean not in self._output_types:
            raise ValueError(
                f"output_type must be one of {sorted(self._output_types)}."
            )
        return DomainValue(
            domain=self._domain,
            operation="output",
            output_type=clean,
            arguments=(),
            properties={str(key): value for key, value in properties.items()},
        )

    @property
    def exported_names(self) -> tuple[str, ...]:
        return (*self._exports, "output")


def create_domain_api(
    domain: str,
    exports: Iterable[str],
    output_types: Iterable[str],
    *,
    compatibility_methods: Iterable[str] = (),
) -> Any:
    """Construct the exact runtime API registered for one domain.

    Production domains use explicit classes with real signatures and
    operation-specific validation.  The generic builder remains available only
    to unfinished, unavailable development packs; surface resolution never
    treats that fallback as production-ready.
    """

    clean_domain = str(domain or "").strip().lower()
    compatibility = tuple(dict.fromkeys(str(item) for item in compatibility_methods))
    if clean_domain == "partdesign":
        from vibescript_partdesign_api import create_partdesign_domain_api

        return create_partdesign_domain_api(
            exports,
            output_types,
            compatibility_methods=compatibility,
        )
    if compatibility:
        raise ValueError(
            "Compatibility methods are supported only for saved Part Design programs."
        )
    if clean_domain == "part":
        from vibescript_part_api import PartDomainAPI

        return PartDomainAPI(exports, output_types)
    if clean_domain == "assembly":
        from vibescript_assembly_api import AssemblyDomainAPI

        return AssemblyDomainAPI(exports, output_types)
    if clean_domain == "sketcher":
        from vibescript_sketcher_api import SketcherDomainAPI

        return SketcherDomainAPI(exports, output_types)
    if clean_domain == "draft":
        from vibescript_draft_api import DraftDomainAPI

        return DraftDomainAPI(exports, output_types)
    if clean_domain == "surface":
        from vibescript_surface_api import SurfaceDomainAPI

        return SurfaceDomainAPI(exports, output_types)
    if clean_domain == "spreadsheet":
        from vibescript_spreadsheet_api import SpreadsheetDomainAPI

        return SpreadsheetDomainAPI(exports, output_types)
    if clean_domain == "material":
        from vibescript_material_api import MaterialDomainAPI

        return MaterialDomainAPI(exports, output_types)
    if clean_domain == "mesh":
        from vibescript_mesh_api import MeshDomainAPI

        return MeshDomainAPI(exports, output_types)
    if clean_domain == "meshpart":
        from vibescript_meshpart_api import MeshPartDomainAPI

        return MeshPartDomainAPI(exports, output_types)
    if clean_domain == "points":
        from vibescript_points_api import PointsDomainAPI

        return PointsDomainAPI(exports, output_types)
    if clean_domain == "reverse_engineering":
        from vibescript_reverse_engineering_api import ReverseEngineeringDomainAPI

        return ReverseEngineeringDomainAPI(exports, output_types)
    if clean_domain == "inspection":
        from vibescript_inspection_api import InspectionDomainAPI

        return InspectionDomainAPI(exports, output_types)
    if clean_domain == "robot":
        from vibescript_robot_api import RobotDomainAPI

        return RobotDomainAPI(exports, output_types)
    if clean_domain == "fem":
        from vibescript_fem_api import FEMDomainAPI

        return FEMDomainAPI(exports, output_types)
    if clean_domain == "cam":
        from vibescript_cam_api import CAMDomainAPI

        return CAMDomainAPI(exports, output_types)
    if clean_domain == "techdraw":
        from vibescript_techdraw_api import TechDrawDomainAPI

        return TechDrawDomainAPI(exports, output_types)
    return DomainAPI(clean_domain, exports, output_types)
