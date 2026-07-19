# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen native CAM proxies used by bounded VibeScript publication.

The worker constructs and validates ordinary Path objects.  Publication must not
run their generators, catalog lookup, simulation, or postprocessors in the live
document process, so accepted state is attached to subclasses whose callbacks
are deliberately inert.  The objects retain native Path proxy ancestry and
their standard property/link graph while recompute is protected by FreeCAD's
document-object freeze contract.
"""

from __future__ import annotations

from typing import Any, Mapping

from Path.Base.SetupSheet import SetupSheet
from Path.Main.Job import ObjectJob
from Path.Main.Stock import StockFromBase
from Path.Op.Drilling import ObjectDrilling
from Path.Op.MillFace import ObjectFace
from Path.Op.PocketShape import ObjectPocket
from Path.Op.Profile import ObjectProfile
from Path.Tool.Controller import ToolController
from Path.Tool.toolbit.models.ballend import ToolBitBallend
from Path.Tool.toolbit.models.chamfer import ToolBitChamfer
from Path.Tool.toolbit.models.drill import ToolBitDrill
from Path.Tool.toolbit.models.endmill import ToolBitEndmill
from Path.Tool.toolbit.models.vbit import ToolBitVBit


PROXY_SCHEMA = "vibecad-frozen-native-cam-proxy-v1"
PROP_PROXY_KIND = "VibeCADCAMProxyKind"


class _FrozenNativeProxy:
    """Suppress every callback that could synchronously derive CAM state."""

    proxy_kind = "native"

    def __init__(self, obj: Any | None = None, *_args: Any, **_kwargs: Any) -> None:
        self.obj = obj
        self._in_update = False
        self.id = ""

    def execute(self, _obj: Any) -> None:
        return None

    def onChanged(self, _obj: Any, _property: str) -> None:
        return None

    def onDocumentRestored(self, obj: Any) -> None:
        self.obj = obj

    def __getstate__(self) -> dict[str, str]:
        return {"schema": PROXY_SCHEMA, "proxy_kind": self.proxy_kind}

    def __setstate__(self, state: Any) -> None:
        del state
        self.obj = None
        self._in_update = False
        self.id = ""


class FrozenJobProxy(_FrozenNativeProxy, ObjectJob):
    proxy_kind = "job"


class FrozenStockProxy(_FrozenNativeProxy, StockFromBase):
    proxy_kind = "stock"


class FrozenToolControllerProxy(_FrozenNativeProxy, ToolController):
    proxy_kind = "tool"


class FrozenProfileProxy(_FrozenNativeProxy, ObjectProfile):
    proxy_kind = "operation:profile"


class FrozenPocketProxy(_FrozenNativeProxy, ObjectPocket):
    proxy_kind = "operation:pocket"


class FrozenDrillingProxy(_FrozenNativeProxy, ObjectDrilling):
    proxy_kind = "operation:drilling"


class FrozenFaceProxy(_FrozenNativeProxy, ObjectFace):
    proxy_kind = "operation:face"


class FrozenSetupSheetProxy(_FrozenNativeProxy, SetupSheet):
    proxy_kind = "setup_sheet"


class FrozenEndmillProxy(_FrozenNativeProxy, ToolBitEndmill):
    proxy_kind = "tool_bit:endmill"


class FrozenBallendProxy(_FrozenNativeProxy, ToolBitBallend):
    proxy_kind = "tool_bit:ballend"


class FrozenDrillProxy(_FrozenNativeProxy, ToolBitDrill):
    proxy_kind = "tool_bit:drill"


class FrozenChamferProxy(_FrozenNativeProxy, ToolBitChamfer):
    proxy_kind = "tool_bit:chamfer"


class FrozenVBitProxy(_FrozenNativeProxy, ToolBitVBit):
    proxy_kind = "tool_bit:vbit"


_OPERATION_PROXIES = {
    "profile": FrozenProfileProxy,
    "pocket": FrozenPocketProxy,
    "drilling": FrozenDrillingProxy,
    "face": FrozenFaceProxy,
}
_TOOL_BIT_PROXIES = {
    "endmill": FrozenEndmillProxy,
    "ballend": FrozenBallendProxy,
    "drill": FrozenDrillProxy,
    "chamfer": FrozenChamferProxy,
    "vbit": FrozenVBitProxy,
}
_PROXIES_BY_KIND = {
    "job": FrozenJobProxy,
    "stock": FrozenStockProxy,
    "tool": FrozenToolControllerProxy,
    "setup_sheet": FrozenSetupSheetProxy,
    **{
        f"operation:{strategy}": proxy
        for strategy, proxy in _OPERATION_PROXIES.items()
    },
}
_ROOT_TYPES = {
    "job": "Path::FeaturePython",
    "stock": "Part::FeaturePython",
    "tool": "Path::FeaturePython",
    "operation": "Path::FeaturePython",
    "toolpath": "Path::Feature",
}


def _properties(obj: Any) -> set[str]:
    return set(getattr(obj, "PropertiesList", []) or [])


def ensure_property(
    obj: Any,
    property_type: str,
    name: str,
    group: str,
    description: str,
) -> None:
    if name not in _properties(obj):
        obj.addProperty(property_type, name, group, description)


def _set_proxy(obj: Any, proxy: _FrozenNativeProxy) -> None:
    proxy.obj = obj
    obj.Proxy = proxy
    ensure_property(
        obj,
        "App::PropertyString",
        PROP_PROXY_KIND,
        "VibeCAD",
        "Stable frozen native CAM proxy role.",
    )
    setattr(obj, PROP_PROXY_KIND, proxy.proxy_kind)


def mark_proxy_kind(obj: Any, kind: str) -> None:
    ensure_property(
        obj,
        "App::PropertyString",
        PROP_PROXY_KIND,
        "VibeCAD",
        "Stable frozen native CAM object role.",
    )
    setattr(obj, PROP_PROXY_KIND, str(kind))


def _ensure_job_properties(obj: Any) -> None:
    for property_type, name, group in (
        ("App::PropertyFile", "PostProcessorOutputFile", "Output"),
        ("App::PropertyEnumeration", "PostProcessor", "Output"),
        ("App::PropertyString", "PostProcessorArgs", "Output"),
        ("App::PropertyString", "LastPostProcessDate", "Output"),
        ("App::PropertyString", "LastPostProcessOutput", "Output"),
        ("App::PropertyString", "Description", "Path"),
        ("App::PropertyString", "CycleTime", "Path"),
        ("App::PropertyLength", "GeometryTolerance", "Geometry"),
        ("App::PropertyLink", "Stock", "Base"),
        ("App::PropertyLink", "Operations", "Base"),
        ("App::PropertyLink", "SetupSheet", "Base"),
        ("App::PropertyLink", "Model", "Base"),
        ("App::PropertyLink", "Tools", "Base"),
        ("App::PropertyEnumeration", "JobType", "Base"),
        ("App::PropertyBool", "SplitOutput", "Output"),
        ("App::PropertyEnumeration", "OrderOutputBy", "WCS"),
        ("App::PropertyStringList", "Fixtures", "WCS"),
        ("App::PropertyString", "Machine", "Output"),
        ("App::PropertyString", "PostProcessorPropertyOverrides", "Output"),
    ):
        ensure_property(obj, property_type, name, group, "Frozen validated CAM state.")
    obj.PostProcessor = ["", "grbl", "linuxcnc"]
    obj.JobType = ["2D", "2.5D", "Lathe", "Multiaxis"]
    obj.OrderOutputBy = ["Fixture", "Tool", "Operation"]


def _ensure_stock_properties(obj: Any) -> None:
    ensure_property(obj, "App::PropertyLink", "Base", "Stock", "Frozen job model group.")
    for name in ("ExtXneg", "ExtXpos", "ExtYneg", "ExtYpos", "ExtZneg", "ExtZpos"):
        ensure_property(obj, "App::PropertyLength", name, "Stock", "Validated stock margin.")


def _ensure_tool_properties(obj: Any) -> None:
    for property_type, name, group in (
        ("App::PropertyIntegerConstraint", "ToolNumber", "Tool"),
        ("App::PropertyFloat", "SpindleSpeed", "Tool"),
        ("App::PropertyEnumeration", "SpindleDir", "Tool"),
        ("App::PropertyIntegerConstraint", "ToolLengthOffset", "Tool"),
        ("App::PropertySpeed", "VertFeed", "Feed"),
        ("App::PropertySpeed", "HorizFeed", "Feed"),
        ("App::PropertySpeed", "VertRapid", "Rapid"),
        ("App::PropertySpeed", "HorizRapid", "Rapid"),
        ("App::PropertySpeed", "RampFeed", "Feed"),
        ("App::PropertySpeed", "LeadInFeed", "Feed"),
        ("App::PropertySpeed", "LeadOutFeed", "Feed"),
        ("App::PropertyLink", "Tool", "Tool"),
    ):
        ensure_property(obj, property_type, name, group, "Frozen validated CAM tool state.")
    obj.SpindleDir = ["Forward", "Reverse", "None"]


def _ensure_operation_properties(obj: Any) -> None:
    for property_type, name, group in (
        ("App::PropertyLinkSubList", "Base", "Path"),
        ("App::PropertyLink", "ToolController", "Path"),
        ("App::PropertyLength", "StartDepth", "Depths"),
        ("App::PropertyLength", "FinalDepth", "Depths"),
        ("App::PropertyLength", "StepDown", "Depths"),
        ("App::PropertyInteger", "StepOver", "Path"),
        ("App::PropertyEnumeration", "Side", "Path"),
        ("App::PropertyEnumeration", "BoundaryShape", "Path"),
        ("App::PropertyBool", "PeckEnabled", "Path"),
        ("App::PropertyLength", "PeckDepth", "Path"),
        ("App::PropertyEnumeration", "Strategy", "Path"),
        ("App::PropertyEnumeration", "CoolantMode", "Path"),
    ):
        ensure_property(
            obj,
            property_type,
            name,
            group,
            "Frozen worker-generated CAM operation state.",
        )
    obj.Side = ["Outside", "Inside"]
    obj.BoundaryShape = ["Boundbox", "Stock", "Perimeter"]
    obj.Strategy = ["Profile", "Pocket", "Drilling", "Face"]
    obj.CoolantMode = ["None", "Flood", "Mist"]


def create_root(
    doc: Any,
    name: str,
    output_type: str,
    cam_data: Mapping[str, Any],
) -> Any:
    native_type = _ROOT_TYPES.get(str(output_type))
    if native_type is None:
        raise RuntimeError(f"No frozen CAM root exists for {output_type!r}.")
    obj = doc.addObject(native_type, str(name))
    if obj is None:
        raise RuntimeError(f"FreeCAD did not create CAM object {name!r}.")
    attach_root_proxy(obj, output_type, cam_data)
    return obj


def attach_root_proxy(
    obj: Any,
    output_type: str,
    cam_data: Mapping[str, Any],
) -> None:
    """Attach the exact inert native subclass while preserving object identity."""

    if output_type == "job":
        _set_proxy(obj, FrozenJobProxy(obj))
        _ensure_job_properties(obj)
    elif output_type == "stock":
        _set_proxy(obj, FrozenStockProxy(obj))
        _ensure_stock_properties(obj)
    elif output_type == "tool":
        _set_proxy(obj, FrozenToolControllerProxy(obj))
        _ensure_tool_properties(obj)
    elif output_type == "operation":
        strategy = str(cam_data.get("strategy") or "")
        proxy_type = _OPERATION_PROXIES.get(strategy)
        if proxy_type is None:
            raise RuntimeError(f"Unsupported frozen CAM strategy {strategy!r}.")
        _set_proxy(obj, proxy_type(obj))
        _ensure_operation_properties(obj)
    elif output_type == "toolpath":
        mark_proxy_kind(obj, "toolpath")
    else:
        raise RuntimeError(f"No frozen CAM proxy exists for {output_type!r}.")


def create_setup_sheet(doc: Any, name: str) -> Any:
    obj = doc.addObject("App::FeaturePython", str(name))
    if obj is None:
        raise RuntimeError("FreeCAD did not create the frozen CAM setup sheet.")
    _set_proxy(obj, FrozenSetupSheetProxy(obj))
    return obj


def create_tool_bit(doc: Any, name: str, kind: str) -> Any:
    obj = doc.addObject("Part::FeaturePython", str(name))
    if obj is None:
        raise RuntimeError("FreeCAD did not create the frozen CAM tool bit.")
    attach_tool_bit_proxy(obj, kind)
    return obj


def attach_tool_bit_proxy(obj: Any, kind: str) -> None:
    """Attach one concrete inert ToolBit subclass to an existing stable object."""

    proxy_type = _TOOL_BIT_PROXIES.get(str(kind))
    if proxy_type is None:
        raise RuntimeError(f"Unsupported frozen CAM tool kind {kind!r}.")
    _set_proxy(obj, proxy_type(obj))
    _ensure_tool_bit_properties(obj)


def _ensure_tool_bit_properties(obj: Any) -> None:
    """Install the one canonical frozen ToolBit property contract."""

    for property_type, property_name in (
        ("App::PropertyString", "ToolBitID"),
        ("App::PropertyString", "ShapeID"),
        ("App::PropertyString", "ShapeType"),
        ("App::PropertyLength", "Diameter"),
        ("App::PropertyLength", "Length"),
        ("App::PropertyInteger", "Flutes"),
        ("App::PropertyEnumeration", "SpindleDirection"),
        ("App::PropertyLength", "CuttingEdgeHeight"),
        ("App::PropertyLength", "ShankDiameter"),
        ("App::PropertyAngle", "TipAngle"),
        ("App::PropertyAngle", "CuttingEdgeAngle"),
        ("App::PropertyLength", "TipDiameter"),
    ):
        ensure_property(
            obj,
            property_type,
            property_name,
            "Tool",
            "Frozen worker-validated tool-bit state.",
        )
    obj.SpindleDirection = ["Forward", "Reverse"]


def attach_proxy_kind(obj: Any, kind: str) -> None:
    """Restore one persisted proxy role without running any native constructor."""

    clean = str(kind)
    if clean in {"toolpath", "group:operations", "group:model", "group:tools", "model_clone"}:
        mark_proxy_kind(obj, clean)
        return
    if clean.startswith("tool_bit:"):
        attach_tool_bit_proxy(obj, clean.partition(":")[2])
        return
    proxy_type = _PROXIES_BY_KIND.get(clean)
    if proxy_type is None:
        raise RuntimeError(f"Unsupported frozen CAM proxy role {clean!r}.")
    _set_proxy(obj, proxy_type(obj))
    if clean == "job":
        _ensure_job_properties(obj)
    elif clean == "stock":
        _ensure_stock_properties(obj)
    elif clean == "tool":
        _ensure_tool_properties(obj)
    elif clean.startswith("operation:"):
        _ensure_operation_properties(obj)


def proxy_is_compatible(
    obj: Any,
    output_type: str,
    cam_data: Mapping[str, Any],
) -> bool:
    if str(getattr(obj, "TypeId", "") or "") != _ROOT_TYPES.get(output_type):
        return False
    proxy = getattr(obj, "Proxy", None)
    if output_type == "toolpath":
        return proxy is None
    expected: type[Any]
    if output_type == "job":
        expected = ObjectJob
    elif output_type == "stock":
        expected = StockFromBase
    elif output_type == "tool":
        expected = ToolController
    elif output_type == "operation":
        expected = _OPERATION_PROXIES.get(str(cam_data.get("strategy") or ""), object)
    else:
        return False
    return isinstance(proxy, expected) and isinstance(proxy, _FrozenNativeProxy)


def tool_bit_is_compatible(obj: Any, kind: str) -> bool:
    expected = _TOOL_BIT_PROXIES.get(str(kind))
    return (
        expected is not None
        and str(getattr(obj, "TypeId", "") or "") == "Part::FeaturePython"
        and isinstance(getattr(obj, "Proxy", None), expected)
    )


def root_type(output_type: str) -> str:
    native_type = _ROOT_TYPES.get(str(output_type))
    if native_type is None:
        raise RuntimeError(f"No native CAM type exists for {output_type!r}.")
    return native_type
