# SPDX-License-Identifier: LGPL-2.1-or-later

"""Derived multipart geometry for solid finite-element analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference, resolve_object


_MODES = frozenset({"separate", "shared"})


@dataclass(frozen=True, slots=True)
class PreparedSolidDomain:
    sources: tuple[Any, ...]
    source_states: tuple[str, ...]
    interface_mode: str
    label: str
    source_visibility: tuple[bool, ...]


def _source_target(document: Any, document_uid: str, value: Any) -> tuple[Any, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "object_name",
        "expected_state_sha256",
    }:
        raise NativeAnalyzeError(
            "Each solid-domain source must contain object_name and "
            "expected_state_sha256."
        )
    source = resolve_object(
        document,
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
    )
    state = mesh_object_state(source)
    expected = str(value["expected_state_sha256"] or "")
    if state.get("state_sha256") != expected:
        raise NativeAnalyzeError(
            f"Solid-domain source {source.Name} changed; inspect the current geometry."
        )
    shape = getattr(source, "Shape", None)
    try:
        usable = (
            shape is not None
            and not shape.isNull()
            and shape.isValid()
            and len(shape.Solids) > 0
        )
    except Exception:
        usable = False
    if not usable:
        raise NativeAnalyzeError(
            f"Solid-domain source {source.Name} has no valid solid geometry."
        )
    if bool(getattr(source, "VibeCADAnalysisDomain", False)):
        raise NativeAnalyzeError(
            f"Solid-domain source {source.Name} is already an analysis domain."
        )
    return source, expected


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def prepare_solid_domain(
    document: Any,
    document_uid: str,
    *,
    sources: Any,
    interface_mode: Any,
    label: Any,
) -> PreparedSolidDomain:
    if not isinstance(sources, list) or not 2 <= len(sources) <= 256:
        raise NativeAnalyzeError(
            "A solid analysis domain requires 2 to 256 source objects."
        )
    mode = str(interface_mode or "")
    if mode not in _MODES:
        raise NativeAnalyzeError(
            "Solid-domain interface_mode must be separate or shared."
        )
    resolved = tuple(
        _source_target(document, document_uid, value) for value in sources
    )
    objects = tuple(value[0] for value in resolved)
    if len(objects) != len(set(objects)):
        raise NativeAnalyzeError("Solid-domain source objects must be distinct.")
    domain_label = str(label or "").strip()
    if not domain_label or len(domain_label) > 160:
        raise NativeAnalyzeError(
            "Solid-domain label must contain 1 to 160 characters."
        )
    return PreparedSolidDomain(
        sources=objects,
        source_states=tuple(value[1] for value in resolved),
        interface_mode=mode,
        label=domain_label,
        source_visibility=tuple(_visible(source) for source in objects),
    )


def _sources_are_exact(prepared: PreparedSolidDomain) -> bool:
    return all(
        getattr(source, "Document", None) is not None
        and mesh_object_state(source).get("state_sha256") == expected
        for source, expected in zip(
            prepared.sources,
            prepared.source_states,
            strict=True,
        )
    )


def _add_identity(domain: Any, prepared: PreparedSolidDomain) -> None:
    domain.addProperty(
        "App::PropertyBool",
        "VibeCADAnalysisDomain",
        "Analysis",
        "Derived solid analysis domain.",
        locked=True,
    )
    domain.addProperty(
        "App::PropertyString",
        "AnalysisInterfaceMode",
        "Analysis",
        "Solid interface treatment.",
        locked=True,
    )
    domain.addProperty(
        "App::PropertyLinkListGlobal",
        "AnalysisSources",
        "Analysis",
        "Source design objects.",
        locked=True,
    )
    domain.VibeCADAnalysisDomain = True
    domain.AnalysisInterfaceMode = prepared.interface_mode
    domain.AnalysisSources = prepared.sources


def _create_separate_domain(document: Any, prepared: PreparedSolidDomain) -> Any:
    domain = document.addObject("Part::Compound", "SolidAnalysisDomain")
    if domain is None or domain.TypeId != "Part::Compound":
        raise NativeAnalyzeError("The separate solid-domain factory failed.")
    domain.Links = prepared.sources
    return domain


def _create_shared_domain(document: Any, prepared: PreparedSolidDomain) -> Any:
    from BOPTools import SplitFeatures

    domain = document.addObject("Part::FeaturePython", "SolidAnalysisDomain")
    if domain is None:
        raise NativeAnalyzeError("The shared solid-domain factory failed.")
    SplitFeatures.FeatureBooleanFragments(domain)
    if bool(getattr(document, "GuiUp", False)):
        SplitFeatures.ViewProviderBooleanFragments(domain.ViewObject)
    else:
        try:
            import FreeCAD

            if FreeCAD.GuiUp:
                SplitFeatures.ViewProviderBooleanFragments(domain.ViewObject)
        except (ImportError, AttributeError):
            pass
    domain.Objects = prepared.sources
    domain.Mode = "CompSolid"
    return domain


def create_solid_domain(
    document: Any,
    prepared: PreparedSolidDomain,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSolidDomain):
        raise TypeError("prepared must be a PreparedSolidDomain")
    if not _sources_are_exact(prepared):
        raise NativeAnalyzeError(
            "A solid-domain source changed after preflight; inspect the geometry."
        )
    domain = (
        _create_separate_domain(document, prepared)
        if prepared.interface_mode == "separate"
        else _create_shared_domain(document, prepared)
    )
    domain.Label = prepared.label
    _add_identity(domain, prepared)
    recomputed = document.recompute([domain], True, True)
    shape = getattr(domain, "Shape", None)
    if (
        recomputed is False
        or not domain.isValid()
        or shape is None
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Solids) == 0
    ):
        status = str(domain.getStatusString() or "").strip()
        raise NativeAnalyzeError(status or "The solid analysis domain is invalid.")
    if prepared.interface_mode == "shared" and len(shape.CompSolids) == 0:
        raise NativeAnalyzeError(
            "The shared solid domain has no connected conformal interface; use "
            "separate interfaces for tie or contact."
        )
    for source in prepared.sources:
        try:
            source.Visibility = False
        except Exception:
            continue
    try:
        domain.Visibility = True
    except Exception:
        pass
    return NativeMutationDraft(
        value={"domain": domain, "prepared": prepared},
        recompute_targets=(domain,),
        created=(object_identity(domain),),
    )


def verify_solid_domain(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    domain = draft.value["domain"]
    prepared = draft.value["prepared"]
    shape = domain.Shape
    if document.getObject(str(domain.Name)) is not domain:
        raise NativeAnalyzeError("The solid analysis domain lost its identity.")
    if (
        str(domain.Label) != prepared.label
        or not bool(domain.VibeCADAnalysisDomain)
        or str(domain.AnalysisInterfaceMode) != prepared.interface_mode
        or tuple(domain.AnalysisSources) != prepared.sources
        or not domain.isValid()
        or shape.isNull()
        or not shape.isValid()
        or len(shape.Solids) == 0
    ):
        raise NativeAnalyzeError("The solid analysis domain changed before commit.")
    if prepared.interface_mode == "separate":
        if domain.TypeId != "Part::Compound" or tuple(domain.Links) != prepared.sources:
            raise NativeAnalyzeError(
                "The separate solid analysis domain lost its source links."
            )
    elif len(shape.CompSolids) == 0:
        raise NativeAnalyzeError(
            "The shared solid analysis domain lost its conformal topology."
        )
    if not _sources_are_exact(prepared):
        raise NativeAnalyzeError(
            "A solid-domain source changed before the domain was committed."
        )
    result = {
        "domain": object_reference(domain),
        "source_count": len(prepared.sources),
        "source_names": [str(source.Name) for source in prepared.sources],
        "interface_mode": prepared.interface_mode,
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "compsolid_count": len(shape.CompSolids),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "volume_mm3": float(shape.Volume),
    }
    return result
