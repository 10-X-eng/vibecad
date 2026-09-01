# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded live-document publication for VibeScript domain candidates."""

from __future__ import annotations

from array import array
import hashlib
from io import BytesIO
import json
import math
import re
from typing import Any, Mapping
import zipfile

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    resolve_reference_target,
)
import VibeCADReferenceContracts as reference_contracts
import VibeCADScriptedPublication as scripted_publication
import VibeCADVibeScriptDomains as contracts

PROP_DEFINITION = "VibeCADVibeScriptDefinition"
PROP_OUTPUT_TYPE = "VibeCADVibeScriptOutputType"
PROP_INPUT_OBJECTS = "VibeCADVibeScriptInputObjects"
PROP_NESTED_INPUT_OBJECTS = "VibeCADVibeScriptNestedInputObjects"
PROP_INPUT_SNAPSHOTS = "VibeCADVibeScriptInputSnapshots"
PROP_MATERIAL_TARGET = "VibeCADMaterialTarget"
PROP_MATERIAL_OWNERSHIP = "VibeCADMaterialOwnership"
PROP_MATERIAL_VALIDATION = "VibeCADMaterialValidation"
PROP_MATERIAL_BASELINE = "VibeCADMaterialBaseline"
PROP_MATERIAL_ACCEPTED = "VibeCADMaterialAccepted"
PROP_APPEARANCE_BASELINE = "VibeCADAppearanceBaseline"
PROP_APPEARANCE_ACCEPTED = "VibeCADAppearanceAccepted"
PROP_PARTDESIGN_PRESENTATION_STATE = "VibeCADPartDesignPresentationState"
PROP_PARTDESIGN_MATERIAL_BASELINE = "VibeCADPartDesignMaterialBaseline"
PROP_PARTDESIGN_MATERIAL_ACCEPTED = "VibeCADPartDesignMaterialAccepted"
PROP_PARTDESIGN_APPEARANCE_BASELINE = "VibeCADPartDesignAppearanceBaseline"
PROP_PARTDESIGN_APPEARANCE_ACCEPTED = "VibeCADPartDesignAppearanceAccepted"
PROP_PARTDESIGN_HISTORY_PRESENTATION = "VibeCADPartDesignHistoryPresentation"
PROP_MESH_VALIDATION = "VibeCADMeshValidation"
PROP_MESHPART_VALIDATION = "VibeCADMeshPartValidation"
PROP_POINTS_VALIDATION = "VibeCADPointsValidation"
PROP_REVERSE_VALIDATION = "VibeCADReverseEngineeringValidation"
PROP_INSPECTION_VALIDATION = "VibeCADInspectionValidation"
PROP_ROBOT_VALIDATION = "VibeCADRobotValidation"
PROP_FEM_VALIDATION = "VibeCADFEMValidation"
PROP_CAM_VALIDATION = "VibeCADCAMValidation"
PROP_TECHDRAW_VALIDATION = "VibeCADTechDrawValidation"
PROP_ASSEMBLY_BOM_VALIDATION = "VibeCADAssemblyBOMValidation"
PROP_ASSEMBLY_BOM_RESTORE_TARGET = "VibeCADAssemblyBOMRestoreTarget"
PROP_ASSEMBLY_BOM_RESTORE_ERROR = "VibeCADAssemblyBOMRestoreError"
PROP_ASSEMBLY_GROUP_ROLE = "VibeCADAssemblyGroupRole"
PROP_MECHANISM_ASSEMBLY_OUTPUT = "VibeCADMechanismAssemblyOutput"
PROP_MECHANISM_STATIC_CHECK = "VibeCADMechanismStaticCheck"
PROP_MECHANISM_VERIFICATION_REPORT = "VibeCADMechanismVerificationReport"
PROP_PARTDESIGN_HISTORY_KEY = "VibeCADPartDesignHistoryKey"
PROP_PARTDESIGN_COMPONENT_OCCURRENCES = "VibeCADPartDesignComponentOccurrences"
PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES = (
    "VibeCADPartDesignComponentOccurrenceNames"
)
PROP_COMPONENT_AUTHORED_PLACEMENT = "VibeCADComponentAuthoredPlacement"
PROP_ASSEMBLY_ADOPTED_OUTPUTS = "VibeCADAssemblyAdoptedOutputs"
PROP_ASSEMBLY_ADOPTED_OCCURRENCES = "VibeCADAssemblyAdoptedOccurrences"
PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES = "VibeCADAssemblyAdoptedOccurrenceNames"
MATERIAL_OWNERSHIP_SCHEMA = "vibecad-material-ownership-v1"
PARTDESIGN_PRESENTATION_OWNERSHIP_SCHEMA = (
    "vibecad-partdesign-presentation-ownership-v1"
)
PARTDESIGN_HISTORY_PRESENTATION_SCHEMA = (
    "vibecad-partdesign-body-renderer-v1"
)
_LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA = (
    "vibecad-partdesign-history-presentation-v1"
)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]")
_ASSEMBLY_DEPENDENCY_SUFFIX = "__dependencies"
_ASSEMBLY_DEPENDENCY_OUTPUT_TYPE = "dependency_anchor"
_ASSEMBLY_FASTENER_SOURCE_SUFFIX = "__fastener_source"
_ASSEMBLY_FASTENER_SOURCE_OUTPUT_TYPE = "standard_fastener_source"
_ASSEMBLY_RESOURCE_GRAPH_OUTPUT_TYPES = frozenset({"exploded_view", "bom"})

_TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN = {
    "partdesign": "design_program_operation",
    "sketcher": "public_outputs",
    "part": "public_outputs",
    "draft": "public_outputs",
    "surface": "public_outputs",
    "assembly": "assembly_resource_graphs",
    "spreadsheet": "public_outputs",
    "material": "material_operations",
    "mesh": "public_outputs",
    "meshpart": "public_outputs",
    "points": "public_outputs",
    "reverse_engineering": "public_outputs",
    "inspection": "public_outputs",
    "robot": "public_outputs",
    "fem": "public_outputs",
    "cam": "cam_job_graphs",
    "techdraw": "techdraw_owner_graphs",
}
_SHIPPED_VIBESCRIPT_DOMAINS = {
    pack.domain for pack in contracts.VIBESCRIPT_WORKBENCH_PACKS.values()
}
if set(_TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN) != _SHIPPED_VIBESCRIPT_DOMAINS:
    missing = sorted(
        _SHIPPED_VIBESCRIPT_DOMAINS
        - set(_TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN)
    )
    stale = sorted(
        set(_TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN)
        - _SHIPPED_VIBESCRIPT_DOMAINS
    )
    raise RuntimeError(
        "Every shipped VibeScript domain requires one explicit semantic History "
        f"publication strategy; missing={missing}, stale={stale}."
    )

_PERSISTED_INPUT_SNAPSHOT_KEYS = (
    "document_uid",
    "object_name",
    "document_path",
    "artifact_kind",
    "shape_type",
    "brep_sha256",
    "brep_bytes",
    "mesh_sha256",
    "mesh_bytes",
    "mesh_segments",
    "mesh_source_placement_matrix",
    "artifact_sha256",
    "artifact_bytes",
    "attribute_artifacts",
    "structured",
    "type_id",
    "source_kind",
    "source_program_id",
    "source_program_domain",
    "source_revision",
    "transient_topology",
    "requires_semantic_interfaces",
    "reference_contract_sha256",
)


_NATIVE_TYPE_BY_OUTPUT: dict[str, str] = {
    "sketch": "Sketcher::SketchObject",
    "sheet": "Spreadsheet::Sheet",
    "assembly": "Assembly::AssemblyObject",
    "component_link": "App::Link",
    "joint": "App::FeaturePython",
    "mechanism_verification": "App::FeaturePython",
    "motion": "App::FeaturePython",
    "exploded_view": "App::FeaturePython",
    "bom": "Assembly::BomObject",
    "solver_diagnostics": "App::FeaturePython",
    "material_assignment": "App::FeaturePython",
    "appearance": "App::FeaturePython",
    "site": "Part::FeaturePython",
    "building": "App::GeometryPython",
    "level": "App::GeometryPython",
    "wall": "Part::FeaturePython",
    "slab": "Part::FeaturePython",
    "structure": "Part::FeaturePython",
    "opening": "Part::FeaturePython",
    "inspection_group": "Inspection::Group",
    "inspection_feature": "Inspection::Feature",
    "measurement": "App::FeaturePython",
    "report": "App::FeaturePython",
    "fit_metrics": "App::FeaturePython",
    "robot": "Robot::RobotObject",
    "trajectory": "Robot::TrajectoryObject",
    "dressup": "Robot::TrajectoryDressUpObject",
    "simulation": "App::FeaturePython",
    "analysis": "App::DocumentObjectGroup",
    "solver": "App::FeaturePython",
    "material": "App::FeaturePython",
    "constraint": "App::FeaturePython",
    "load_case": "App::DocumentObjectGroup",
    "result": "App::FeaturePython",
    "job": "Path::FeaturePython",
    "stock": "Part::Feature",
    "tool": "Path::FeaturePython",
    "operation": "Path::FeaturePython",
    "toolpath": "Path::FeaturePython",
    "page": "TechDraw::DrawPage",
    "template": "TechDraw::DrawTemplate",
    "view": "TechDraw::DrawViewPart",
    "projection": "TechDraw::DrawProjGroup",
    "dimension": "TechDraw::DrawViewDimension",
    "annotation": "TechDraw::DrawViewAnnotation",
    "circle": "Part::Part2DObjectPython",
    "rectangle": "Part::Part2DObjectPython",
    "bspline": "Part::FeaturePython",
    "array": "Part::FeaturePython",
    "text": "App::FeaturePython",
}

_BREP_OUTPUT_TYPES = frozenset(
    {
        "solid",
        "shell",
        "face",
        "wire",
        "compound",
        "surface",
        "fill",
        "blend",
        "extension",
        "loft",
        "brep",
        "curve",
    }
)

_MESH_ROLLBACK_PROPERTIES = (
    contracts.PROP_PROGRAM_ID,
    contracts.PROP_PROGRAM_DOMAIN,
    contracts.PROP_PROGRAM_WORKBENCH,
    contracts.PROP_PROGRAM_REVISION,
    contracts.PROP_PROGRAM_OUTPUT,
    contracts.PROP_PROGRAM_LABEL,
    contracts.PROP_PROGRAM_CONTRACT,
    contracts.PROP_PROGRAM_EDITOR_DRAFT,
    PROP_OUTPUT_TYPE,
    PROP_DEFINITION,
    PROP_INPUT_OBJECTS,
    PROP_INPUT_SNAPSHOTS,
    PROP_MESH_VALIDATION,
    PROP_MESHPART_VALIDATION,
    reference_contracts.PROP_DERIVED_STATE,
    reference_contracts.PROP_STALE_REASON,
    reference_contracts.PROP_SOURCE_REVISION,
)
_MAX_MESH_ROLLBACK_PROPERTIES = 256
_MAX_MESH_ROLLBACK_PROPERTY_BYTES = 2 * 1024 * 1024
_MAX_SHAPE_ROLLBACK_BREP_BYTES = 256 * 1024 * 1024
_MAX_POINTS_ROLLBACK_PROPERTIES = 256
_MAX_POINTS_ROLLBACK_PROPERTY_BYTES = 2 * 1024 * 1024
_MAX_INSPECTION_ROLLBACK_PROPERTIES = 256
_MAX_INSPECTION_ROLLBACK_PROPERTY_BYTES = 2 * 1024 * 1024
_MAX_INSPECTION_ROLLBACK_DISTANCES = 2_000_000
_MAX_ROBOT_ROLLBACK_PROPERTIES = 256
_MAX_ROBOT_ROLLBACK_PROPERTY_BYTES = 4 * 1024 * 1024
_MAX_ROLLBACK_PROPERTY_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
_ROBOT_TRAJECTORY_TYPES = frozenset(
    {"Robot::TrajectoryObject", "Robot::TrajectoryDressUpObject"}
)
_INSPECTION_FEATURE_KERNEL_PROPERTIES = frozenset(
    {
        "Actual",
        "Nominals",
        "SearchRadius",
        "Thickness",
        "Distances",
    }
)


def _property_content_sha256(content: bytes | bytearray) -> str:
    """Hash persisted property data while ignoring ZIP container metadata."""

    raw = bytes(content)
    digest = hashlib.sha256()
    try:
        with zipfile.ZipFile(BytesIO(raw), "r") as archive:
            infos = sorted(
                archive.infolist(),
                key=lambda item: (str(item.filename), int(item.header_offset)),
            )
            uncompressed_bytes = sum(int(item.file_size) for item in infos)
            if uncompressed_bytes > _MAX_ROLLBACK_PROPERTY_UNCOMPRESSED_BYTES:
                raise RuntimeError(
                    "A rollback property exceeds the bounded uncompressed content limit."
                )
            digest.update(b"zip\0")
            for info in infos:
                name = str(info.filename).encode("utf-8", errors="surrogatepass")
                with archive.open(info, "r") as handle:
                    payload = handle.read(
                        _MAX_ROLLBACK_PROPERTY_UNCOMPRESSED_BYTES + 1
                    )
                if len(payload) > _MAX_ROLLBACK_PROPERTY_UNCOMPRESSED_BYTES:
                    raise RuntimeError(
                        "A rollback property entry exceeds the bounded content limit."
                    )
                digest.update(len(name).to_bytes(8, "big"))
                digest.update(name)
                digest.update(len(payload).to_bytes(8, "big"))
                digest.update(payload)
            return digest.hexdigest()
    except zipfile.BadZipFile:
        digest.update(b"raw\0")
        digest.update(raw)
        return digest.hexdigest()


def _properties(obj: Any) -> set[str]:
    return set(getattr(obj, "PropertiesList", []) or [])


def _add_string_property(obj: Any, name: str, description: str) -> None:
    if name not in _properties(obj):
        obj.addProperty("App::PropertyString", name, "VibeCAD", description)


def _add_property(obj: Any, property_type: str, name: str, description: str) -> None:
    if name not in _properties(obj):
        obj.addProperty(property_type, name, "VibeCAD", description)


def _ensure_input_link_property(
    obj: Any,
    property_type: str,
    description: str,
) -> None:
    """Keep accepted input links capable of representing their exact documents."""

    if PROP_INPUT_OBJECTS not in _properties(obj):
        obj.addProperty(
            property_type,
            PROP_INPUT_OBJECTS,
            "VibeCAD",
            description,
        )
        return
    current_type = str(obj.getTypeIdOfProperty(PROP_INPUT_OBJECTS) or "")
    if current_type == property_type:
        return
    if (
        current_type == "App::PropertyLinkList"
        and property_type == "App::PropertyXLinkList"
    ):
        previous = list(getattr(obj, PROP_INPUT_OBJECTS, []) or [])
        if not obj.removeProperty(PROP_INPUT_OBJECTS):
            raise RuntimeError(
                f"Could not upgrade {PROP_INPUT_OBJECTS!r} for cross-document inputs."
            )
        obj.addProperty(
            property_type,
            PROP_INPUT_OBJECTS,
            "VibeCAD",
            description,
        )
        setattr(obj, PROP_INPUT_OBJECTS, previous)
        return
    raise RuntimeError(
        f"{PROP_INPUT_OBJECTS!r} has unsupported type {current_type!r}; "
        f"expected {property_type!r}."
    )


def _hide_property(obj: Any, name: str) -> None:
    setter = getattr(obj, "setEditorMode", None)
    if callable(setter):
        setter(name, 2)


def _make_timeline_property_internal(obj: Any, name: str) -> None:
    """Persist the native status contract required by document History."""

    setter = getattr(obj, "setPropertyStatus", None)
    if not callable(setter):
        raise RuntimeError(
            f"History metadata {name!r} cannot be made internal on "
            f"{getattr(obj, 'Name', '<unknown>')!r}."
        )
    setter(name, ("Hidden", "LockDynamic", "NoRecompute"))
    _hide_property(obj, name)


def _set_partdesign_program_history_commands(operation: Any) -> None:
    """Bind one global program operation to exact source lifecycle commands."""

    commands = {
        "VibeCADTimelineEditCommand": (
            "VibeCAD_EditScriptedModel",
            "Approved command which edits this VibeScript program.",
        ),
        "VibeCADTimelineDeleteCommand": (
            "VibeCAD_DeleteScriptedModel",
            "Approved command which deletes this VibeScript program.",
        ),
    }
    for name, (command, description) in commands.items():
        if name in _properties(operation):
            if operation.getTypeIdOfProperty(name) != "App::PropertyString":
                raise RuntimeError(
                    "A VibeScript History operation has incompatible command "
                    "metadata."
                )
        else:
            operation.addProperty(
                "App::PropertyString",
                name,
                "Timeline",
                description,
                attr=16,
                hidden=True,
                locked=True,
            )
        setattr(operation, name, command)
        _make_timeline_property_internal(operation, name)


def compact_persisted_input_snapshots(doc: Any) -> dict[str, Any]:
    """Remove obsolete full input facts from accepted-revision metadata.

    Runtime execution receives authenticated facts from the candidate input
    bundle. Persisted live objects need only stable identities and digests.
    Older documents duplicated the full facts payload on every output, so
    equal property strings are decoded once and compacted in place.
    """

    compacted_by_raw: dict[str, str | None] = {}
    changed_objects: list[str] = []
    invalid_objects: list[str] = []
    before_bytes = 0
    after_bytes = 0
    for obj in list(getattr(doc, "Objects", []) or []):
        if PROP_INPUT_SNAPSHOTS not in _properties(obj):
            continue
        raw = str(getattr(obj, PROP_INPUT_SNAPSHOTS, "") or "")
        if raw not in compacted_by_raw:
            compacted: str | None = raw
            try:
                snapshots = json.loads(raw)
                if not isinstance(snapshots, list) or not all(
                    isinstance(snapshot, dict) for snapshot in snapshots
                ):
                    raise ValueError("input snapshots must be a list of objects")
                if any("facts" in snapshot for snapshot in snapshots):
                    compacted = json.dumps(
                        [
                            {
                                key: value
                                for key, value in snapshot.items()
                                if key != "facts"
                            }
                            for snapshot in snapshots
                        ],
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
            except (TypeError, ValueError):
                compacted = None
            compacted_by_raw[raw] = compacted
        compacted = compacted_by_raw[raw]
        if compacted is None:
            invalid_objects.append(str(getattr(obj, "Name", "") or ""))
            continue
        if compacted == raw:
            continue
        before_bytes += len(raw.encode("utf-8"))
        after_bytes += len(compacted.encode("utf-8"))
        setattr(obj, PROP_INPUT_SNAPSHOTS, compacted)
        changed_objects.append(str(getattr(obj, "Name", "") or ""))
    return {
        "changed_objects": changed_objects,
        "invalid_objects": invalid_objects,
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
    }


def _assembly_dependency_output_name(assembly_output: str) -> str:
    return f"{assembly_output}.{_ASSEMBLY_DEPENDENCY_SUFFIX}"


def _find_assembly_dependency_anchor(
    doc: Any,
    program_id: str,
    assembly_output: str,
) -> Any | None:
    output_name = _assembly_dependency_output_name(assembly_output)
    matches = [
        obj
        for obj in _program_objects(doc, program_id, "assembly")
        if str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        == output_name
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple Assembly dependency anchors claim {output_name!r}."
        )
    if not matches:
        return None
    anchor = matches[0]
    if str(getattr(anchor, "TypeId", "") or "") != "App::FeaturePython":
        raise RuntimeError(
            f"Assembly dependency anchor {anchor.Name!r} is not an App::FeaturePython."
        )
    return anchor


def _create_assembly_dependency_anchor(
    doc: Any,
    program_id: str,
    assembly_output: str,
) -> Any:
    name = _SAFE_NAME.sub(
        "_",
        f"VibeAssembly_{program_id[:8]}_{assembly_output}_Dependencies",
    )[:120]
    anchor = doc.addObject("App::FeaturePython", name)
    if anchor is None:
        raise RuntimeError("FreeCAD did not create the Assembly dependency anchor.")
    anchor.Label = "VibeScript Assembly dependencies"
    view = getattr(anchor, "ViewObject", None)
    if view is not None:
        view.Visibility = False
        if hasattr(view, "ShowInTree"):
            view.ShowInTree = False
    return anchor


def _configure_assembly_dependency_anchor(
    anchor: Any,
    prepared: Mapping[str, Any],
    assembly_item: Mapping[str, Any],
) -> None:
    """Configure the one internal dependency resource owned by an Assembly."""

    assembly_output = str(assembly_item["name"])
    anchor.Label = "VibeScript Assembly dependencies"
    view = getattr(anchor, "ViewObject", None)
    if view is not None:
        view.Visibility = False
        if hasattr(view, "ShowInTree"):
            view.ShowInTree = False
    _set_metadata(
        anchor,
        prepared,
        _assembly_dependency_output_name(assembly_output),
        _ASSEMBLY_DEPENDENCY_OUTPUT_TYPE,
        _definition(assembly_item),
    )


def _assembly_adopted_occurrences(anchor: Any | None) -> dict[str, Any]:
    """Read the exact Assembly-output mapping for borrowed Model occurrences."""

    if anchor is None:
        return {}
    properties = _properties(anchor)
    if PROP_ASSEMBLY_ADOPTED_OUTPUTS not in properties:
        return {}
    names = [
        str(value or "")
        for value in list(getattr(anchor, PROP_ASSEMBLY_ADOPTED_OUTPUTS, []) or [])
    ]
    if PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES in properties:
        object_names = [
            str(value or "")
            for value in list(
                getattr(anchor, PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES, []) or []
            )
        ]
        document = getattr(anchor, "Document", None)
        occurrences = [
            document.getObject(object_name)
            if document is not None and object_name
            else None
            for object_name in object_names
        ]
    elif PROP_ASSEMBLY_ADOPTED_OCCURRENCES in properties:
        # Compatibility with the first occurrence-adoption documents. New
        # documents store stable object names because a top-level resource link
        # into an Assembly child is out of native PropertyXLink scope.
        occurrences = list(
            getattr(anchor, PROP_ASSEMBLY_ADOPTED_OCCURRENCES, []) or []
        )
    else:
        return {}
    if (
        len(names) != len(occurrences)
        or any(not name or "." in name for name in names)
        or len(set(names)) != len(names)
        or any(occurrence is None for occurrence in occurrences)
        or len({id(occurrence) for occurrence in occurrences})
        != len(occurrences)
    ):
        raise RuntimeError(
            "Assembly borrowed-occurrence metadata is malformed."
        )
    return dict(zip(names, occurrences))


def _set_assembly_adopted_occurrences(
    anchor: Any,
    occurrences: Mapping[str, Any],
) -> None:
    """Persist output identity without overwriting the borrowed object's owner."""

    ordered = sorted((str(name), obj) for name, obj in occurrences.items())
    if any(not name or "." in name or obj is None for name, obj in ordered):
        raise RuntimeError("Assembly borrowed-occurrence mapping is invalid.")
    if len({id(obj) for _name, obj in ordered}) != len(ordered):
        raise RuntimeError(
            "One existing occurrence cannot back multiple Assembly outputs. "
            "Create another linked occurrence with api.component or api.instances."
        )
    _add_property(
        anchor,
        "App::PropertyStringList",
        PROP_ASSEMBLY_ADOPTED_OUTPUTS,
        "Assembly output names backed by existing Model occurrences.",
    )
    _add_property(
        anchor,
        "App::PropertyStringList",
        PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES,
        "Stable object names of existing occurrences adopted by this Assembly.",
    )
    setattr(anchor, PROP_ASSEMBLY_ADOPTED_OUTPUTS, [name for name, _obj in ordered])
    setattr(
        anchor,
        PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES,
        [str(obj.Name) for _name, obj in ordered],
    )
    if PROP_ASSEMBLY_ADOPTED_OCCURRENCES in _properties(anchor):
        setattr(anchor, PROP_ASSEMBLY_ADOPTED_OCCURRENCES, [])
    _hide_property(anchor, PROP_ASSEMBLY_ADOPTED_OUTPUTS)
    _hide_property(anchor, PROP_ASSEMBLY_ADOPTED_OCCURRENCE_NAMES)
    if PROP_ASSEMBLY_ADOPTED_OCCURRENCES in _properties(anchor):
        _hide_property(anchor, PROP_ASSEMBLY_ADOPTED_OCCURRENCES)


def _assembly_adoption_target(
    doc: Any,
    item: Mapping[str, Any],
) -> Any | None:
    """Return an existing local occurrence that Assembly must reuse in place."""

    if (
        str(item.get("type") or "") != "component_link"
        or str(_definition(item).get("operation") or "") != "component"
    ):
        return None
    data = item.get("assembly_data")
    data = dict(data) if isinstance(data, Mapping) else {}
    source = data.get("source")
    if source is None:
        source = _definition_argument(_definition(item), 0, "source")
    target = _reference_target(
        doc,
        source,
        f"output {item.get('name')} source",
    )
    if getattr(target, "Document", None) is not doc:
        return None
    properties = _properties(target)
    if (
        str(getattr(target, "TypeId", "") or "") != "App::Link"
        or contracts.PROP_PROGRAM_ID not in properties
        or contracts.PROP_PROGRAM_DOMAIN not in properties
        or PROP_OUTPUT_TYPE not in properties
        or str(getattr(target, PROP_OUTPUT_TYPE, "") or "") != "component_link"
        or str(getattr(target, contracts.PROP_PROGRAM_DOMAIN, "") or "")
        not in {"partdesign", "robot"}
    ):
        return None
    return target


def _assembly_occurrence_containers(occurrence: Any) -> list[Any]:
    return [
        owner
        for owner in list(getattr(occurrence, "InList", []) or [])
        if str(getattr(owner, "TypeId", "") or "")
        == "Assembly::AssemblyObject"
    ]


def _adopt_assembly_occurrence(
    assembly: Any,
    occurrence: Any,
    *,
    output_name: str,
) -> None:
    owners = _assembly_occurrence_containers(occurrence)
    foreign = [owner for owner in owners if owner is not assembly]
    if foreign:
        raise RuntimeError(
            f"Model occurrence {occurrence.Name!r} is already adopted by Assembly "
            f"{foreign[0].Name!r}; one occurrence cannot belong to two mechanisms. "
            "Create another linked occurrence with api.component or api.instances."
        )
    if not owners:
        add_object = getattr(assembly, "addObject", None)
        if not callable(add_object):
            raise RuntimeError(
                "The native Assembly container cannot adopt an existing occurrence."
            )
        add_object(occurrence)
    if assembly not in _assembly_occurrence_containers(occurrence):
        raise RuntimeError(
            f"Assembly failed to adopt Model occurrence for output {output_name!r}."
        )


def _release_assembly_occurrence(assembly: Any, occurrence: Any) -> None:
    if assembly not in _assembly_occurrence_containers(occurrence):
        return
    remove_object = getattr(assembly, "removeObject", None)
    if not callable(remove_object):
        raise RuntimeError(
            "The native Assembly container cannot release a borrowed occurrence."
        )
    remove_object(occurrence)
    if assembly in _assembly_occurrence_containers(occurrence):
        raise RuntimeError(
            f"Assembly failed to release borrowed occurrence {occurrence.Name!r}."
        )


def migrate_assembly_dependency_anchors(doc: Any) -> dict[str, Any]:
    """Move external dependency links off native Assembly containers.

    ``Assembly::AssemblyObject`` enforces GeoFeatureGroup scope for its link
    properties. Older publications put cross-container dependency links on the
    assembly itself, causing an out-of-scope warning on every recompute. A
    hidden top-level ``App::FeaturePython`` owns those invalidation links instead.
    """

    migrated: list[str] = []
    created: list[str] = []
    for assembly in list(getattr(doc, "Objects", []) or []):
        if str(getattr(assembly, "TypeId", "") or "") != "Assembly::AssemblyObject":
            continue
        if (
            str(getattr(assembly, contracts.PROP_PROGRAM_DOMAIN, "") or "")
            != "assembly"
        ):
            continue
        direct = list(getattr(assembly, PROP_INPUT_OBJECTS, []) or [])
        nested = list(getattr(assembly, PROP_NESTED_INPUT_OBJECTS, []) or [])
        if not direct and not nested:
            continue
        program_id = str(
            getattr(assembly, contracts.PROP_PROGRAM_ID, "") or ""
        )
        output_name = str(
            getattr(assembly, contracts.PROP_PROGRAM_OUTPUT, "") or ""
        )
        if not program_id or not output_name:
            raise RuntimeError(
                f"Assembly {assembly.Name!r} has dependency links without stable "
                "VibeScript program metadata."
            )
        anchor = _find_assembly_dependency_anchor(
            doc,
            program_id,
            output_name,
        )
        if anchor is None:
            anchor = _create_assembly_dependency_anchor(
                doc,
                program_id,
                output_name,
            )
            created.append(str(anchor.Name))
        view = getattr(anchor, "ViewObject", None)
        if view is not None:
            view.Visibility = False
            if hasattr(view, "ShowInTree"):
                view.ShowInTree = False
        string_fields = (
            contracts.PROP_PROGRAM_ID,
            contracts.PROP_PROGRAM_DOMAIN,
            contracts.PROP_PROGRAM_WORKBENCH,
            contracts.PROP_PROGRAM_REVISION,
            PROP_DEFINITION,
            PROP_INPUT_SNAPSHOTS,
            reference_contracts.PROP_DERIVED_STATE,
            reference_contracts.PROP_STALE_REASON,
            reference_contracts.PROP_SOURCE_REVISION,
        )
        for name in string_fields:
            _add_string_property(anchor, name, "Migrated Assembly dependency metadata.")
            setattr(anchor, name, str(getattr(assembly, name, "") or ""))
        _add_string_property(
            anchor,
            contracts.PROP_PROGRAM_OUTPUT,
            "Internal Assembly dependency owner.",
        )
        setattr(
            anchor,
            contracts.PROP_PROGRAM_OUTPUT,
            _assembly_dependency_output_name(output_name),
        )
        _add_string_property(
            anchor,
            PROP_OUTPUT_TYPE,
            "Internal VibeScript publication type.",
        )
        setattr(anchor, PROP_OUTPUT_TYPE, _ASSEMBLY_DEPENDENCY_OUTPUT_TYPE)
        _add_property(
            anchor,
            "App::PropertyXLinkList",
            PROP_INPUT_OBJECTS,
            "Live document objects used by the accepted Assembly revision.",
        )
        setattr(anchor, PROP_INPUT_OBJECTS, direct)
        _add_property(
            anchor,
            "App::PropertyXLinkList",
            PROP_NESTED_INPUT_OBJECTS,
            "Nested objects used by the accepted Assembly revision.",
        )
        setattr(anchor, PROP_NESTED_INPUT_OBJECTS, nested)
        setattr(assembly, PROP_INPUT_OBJECTS, [])
        if PROP_NESTED_INPUT_OBJECTS in _properties(assembly):
            setattr(assembly, PROP_NESTED_INPUT_OBJECTS, [])
        migrated.append(str(assembly.Name))
    return {"migrated_assemblies": migrated, "created_anchors": created}


def _ensure_assembly_motion_properties(obj: Any) -> None:
    for property_type, name, description in (
        (
            "App::PropertyXLinkSubHidden",
            "Joint",
            "The native joint driven by this motion.",
        ),
        ("App::PropertyString", "Formula", "The native symbolic motion formula."),
        ("App::PropertyEnumeration", "MotionType", "Angular or linear motion."),
    ):
        if name not in _properties(obj):
            obj.addProperty(property_type, name, "Motion", description, locked=True)


def _ensure_assembly_simulation_properties(obj: Any) -> None:
    if "Group" not in _properties(obj):
        obj.addExtension("App::GroupExtensionPython")
    for property_type, name, description in (
        ("App::PropertyTime", "aTimeStart", "Simulation start time."),
        ("App::PropertyTime", "bTimeEnd", "Simulation end time."),
        ("App::PropertyTime", "cTimeStepOutput", "Simulation output time step."),
        (
            "App::PropertyFloat",
            "fGlobalErrorTolerance",
            "Integration global error tolerance.",
        ),
        ("App::PropertyInteger", "jFramesPerSecond", "Playback frames per second."),
    ):
        if name not in _properties(obj):
            obj.addProperty(property_type, name, "Simulation", description, locked=True)


class AssemblyMotionProxy:
    """Persistent headless-safe proxy for a native Assembly motion contract."""

    def __init__(self, obj: Any | None = None) -> None:
        if obj is not None:
            obj.Proxy = self
            _ensure_assembly_motion_properties(obj)

    def onDocumentRestored(self, obj: Any) -> None:  # noqa: N802
        _ensure_assembly_motion_properties(obj)

    def getSimulation(self, obj: Any) -> Any:  # noqa: N802
        for owner in list(getattr(obj, "InList", []) or []):
            proxy = getattr(owner, "Proxy", None)
            if callable(getattr(proxy, "setMotionsChangedCallback", None)):
                return owner
        return None

    def getAssembly(self, obj: Any) -> Any:  # noqa: N802
        simulation = self.getSimulation(obj)
        proxy = getattr(simulation, "Proxy", None)
        getter = getattr(proxy, "getAssembly", None)
        return getter(simulation) if callable(getter) else None

    def execute(self, _obj: Any) -> None:
        return None

    def dumps(self) -> None:
        return None

    def loads(self, _state: Any) -> None:
        return None


class AssemblySimulationProxy:
    """Persistent proxy for precomputed native Assembly simulation settings."""

    def __init__(self, obj: Any | None = None) -> None:
        self.motionsChangedCallback = None
        if obj is not None:
            obj.Proxy = self
            _ensure_assembly_simulation_properties(obj)
            self._mark_native_contract(obj)
            _ensure_native_simulation_view_provider(obj)

    def onDocumentRestored(self, obj: Any) -> None:  # noqa: N802
        self.motionsChangedCallback = None
        _ensure_assembly_simulation_properties(obj)
        self._mark_native_contract(obj)
        _ensure_native_simulation_view_provider(obj)

    @staticmethod
    def _mark_native_contract(obj: Any) -> None:
        import UtilsAssembly

        UtilsAssembly.markTimelineOperationEditor(
            obj,
            "Assembly_EditHistoryOperation",
        )

    def onChanged(self, obj: Any, prop: str) -> None:  # noqa: N802
        if prop != "Group":
            return
        self._mark_native_contract(obj)
        callback = getattr(self, "motionsChangedCallback", None)
        if callable(callback):
            callback()

    def setMotionsChangedCallback(self, callback: Any) -> None:  # noqa: N802
        self.motionsChangedCallback = callback

    def getAssembly(self, obj: Any) -> Any:  # noqa: N802
        import UtilsAssembly

        return UtilsAssembly.findOwningAssembly(obj)

    def execute(self, _obj: Any) -> None:
        return None

    def dumps(self) -> None:
        return None

    def loads(self, _state: Any) -> None:
        return None


def _ensure_native_simulation_view_provider(obj: Any) -> None:
    """Attach FreeCAD's real Simulation view provider when a GUI is present."""

    try:
        import FreeCAD as App

        if not bool(App.GuiUp):
            return
        view = getattr(obj, "ViewObject", None)
        if view is None:
            return
        from CommandCreateSimulation import ViewProviderSimulation

        if not isinstance(getattr(view, "Proxy", None), ViewProviderSimulation):
            ViewProviderSimulation(view)
    except ImportError:
        return


def _ensure_assembly_bom_restore_properties(obj: Any) -> None:
    _add_property(
        obj,
        "App::PropertyLink",
        PROP_ASSEMBLY_BOM_RESTORE_TARGET,
        "The accepted frozen Assembly BOM restored without native regeneration.",
    )
    _add_string_property(
        obj,
        PROP_ASSEMBLY_BOM_RESTORE_ERROR,
        "A precise failure if the accepted BOM table could not be restored.",
    )


class AssemblyBOMRestoreProxy:
    """Restore an accepted literal BOM after native document-load generation.

    ``Assembly::BomObject`` rebuilds its native table while an FCStd document is
    loaded, before the persisted frozen state is effective.  This managed proxy
    runs after document restoration, replays only the already-authenticated
    literal cells, and freezes the BOM again.  It never traverses sources,
    recomputes, or invokes native BOM generation.
    """

    def __init__(self, obj: Any | None = None) -> None:
        if obj is not None:
            obj.Proxy = self
            _ensure_assembly_bom_restore_properties(obj)

    def onDocumentRestored(self, obj: Any) -> None:  # noqa: N802
        _ensure_assembly_bom_restore_properties(obj)
        try:
            target = getattr(obj, PROP_ASSEMBLY_BOM_RESTORE_TARGET, None)
            if target is None or str(getattr(target, "TypeId", "") or "") != (
                "Assembly::BomObject"
            ):
                raise RuntimeError("The managed Assembly BOM target is unavailable.")
            if str(getattr(target, contracts.PROP_PROGRAM_ID, "") or "") != str(
                getattr(obj, contracts.PROP_PROGRAM_ID, "") or ""
            ):
                raise RuntimeError("The managed Assembly BOM target belongs to another program.")
            encoded = str(getattr(target, PROP_ASSEMBLY_BOM_VALIDATION, "") or "")
            if not encoded or len(encoded.encode("utf-8")) > 1_000_000:
                raise RuntimeError(
                    "The accepted Assembly BOM validation is missing or exceeds 1 MB."
                )
            data = json.loads(encoded)
            if not isinstance(data, dict) or str(data.get("schema") or "") != (
                "vibecad-assembly-bom-v1"
            ):
                raise RuntimeError("The accepted Assembly BOM validation schema is invalid.")
            _unfreeze_object(target, "Assembly BOM")
            _populate_assembly_bom_without_recomputing(target, data)
            if _assembly_bom_live_readback(
                target, data
            ) != _assembly_bom_expected_readback(data):
                raise RuntimeError(
                    "The accepted Assembly BOM literal table did not restore exactly."
                )
            _freeze_object(target, "Assembly BOM")
            setattr(obj, PROP_ASSEMBLY_BOM_RESTORE_ERROR, "")
        except Exception as exc:
            setattr(
                obj,
                PROP_ASSEMBLY_BOM_RESTORE_ERROR,
                f"{type(exc).__name__}: {exc}",
            )
            raise

    def execute(self, _obj: Any) -> None:
        return None

    def dumps(self) -> None:
        return None

    def loads(self, _state: Any) -> None:
        return None


def _object_is_frozen(obj: Any, contract: str) -> bool:
    checker = getattr(obj, "isFrozen", None)
    if not callable(checker):
        raise RuntimeError(
            f"This FreeCAD build cannot freeze native {contract} results; "
            "synchronous recompute protection is unavailable."
        )
    return bool(checker())


def _freeze_object(obj: Any, contract: str) -> None:
    freezer = getattr(obj, "freeze", None)
    if not callable(freezer):
        raise RuntimeError(
            f"This FreeCAD build cannot freeze native {contract} results; "
            "synchronous recompute protection is unavailable."
        )
    obj.purgeTouched()
    freezer()
    obj.purgeTouched()
    if not _object_is_frozen(obj, contract):
        raise RuntimeError(f"The native {contract} result did not enter frozen state.")


def _unfreeze_object(obj: Any, contract: str) -> None:
    if not _object_is_frozen(obj, contract):
        return
    unfreezer = getattr(obj, "unfreeze", None)
    if not callable(unfreezer):
        raise RuntimeError(f"The native {contract} result cannot be unfrozen safely.")
    unfreezer(True)
    if _object_is_frozen(obj, contract):
        raise RuntimeError(f"The native {contract} result remained frozen during update.")


def _inspection_feature_is_frozen(obj: Any) -> bool:
    return _object_is_frozen(obj, "Inspection")


def _freeze_inspection_feature(obj: Any) -> None:
    _freeze_object(obj, "Inspection")


def _unfreeze_inspection_feature(obj: Any) -> None:
    _unfreeze_object(obj, "Inspection")


def _robot_dressup_is_frozen(obj: Any) -> bool:
    return _object_is_frozen(obj, "Robot dress-up")


def _freeze_robot_dressup(obj: Any) -> None:
    _freeze_object(obj, "Robot dress-up")


def _unfreeze_robot_dressup(obj: Any) -> None:
    _unfreeze_object(obj, "Robot dress-up")


def _set_metadata(
    obj: Any,
    prepared: Mapping[str, Any],
    output_name: str,
    output_type: str,
    definition: Mapping[str, Any],
) -> None:
    expected_outputs = list(prepared.get("expected_outputs") or [])
    contract_owner = bool(
        expected_outputs
        and str(expected_outputs[0].get("name") or "") == output_name
    )
    fields = (
        (
            contracts.PROP_PROGRAM_ID,
            "Stable VibeScript program id.",
            str(prepared["program_id"]),
        ),
        (
            contracts.PROP_PROGRAM_DOMAIN,
            "VibeScript workbench domain.",
            prepared["pack"].domain,
        ),
        (
            contracts.PROP_PROGRAM_WORKBENCH,
            "Workbench owning this VibeScript program.",
            prepared["pack"].workbench,
        ),
        (
            contracts.PROP_PROGRAM_REVISION,
            "Accepted VibeScript program revision.",
            str(prepared["revision"]),
        ),
        (
            contracts.PROP_PROGRAM_OUTPUT,
            "Stable VibeScript output name.",
            output_name,
        ),
        (
            contracts.PROP_PROGRAM_LABEL,
            "Stable VibeScript program label.",
            str(prepared["program_name"]),
        ),
        (PROP_OUTPUT_TYPE, "Declared VibeScript output type.", output_type),
        (
            PROP_DEFINITION,
            "Validated declarative VibeScript output definition.",
            json.dumps(
                dict(definition),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    for name, description, value in fields:
        _add_string_property(obj, name, description)
        setattr(obj, name, value)
    _add_string_property(
        obj,
        contracts.PROP_PROGRAM_CONTRACT,
        "Portable accepted VibeScript source, inputs, and output contract.",
    )
    setattr(
        obj,
        contracts.PROP_PROGRAM_CONTRACT,
        (
            str(prepared.get("document_program_contract") or "")
            if contract_owner
            else ""
        ),
    )
    _hide_property(obj, contracts.PROP_PROGRAM_CONTRACT)
    document = getattr(obj, "Document", None)
    resolved_references = list(prepared.get("resolved_references") or [])
    document_uid = str(getattr(document, "Uid", "") or "")
    has_external_input = any(
        str(reference.get("document_uid") or "")
        and str(reference.get("document_uid") or "") != document_uid
        for reference in resolved_references
    )
    input_link_property_type = (
        "App::PropertyXLinkList"
        if has_external_input
        or (
            prepared["pack"].domain == "assembly"
            and output_type == _ASSEMBLY_DEPENDENCY_OUTPUT_TYPE
        )
        else "App::PropertyLinkList"
    )
    _ensure_input_link_property(
        obj,
        input_link_property_type,
        "Live document objects snapshotted as inputs for this accepted output.",
    )
    targets = []
    snapshots = []
    for reference in resolved_references:
        if reference.get("reference_kind") == "point_artifact":
            snapshots.append(
                {
                    key: reference.get(key)
                    for key in (
                        "reference_kind",
                        "artifact_id",
                        "name",
                        "label",
                        "format",
                        "artifact_sha256",
                        "artifact_bytes",
                    )
                }
            )
            continue
        try:
            target_reference = {
                key: reference[key]
                for key in (
                    "document_uid",
                    "object_name",
                    "document_path",
                )
                if reference.get(key)
            }
            target = (
                resolve_reference_target(
                    document,
                    target_reference,
                    f"Accepted input object {reference.get('object_name')!r}",
                )
                if document is not None
                else None
            )
        except DocumentReferenceError as exc:
            raise RuntimeError(str(exc)) from exc
        if target is None:
            raise RuntimeError(
                f"Accepted input object {reference.get('object_name')!r} disappeared "
                "before publication metadata was applied."
            )
        targets.append(target)
        snapshots.append(
            {
                key: reference.get(key)
                for key in _PERSISTED_INPUT_SNAPSHOT_KEYS
            }
        )
    dependency_targets = targets
    if prepared["pack"].domain == "assembly":
        # Native Assembly containers and children reject links outside their
        # GeoFeatureGroup scope. A hidden top-level dependency anchor owns all
        # source links used for downstream invalidation.
        dependency_targets = (
            targets
            if output_type == _ASSEMBLY_DEPENDENCY_OUTPUT_TYPE
            else []
        )
    setattr(obj, PROP_INPUT_OBJECTS, dependency_targets)
    if prepared["pack"].domain == "assembly":
        _add_property(
            obj,
            "App::PropertyXLinkList",
            PROP_NESTED_INPUT_OBJECTS,
            "Nested native source objects authenticated through an Assembly/App::Part hierarchy.",
        )
        nested_targets: list[Any] = []
        if output_type == _ASSEMBLY_DEPENDENCY_OUTPUT_TYPE:
            import FreeCAD as App

            documents_by_uid = {
                str(getattr(candidate, "Uid", "") or ""): candidate
                for candidate in App.listDocuments().values()
            }
            seen_identities = {
                (
                    str(getattr(getattr(target, "Document", None), "Uid", "") or ""),
                    str(getattr(target, "Name", "") or ""),
                )
                for target in targets
            }
            for reference in resolved_references:
                hierarchy = reference.get("assembly_hierarchy")
                if not isinstance(hierarchy, Mapping):
                    continue
                for node in list(hierarchy.get("nodes") or []):
                    if not isinstance(node, Mapping):
                        continue
                    identity = node.get("identity")
                    if not isinstance(identity, Mapping):
                        continue
                    identity_key = (
                        str(identity.get("document_uid") or ""),
                        str(identity.get("object_name") or ""),
                    )
                    source_document = documents_by_uid.get(identity_key[0])
                    target = (
                        source_document.getObject(identity_key[1])
                        if source_document is not None
                        else None
                    )
                    if target is None:
                        raise RuntimeError(
                            "Accepted nested Assembly source "
                            f"{identity_key[1]!r} disappeared before publication could "
                            "install downstream invalidation."
                        )
                    if identity_key not in seen_identities:
                        nested_targets.append(target)
                        seen_identities.add(identity_key)
        setattr(obj, PROP_NESTED_INPUT_OBJECTS, nested_targets)
    _add_string_property(
        obj,
        PROP_INPUT_SNAPSHOTS,
        "Immutable identities and input artifact digests used by the accepted revision.",
    )
    setattr(
        obj,
        PROP_INPUT_SNAPSHOTS,
        json.dumps(snapshots, sort_keys=True, separators=(",", ":")),
    )
    for name in (
        reference_contracts.PROP_DERIVED_STATE,
        reference_contracts.PROP_STALE_REASON,
        reference_contracts.PROP_SOURCE_REVISION,
    ):
        _add_string_property(obj, name, "Accepted input snapshot state.")
    setattr(obj, reference_contracts.PROP_DERIVED_STATE, "accepted")
    setattr(obj, reference_contracts.PROP_STALE_REASON, "")
    setattr(obj, reference_contracts.PROP_SOURCE_REVISION, str(prepared["revision"]))


def source_property_affects_vibescript_snapshot(property_name: str) -> bool:
    """Return whether one native property notification changes source semantics."""

    changed_property = str(property_name or "")
    return not (
        not changed_property
        or changed_property.startswith("VibeCAD")
        or changed_property
        in {
            "_GroupTouched",
            "_LinkTouched",
            "ShowInTree",
            "Visibility",
        }
    )


def mark_programs_stale_from_source(source: Any, property_name: str) -> list[str]:
    """Mark v2 outputs stale when a linked native snapshot source changes."""

    changed_property = str(property_name or "")
    if not source_property_affects_vibescript_snapshot(changed_property):
        # Group and Link extensions emit their private touched properties while
        # executing and restoring. Actual membership, placement, geometry, and
        # published-contract edits emit their own properties separately.
        return []
    label_only = changed_property == "Label"
    marked: list[str] = []
    programs: dict[tuple[str, str, str], Any] = {}
    for output in list(getattr(source, "InList", []) or []):
        properties = _properties(output)
        if not ({PROP_INPUT_OBJECTS, PROP_NESTED_INPUT_OBJECTS} & properties):
            continue
        inputs = [
            *list(getattr(output, PROP_INPUT_OBJECTS, []) or []),
            *list(getattr(output, PROP_NESTED_INPUT_OBJECTS, []) or []),
        ]
        if not any(item is source for item in inputs):
            continue
        output_document = getattr(output, "Document", None)
        program_id = str(getattr(output, contracts.PROP_PROGRAM_ID, "") or "")
        domain = str(getattr(output, contracts.PROP_PROGRAM_DOMAIN, "") or "")
        if label_only and domain != "assembly":
            continue
        if output_document is None or not program_id or not domain:
            continue
        programs[
            (
                str(getattr(output_document, "Uid", "") or ""),
                program_id,
                domain,
            )
        ] = output_document
    for (_document_uid, program_id, domain), document in programs.items():
        candidates = _program_objects(document, program_id, domain)
        for output in candidates:
            inspection_feature = (
                domain == "inspection"
                and str(getattr(output, "TypeId", "") or "")
                == "Inspection::Feature"
            )
            assembly_bom = (
                domain == "assembly"
                and str(getattr(output, "TypeId", "") or "")
                == "Assembly::BomObject"
            )
            already_stale = (
                str(getattr(output, reference_contracts.PROP_DERIVED_STATE, "") or "")
                == "stale"
            )
            if not already_stale:
                if inspection_feature:
                    _unfreeze_inspection_feature(output)
                elif assembly_bom:
                    _unfreeze_object(output, "Assembly BOM")
                revision = str(
                    getattr(output, contracts.PROP_PROGRAM_REVISION, "") or ""
                )
                try:
                    reference_contracts.mark_stale(
                        output,
                        revision,
                        f"Input object {getattr(source, 'Name', '<object>')}."
                        f"{changed_property} changed after this VibeScript snapshot; "
                        "regenerate the program.",
                    )
                finally:
                    if inspection_feature:
                        _freeze_inspection_feature(output)
                    elif assembly_bom:
                        _freeze_object(output, "Assembly BOM")
            elif inspection_feature and not _inspection_feature_is_frozen(output):
                _freeze_inspection_feature(output)
            elif assembly_bom and not _object_is_frozen(output, "Assembly BOM"):
                _freeze_object(output, "Assembly BOM")
            if not already_stale:
                marked.append(str(getattr(output, "Name", "") or ""))
    return sorted(set(marked))


def _program_objects(doc: Any, program_id: str, domain: str) -> list[Any]:
    result = []
    for obj in list(getattr(doc, "Objects", []) or []):
        properties = _properties(obj)
        if not {contracts.PROP_PROGRAM_ID, contracts.PROP_PROGRAM_DOMAIN} <= properties:
            continue
        if (
            str(getattr(obj, contracts.PROP_PROGRAM_ID, "") or "") == program_id
            and str(getattr(obj, contracts.PROP_PROGRAM_DOMAIN, "") or "") == domain
        ):
            result.append(obj)
    return result


def _objects_by_output(doc: Any, prepared: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for obj in _program_objects(
        doc, str(prepared["program_id"]), prepared["pack"].domain
    ):
        output_name = str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        if not output_name or "." in output_name:
            continue
        if output_name in result:
            raise RuntimeError(
                f"Multiple live objects claim VibeScript output {output_name!r}."
            )
        result[output_name] = obj
    return result


def _retired_program_objects(
    doc: Any,
    prepared: Mapping[str, Any],
    desired_outputs: set[str],
) -> list[Any]:
    owned = _program_objects(doc, str(prepared["program_id"]), prepared["pack"].domain)
    retired = []
    for obj in owned:
        output_name = str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        root_name = output_name.partition(".")[0]
        if not output_name or root_name in desired_outputs:
            continue
        retired.append(obj)
    return retired


def _deletion_object_identity(obj: Any, *, context: str) -> tuple[str, int]:
    name = str(getattr(obj, "Name", "") or "")
    try:
        object_id = int(getattr(obj, "ID"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{context} has no stable native document identity."
        ) from exc
    if not name or object_id <= 0:
        raise RuntimeError(f"{context} has an invalid native document identity.")
    return name, object_id


def _resolve_timeline_identity(
    doc: Any,
    identity: tuple[str, int],
) -> Any | None:
    candidate = doc.getObject(identity[0])
    if candidate is None:
        return None
    try:
        candidate_id = int(getattr(candidate, "ID"))
    except (AttributeError, TypeError, ValueError):
        return None
    return candidate if candidate_id == identity[1] else None


def _is_created_timeline_object(
    doc: Any,
    obj: Any,
    created_objects: list[Any],
) -> bool:
    identity = _deletion_object_identity(
        obj,
        context="A VibeScript publication object",
    )
    return any(
        _deletion_object_identity(
            candidate,
            context="A VibeScript publication object",
        )
        == identity
        and _resolve_timeline_identity(doc, identity) is obj
        for candidate in created_objects
    )


def _is_current_transaction_timeline_object(doc: Any, obj: Any) -> bool:
    """Return the native timeline's exact current-transaction proof."""

    identity = _deletion_object_identity(
        obj,
        context="A VibeScript publication object",
    )
    if _resolve_timeline_identity(doc, identity) is not obj:
        raise RuntimeError(
            "A VibeScript publication object is no longer live in its document."
        )
    query = getattr(
        doc,
        "isProvisionallyEnrolledInTimelineByCurrentTransaction",
        None,
    )
    if not callable(query):
        raise RuntimeError(
            "The native current-transaction timeline identity query is unavailable."
        )
    return bool(query(obj))


def _timeline_role(obj: Any, *, context: str) -> str:
    properties = _properties(obj)
    if "VibeCADTimelineRole" not in properties:
        return ""
    if obj.getTypeIdOfProperty("VibeCADTimelineRole") != "App::PropertyString":
        raise RuntimeError(f"{context} has an invalid native History role property.")
    role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    if role not in {"", "operation", "resource", "internal"}:
        raise RuntimeError(f"{context} has unsupported native History role {role!r}.")
    return role


def _timeline_owner(obj: Any, *, context: str) -> Any | None:
    properties = _properties(obj)
    if "VibeCADTimelineOwner" not in properties:
        return None
    if obj.getTypeIdOfProperty("VibeCADTimelineOwner") != "App::PropertyLinkHidden":
        raise RuntimeError(f"{context} has an invalid native History owner property.")
    return getattr(obj, "VibeCADTimelineOwner", None)


def _mark_timeline_operation(obj: Any, *, context: str) -> None:
    role = _timeline_role(obj, context=context)
    owner = _timeline_owner(obj, context=context)
    if role not in {"", "operation"} or owner is not None:
        raise RuntimeError(f"{context} cannot be published as a History operation.")
    _add_property(
        obj,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document History classification.",
    )
    if (
        obj.getTypeIdOfProperty("VibeCADTimelineRole")
        != "App::PropertyString"
    ):
        raise RuntimeError(f"{context} has an invalid History role property.")
    obj.VibeCADTimelineRole = "operation"
    _make_timeline_property_internal(obj, "VibeCADTimelineRole")
    if "VibeCADTimelineOwner" in _properties(obj):
        _make_timeline_property_internal(obj, "VibeCADTimelineOwner")


def _mark_timeline_resource(
    obj: Any,
    owner: Any,
    *,
    context: str,
) -> None:
    if obj is owner or getattr(obj, "Document", None) is not getattr(
        owner,
        "Document",
        None,
    ):
        raise RuntimeError(f"{context} has an invalid History owner.")
    role = _timeline_role(obj, context=context)
    current_owner = _timeline_owner(obj, context=context)
    if role not in {"", "resource"} or (
        current_owner is not None and current_owner is not owner
    ):
        raise RuntimeError(f"{context} has incompatible History ownership.")
    _mark_timeline_operation(owner, context=f"{context} owner")
    _add_property(
        obj,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document History classification.",
    )
    _add_property(
        obj,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Semantic History operation which owns this implementation object.",
    )
    if (
        obj.getTypeIdOfProperty("VibeCADTimelineRole")
        != "App::PropertyString"
        or obj.getTypeIdOfProperty("VibeCADTimelineOwner")
        != "App::PropertyLinkHidden"
    ):
        raise RuntimeError(f"{context} has invalid History metadata.")
    obj.VibeCADTimelineOwner = owner
    obj.VibeCADTimelineRole = "resource"
    _make_timeline_property_internal(obj, "VibeCADTimelineRole")
    _make_timeline_property_internal(obj, "VibeCADTimelineOwner")


def _canonical_timeline_resource_graph(
    operation: Any,
    authored_resources: list[Any],
    *,
    context: str,
) -> tuple[list[Any], list[Any]]:
    """Validate and canonicalize one explicitly authored resource graph.

    ``authored_resources`` is an identity list supplied by the domain
    publisher.  Its order is the stable authored sibling order; ownership is
    read only from the typed native owner link.  Names, labels, native types,
    document deltas, and group membership never participate in classification.
    """

    operation_identity = _deletion_object_identity(
        operation,
        context=f"{context} operation",
    )
    resources: list[Any] = []
    resource_by_identity: dict[tuple[str, int], Any] = {}
    for resource in authored_resources:
        identity = _deletion_object_identity(
            resource,
            context=f"{context} resource",
        )
        if identity == operation_identity or identity in resource_by_identity:
            raise RuntimeError(f"{context} contains a duplicate or self-owned resource.")
        resource_by_identity[identity] = resource
        resources.append(resource)

    resource_set = set(resources)
    owner_by_resource: dict[Any, Any] = {}
    children: dict[Any, list[Any]] = {operation: []}
    children.update({resource: [] for resource in resources})
    for resource in resources:
        resource_context = (
            f"{context} resource {str(getattr(resource, 'Name', '') or '')!r}"
        )
        if _timeline_role(resource, context=resource_context) != "resource":
            raise RuntimeError(f"{resource_context} is not explicitly a resource.")
        owner = _timeline_owner(resource, context=resource_context)
        if owner is not operation and owner not in resource_set:
            raise RuntimeError(
                f"{resource_context} does not resolve to the declared operation."
            )
        owner_by_resource[resource] = owner
        children[owner].append(resource)

    ordered: list[Any] = []
    ordered_owners: list[Any] = []
    visiting: set[Any] = set()
    visited: set[Any] = set()

    def visit(resource: Any) -> None:
        if resource in visiting:
            raise RuntimeError(f"{context} contains a cyclic resource-owner graph.")
        if resource in visited:
            raise RuntimeError(f"{context} contains a multiply owned resource.")
        visiting.add(resource)
        for child in children[resource]:
            visit(child)
        visiting.remove(resource)
        visited.add(resource)
        ordered.append(resource)
        ordered_owners.append(owner_by_resource[resource])

    for root in children[operation]:
        visit(root)
    if len(visited) != len(resources):
        raise RuntimeError(f"{context} contains an unreachable resource.")
    return ordered, ordered_owners


def _timeline_resource_key(obj: Any, *, context: str) -> str:
    properties = _properties(obj)
    if contracts.PROP_PROGRAM_OUTPUT not in properties:
        raise RuntimeError(f"{context} has no stable authored resource key.")
    if (
        obj.getTypeIdOfProperty(contracts.PROP_PROGRAM_OUTPUT)
        != "App::PropertyString"
    ):
        raise RuntimeError(f"{context} has an invalid stable authored resource key.")
    key = str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
    if not key:
        raise RuntimeError(f"{context} has an empty stable authored resource key.")
    return key


_ASSEMBLY_SOURCE_IDENTITY_PROPERTIES = {
    "VibeCADAssemblySourceDocument": "App::PropertyString",
    "VibeCADAssemblySourceObjectId": "App::PropertyInteger",
    "VibeCADAssemblySourceObjectName": "App::PropertyString",
}


def _assembly_managed_resource_identity(
    obj: Any,
    *,
    context: str,
) -> tuple[str, int, str] | None:
    """Read the exact native AssemblyLink source identity, when present."""

    properties = _properties(obj)
    present = [
        name in properties for name in _ASSEMBLY_SOURCE_IDENTITY_PROPERTIES
    ]
    if not any(present):
        return None
    if not all(present):
        raise RuntimeError(f"{context} has incomplete AssemblyLink source identity.")
    for name, type_id in _ASSEMBLY_SOURCE_IDENTITY_PROPERTIES.items():
        if obj.getTypeIdOfProperty(name) != type_id:
            raise RuntimeError(
                f"{context} has an invalid AssemblyLink source identity property "
                f"{name!r}."
            )
    document_uid = str(
        getattr(obj, "VibeCADAssemblySourceDocument", "") or ""
    )
    object_id = int(getattr(obj, "VibeCADAssemblySourceObjectId", -1))
    object_name = str(
        getattr(obj, "VibeCADAssemblySourceObjectName", "") or ""
    )
    if not document_uid or object_id < 0 or not object_name:
        raise RuntimeError(f"{context} has an empty AssemblyLink source identity.")
    return document_uid, object_id, object_name


def _assembly_timeline_resource_key(obj: Any, *, context: str) -> str:
    """Return an authored key for one Assembly resource.

    VibeScript helpers use their persisted output key.  Native occurrence
    resources use the exact source identity written by ``AssemblyLink``.
    No name, label, native type, group, or dependency inference participates.
    """

    if contracts.PROP_PROGRAM_OUTPUT in _properties(obj):
        return f"vibescript:{_timeline_resource_key(obj, context=context)}"
    path: list[tuple[str, int, str]] = []
    current = obj
    visited: set[tuple[str, int]] = set()
    while True:
        document_identity = _deletion_object_identity(
            current,
            context=f"{context} owner path",
        )
        if document_identity in visited:
            raise RuntimeError(f"{context} has cyclic Assembly ownership.")
        visited.add(document_identity)
        identity = _assembly_managed_resource_identity(
            current,
            context=f"{context} owner path",
        )
        if identity is None:
            if current is obj:
                raise RuntimeError(f"{context} has no exact Assembly resource identity.")
            break
        path.append(identity)
        owner = _timeline_owner(current, context=f"{context} owner path")
        if owner is None or _timeline_role(
            owner,
            context=f"{context} owner path",
        ) != "resource":
            break
        current = owner
    path.reverse()
    return json.dumps(path, ensure_ascii=True, separators=(",", ":"))


def _capture_timeline_resource_reconciliation(
    doc: Any,
    operation: Any,
    *,
    key_for_resource=_timeline_resource_key,
    context: str,
) -> dict[str, Any]:
    """Capture one surviving operation's exact current resource graph."""

    closure_query = getattr(doc, "semanticTimelineCopyClosure", None)
    if not callable(closure_query):
        raise RuntimeError("The native semantic History closure query is unavailable.")
    closure = list(closure_query([operation]))
    if not any(candidate is operation for candidate in closure):
        raise RuntimeError(f"{context} is not present in native History.")

    resources: list[Any] = []
    for candidate in closure:
        if candidate is operation:
            continue
        current = candidate
        visited: set[tuple[str, int]] = set()
        while _timeline_role(
            current,
            context=f"{context} closure member",
        ) == "resource":
            identity = _deletion_object_identity(
                current,
                context=f"{context} closure member",
            )
            if identity in visited:
                raise RuntimeError(f"{context} contains a cyclic resource-owner graph.")
            visited.add(identity)
            owner = _timeline_owner(
                current,
                context=f"{context} closure member",
            )
            if owner is operation:
                resources.append(candidate)
                break
            if owner is None:
                break
            current = owner

    ordered, _owners = _canonical_timeline_resource_graph(
        operation,
        resources,
        context=context,
    )
    if ordered != resources:
        raise RuntimeError(
            f"{context} resources are not in canonical native History order."
        )
    direct_roots = [
        resource
        for resource in ordered
        if _timeline_owner(resource, context=f"{context} resource") is operation
    ]
    keys: list[str] = []
    key_indices: dict[str, int] = {}
    for index, resource in enumerate(ordered):
        key = key_for_resource(
            resource,
            context=f"{context} resource {resource.Name!r}",
        )
        if key in key_indices:
            raise RuntimeError(f"{context} has duplicate authored resource key {key!r}.")
        key_indices[key] = index
        keys.append(key)
    return {
        "operation_identity": _deletion_object_identity(
            operation,
            context=f"{context} operation",
        ),
        "resources": ordered,
        "resource_identities": [
            _deletion_object_identity(resource, context=f"{context} resource")
            for resource in ordered
        ],
        "resource_keys": keys,
        "direct_roots": direct_roots,
    }


def _stage_timeline_resource_reconciliation(
    doc: Any,
    operation: Any,
    captured: Mapping[str, Any],
    *,
    context: str,
) -> None:
    if _resolve_timeline_identity(
        doc,
        tuple(captured["operation_identity"]),
    ) is not operation:
        raise RuntimeError(f"{context} changed identity before reconciliation.")
    stage = getattr(doc, "stageTimelineOperationResourceReconciliation", None)
    if not callable(stage):
        raise RuntimeError(
            "The native staged History resource-reconciliation API is unavailable."
        )
    stage(operation, list(captured["direct_roots"]))


def _finalize_timeline_resource_reconciliation(
    doc: Any,
    operation: Any,
    captured: Mapping[str, Any],
    authored_final_resources: list[Any],
    *,
    key_for_resource=_timeline_resource_key,
    context: str,
) -> list[Any]:
    """Atomically reconcile retained, new, replaced, and retired resources."""

    final_resources, _owners = _canonical_timeline_resource_graph(
        operation,
        authored_final_resources,
        context=context,
    )
    old_resources = list(captured["resources"])
    old_identities = [tuple(value) for value in captured["resource_identities"]]
    old_keys = [str(value) for value in captured["resource_keys"]]
    old_index_by_identity = {
        identity: index for index, identity in enumerate(old_identities)
    }
    old_index_by_key = {key: index for index, key in enumerate(old_keys)}

    final_identities: list[tuple[str, int]] = []
    final_keys: list[str] = []
    final_index_by_identity: dict[tuple[str, int], int] = {}
    final_index_by_key: dict[str, int] = {}
    state_sources: list[int] = []
    for index, resource in enumerate(final_resources):
        identity = _deletion_object_identity(
            resource,
            context=f"{context} final resource",
        )
        key = key_for_resource(
            resource,
            context=f"{context} final resource {resource.Name!r}",
        )
        if identity in final_index_by_identity or key in final_index_by_key:
            raise RuntimeError(
                f"{context} contains duplicate final identity or authored key {key!r}."
            )
        final_identities.append(identity)
        final_keys.append(key)
        final_index_by_identity[identity] = index
        final_index_by_key[key] = index
        state_sources.append(
            old_index_by_identity.get(identity, old_index_by_key.get(key, -1))
        )

    consumer_replacements = [
        final_index_by_identity.get(
            identity,
            final_index_by_key.get(key, -1),
        )
        for identity, key in zip(old_identities, old_keys)
    ]
    finalize = getattr(
        doc,
        "finalizeProvisionalTimelineOperationResourceReconciliation",
        None,
    )
    if not callable(finalize):
        raise RuntimeError(
            "The native staged History resource-reconciliation finalizer is unavailable."
        )
    finalize(
        operation,
        final_resources,
        state_sources,
        consumer_replacements,
    )

    final_identity_set = set(final_identities)
    return [
        resource
        for resource, identity in zip(old_resources, old_identities)
        if identity not in final_identity_set
        and _resolve_timeline_identity(doc, identity) is resource
    ]


def _publish_new_timeline_resource_block(
    doc: Any,
    operation: Any,
    authored_resources: list[Any],
    *,
    context: str,
) -> list[str]:
    """Atomically publish one new exact operation/resource graph."""

    ordered, owners = _canonical_timeline_resource_graph(
        operation,
        authored_resources,
        context=context,
    )
    publish = getattr(doc, "publishProvisionalTimelineOperationBlock", None)
    if not callable(publish):
        raise RuntimeError(
            "The native atomic History operation publication API is unavailable."
        )
    publish(operation, ordered, owners)
    return [str(obj.Name) for obj in [*ordered, operation]]


def _remove_reconciled_timeline_resources(
    doc: Any,
    resources: list[Any],
    *,
    context: str,
) -> list[str]:
    """Delete resources which the native reconciler already made internal."""

    removed: list[str] = []
    for resource in resources:
        identity = _deletion_object_identity(
            resource,
            context=f"{context} retired resource",
        )
        live = _resolve_timeline_identity(doc, identity)
        if live is None:
            continue
        role = _timeline_role(
            live,
            context=f"{context} retired resource {identity[0]!r}",
        )
        owner = _timeline_owner(
            live,
            context=f"{context} retired resource {identity[0]!r}",
        )
        if role != "internal" or owner is not None:
            raise RuntimeError(
                f"{context} retired resource {identity[0]!r} was not "
                "released by native History reconciliation."
            )
        doc.removeObject(identity[0])
        if _resolve_timeline_identity(doc, identity) is not None:
            raise RuntimeError(
                f"{context} retired resource {identity[0]!r} survived deletion."
            )
        removed.append(identity[0])
    return removed


def _finalize_timeline_resource_block(
    doc: Any,
    operation: Any,
    ordered_resources: list[Any],
    created_objects: list[Any],
) -> list[str]:
    """Finalize only exact resources created by the current publication.

    Callers establish the domain-owned role/owner metadata first.  This
    boundary deliberately does not infer ownership from names, types, groups,
    or dependency edges: ``ordered_resources`` is the exact semantic resource
    list authored by the publisher, and ``created_objects`` is the exact set
    returned by its native factories in the still-active transaction.
    """

    operation_identity = _deletion_object_identity(
        operation,
        context="A VibeScript timeline operation",
    )
    if _resolve_timeline_identity(doc, operation_identity) is not operation:
        raise RuntimeError(
            "A VibeScript timeline operation is no longer live in its document."
        )

    created_identities: set[tuple[str, int]] = set()
    for candidate in created_objects:
        identity = _deletion_object_identity(
            candidate,
            context="A VibeScript publication object",
        )
        if _resolve_timeline_identity(doc, identity) is candidate:
            created_identities.add(identity)

    operation_properties = _properties(operation)
    if (
        "VibeCADTimelineRole" not in operation_properties
        or operation.getTypeIdOfProperty("VibeCADTimelineRole")
        != "App::PropertyString"
        or str(getattr(operation, "VibeCADTimelineRole", "") or "")
        != "operation"
    ):
        raise RuntimeError(
            f"VibeScript timeline operation {operation.Name!r} has no exact "
            "operation role."
        )
    if "VibeCADTimelineOwner" in operation_properties and (
        operation.getTypeIdOfProperty("VibeCADTimelineOwner")
        != "App::PropertyLinkHidden"
        or getattr(operation, "VibeCADTimelineOwner", None) is not None
    ):
        raise RuntimeError(
            f"VibeScript timeline operation {operation.Name!r} retains resource "
            "owner metadata."
        )

    new_resources: list[Any] = []
    seen_resources: set[tuple[str, int]] = set()
    for resource in ordered_resources:
        identity = _deletion_object_identity(
            resource,
            context="A VibeScript timeline resource",
        )
        if (
            identity in seen_resources
            or _resolve_timeline_identity(doc, identity) is not resource
            or resource is operation
        ):
            raise RuntimeError(
                "A VibeScript timeline resource list contains a duplicate, "
                "detached, or self-owned object."
            )
        seen_resources.add(identity)
        properties = _properties(resource)
        if (
            "VibeCADTimelineRole" not in properties
            or resource.getTypeIdOfProperty("VibeCADTimelineRole")
            != "App::PropertyString"
            or str(getattr(resource, "VibeCADTimelineRole", "") or "")
            != "resource"
            or "VibeCADTimelineOwner" not in properties
            or resource.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
            or getattr(resource, "VibeCADTimelineOwner", None) is not operation
        ):
            raise RuntimeError(
                f"VibeScript timeline resource {resource.Name!r} does not have "
                f"the exact owner {operation.Name!r}."
            )
        if _is_current_transaction_timeline_object(doc, resource):
            new_resources.append(resource)

    operation_is_new = _is_current_transaction_timeline_object(doc, operation)
    if operation_is_new and operation_identity not in created_identities:
        raise RuntimeError(
            "A new VibeScript timeline operation was not created by the current "
            "publication."
        )
    if operation_is_new and len(new_resources) != len(ordered_resources):
        raise RuntimeError(
            "A new VibeScript timeline operation cannot adopt a pre-existing "
            "resource without the native staged-adoption contract."
        )
    if not operation_is_new and not new_resources:
        return []

    ordered_new_objects = list(new_resources)
    if operation_is_new:
        ordered_new_objects.append(operation)
    finalizer = getattr(
        doc,
        "finalizeProvisionalTimelineOperationBlock",
        None,
    )
    if not callable(finalizer):
        raise RuntimeError(
            "The native provisional timeline finalizer is unavailable."
        )
    finalizer(operation, ordered_new_objects)
    return [str(obj.Name) for obj in ordered_new_objects]


def _prepare_timeline_deletion(
    doc: Any,
    objects: list[Any],
) -> dict[str, Any]:
    """Resolve every native deletion contract before document mutation.

    The graph itself is owned and validated by ``App::DocumentTimeline``.
    Python stores only exact object identities so wrappers cannot silently
    retarget after one of the planned objects has been removed.
    """

    if not objects:
        return {
            "delete_objects": [],
            "resource_objects": [],
            "root_objects": [],
            "delete_identities": [],
            "reveal_identities": [],
        }

    try:
        import FreeCAD as App
    except ImportError:
        App = None

    planner = getattr(App, "timelineOperationDeletionPlan", None)
    if not callable(planner):
        # Compatibility with builds that exposed the App-owned planner only
        # through the GUI module.
        import FreeCADGui

        planner = getattr(FreeCADGui, "timelineOperationDeletionPlan", None)
    if not callable(planner):
        raise RuntimeError(
            "The native document-history deletion planner is unavailable."
        )

    delete_objects: list[Any] = []
    delete_identities: list[tuple[str, int]] = []
    resource_objects: list[Any] = []
    root_objects: list[Any] = []
    reveal_identities: list[tuple[str, int]] = []
    seen_delete: set[tuple[str, int]] = set()
    seen_reveal: set[tuple[str, int]] = set()

    def require_live(item: Any, *, context: str) -> tuple[str, int]:
        if getattr(item, "Document", None) is not doc:
            raise RuntimeError(f"{context} left its owning document.")
        identity = _deletion_object_identity(item, context=context)
        if _resolve_timeline_identity(doc, identity) is None:
            raise RuntimeError(f"{context} is no longer live in its document.")
        return identity

    roots: list[Any] = []
    seen_roots: set[tuple[str, int]] = set()
    for obj in objects:
        identity = require_live(obj, context="A VibeScript deletion target")
        if identity not in seen_roots:
            roots.append(obj)
            seen_roots.add(identity)

    def semantic_owner(resource: Any) -> Any:
        current = resource
        visited: set[tuple[str, int]] = set()
        while str(getattr(current, "VibeCADTimelineRole", "") or "") == "resource":
            identity = require_live(
                current,
                context="A native document-history resource",
            )
            if identity in visited:
                raise RuntimeError(
                    f"Cannot delete {identity[0]!r}; its native "
                    "document-history ownership is cyclic."
                )
            visited.add(identity)

            properties = set(getattr(current, "PropertiesList", []) or [])
            property_type = getattr(current, "getTypeIdOfProperty", None)
            try:
                valid_metadata = (
                    "VibeCADTimelineRole" in properties
                    and "VibeCADTimelineOwner" in properties
                    and callable(property_type)
                    and property_type("VibeCADTimelineRole")
                    == "App::PropertyString"
                    and property_type("VibeCADTimelineOwner")
                    == "App::PropertyLinkHidden"
                )
            except (AttributeError, KeyError, ReferenceError, RuntimeError, TypeError):
                valid_metadata = False
            if not valid_metadata:
                raise RuntimeError(
                    f"Cannot delete {identity[0]!r}; its native "
                    "document-history ownership metadata is malformed."
                )

            owner = getattr(current, "VibeCADTimelineOwner", None)
            if owner is None:
                raise RuntimeError(
                    f"Cannot delete {identity[0]!r}; its native "
                    "document-history owner is missing."
                )
            require_live(
                owner,
                context="A native document-history resource owner",
            )
            current = owner
        return current

    # A resource is implementation state, never an independently deletable
    # program result. Validate the complete ownership chain against the
    # aggregate requested roots before asking the native planner for any
    # deletion plan. This keeps a resource-only VibeScript cleanup from
    # orphaning the durable operation it implements.
    for root in roots:
        if str(getattr(root, "VibeCADTimelineRole", "") or "") != "resource":
            continue
        owner = semantic_owner(root)
        owner_identity = require_live(
            owner,
            context="A native document-history operation",
        )
        if owner_identity not in seen_roots:
            label = str(
                getattr(owner, "Label", "")
                or getattr(owner, "Name", "")
                or owner_identity[0]
            )
            raise RuntimeError(
                "This result belongs to the history operation "
                f"{label!r} and cannot be deleted by itself. Delete or edit "
                "that operation in History instead."
            )

    plans: list[Mapping[str, Any]] = []
    for root in roots:
        raw_plan = planner(root)
        if not isinstance(raw_plan, Mapping):
            raise RuntimeError(
                "The native document-history deletion planner returned an "
                "invalid result."
            )
        if not isinstance(raw_plan.get("applicable"), bool) or not isinstance(
            raw_plan.get("valid"), bool
        ):
            raise RuntimeError(
                "The native document-history deletion planner returned an "
                "invalid status."
            )
        for field in (
            "replaced_inputs",
            "objects_to_reveal",
            "owned_resources",
        ):
            if not isinstance(raw_plan.get(field), list):
                raise RuntimeError(
                    "The native document-history deletion planner returned an "
                    f"invalid {field!r} collection."
                )
        if not raw_plan["valid"]:
            name = str(getattr(root, "Name", "") or "")
            raise RuntimeError(
                f"Cannot delete {name!r}; its native document-history metadata "
                "is malformed."
            )
        plans.append(raw_plan)

    # Native plans already order owned resources deepest-first. Preserve that
    # order across roots, then add the requested program objects.
    for plan in plans:
        if not plan["applicable"]:
            continue
        for resource in plan["owned_resources"]:
            identity = require_live(
                resource,
                context="A native document-history resource",
            )
            if identity not in seen_delete:
                delete_objects.append(resource)
                resource_objects.append(resource)
                delete_identities.append(identity)
                seen_delete.add(identity)
        for replacement in plan["objects_to_reveal"]:
            identity = require_live(
                replacement,
                context="A native document-history replacement input",
            )
            if identity not in seen_reveal:
                reveal_identities.append(identity)
                seen_reveal.add(identity)

    for root in roots:
        identity = require_live(root, context="A VibeScript deletion target")
        if identity not in seen_delete:
            delete_objects.append(root)
            root_objects.append(root)
            delete_identities.append(identity)
            seen_delete.add(identity)

    return {
        "delete_objects": delete_objects,
        "resource_objects": resource_objects,
        "root_objects": root_objects,
        "delete_identities": delete_identities,
        "reveal_identities": reveal_identities,
    }


def _finish_timeline_deletion(
    doc: Any,
    deletion: Mapping[str, Any],
) -> list[str]:
    delete_identities = list(deletion["delete_identities"])
    delete_set = set(delete_identities)
    survivors = [
        identity[0]
        for identity in delete_identities
        if _resolve_timeline_identity(doc, identity) is not None
    ]
    if survivors:
        raise RuntimeError(
            "Native document-history objects survived deletion: "
            + ", ".join(sorted(survivors))
        )

    revealed: list[str] = []
    for identity in list(deletion["reveal_identities"]):
        if identity in delete_set:
            continue
        replacement = _resolve_timeline_identity(doc, identity)
        if replacement is None:
            raise RuntimeError(
                f"Native document-history input {identity[0]!r} disappeared "
                "during deletion."
            )
        view = getattr(replacement, "ViewObject", None)
        if view is None or not hasattr(view, "Visibility"):
            raise RuntimeError(
                f"Native document-history input {identity[0]!r} has no "
                "persistent visibility."
            )
        view.Visibility = True
        revealed.append(identity[0])
    return revealed


def _remove_objects_dependency_order(
    doc: Any,
    objects: list[Any],
) -> list[str]:
    remaining = {str(obj.Name): obj for obj in objects}
    removed: list[str] = []
    while remaining:
        children = [
            obj
            for obj in remaining.values()
            if not any(
                str(getattr(parent, "Name", "") or "") in remaining
                for parent in list(getattr(obj, "InList", []) or [])
            )
        ]
        obj = children[0] if children else next(iter(remaining.values()))
        name = str(obj.Name)
        remaining.pop(name, None)
        if doc.getObject(name) is not None:
            doc.removeObject(name)
            removed.append(name)
    return removed


def _remove_timeline_resources(
    doc: Any,
    deletion: Mapping[str, Any],
) -> list[str]:
    removed: list[str] = []
    # The native plan is already deepest-first. Remove implementation
    # resources while their semantic owners and editor metadata are still
    # live, then remove the requested program objects.
    for resource in list(deletion["resource_objects"]):
        identity = _deletion_object_identity(
            resource,
            context="A native document-history resource",
        )
        if _resolve_timeline_identity(doc, identity) is None:
            continue
        doc.removeObject(identity[0])
        if _resolve_timeline_identity(doc, identity) is not None:
            raise RuntimeError(
                f"Native document-history resource {identity[0]!r} survived "
                "deletion."
            )
        removed.append(identity[0])
    return removed


def _remove_timeline_deletion(
    doc: Any,
    deletion: Mapping[str, Any],
) -> list[str]:
    removed = _remove_timeline_resources(doc, deletion)
    removed.extend(
        _remove_objects_dependency_order(
            doc,
            list(deletion["root_objects"]),
        )
    )
    _finish_timeline_deletion(doc, deletion)
    return removed


def _remove_owned_objects(doc: Any, objects: list[Any]) -> list[str]:
    return _remove_timeline_deletion(
        doc,
        _prepare_timeline_deletion(doc, objects),
    )


def _remove_failed_domain_creations(doc: Any, object_names: list[str]) -> list[str]:
    """Remove only objects created by the failed publication attempt.

    Aborting a native document transaction may already have removed some of
    these objects.  Resolve names again after the abort, remove the survivors
    in dependency-safe order, and prove that none remain.  This deliberately
    operates on the captured creation list rather than rediscovering objects
    from program metadata, which could include the previously accepted state.
    """

    names = list(dict.fromkeys(str(name) for name in object_names if str(name)))
    objects = [obj for name in names if (obj := doc.getObject(name)) is not None]
    # These are unaccepted objects captured from a failed attempt, not live
    # timeline operations. Their transaction may already have rolled back
    # some members, so cleanup must use only this exact captured set.
    removed = _remove_objects_dependency_order(doc, objects)
    survivors = [name for name in names if doc.getObject(name) is not None]
    if survivors:
        raise RuntimeError(
            "Failed publication objects remain after cleanup: "
            + ", ".join(sorted(survivors))
        )
    return removed


def _external_uses(
    doc: Any,
    targets: list[Any],
    internal: list[Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    return scripted_publication.external_reference_uses(
        doc,
        targets,
        internal_objects=internal,
    )


def _reference_error(prefix: str, uses: list[dict[str, Any]]) -> RuntimeError:
    details = scripted_publication.json_reference_uses(uses)
    return RuntimeError(f"{prefix}: {json.dumps(details, sort_keys=True)}")


def _preflight_output_updates(
    doc: Any,
    targets: list[Any],
    internal: list[Any],
) -> list[dict[str, Any]]:
    uses = _external_uses(doc, targets, internal)
    unsafe = [item for item in uses if list(item.get("subelements") or [])]
    if unsafe:
        raise _reference_error(
            "Cannot regenerate these stable VibeScript outputs while native objects "
            "hold Face/Edge/Vertex references. This domain does not claim those transient "
            "subelement names are semantically stable; remove or retarget the listed "
            "consumers, then retry",
            unsafe,
        )
    return uses


def _refresh_external_consumers(
    uses: list[dict[str, Any]],
    *,
    revision: str,
) -> dict[str, Any]:
    touched: list[str] = []
    stale: list[str] = []
    owners: dict[int, Any] = {
        id(item["owner"]): item["owner"]
        for item in uses
        if item.get("owner") is not None
    }
    for owner in owners.values():
        name = str(getattr(owner, "Name", "") or "")
        touch = getattr(owner, "touch", None)
        if callable(touch):
            touch()
            touched.append(name)
        type_id = str(getattr(owner, "TypeId", "") or "").lower()
        if any(
            marker in type_id
            for marker in (
                "fem",
                "path::",
                "techdraw",
                "robot",
                "inspection",
            )
        ):
            reference_contracts.mark_stale(
                owner,
                revision,
                "A referenced VibeScript Part output changed; regenerate this derived result.",
            )
            stale.append(name)
    return {"touched": sorted(set(touched)), "marked_stale": sorted(set(stale))}


def _internal_name(prepared: Mapping[str, Any], output_name: str) -> str:
    domain = _SAFE_NAME.sub("_", prepared["pack"].domain.title())
    output = _SAFE_NAME.sub("_", output_name)
    return f"Vibe{domain}_{str(prepared['program_id'])[:8]}_{output}"[:120]


def _native_type(output_type: str, domain: str = "") -> str:
    if domain == "fem":
        native_type = {
            "analysis": "Fem::FemAnalysis",
            "solver": "Fem::FemSolverObjectPython",
            "material": "App::MaterialObjectPython",
            "constraint": "Fem::ConstraintFixed",
            "load_case": "App::DocumentObjectGroup",
            "mesh": "Fem::FemMeshShapeBaseObjectPython",
            "result": "Fem::FemResultObjectPython",
        }.get(output_type)
        if native_type is None:
            raise RuntimeError(
                f"No native FEM publisher exists for output type {output_type!r}."
            )
        return native_type
    if domain == "draft" and output_type == "wire":
        return "Part::FeaturePython"
    if output_type in _BREP_OUTPUT_TYPES:
        return "Part::Feature"
    if output_type == "mesh":
        return "Mesh::Feature"
    if output_type == "points":
        return "Points::Feature"
    native_type = _NATIVE_TYPE_BY_OUTPUT.get(output_type)
    if native_type is None:
        raise RuntimeError(
            f"No native publisher exists for output type {output_type!r}."
        )
    return native_type


def _definition_argument(
    definition: Mapping[str, Any],
    index: int,
    *names: str,
    default: Any = None,
) -> Any:
    arguments = list(definition.get("arguments") or [])
    if index < len(arguments):
        return arguments[index]
    properties = dict(definition.get("properties") or {})
    for name in names:
        if name in properties:
            return properties[name]
    return default


def _native_vector(value: Any, label: str) -> Any:
    import FreeCAD as App

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RuntimeError(f"{label} must be [x, y, z].")
    return App.Vector(*(float(item) for item in value))


def _create_draft_object(
    doc: Any,
    definition: Mapping[str, Any],
    output_type: str,
    name: str,
) -> Any:
    import FreeCAD as App

    if output_type == "wire":
        from draftobjects.wire import Wire

        obj = doc.addObject("Part::FeaturePython", name)
        obj.addExtension("Part::AttachExtensionPython")
        Wire(obj)
        if App.GuiUp:
            from draftviewproviders.view_wire import ViewProviderWire

            ViewProviderWire(obj.ViewObject)
    elif output_type == "circle":
        from draftobjects.circle import Circle

        obj = doc.addObject("Part::Part2DObjectPython", name)
        Circle(obj)
        if App.GuiUp:
            from draftviewproviders.view_base import ViewProviderDraft

            ViewProviderDraft(obj.ViewObject)
    elif output_type == "rectangle":
        from draftobjects.rectangle import Rectangle

        obj = doc.addObject("Part::Part2DObjectPython", name)
        Rectangle(obj)
        if App.GuiUp:
            from draftviewproviders.view_rectangle import ViewProviderRectangle

            ViewProviderRectangle(obj.ViewObject)
    elif output_type == "bspline":
        from draftobjects.bspline import BSpline

        obj = doc.addObject("Part::FeaturePython", name)
        obj.addExtension("Part::AttachExtensionPython")
        BSpline(obj)
        if App.GuiUp:
            from draftviewproviders.view_bspline import ViewProviderBSpline

            ViewProviderBSpline(obj.ViewObject)
    elif output_type == "text":
        from draftobjects.text import Text

        obj = doc.addObject("App::FeaturePython", name)
        Text(obj)
        if App.GuiUp:
            from draftviewproviders.view_text import ViewProviderText

            ViewProviderText(obj.ViewObject)
    elif output_type == "array":
        from draftobjects.array import Array

        properties = dict(definition.get("properties") or {})
        use_link = bool(properties.get("use_link"))
        if use_link:
            obj = doc.addObject(
                "Part::FeaturePython",
                name,
                Array(None),
                None,
                True,
            )
        else:
            obj = doc.addObject("Part::FeaturePython", name)
            Array(obj)
        if App.GuiUp:
            if use_link:
                from draftviewproviders.view_draftlink import ViewProviderDraftLink

                ViewProviderDraftLink(obj.ViewObject)
            else:
                from draftviewproviders.view_array import ViewProviderDraftArray

                ViewProviderDraftArray(obj.ViewObject)
    else:
        raise RuntimeError(f"No native Draft factory exists for {output_type!r}.")
    if obj is None:
        raise RuntimeError(
            f"The native Draft factory returned no {output_type} object."
        )
    return obj


def _create_object(
    doc: Any,
    prepared: Mapping[str, Any],
    output_name: str,
    output_type: str,
    definition: Mapping[str, Any],
    assembly: Any | None,
    assembly_fastener_sources: Mapping[str, Any] | None = None,
) -> Any:
    native_type = _native_type(output_type, prepared["pack"].domain)
    name = _internal_name(prepared, output_name)
    if prepared["pack"].domain == "draft":
        obj = _create_draft_object(doc, definition, output_type, name)
    elif prepared["pack"].domain == "fem":
        import ObjectsFem

        if output_type == "analysis":
            obj = ObjectsFem.makeAnalysis(doc, name)
        elif output_type == "solver":
            obj = ObjectsFem.makeSolverCalculiXCcxTools(doc, name)
        elif output_type == "material":
            obj = ObjectsFem.makeMaterialSolid(doc, name)
        elif output_type == "constraint":
            kind = str(dict(definition.get("properties") or {}).get("kind") or "")
            factory = {
                "fixed": ObjectsFem.makeConstraintFixed,
                "force": ObjectsFem.makeConstraintForce,
                "pressure": ObjectsFem.makeConstraintPressure,
            }.get(kind)
            if factory is None:
                raise RuntimeError(f"Unsupported native FEM constraint kind {kind!r}.")
            obj = factory(doc, name)
        elif output_type == "load_case":
            obj = doc.addObject("App::DocumentObjectGroup", name)
        elif output_type == "mesh":
            obj = ObjectsFem.makeMeshGmsh(doc, name)
        elif output_type == "result":
            obj = ObjectsFem.makeResultMechanical(doc, name)
        else:
            raise RuntimeError(f"No native FEM factory exists for {output_type!r}.")
    elif prepared["pack"].domain == "inspection":
        import Inspection

        del Inspection
        obj = doc.addObject(native_type, name)
    elif prepared["pack"].domain == "robot":
        import Robot

        del Robot
        obj = doc.addObject(native_type, name)
    elif output_type == "component_link" and assembly is not None:
        if str(definition.get("operation") or "") == "fastener":
            target = dict(assembly_fastener_sources or {}).get(output_name)
            if target is None:
                raise RuntimeError(
                    f"Assembly fastener source for output {output_name!r} "
                    "was not prepared."
                )
        else:
            source = _definition_argument(definition, 0, "source")
            target = _reference_target(
                doc,
                source,
                f"output {output_name} source",
            )
        native_type = (
            "Assembly::AssemblyLink"
            if bool(
                getattr(target, "isDerivedFrom", lambda _type: False)(
                    "Assembly::AssemblyObject"
                )
            )
            else "App::Link"
        )
        obj = assembly.newObject(native_type, name)
    elif output_type == "joint" and assembly is not None:
        joint_group = _assembly_joint_group(assembly)
        if joint_group is None:
            joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        obj = joint_group.newObject(native_type, name)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "motion"
        and assembly is not None
    ):
        obj = assembly.newObject(native_type, name)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "mechanism_verification"
        and assembly is not None
    ):
        verification_group = _ensure_assembly_verification_group(assembly)
        obj = verification_group.newObject(native_type, name)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "simulation"
        and assembly is not None
    ):
        simulation_group = _assembly_simulation_group(assembly)
        if simulation_group is None:
            simulation_group = assembly.newObject(
                "Assembly::SimulationGroup", "Simulations"
            )
        obj = simulation_group.newObject(native_type, name)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "exploded_view"
        and assembly is not None
    ):
        view_group = _assembly_view_group(assembly)
        if view_group is None:
            view_group = assembly.newObject("Assembly::ViewGroup", "Exploded Views")
        obj = view_group.newObject(native_type, name)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "bom"
        and assembly is not None
    ):
        bom_group = _assembly_bom_group(assembly)
        if bom_group is None:
            bom_group = assembly.newObject("Assembly::BomGroup", "Bills of Materials")
        obj = bom_group.newObject(native_type, name)
    else:
        obj = doc.addObject(native_type, name)
    if obj is None:
        raise RuntimeError(
            f"FreeCAD did not create native type {native_type!r} for output {output_name!r}."
        )
    if output_type == "assembly":
        obj.Type = "Assembly"
        if _assembly_joint_group(obj) is None:
            obj.newObject("Assembly::JointGroup", "Joints")
    return obj


def _definition(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = item.get("definition")
    if not isinstance(raw, dict):
        raise RuntimeError(f"Output {item.get('name')!r} has no validated definition.")
    return raw


def _definition_properties(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = _definition(item).get("properties")
    return dict(raw) if isinstance(raw, dict) else {}


def _label(item: Mapping[str, Any], fallback: str) -> str:
    value = str(_definition_properties(item).get("label") or "").strip()
    return value or fallback


def _reference_target(doc: Any, value: Any, label: str) -> Any:
    try:
        return resolve_reference_target(doc, value, label)
    except DocumentReferenceError as exc:
        raise RuntimeError(str(exc)) from exc


def _component_native_type(doc: Any, item: Mapping[str, Any]) -> str:
    definition = _definition(item)
    if str(definition.get("operation") or "") == "fastener":
        return "App::Link"
    data = item.get("assembly_data")
    data = dict(data) if isinstance(data, dict) else {}
    source = data.get("source")
    if source is None:
        source = _definition_argument(definition, 0, "source")
    target = _reference_target(doc, source, f"output {item.get('name')} source")
    return (
        "Assembly::AssemblyLink"
        if bool(
            getattr(target, "isDerivedFrom", lambda _type: False)(
                "Assembly::AssemblyObject"
            )
        )
        else "App::Link"
    )


def _assembly_fastener_source_output(output_name: str) -> str:
    return f"{output_name}.{_ASSEMBLY_FASTENER_SOURCE_SUFFIX}"


def _assembly_fastener_identity(
    definition: Mapping[str, Any],
    *,
    output_name: str,
) -> dict[str, Any]:
    arguments = list(definition.get("arguments") or [])
    properties = definition.get("properties")
    if (
        str(definition.get("operation") or "") != "fastener"
        or len(arguments) != 2
        or not isinstance(properties, Mapping)
        or "model_thread" not in properties
    ):
        raise RuntimeError(
            f"Assembly component output {output_name!r} has a malformed "
            "api.fastener definition."
        )
    try:
        from VibeCADFasteners import resolve_fastener

        return resolve_fastener(
            standard=arguments[0],
            nominal_thread=arguments[1],
            length_mm=properties.get("length_mm"),
            model_thread=properties["model_thread"],
            left_handed=properties.get("left_handed"),
            options=properties.get("options"),
        )
    except Exception as exc:
        raise RuntimeError(
            f"Assembly component output {output_name!r} no longer resolves in "
            f"the bundled fastener catalog: {exc}"
        ) from exc


def _prepare_assembly_fastener_sources(
    doc: Any,
    prepared: Mapping[str, Any],
    outputs: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[Any], list[Any]]:
    """Create or reuse hidden parametric definitions for catalog occurrences."""

    from VibeCADFasteners import (
        PROP_CANONICAL_KEY,
        create_fastener_feature,
        install_fastener_view_provider,
    )

    owned = _program_objects(
        doc,
        str(prepared["program_id"]),
        prepared["pack"].domain,
    )
    sources: dict[str, Any] = {}
    created: list[Any] = []
    replaced: list[Any] = []
    for item in outputs:
        if str(item.get("type") or "") != "component_link":
            continue
        definition = _definition(item)
        if str(definition.get("operation") or "") != "fastener":
            continue
        output_name = str(item["name"])
        source_output = _assembly_fastener_source_output(output_name)
        matches = [
            obj
            for obj in owned
            if str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
            == source_output
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple managed fastener definitions claim output "
                f"{source_output!r}."
            )
        identity = _assembly_fastener_identity(
            definition,
            output_name=output_name,
        )
        source = matches[0] if matches else None
        replaced_source = None
        if source is not None and (
            str(getattr(source, "TypeId", "") or "") != "Part::FeaturePython"
            or str(getattr(source, PROP_CANONICAL_KEY, "") or "")
            != str(identity["canonical_key"])
        ):
            external = _external_uses(doc, [source], owned)
            if external:
                raise _reference_error(
                    f"Cannot replace the internal catalog definition for Assembly "
                    f"component {output_name!r}; foreign objects reference it",
                    external,
                )
            replaced_source = source
            source = None
        if source is None:
            object_name = _internal_name(
                prepared,
                (
                    f"{output_name}_Fastener_"
                    f"{str(identity['canonical_key']).rsplit(':', 1)[-1][:8]}"
                ),
            )
            source, observed = create_fastener_feature(
                doc,
                standard=identity["standard"],
                nominal_thread=identity["nominal_size"],
                length_mm=identity["length_mm"],
                model_thread=bool(identity["model_thread"]),
                left_handed=bool(identity["left_handed"]),
                options=dict(identity["options"]),
                object_name=object_name,
                label=str(identity["part_number"]),
            )
            if observed["canonical_key"] != identity["canonical_key"]:
                raise RuntimeError(
                    f"Assembly component output {output_name!r} generated a "
                    "different catalog identity during publication."
                )
            created.append(source)
            owned.append(source)
        if replaced_source is not None:
            # Retarget managed occurrences while the old definition remains live.
            # The occurrence resource reconciler releases and deletes that exact
            # old definition only after all retained consumers have been mapped.
            for candidate in owned:
                if (
                    candidate is not replaced_source
                    and getattr(candidate, "LinkedObject", None)
                    is replaced_source
                ):
                    candidate.LinkedObject = source
                    if (
                        "VibeCADTimelineEditor" in _properties(candidate)
                        and getattr(
                            candidate,
                            "VibeCADTimelineEditor",
                            None,
                        )
                        is replaced_source
                    ):
                        candidate.VibeCADTimelineEditor = source
            owned = [
                candidate for candidate in owned if candidate is not replaced_source
            ]
            replaced.append(replaced_source)
        _set_metadata(
            source,
            prepared,
            source_output,
            _ASSEMBLY_FASTENER_SOURCE_OUTPUT_TYPE,
            definition,
        )
        source.Label = str(identity["part_number"])
        view = getattr(source, "ViewObject", None)
        if view is not None:
            view.Visibility = False
            if hasattr(view, "ShowInTree"):
                view.ShowInTree = False
        install_fastener_view_provider(source)
        sources[output_name] = source
    return sources, created, replaced


def _placement(value: Any) -> Any:
    import FreeCAD as App

    if value is None:
        return App.Placement()
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return App.Placement(
            App.Vector(*(float(item) for item in value)), App.Rotation()
        )
    if not isinstance(value, dict):
        raise RuntimeError("placement must be [x,y,z] or an object.")
    position = value.get("position", [0.0, 0.0, 0.0])
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise RuntimeError("placement.position must be [x,y,z].")
    rotation = value.get("rotation", [0.0, 0.0, 0.0, 1.0])
    if not isinstance(rotation, (list, tuple)) or len(rotation) != 4:
        raise RuntimeError("placement.rotation must be quaternion [x,y,z,w].")
    return App.Placement(
        App.Vector(*(float(item) for item in position)),
        App.Rotation(
            float(rotation[0]),
            float(rotation[1]),
            float(rotation[2]),
            float(rotation[3]),
        ),
    )


def _placement_from_matrix(values: Any) -> Any:
    import FreeCAD as App

    if not isinstance(values, list) or len(values) != 16:
        raise RuntimeError("A solved placement matrix must contain 16 numbers.")
    matrix = App.Matrix()
    for name, value in zip(
        (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        ),
        values,
    ):
        setattr(matrix, name, float(value))
    return App.Placement(matrix)


def _assembly_joint_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::JointGroup":
            return child
    for child in list(getattr(assembly, "OutList", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::JointGroup":
            return child
    return None


def _assembly_simulation_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::SimulationGroup":
            return child
    for child in list(getattr(assembly, "OutList", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::SimulationGroup":
            return child
    return None


def _assembly_view_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::ViewGroup":
            return child
    for child in list(getattr(assembly, "OutList", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::ViewGroup":
            return child
    return None


def _assembly_bom_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::BomGroup":
            return child
    for child in list(getattr(assembly, "OutList", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::BomGroup":
            return child
    return None


def _assembly_verification_group(assembly: Any) -> Any | None:
    for child in list(getattr(assembly, "Group", []) or []):
        if (
            str(getattr(child, "TypeId", "")) == "App::DocumentObjectGroup"
            and str(getattr(child, PROP_ASSEMBLY_GROUP_ROLE, "") or "")
            == "verification"
        ):
            return child
    for child in list(getattr(assembly, "OutList", []) or []):
        if (
            str(getattr(child, "TypeId", "")) == "App::DocumentObjectGroup"
            and str(getattr(child, PROP_ASSEMBLY_GROUP_ROLE, "") or "")
            == "verification"
        ):
            return child
    return None


def _ensure_assembly_verification_group(assembly: Any) -> Any:
    group = _assembly_verification_group(assembly)
    if group is None:
        group = assembly.newObject(
            "App::DocumentObjectGroup",
            "VibeCADVerification",
        )
        if group is None:
            raise RuntimeError(
                "FreeCAD did not create the Assembly Verification group."
            )
        _add_string_property(
            group,
            PROP_ASSEMBLY_GROUP_ROLE,
            "Stable engineering-type role for this Assembly child group.",
        )
        setattr(group, PROP_ASSEMBLY_GROUP_ROLE, "verification")
        _hide_property(group, PROP_ASSEMBLY_GROUP_ROLE)
    group.Label = "Verification"
    return group


def _assembly_component_reference(
    prepared: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any] | None:
    data = item.get("assembly_data")
    data = dict(data) if isinstance(data, Mapping) else {}
    source = data.get("source")
    if not isinstance(source, Mapping):
        arguments = list(_definition(item).get("arguments") or [])
        source = arguments[0] if arguments else None
    if not isinstance(source, Mapping):
        return None
    key = (
        str(source.get("document_uid") or ""),
        str(source.get("object_name") or ""),
    )
    return next(
        (
            dict(reference)
            for reference in list(prepared.get("resolved_references") or [])
            if (
                str(reference.get("document_uid") or ""),
                str(reference.get("object_name") or ""),
            )
            == key
        ),
        None,
    )


def _live_assembly_reference(
    component: Any,
    descriptor: Mapping[str, Any],
    stable_path: str,
    subelements: list[str],
    *,
    context: str,
) -> dict[str, Any]:
    """Resolve a stable source path without consuming generated child names."""

    nodes = descriptor.get("nodes")
    node_by_id = {
        str(node.get("node_id") or ""): node
        for node in list(nodes or [])
        if isinstance(node, Mapping)
    }
    root_node_id = str(descriptor.get("root_node_id") or "")
    current_node = node_by_id.get(root_node_id)
    if current_node is None or not stable_path:
        raise RuntimeError(f"{context} has no authenticated stable occurrence path.")
    container = component
    target = component
    prefix_names: list[str] = []
    locked = False
    leaf = None
    leaf_live = False
    for index, segment in enumerate(stable_path.split("/")):
        occurrence = next(
            (
                item
                for item in list(current_node.get("occurrences") or [])
                if isinstance(item, Mapping)
                and str(item.get("name") or "") == segment
            ),
            None,
        )
        if occurrence is None:
            raise RuntimeError(
                f"{context} occurrence_path {stable_path!r} changed after validation."
            )
        source_node = node_by_id.get(str(occurrence.get("source_node_id") or ""))
        if source_node is None:
            raise RuntimeError(f"{context} occurrence path reaches a missing source node.")
        source_identity = current_node.get("identity")
        if not isinstance(source_identity, Mapping):
            raise RuntimeError(f"{context} source node has no stable identity.")
        source_document = getattr(component, "Document", None)
        source_occurrence = (
            source_document.getObject(str(occurrence.get("name") or ""))
            if source_document is not None
            else None
        )
        source_container = (
            source_document.getObject(str(source_identity.get("object_name") or ""))
            if source_document is not None
            else None
        )
        if source_occurrence is None or source_container is None or source_occurrence not in list(
            getattr(source_container, "Group", []) or []
        ):
            raise RuntimeError(
                f"{context} source occurrence {segment!r} disappeared before publication."
            )

        container_type = str(getattr(container, "TypeId", "") or "")
        if container_type == "Assembly::AssemblyLink":
            matches = [
                child
                for child in list(getattr(container, "Group", []) or [])
                if getattr(child, "LinkedObject", None) is source_occurrence
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"{context} could not map stable occurrence_path {stable_path!r} "
                    f"at segment {index}; native synchronization produced "
                    f"{len(matches)} matches. Recompute the source Assembly and retry."
                )
            actual = matches[0]
            actual_live = True
        else:
            # An App::Link to an App::Part addresses deeper source objects by
            # subname; those objects are not independent live occurrences.
            actual = source_occurrence
            actual_live = False

        if locked:
            prefix_names.append(str(actual.Name))
        elif container_type == "Assembly::AssemblyLink":
            if bool(getattr(container, "Rigid", True)):
                locked = True
                prefix_names.append(str(actual.Name))
            else:
                target = actual
                prefix_names = []
        else:
            locked = True
            prefix_names.append(str(actual.Name))
        leaf = actual
        leaf_live = actual_live
        container = actual
        current_node = source_node
    if leaf is None:
        raise RuntimeError(f"{context} did not resolve an occurrence.")
    prefix = ".".join(prefix_names)
    native_subelements = [
        (
            f"{prefix}.{value}"
            if prefix and value
            else f"{prefix}."
            if prefix
            else value
        )
        for value in subelements
    ]
    return {
        "target": target,
        "subelements": native_subelements,
        "leaf": leaf,
        "leaf_live": leaf_live,
    }


def _configure_component(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
    assembly_fastener_sources: Mapping[str, Any] | None = None,
) -> list[Any]:
    properties = _definition_properties(item)
    definition = _definition(item)
    assembly_data = item.get("assembly_data")
    assembly_data = dict(assembly_data) if isinstance(assembly_data, dict) else {}
    if str(definition.get("operation") or "") == "fastener":
        if assembly_fastener_sources is not None:
            target = assembly_fastener_sources.get(str(item["name"]))
        else:
            target = next(
                (
                    source
                    for source in _program_objects(
                        doc,
                        str(prepared["program_id"]),
                        prepared["pack"].domain,
                    )
                    if str(
                        getattr(source, contracts.PROP_PROGRAM_OUTPUT, "") or ""
                    )
                    == _assembly_fastener_source_output(str(item["name"]))
                ),
                None,
            )
        if target is None:
            raise RuntimeError(
                f"Assembly fastener source for output {item['name']!r} disappeared "
                "before publication."
            )
        flexible = False
    else:
        source = assembly_data.get("source", properties.get("source"))
        if source is None:
            arguments = list(definition.get("arguments") or [])
            source = arguments[0] if arguments else None
        target = _reference_target(
            doc,
            source,
            f"output {item['name']} source",
        )
        flexible = bool(
            assembly_data.get(
                "flexible",
                properties.get("flexible", False),
            )
        )
    is_assembly_link = str(getattr(obj, "TypeId", "") or "") == (
        "Assembly::AssemblyLink"
    )
    if flexible and not is_assembly_link:
        raise RuntimeError(
            f"Flexible component output {item['name']!r} requires a native "
            "Assembly::AssemblyLink."
        )
    was_linked = getattr(obj, "LinkedObject", None) is not None
    mode_changed = is_assembly_link and bool(getattr(obj, "Rigid", True)) is flexible
    if was_linked and mode_changed:
        managed = _program_objects(
            doc,
            str(prepared["program_id"]),
            prepared["pack"].domain,
        )
        external = _external_uses(doc, [obj], managed)
        if external:
            raise _reference_error(
                f"Cannot change flexible mode for component output {item['name']!r}; "
                "external objects reference its current rigid/flexible identity. "
                "Keep the mode or return the changed component under a new output name",
                external,
            )
    authored_placement = properties.get("placement") or properties.get("position")
    authored_placement_state = {
        "placement": authored_placement,
        "placement_authored": bool(properties.get("placement_authored")),
    }
    encoded_authored_placement = json.dumps(
        authored_placement_state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    preserve_live_placement = (
        prepared["pack"].domain in {"partdesign", "robot"}
        and PROP_COMPONENT_AUTHORED_PLACEMENT in _properties(obj)
        and str(getattr(obj, PROP_COMPONENT_AUTHORED_PLACEMENT, "") or "")
        == encoded_authored_placement
        and getattr(obj, "LinkedObject", None) is not None
    )
    initial_placement = _placement(authored_placement)
    obj.LinkedObject = target
    if is_assembly_link:
        if not was_linked or mode_changed:
            obj.Placement = initial_placement
            obj.Rigid = not flexible
    if item.get("solved_placement_matrix") is not None:
        obj.Placement = _placement_from_matrix(item["solved_placement_matrix"])
    elif not preserve_live_placement:
        obj.Placement = initial_placement
    _add_string_property(
        obj,
        PROP_COMPONENT_AUTHORED_PLACEMENT,
        "Last source-authored component placement; live solved placement may differ.",
    )
    setattr(obj, PROP_COMPONENT_AUTHORED_PLACEMENT, encoded_authored_placement)
    _hide_property(obj, PROP_COMPONENT_AUTHORED_PLACEMENT)
    reference = _assembly_component_reference(prepared, item)
    descriptor = (
        reference.get("assembly_hierarchy")
        if isinstance(reference, Mapping)
        else None
    )
    solved_occurrences = assembly_data.get("solved_occurrences")
    if solved_occurrences is not None:
        states = list(solved_occurrences)
        if not isinstance(descriptor, Mapping) or any(
            not isinstance(state, Mapping) for state in states
        ):
            raise RuntimeError(
                f"Component output {item['name']!r} has malformed occurrence evidence."
            )
        if not is_assembly_link and any(
            bool(state.get("live_occurrence")) for state in states
        ):
            raise RuntimeError(
                f"Component output {item['name']!r} claims live occurrence placements "
                "without a native AssemblyLink."
            )
        for state in states:
            if not bool(state.get("live_occurrence")):
                continue
            path = str(state.get("occurrence_path") or "")
            resolved = _live_assembly_reference(
                obj,
                descriptor,
                path,
                ["", ""],
                context=f"component output {item['name']!r}",
            )
            local = state.get("local_placement")
            if not bool(resolved["leaf_live"]) or not isinstance(local, Mapping):
                raise RuntimeError(
                    f"Component output {item['name']!r} occurrence {path!r} lost "
                    "its live placement before publication."
                )
            resolved["leaf"].Placement = _placement_from_matrix(
                list(local.get("matrix") or [])
            )
    return []


def _configure_adopted_assembly_component(
    obj: Any,
    item: Mapping[str, Any],
) -> None:
    """Apply Assembly state without turning a borrowed link into a self-link."""

    properties = _definition_properties(item)
    if bool(properties.get("flexible")):
        raise RuntimeError(
            f"Borrowed Model occurrence {item['name']!r} cannot be flexible. "
            "Flexible mode is only valid for a native subassembly definition."
        )
    solved = item.get("solved_placement_matrix")
    if solved is not None:
        obj.Placement = _placement_from_matrix(solved)
    elif bool(properties.get("placement_authored")):
        obj.Placement = _placement(
            properties.get("placement") or properties.get("position")
        )


def _configure_new_assembly_presentation(
    assembly: Any,
    items: list[Mapping[str, Any]],
    outputs: Mapping[str, Any],
) -> None:
    """Give a new Assembly one visible occurrence set without overriding edits."""

    _set_view_visibility(assembly, True)
    for item in items:
        if str(item.get("type") or "") != "component_link":
            continue
        occurrence = outputs.get(str(item.get("name") or ""))
        if occurrence is not None:
            _set_view_visibility(occurrence, True)


def _configure_component_grounding(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> tuple[Any | None, list[str]]:
    """Create, update, or retire one explicit grounding operation.

    Grounding changes mechanism state and is therefore a public History
    operation.  It is not implementation state owned by the component
    occurrence.
    """

    properties = _definition_properties(item)
    assembly_data = item.get("assembly_data")
    assembly_data = dict(assembly_data) if isinstance(assembly_data, dict) else {}
    grounded = bool(assembly_data.get("grounded", properties.get("grounded")))
    assembly = next(
        (
            output
            for output in outputs.values()
            if str(getattr(output, "TypeId", "")) == "Assembly::AssemblyObject"
        ),
        None,
    )
    ground_output = f"{item['name']}.ground"
    joint_group = _assembly_joint_group(assembly) if assembly is not None else None
    existing = next(
        (
            child
            for child in list(getattr(joint_group, "Group", []) or [])
            if str(getattr(child, contracts.PROP_PROGRAM_OUTPUT, "") or "")
            == ground_output
        ),
        None,
    )
    if grounded:
        if assembly is None:
            raise RuntimeError("A grounded component requires an assembly output.")
        assert joint_group is not None
        if existing is not None and _timeline_role(
            existing,
            context=f"Assembly grounding {ground_output!r}",
        ) == "resource":
            raise RuntimeError(
                f"Assembly grounding {ground_output!r} remains component-owned "
                "implementation state. Reconcile the occurrence before publishing "
                "the independent grounding operation."
            )
        if existing is None:
            import JointObject

            existing = joint_group.newObject(
                "App::FeaturePython", _SAFE_NAME.sub("_", f"Ground_{item['name']}")
            )
            JointObject.GroundedJoint(existing, obj)
            JointObject.ensureViewProviderGroundedJoint(existing)
        _set_metadata(
            existing,
            prepared,
            ground_output,
            "joint",
            {"operation": "ground", "component_output": item["name"]},
        )
    elif existing is not None:
        if _timeline_role(
            existing,
            context=f"Assembly grounding {ground_output!r}",
        ) == "resource":
            raise RuntimeError(
                f"Assembly grounding {ground_output!r} remains component-owned "
                "implementation state. Reconcile the occurrence before retiring "
                "the grounding operation."
            )
        external = _external_uses(
            doc,
            [existing],
            _program_objects(
                doc,
                str(prepared["program_id"]),
                prepared["pack"].domain,
            ),
        )
        if external:
            raise _reference_error(
                f"Cannot unground component output {item['name']!r}; external objects "
                "reference its managed grounding joint",
                external,
            )
        retired = _remove_timeline_deletion(
            doc,
            _prepare_timeline_deletion(doc, [existing]),
        )
        return None, retired
    return (existing if grounded else None), []


def _configure_joint_while_suspended(
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> None:
    properties = _definition_properties(item)
    assembly_data = item.get("assembly_data")
    assembly_data = dict(assembly_data) if isinstance(assembly_data, dict) else {}
    kind = str(
        assembly_data.get("kind") or properties.get("type") or "revolute"
    ).lower()
    native_names = {
        "fixed": "Fixed",
        "revolute": "Revolute",
        "cylindrical": "Cylindrical",
        "slider": "Slider",
        "ball": "Ball",
        "distance": "Distance",
        "parallel": "Parallel",
        "perpendicular": "Perpendicular",
        "angle": "Angle",
        "rack_pinion": "RackPinion",
        "screw": "Screw",
        "gears": "Gears",
        "belt": "Belt",
    }
    try:
        import JointObject
    except Exception as exc:
        raise RuntimeError(
            f"Native Assembly JointObject is unavailable: {exc}"
        ) from exc
    native = native_names.get(kind)
    if native is None or native not in list(JointObject.JointTypes):
        raise RuntimeError(f"Unsupported native assembly joint type {kind!r}.")
    if not hasattr(obj, "Proxy") or obj.Proxy is None:
        JointObject.Joint(obj, JointObject.JointTypes.index(native))
    elif str(getattr(obj, "JointType", "") or "") != native:
        obj.Proxy.setJointType(obj, native)
    JointObject.ensureViewProviderJoint(obj)
    if assembly_data:
        obj.Detach1 = True
        obj.Detach2 = True
        obj.Reference1 = None
        obj.Reference2 = None
        parameters = dict(assembly_data.get("parameters") or {})
        if kind == "distance":
            obj.Distance = float(parameters["distance_mm"])
        elif kind == "angle":
            obj.Angle = float(parameters["angle_degrees"])
        elif kind == "rack_pinion":
            obj.Distance = float(parameters["pitch_radius_mm"])
        elif kind == "screw":
            obj.Distance = float(parameters["thread_pitch_mm"])
        elif kind in {"gears", "belt"}:
            obj.Distance = float(parameters["radius1_mm"])
            obj.Distance2 = float(parameters["radius2_mm"])
        length_limits = assembly_data.get("length_limits_mm")
        length_min = length_limits[0] if length_limits is not None else None
        length_max = length_limits[1] if length_limits is not None else None
        obj.EnableLengthMin = length_min is not None
        obj.EnableLengthMax = length_max is not None
        if length_limits is not None:
            if length_min is not None:
                obj.LengthMin = float(length_min)
            if length_max is not None:
                obj.LengthMax = float(length_max)
        angle_limits = assembly_data.get("angle_limits_degrees")
        angle_min = angle_limits[0] if angle_limits is not None else None
        angle_max = angle_limits[1] if angle_limits is not None else None
        obj.EnableAngleMin = angle_min is not None
        obj.EnableAngleMax = angle_max is not None
        if angle_limits is not None:
            if angle_min is not None:
                obj.AngleMin = float(angle_min)
            if angle_max is not None:
                obj.AngleMax = float(angle_max)
        connectors = list(assembly_data.get("connectors") or [])
        if len(connectors) != 2:
            raise RuntimeError("A validated Assembly joint must have two connectors.")
        for index, connector in enumerate(connectors, start=1):
            component_name = str(connector.get("component_output") or "")
            component = outputs.get(component_name)
            if component is None:
                raise RuntimeError(
                    f"Assembly joint refers to unknown component output {component_name!r}."
                )
            element = str(connector.get("element") or "")
            anchor = str(connector.get("anchor") or element)
            native_target = component
            native_subelements = [element, anchor]
            occurrence_path = str(connector.get("occurrence_path") or "")
            if occurrence_path:
                source = getattr(component, "LinkedObject", None)
                key = (
                    str(getattr(getattr(source, "Document", None), "Uid", "") or ""),
                    str(getattr(source, "Name", "") or ""),
                )
                reference = next(
                    (
                        value
                        for value in list(prepared.get("resolved_references") or [])
                        if (
                            str(value.get("document_uid") or ""),
                            str(value.get("object_name") or ""),
                        )
                        == key
                    ),
                    None,
                )
                descriptor = (
                    reference.get("assembly_hierarchy")
                    if isinstance(reference, Mapping)
                    else None
                )
                if not isinstance(descriptor, Mapping):
                    raise RuntimeError(
                        f"Assembly joint occurrence_path {occurrence_path!r} has no "
                        "authenticated live hierarchy."
                    )
                resolved = _live_assembly_reference(
                    component,
                    descriptor,
                    occurrence_path,
                    [element, anchor],
                    context=(
                        f"joint output {item['name']!r} connector {index}"
                    ),
                )
                native_target = resolved["target"]
                native_subelements = list(resolved["subelements"])
            setattr(
                obj,
                f"Offset{index}",
                _placement_from_matrix(
                    list((connector.get("offset") or {}).get("matrix") or [])
                ),
            )
            setattr(
                obj,
                f"Reference{index}",
                [native_target, native_subelements],
            )
            setattr(
                obj,
                f"Placement{index}",
                _placement_from_matrix(
                    list((connector.get("local_frame") or {}).get("matrix") or [])
                ),
            )
        if hasattr(obj, "Suppressed"):
            obj.Suppressed = bool(assembly_data.get("suppressed"))
        _add_string_property(
            obj,
            "VibeCADAssemblyJointValidation",
            "Precomputed native connector frames, compatibility, and parameter readback.",
        )
        obj.VibeCADAssemblyJointValidation = json.dumps(
            assembly_data,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return
    references = []
    for key in ("reference1", "reference2"):
        reference = properties.get(key)
        if not isinstance(reference, dict):
            raise RuntimeError(f"Assembly joint {key} must be an object.")
        component_name = str(reference.get("component_output") or "")
        component = outputs.get(component_name)
        if component is None:
            raise RuntimeError(
                f"Assembly joint refers to unknown component output {component_name!r}."
            )
        element = str(reference.get("element") or "")
        references.append([component, [element, element]])
    obj.Proxy.setJointConnectors(obj, references)


def _configure_joint(
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> None:
    """Apply precomputed joint state with native auto-solve temporarily disabled."""

    from VibeCADAssemblySolverPolicy import suspend_joint_autosolve

    with suspend_joint_autosolve():
        _configure_joint_while_suspended(obj, item, outputs, prepared)


def _configure_assembly_motion(
    obj: Any, item: Mapping[str, Any], outputs: Mapping[str, Any]
) -> None:
    """Apply one authenticated native motion contract without running kinematics."""

    data = item.get("assembly_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("An Assembly motion has no validated native data.")
    data = dict(data)
    joint_name = str(data.get("joint_output") or "")
    joint = outputs.get(joint_name)
    if joint is None or str(getattr(joint, "TypeId", "")) != "App::FeaturePython":
        raise RuntimeError(
            f"Assembly motion {item['name']!r} joint {joint_name!r} is unavailable."
        )
    if not isinstance(getattr(obj, "Proxy", None), AssemblyMotionProxy):
        AssemblyMotionProxy(obj)
    else:
        _ensure_assembly_motion_properties(obj)
    obj.MotionType = ["Angular", "Linear"]
    obj.MotionType = str(data["native_motion_type"])
    obj.Joint = joint
    obj.Formula = str(data["formula"])
    reference = getattr(obj, "Joint", None)
    if (
        not isinstance(reference, (list, tuple))
        or not reference
        or reference[0] is not joint
        or str(obj.MotionType) != str(data["native_motion_type"])
        or str(obj.Formula) != str(data["formula"])
    ):
        raise RuntimeError(
            f"Live Assembly motion {item['name']!r} changed its validated contract."
        )
    _add_string_property(
        obj,
        "VibeCADAssemblyMotionValidation",
        "Authenticated native Assembly motion definition and driven joint identity.",
    )
    obj.VibeCADAssemblyMotionValidation = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _configure_assembly_mechanism_verification(
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> None:
    """Persist one authenticated static mechanism report as native document state."""

    data = item.get("assembly_data")
    if not isinstance(data, Mapping) or set(data) != {
        "assembly_output",
        "static_check",
        "report",
    }:
        raise RuntimeError(
            "A mechanism verification output has no authenticated report."
        )
    static_check = data.get("static_check")
    report = data.get("report")
    if not isinstance(static_check, Mapping) or not isinstance(report, Mapping):
        raise RuntimeError(
            "A mechanism verification output has malformed persisted evidence."
        )
    assembly_name = str(data.get("assembly_output") or "")
    assembly = outputs.get(assembly_name)
    if assembly is None or str(getattr(assembly, "TypeId", "")) != (
        "Assembly::AssemblyObject"
    ):
        raise RuntimeError(
            f"Mechanism verification {item['name']!r} assembly "
            f"{assembly_name!r} is unavailable."
        )
    verdict = str(report.get("verdict") or "")
    if verdict not in {"pass", "fail", "indeterminate"}:
        raise RuntimeError(
            f"Mechanism verification {item['name']!r} has invalid verdict "
            f"{verdict!r}."
        )
    summary = report.get("summary")
    scope = report.get("scope")
    engine = dict(
        dict(report.get("geometry_evidence") or {}).get(
            "geometry_engine"
        )
        or {}
    )
    if not isinstance(summary, Mapping) or not isinstance(scope, Mapping):
        raise RuntimeError(
            f"Mechanism verification {item['name']!r} has no report summary."
        )

    _add_property(
        obj,
        "App::PropertyEnumeration",
        "VibeCADMechanismVerdict",
        "Top-level static mechanism-verification verdict.",
    )
    for name, description in (
        (
            PROP_MECHANISM_ASSEMBLY_OUTPUT,
            "Stable VibeScript output identity of the containing Assembly.",
        ),
        (
            "VibeCADMechanismReportSchema",
            "Versioned persisted mechanism-verification report schema.",
        ),
        (
            "VibeCADMechanismScenarioSHA256",
            "SHA-256 of the exact normalized mechanism scenario.",
        ),
        (
            "VibeCADMechanismSolveReportSHA256",
            "SHA-256 of the exact authenticated native solve report.",
        ),
        (
            "VibeCADMechanismStaticCheckSHA256",
            "SHA-256 of the exact declared static requirements.",
        ),
        (
            "VibeCADMechanismAnalysisScope",
            "Certified analysis scope; static does not imply motion certification.",
        ),
        (
            "VibeCADMechanismGeometryEngine",
            "Exact BREP geometry engine and version used for evidence.",
        ),
        (
            PROP_MECHANISM_STATIC_CHECK,
            "Complete normalized static requirement and contact-policy contract.",
        ),
        (
            PROP_MECHANISM_VERIFICATION_REPORT,
            "Complete portable v1 mechanism-verification report and exact evidence.",
        ),
    ):
        _add_string_property(obj, name, description)
    for name, description in (
        ("VibeCADMechanismDeclarationCount", "Declared static pair count."),
        ("VibeCADMechanismPassCount", "Proven static declaration count."),
        ("VibeCADMechanismFailCount", "Failed static declaration count."),
        (
            "VibeCADMechanismIndeterminateCount",
            "Static declarations that could not be proven or disproven.",
        ),
        ("VibeCADMechanismIgnoredCount", "Explicitly excluded pair count."),
    ):
        _add_property(obj, "App::PropertyInteger", name, description)

    static_json = json.dumps(
        static_check,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    report_json = json.dumps(
        report,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    setattr(obj, PROP_MECHANISM_ASSEMBLY_OUTPUT, assembly_name)
    obj.VibeCADMechanismVerdict = ["pass", "fail", "indeterminate"]
    obj.VibeCADMechanismVerdict = verdict
    obj.VibeCADMechanismReportSchema = str(report.get("schema") or "")
    obj.VibeCADMechanismScenarioSHA256 = str(
        report.get("scenario_sha256") or ""
    )
    obj.VibeCADMechanismSolveReportSHA256 = str(
        report.get("solve_report_sha256") or ""
    )
    obj.VibeCADMechanismStaticCheckSHA256 = str(
        report.get("static_check_sha256") or ""
    )
    obj.VibeCADMechanismAnalysisScope = str(scope.get("analysis") or "")
    obj.VibeCADMechanismGeometryEngine = (
        f"{str(engine.get('name') or '')} {str(engine.get('version') or '')}"
    ).strip()
    setattr(obj, PROP_MECHANISM_STATIC_CHECK, static_json)
    setattr(obj, PROP_MECHANISM_VERIFICATION_REPORT, report_json)
    obj.VibeCADMechanismDeclarationCount = int(
        summary.get("declaration_count", 0)
    )
    obj.VibeCADMechanismPassCount = int(summary.get("pass_count", 0))
    obj.VibeCADMechanismFailCount = int(summary.get("fail_count", 0))
    obj.VibeCADMechanismIndeterminateCount = int(
        summary.get("indeterminate_count", 0)
    )
    obj.VibeCADMechanismIgnoredCount = int(summary.get("ignored_count", 0))

    set_editor_mode = getattr(obj, "setEditorMode", None)
    if callable(set_editor_mode):
        for property_name in (
            PROP_MECHANISM_ASSEMBLY_OUTPUT,
            "VibeCADMechanismVerdict",
            "VibeCADMechanismReportSchema",
            "VibeCADMechanismScenarioSHA256",
            "VibeCADMechanismSolveReportSHA256",
            "VibeCADMechanismStaticCheckSHA256",
            "VibeCADMechanismAnalysisScope",
            "VibeCADMechanismGeometryEngine",
            PROP_MECHANISM_STATIC_CHECK,
            PROP_MECHANISM_VERIFICATION_REPORT,
            "VibeCADMechanismDeclarationCount",
            "VibeCADMechanismPassCount",
            "VibeCADMechanismFailCount",
            "VibeCADMechanismIndeterminateCount",
            "VibeCADMechanismIgnoredCount",
        ):
            set_editor_mode(property_name, 1)

    if (
        str(getattr(obj, PROP_MECHANISM_ASSEMBLY_OUTPUT, "") or "")
        != assembly_name
        or str(obj.VibeCADMechanismVerdict) != verdict
        or json.loads(
            str(getattr(obj, PROP_MECHANISM_STATIC_CHECK, "") or "")
        )
        != static_check
        or json.loads(
            str(
                getattr(
                    obj,
                    PROP_MECHANISM_VERIFICATION_REPORT,
                    "",
                )
                or ""
            )
        )
        != report
    ):
        raise RuntimeError(
            f"Live mechanism verification {item['name']!r} changed during "
            "publication."
        )


def _configure_assembly_simulation(
    obj: Any, item: Mapping[str, Any], outputs: Mapping[str, Any]
) -> None:
    """Publish worker-generated kinematic settings and a bounded trace preview."""

    data = item.get("assembly_data")
    preview = item.get("simulation_trace_preview")
    if not isinstance(data, Mapping) or not isinstance(preview, list):
        raise RuntimeError("An Assembly simulation has no authenticated trace summary.")
    data = dict(data)
    motion_names = [str(name) for name in list(data.get("motion_outputs") or [])]
    motion_objects = []
    for name in motion_names:
        motion = outputs.get(name)
        if motion is None or str(getattr(motion, "TypeId", "")) != "App::FeaturePython":
            raise RuntimeError(
                f"Assembly simulation {item['name']!r} motion {name!r} is unavailable."
            )
        motion_objects.append(motion)
    if not isinstance(getattr(obj, "Proxy", None), AssemblySimulationProxy):
        AssemblySimulationProxy(obj)
    else:
        _ensure_assembly_simulation_properties(obj)
        obj.Proxy._mark_native_contract(obj)
        _ensure_native_simulation_view_provider(obj)
    parameters = data.get("parameters")
    if not isinstance(parameters, Mapping):
        raise RuntimeError("An Assembly simulation has no validated parameters.")
    obj.aTimeStart = float(parameters["start_time_s"])
    obj.bTimeEnd = float(parameters["end_time_s"])
    obj.cTimeStepOutput = float(parameters["time_step_s"])
    obj.fGlobalErrorTolerance = float(parameters["error_tolerance"])
    obj.jFramesPerSecond = int(parameters["frames_per_second"])
    obj.Group = motion_objects
    observed_group = list(getattr(obj, "Group", []) or [])
    observed_parameters = (
        float(getattr(obj.aTimeStart, "Value", obj.aTimeStart)),
        float(getattr(obj.bTimeEnd, "Value", obj.bTimeEnd)),
        float(getattr(obj.cTimeStepOutput, "Value", obj.cTimeStepOutput)),
        float(obj.fGlobalErrorTolerance),
        int(obj.jFramesPerSecond),
    )
    expected_parameters = (
        float(parameters["start_time_s"]),
        float(parameters["end_time_s"]),
        float(parameters["time_step_s"]),
        float(parameters["error_tolerance"]),
        int(parameters["frames_per_second"]),
    )
    if observed_group != motion_objects or any(
        not math.isclose(observed, expected, rel_tol=1.0e-12, abs_tol=1.0e-12)
        for observed, expected in zip(
            observed_parameters[:4], expected_parameters[:4], strict=True
        )
    ) or observed_parameters[4] != expected_parameters[4]:
        raise RuntimeError(
            f"Live Assembly simulation {item['name']!r} changed its validated settings."
        )
    for property_type, name, value, description in (
        (
            "App::PropertyInteger",
            "VibeCADFrameCount",
            int(data["frame_count"]),
            "Authenticated native simulation frame count.",
        ),
        (
            "App::PropertyInteger",
            "VibeCADPoseCount",
            int(data["pose_count"]),
            "Authenticated component-placement sample count.",
        ),
        (
            "App::PropertyString",
            "VibeCADTraceSHA256",
            str(data["artifact_sha256"]),
            "SHA-256 of the retained complete native simulation trace.",
        ),
    ):
        _add_property(obj, property_type, name, description)
        setattr(obj, name, value)
    collision = data.get("collision_summary")
    if (
        not isinstance(collision, Mapping)
        or collision.get("status") not in {"complete", "incomplete"}
    ):
        raise RuntimeError(
            "An Assembly simulation has no authenticated collision summary."
        )
    for property_type, name, value, description in (
        (
            "App::PropertyBool",
            "VibeCADCollisionAnalysisComplete",
            bool(collision.get("analysis_complete", True)),
            "True when every requested component pair and simulation frame was evaluated for collision.",
        ),
        (
            "App::PropertyBool",
            "VibeCADCollisionFree",
            bool(collision["collision_free"]),
            "True when no component collision is detected in any simulated frame.",
        ),
        (
            "App::PropertyInteger",
            "VibeCADCollisionWarningCount",
            int(collision.get("warning_count", 0)),
            "Number of authenticated warnings produced by automatic collision analysis.",
        ),
        (
            "App::PropertyInteger",
            "VibeCADCollidingPairCount",
            int(collision["colliding_pair_count"]),
            "Number of component pairs that collide during the simulation.",
        ),
        (
            "App::PropertyInteger",
            "VibeCADCollidingFrameCount",
            int(collision["colliding_frame_count"]),
            "Number of simulated frames containing at least one interference.",
        ),
        (
            "App::PropertyBool",
            "VibeCADInterferenceVolumeComplete",
            bool(collision.get("interference_volume_complete", True)),
            "True only when exact common volume was measured for every collision; false for bounded collision-mesh or strict-containment detection.",
        ),
        (
            "App::PropertyVolume",
            "VibeCADWorstInterferenceVolume",
            (
                0.0
                if collision["worst_collision"] is None
                else float(
                    collision["worst_collision"][
                        "maximum_interference_volume_mm3"
                    ]
                )
            ),
            "Largest measured common volume; zero when detected collisions were not volume-measured.",
        ),
    ):
        _add_property(obj, property_type, name, description)
        setattr(obj, name, value)
    _add_string_property(
        obj,
        "VibeCADAssemblySimulationValidation",
        "Authenticated native Assembly simulation settings, motion effects, and trace identity.",
    )
    obj.VibeCADAssemblySimulationValidation = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    _add_string_property(
        obj,
        "VibeCADSimulationTracePreview",
        "Input, middle, and final authenticated trace frames; the complete trace is retained as a program artifact.",
    )
    obj.VibeCADSimulationTracePreview = json.dumps(
        preview,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _configure_assembly_exploded_view(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> list[Any]:
    """Publish only authenticated native view settings; never calculate geometry."""

    data = item.get("assembly_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("An Assembly exploded view has no authenticated native data.")
    data = dict(data)
    assembly_name = str(data.get("assembly_output") or "")
    assembly = outputs.get(assembly_name)
    if assembly is None or str(getattr(assembly, "TypeId", "")) != (
        "Assembly::AssemblyObject"
    ):
        raise RuntimeError(
            f"Assembly exploded view {item['name']!r} assembly "
            f"{assembly_name!r} is unavailable."
        )
    moves = data.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= 4096:
        raise RuntimeError(
            f"Assembly exploded view {item['name']!r} has no bounded move graph."
        )
    try:
        import CommandCreateView
    except Exception as exc:
        raise RuntimeError(
            f"Native Assembly exploded-view API is unavailable: {exc}"
        ) from exc
    if type(getattr(obj, "Proxy", None)).__name__ != "ExplodedView":
        CommandCreateView.ExplodedView(obj)
    elif "Group" not in _properties(obj):
        obj.addExtension("App::GroupExtensionPython")

    view_name = str(item["name"])
    prefix = f"{view_name}.move."
    program_objects = _program_objects(
        doc,
        str(prepared["program_id"]),
        prepared["pack"].domain,
    )
    existing_steps: dict[str, Any] = {}
    for candidate in program_objects:
        output_key = str(
            getattr(candidate, contracts.PROP_PROGRAM_OUTPUT, "") or ""
        )
        if not output_key.startswith(prefix):
            continue
        if output_key in existing_steps:
            raise RuntimeError(
                f"Multiple managed exploded-view moves claim identity {output_key!r}."
            )
        existing_steps[output_key] = candidate
    foreign_group_members = [
        child
        for child in list(getattr(obj, "Group", []) or [])
        if existing_steps.get(
            str(getattr(child, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        )
        is not child
    ]
    if foreign_group_members:
        names = [str(getattr(child, "Name", "") or "") for child in foreign_group_members]
        raise RuntimeError(
            f"Assembly exploded view {view_name!r} contains unmanaged move objects "
            f"{names}; remove them or use a separate human-authored view before "
            "regenerating this program."
        )

    desired_steps: list[Any] = []
    desired_keys: set[str] = set()
    for move_index, move in enumerate(moves):
        if not isinstance(move, Mapping):
            raise RuntimeError(
                f"Assembly exploded view {view_name!r} move {move_index} is malformed."
            )
        kind = str(move.get("kind") or "")
        if kind not in {"normal", "radial"}:
            raise RuntimeError(
                f"Assembly exploded view {view_name!r} move {move_index} has "
                f"unsupported kind {kind!r}."
            )
        key = f"{prefix}{move_index:03d}"
        desired_keys.add(key)
        step = existing_steps.get(key)
        if step is None:
            step = assembly.newObject(
                "App::FeaturePython",
                _SAFE_NAME.sub("_", f"{view_name}_Move_{move_index + 1}"),
            )
            if step is None:
                raise RuntimeError(
                    f"FreeCAD did not create exploded-view move {move_index}."
                )
        elif str(getattr(step, "TypeId", "")) != "App::FeaturePython":
            raise RuntimeError(
                f"Stable exploded-view move {key!r} changed native type."
            )
        if type(getattr(step, "Proxy", None)).__name__ != "ExplodedViewStep":
            CommandCreateView.ExplodedViewStep(step, 1 if kind == "radial" else 0)
        step.MoveType = "Radial" if kind == "radial" else "Normal"
        movement = move.get("movement_transform")
        if not isinstance(movement, Mapping):
            raise RuntimeError(
                f"Assembly exploded view {view_name!r} move {move_index} has no "
                "authenticated movement transform."
            )
        step.MovementTransform = _placement_from_matrix(movement.get("matrix"))
        component_names = [
            str(name) for name in list(move.get("component_outputs") or [])
        ]
        component_objects = []
        reference_paths = []
        for component_name in component_names:
            component = outputs.get(component_name)
            if component is None or str(getattr(component, "TypeId", "")) not in {
                "App::Link",
                "Assembly::AssemblyLink",
            }:
                raise RuntimeError(
                    f"Assembly exploded view {view_name!r} move {move_index} "
                    f"component {component_name!r} is unavailable."
                )
            component_objects.append(component)
            reference_paths.append(f"{component.Name}.")
        if not component_objects:
            raise RuntimeError(
                f"Assembly exploded view {view_name!r} move {move_index} is empty."
            )
        step.References = [assembly, reference_paths]
        step.Label = f"{_label(item, view_name)} / Move {move_index + 1}"
        readback = getattr(step, "References", None)
        if (
            not isinstance(readback, (list, tuple))
            or len(readback) < 2
            or readback[0] is not assembly
            or list(readback[1]) != reference_paths
            or str(step.MoveType) != ("Radial" if kind == "radial" else "Normal")
        ):
            raise RuntimeError(
                f"Live exploded-view move {key!r} changed its validated references."
            )
        expected_placement = _placement_from_matrix(movement.get("matrix"))
        if any(
            not math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-9)
            for left, right in zip(
                _matrix_values(step.MovementTransform),
                _matrix_values(expected_placement),
                strict=True,
            )
        ):
            raise RuntimeError(
                f"Live exploded-view move {key!r} changed its validated transform."
            )
        _set_metadata(
            step,
            prepared,
            key,
            "exploded_view_step",
            {
                "operation": "exploded_view_move",
                "parent_output": view_name,
                "move_index": move_index,
                "kind": kind,
                "component_outputs": component_names,
            },
        )
        desired_steps.append(step)

    surplus = [
        candidate
        for key, candidate in existing_steps.items()
        if key not in desired_keys
    ]
    if surplus:
        view_group = _assembly_view_group(assembly)
        internal = list(program_objects)
        if view_group is not None:
            internal.append(view_group)
        external = _external_uses(
            doc,
            surplus,
            [*internal, *surplus],
        )
        if external:
            raise _reference_error(
                f"Cannot shorten exploded view {view_name!r}; human-created or "
                "foreign objects reference retired move identities",
                external,
            )
    obj.Group = desired_steps
    if list(getattr(obj, "Group", []) or []) != desired_steps:
        raise RuntimeError(
            f"Live Assembly exploded view {view_name!r} changed its validated move order."
        )
    _add_string_property(
        obj,
        "VibeCADAssemblyExplodedViewValidation",
        "Authenticated native exploded-view moves, placements, lines, and source bounds.",
    )
    obj.VibeCADAssemblyExplodedViewValidation = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return desired_steps


def _assembly_bom_column(number: int) -> str:
    result = ""
    value = number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _assembly_bom_content(obj: Any, address: str) -> str:
    try:
        value = str(obj.getContents(address) or "")
    except ValueError:
        return ""
    return value[1:] if value.startswith("'") else value


def _assembly_bom_live_readback(
    obj: Any, data: Mapping[str, Any]
) -> dict[str, Any]:
    columns = list(data.get("columns") or [])
    rows = list(data.get("rows") or [])
    settings = dict(data.get("settings") or {})
    native_names = [str(column.get("native_name") or "") for column in columns]
    raw_range = obj.getUsedRange()
    used_range = (
        [str(value) for value in raw_range]
        if isinstance(raw_range, tuple) and len(raw_range) == 2
        else []
    )
    return {
        "type_id": str(getattr(obj, "TypeId", "") or ""),
        "auto_generate": bool(getattr(obj, "autoGenerate", True)),
        "columns_names": list(getattr(obj, "columnsNames", []) or []),
        "settings": {
            "detail_subassemblies": bool(
                getattr(obj, "detailSubAssemblies", False)
            ),
            "detail_parts": bool(getattr(obj, "detailParts", False)),
            "only_parts": bool(getattr(obj, "onlyParts", False)),
        },
        "used_range": used_range,
        "headers": [
            _assembly_bom_content(obj, f"{_assembly_bom_column(index + 1)}1")
            for index in range(len(columns))
        ],
        "rows": [
            {
                str(column["heading"]): _assembly_bom_content(
                    obj,
                    f"{_assembly_bom_column(column_index + 1)}{row_index + 2}",
                )
                for column_index, column in enumerate(columns)
            }
            for row_index, _row in enumerate(rows)
        ],
        "expected_native_names": native_names,
        "expected_settings": settings,
    }


def _assembly_bom_expected_readback(data: Mapping[str, Any]) -> dict[str, Any]:
    columns = list(data.get("columns") or [])
    return {
        "type_id": "Assembly::BomObject",
        "auto_generate": False,
        "columns_names": [str(column["native_name"]) for column in columns],
        "settings": dict(data.get("settings") or {}),
        "used_range": list(data.get("used_range") or []),
        "headers": [str(column["heading"]) for column in columns],
        "rows": [dict(row["cells"]) for row in list(data.get("rows") or [])],
        "expected_native_names": [
            str(column["native_name"]) for column in columns
        ],
        "expected_settings": dict(data.get("settings") or {}),
    }


def _populate_assembly_bom_without_recomputing(
    obj: Any, data: Mapping[str, Any]
) -> None:
    """Replay only authenticated cells/properties; never execute native generation."""

    columns = list(data.get("columns") or [])
    rows = list(data.get("rows") or [])
    settings = dict(data.get("settings") or {})
    if (
        str(data.get("schema") or "") != "vibecad-assembly-bom-v1"
        or not columns
        or int(data.get("row_count", -1)) != len(rows)
        or set(settings)
        != {"detail_subassemblies", "detail_parts", "only_parts"}
    ):
        raise RuntimeError("The authenticated Assembly BOM publication data is malformed.")
    if "autoGenerate" not in _properties(obj):
        raise RuntimeError(
            "This FreeCAD build cannot disable synchronous native Assembly BOM "
            "generation; rebuild the Assembly module before publication."
        )
    obj.autoGenerate = False
    obj.clearAll()
    obj.columnsNames = [str(column["native_name"]) for column in columns]
    obj.detailSubAssemblies = bool(settings["detail_subassemblies"])
    obj.detailParts = bool(settings["detail_parts"])
    obj.onlyParts = bool(settings["only_parts"])

    def set_literal(address: str, value: Any) -> None:
        text = str(value)
        if text:
            obj.set(address, f"'{text}")

    for column_index, column in enumerate(columns, start=1):
        set_literal(
            f"{_assembly_bom_column(column_index)}1", str(column["heading"])
        )
    for row_index, row in enumerate(rows, start=2):
        cells = row.get("cells")
        if not isinstance(cells, Mapping):
            raise RuntimeError(
                f"Authenticated Assembly BOM row {row_index - 1} has no cells."
            )
        for column_index, column in enumerate(columns, start=1):
            heading = str(column["heading"])
            if heading not in cells:
                raise RuntimeError(
                    f"Authenticated Assembly BOM row {row_index - 1} omits "
                    f"column {heading!r}."
                )
            set_literal(
                f"{_assembly_bom_column(column_index)}{row_index}", cells[heading]
            )


def _configure_assembly_bom(
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> list[Any]:
    data = item.get("assembly_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("An Assembly BOM has no authenticated native data.")
    data = dict(data)
    if str(getattr(obj, "TypeId", "") or "") != "Assembly::BomObject":
        raise RuntimeError("A stable Assembly BOM output changed native type.")
    assembly_name = str(data.get("assembly_output") or "")
    assembly = outputs.get(assembly_name)
    if assembly is None or str(getattr(assembly, "TypeId", "") or "") != (
        "Assembly::AssemblyObject"
    ):
        raise RuntimeError(
            f"Assembly BOM {item['name']!r} assembly {assembly_name!r} is unavailable."
        )
    group = _assembly_bom_group(assembly)
    if group is None or obj not in list(getattr(group, "Group", []) or []):
        raise RuntimeError(
            f"Assembly BOM {item['name']!r} is not owned by the live native BOM group."
        )
    _populate_assembly_bom_without_recomputing(obj, data)
    readback = _assembly_bom_live_readback(obj, data)
    expected = _assembly_bom_expected_readback(data)
    if readback != expected:
        raise RuntimeError(
            f"Live Assembly BOM {item['name']!r} disagrees with the authenticated "
            "precomputed table; publication was aborted without native generation."
        )
    _add_string_property(
        obj,
        PROP_ASSEMBLY_BOM_VALIDATION,
        "Authenticated precomputed native Assembly BOM table and worker readback.",
    )
    setattr(
        obj,
        PROP_ASSEMBLY_BOM_VALIDATION,
        json.dumps(
            data,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )
    guard_output = f"{item['name']}.__bom_restore"
    guard = next(
        (
            child
            for child in list(getattr(group, "Group", []) or [])
            if str(getattr(child, contracts.PROP_PROGRAM_OUTPUT, "") or "")
            == guard_output
        ),
        None,
    )
    if guard is None:
        guard = group.newObject(
            "App::FeaturePython",
            _SAFE_NAME.sub("_", f"Restore_{item['name']}"),
        )
    if guard is None or str(getattr(guard, "TypeId", "") or "") != (
        "App::FeaturePython"
    ):
        raise RuntimeError(
            f"Assembly BOM {item['name']!r} could not create its managed restore guard."
        )
    AssemblyBOMRestoreProxy(guard)
    setattr(guard, PROP_ASSEMBLY_BOM_RESTORE_TARGET, obj)
    setattr(guard, PROP_ASSEMBLY_BOM_RESTORE_ERROR, "")
    guard.Label = f"{item['name']} accepted table restore"
    _set_metadata(
        guard,
        prepared,
        guard_output,
        "bom_restore_guard",
        {
            "operation": "restore_accepted_bom",
            "bom_output": str(item["name"]),
            "table_sha256": str(data.get("table_sha256") or ""),
        },
    )
    return [guard]


def _assembly_bom_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Authenticate accepted frozen BOM tables before any live mutation."""

    states: list[dict[str, Any]] = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "") or "") != "Assembly::BomObject":
            continue
        name = str(getattr(obj, "Name", "") or "")
        if not _object_is_frozen(obj, "Assembly BOM"):
            raise RuntimeError(
                f"Cannot regenerate Assembly BOM {name!r}: its accepted native object "
                "is not frozen. Restore the accepted program revision before retrying."
            )
        try:
            data = json.loads(
                str(getattr(obj, PROP_ASSEMBLY_BOM_VALIDATION, "") or "")
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot regenerate Assembly BOM {name!r}: its accepted validation "
                f"contract is missing or malformed ({exc})."
            ) from exc
        if not isinstance(data, dict) or str(data.get("schema") or "") != (
            "vibecad-assembly-bom-v1"
        ):
            raise RuntimeError(
                f"Cannot regenerate Assembly BOM {name!r}: its accepted validation "
                "schema is unavailable."
            )
        if _assembly_bom_live_readback(obj, data) != _assembly_bom_expected_readback(
            data
        ):
            raise RuntimeError(
                f"Cannot regenerate Assembly BOM {name!r}: its cells, columns, or "
                "detail settings changed outside the accepted VibeScript revision. "
                "Restore or explicitly accept the manual edits before regeneration."
            )
        states.append(
            {
                "object": obj,
                "data": data,
                "label": str(getattr(obj, "Label", "") or ""),
            }
        )
    return states


def _restore_assembly_bom_rollback_states(states: list[dict[str, Any]]) -> None:
    for state in states:
        obj = state["object"]
        _unfreeze_object(obj, "Assembly BOM")
        _populate_assembly_bom_without_recomputing(obj, state["data"])
        obj.Label = str(state["label"])
        setattr(
            obj,
            PROP_ASSEMBLY_BOM_VALIDATION,
            json.dumps(
                state["data"],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        if _assembly_bom_live_readback(
            obj, state["data"]
        ) != _assembly_bom_expected_readback(state["data"]):
            raise RuntimeError(
                f"Assembly BOM rollback failed for {getattr(obj, 'Name', '')!r}."
            )
        _freeze_object(obj, "Assembly BOM")


def _configure_sheet(obj: Any, item: Mapping[str, Any]) -> None:
    from vibescript_spreadsheet_worker import (
        populate_sheet_without_recomputing,
        sheet_readback,
    )

    if str(getattr(obj, "TypeId", "")) != "Spreadsheet::Sheet":
        raise RuntimeError("A stable Spreadsheet output changed native type.")
    definition = _definition(item)
    validation = item.get("sheet_validation")
    if not isinstance(validation, dict):
        raise RuntimeError("The spreadsheet batch has no detached validation.")
    counts = populate_sheet_without_recomputing(obj, definition, clear=True)
    readback = sheet_readback(obj, definition)
    if str(readback.get("sha256") or "") != str(
        validation.get("readback_sha256") or ""
    ):
        raise RuntimeError(
            "Live Spreadsheet replay disagrees with the isolated native readback; "
            "the publication transaction was aborted."
        )
    expected_counts = {
        "cell_count": counts["cell_count"],
        "range_style_count": counts["range_style_count"],
        "merged_range_count": counts["merged_range_count"],
        "column_width_count": counts["column_width_count"],
        "row_height_count": counts["row_height_count"],
        "affected_cell_count": int(readback["affected_cell_count"]),
    }
    if any(
        int(validation.get(name, -1)) != value
        for name, value in expected_counts.items()
    ):
        raise RuntimeError(
            "Live Spreadsheet replay counts disagree with worker validation."
        )
    _add_string_property(
        obj,
        "VibeCADSpreadsheetValidation",
        "Bounded isolated native Spreadsheet validation and readback diagnostics.",
    )
    obj.VibeCADSpreadsheetValidation = json.dumps(
        validation,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _spreadsheet_rollback_states(
    objects: list[Any],
) -> list[dict[str, Any]]:
    """Authorize and capture accepted sheet state before any live mutation."""

    from vibescript_spreadsheet_worker import (
        sheet_readback,
        validate_spreadsheet_definition,
    )

    states: list[dict[str, Any]] = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "")) != "Spreadsheet::Sheet":
            continue
        name = str(getattr(obj, "Name", "") or "")
        try:
            definition = validate_spreadsheet_definition(
                json.loads(str(getattr(obj, PROP_DEFINITION) or "")),
                context=f"live sheet {name!r} accepted definition",
            )
            validation = json.loads(
                str(getattr(obj, "VibeCADSpreadsheetValidation") or "")
            )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely update Spreadsheet output {name!r}: its accepted "
                f"definition or rollback validation is missing or invalid ({exc})."
            ) from exc
        if not isinstance(validation, dict):
            raise RuntimeError(
                f"Cannot safely update Spreadsheet output {name!r}: its accepted "
                "rollback validation is not an object."
            )
        raw_used_range = obj.getUsedRange()
        live_used_range = (
            [str(value) for value in raw_used_range]
            if isinstance(raw_used_range, tuple) and len(raw_used_range) == 2
            else []
        )
        if live_used_range != list(validation.get("used_range") or []):
            raise RuntimeError(
                f"Cannot regenerate Spreadsheet output {name!r}: its live used range "
                "changed outside the accepted VibeScript revision. Restore or accept the "
                "manual edits explicitly before regeneration."
            )
        readback = sheet_readback(obj, definition)
        accepted_digest = str(validation.get("readback_sha256") or "")
        if str(readback.get("sha256") or "") != accepted_digest:
            raise RuntimeError(
                f"Cannot regenerate Spreadsheet output {name!r}: its cells, aliases, "
                "formats, units, dimensions, or merged ranges changed outside the accepted VibeScript "
                "revision. Restore or accept the manual edits explicitly before regeneration."
            )
        states.append(
            {
                "object": obj,
                "name": name,
                "label": str(getattr(obj, "Label", "") or ""),
                "definition": definition,
                "readback_sha256": accepted_digest,
            }
        )
    return states


def _restore_spreadsheet_rollback_states(states: list[dict[str, Any]]) -> list[str]:
    """Restore assigned native state after FreeCAD's transaction abort."""

    from vibescript_spreadsheet_worker import (
        restore_sheet_without_recomputing,
        sheet_readback,
    )

    restored: list[str] = []
    failures: list[str] = []
    for state in states:
        obj = state["object"]
        name = str(state["name"])
        try:
            restore_sheet_without_recomputing(obj, state["definition"])
            obj.Label = str(state["label"])
            readback = sheet_readback(obj, state["definition"])
            if str(readback.get("sha256") or "") != str(state["readback_sha256"]):
                raise RuntimeError("restored assigned-state digest does not match")
            restored.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Spreadsheet publication failed and accepted assigned state could not be "
            f"fully restored: {'; '.join(failures)}"
        )
    return restored


def _material_definition_target(doc: Any, item: Mapping[str, Any]) -> Any:
    definition = _definition(item)
    arguments = list(definition.get("arguments") or [])
    if not arguments:
        raise RuntimeError(
            f"Material output {item.get('name')!r} has no target reference."
        )
    return _reference_target(
        doc, arguments[0], f"material output {item.get('name')} target"
    )


def _material_card_state(material: Any) -> dict[str, str]:
    from vibescript_material_worker import material_card_digest

    if material is None:
        raise RuntimeError("A native material state is unavailable.")
    return {
        "uuid": str(getattr(material, "UUID", "") or "").lower(),
        "name": str(getattr(material, "Name", "") or ""),
        "card_sha256": material_card_digest(material),
    }


def _display_material_payload(material: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("AmbientColor", "DiffuseColor", "SpecularColor", "EmissiveColor"):
        value = getattr(material, name)
        # App::PropertyMaterialList persists colors as 8-bit channels even
        # though the live Python wrapper exposes floats.  Canonicalize to that
        # native save/reopen precision so an unchanged document has one digest.
        result[name] = [
            round(float(channel) * 255.0) / 255.0 for channel in tuple(value)
        ]
    result["Shininess"] = round(float(getattr(material, "Shininess")), 6)
    result["Transparency"] = round(float(getattr(material, "Transparency")), 6)
    return result


def _shape_appearance_payload(values: Any) -> list[dict[str, Any]]:
    return [_display_material_payload(value) for value in list(values or [])]


def _shape_appearance_sha256(values: Any) -> str:
    import hashlib

    encoded = json.dumps(
        _shape_appearance_payload(values),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_MATERIAL_SIMPLE_VIEW_PROPERTIES = (
    "LineColor",
    "PointColor",
    "LineWidth",
    "PointSize",
    "DisplayMode",
    "Visibility",
    "Selectable",
    "OverrideMaterial",
)


def _material_json_view_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        clean = float(value)
        if not math.isfinite(clean):
            raise RuntimeError("A native display property has a non-finite value.")
        return int(value) if type(value) is int else clean
    if isinstance(value, (list, tuple)):
        return [_material_json_view_value(item) for item in value]
    raise RuntimeError(
        f"A native display property has unsupported type {type(value).__name__}."
    )


def _capture_simple_view_state(
    view: Any, names: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for name in names:
        if not hasattr(view, name):
            raise RuntimeError(
                f"The target view no longer supports native property {name!r}."
            )
        state[name] = _material_json_view_value(getattr(view, name))
    return state


def _set_simple_view_state(view: Any, state: Mapping[str, Any]) -> None:
    for name in _MATERIAL_SIMPLE_VIEW_PROPERTIES:
        if name not in state:
            continue
        desired = state[name]
        if hasattr(view, name):
            current = _material_json_view_value(getattr(view, name))
            if _material_state_equal(current, desired):
                continue
        value = desired
        if name in {"LineColor", "PointColor"}:
            value = tuple(float(channel) for channel in list(value))
        setattr(view, name, value)


def _material_state_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return (
            math.isfinite(float(left))
            and math.isfinite(float(right))
            and abs(float(left) - float(right)) <= 2.0e-6
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _material_state_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _material_state_equal(left[key], right[key]) for key in left
        )
    return type(left) is type(right) and left == right


def _capture_complete_view_state(target: Any) -> dict[str, Any] | None:
    view = getattr(target, "ViewObject", None)
    if view is None:
        return None
    simple_names = [
        name for name in _MATERIAL_SIMPLE_VIEW_PROPERTIES if hasattr(view, name)
    ]
    return {
        "view": view,
        "shape_appearance": (
            list(view.ShapeAppearance) if hasattr(view, "ShapeAppearance") else None
        ),
        "simple": _capture_simple_view_state(view, simple_names),
    }


def _view_state_values(
    state: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "shape_appearance": state.get("shape_appearance"),
        "simple": dict(state.get("simple") or {}),
    }


def _restore_view_state_values(
    target: Any,
    state: Mapping[str, Any] | None,
) -> None:
    if state is None:
        return
    view = getattr(target, "ViewObject", None)
    if view is None:
        raise RuntimeError(
            f"Presentation target {getattr(target, 'Name', '')!r} has no view provider."
        )
    shape_appearance = state.get("shape_appearance")
    if shape_appearance is not None and (
        not hasattr(view, "ShapeAppearance")
        or _shape_appearance_payload(view.ShapeAppearance)
        != _shape_appearance_payload(shape_appearance)
    ):
        view.ShapeAppearance = list(shape_appearance)
    _set_simple_view_state(view, dict(state.get("simple") or {}))


def _restore_complete_view_state(state: Mapping[str, Any] | None) -> None:
    if state is None:
        return
    view = state["view"]
    target = getattr(view, "Object", None)
    if target is not None:
        _restore_view_state_values(target, state)
        return
    shape_appearance = state.get("shape_appearance")
    if shape_appearance is not None and (
        not hasattr(view, "ShapeAppearance")
        or _shape_appearance_payload(view.ShapeAppearance)
        != _shape_appearance_payload(shape_appearance)
    ):
        view.ShapeAppearance = list(shape_appearance)
    _set_simple_view_state(view, dict(state.get("simple") or {}))


def _set_physical_material_preserving_view(target: Any, material: Any) -> None:
    if not hasattr(target, "ShapeMaterial"):
        raise RuntimeError(
            f"Material target {getattr(target, 'Name', '')!r} has no ShapeMaterial property."
        )
    view_state = _capture_complete_view_state(target)
    try:
        target.ShapeMaterial = material
    finally:
        _restore_complete_view_state(view_state)


def _material_ownership(obj: Any) -> dict[str, Any]:
    if PROP_MATERIAL_OWNERSHIP not in _properties(obj):
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} has no ownership metadata."
        )
    try:
        value = json.loads(str(getattr(obj, PROP_MATERIAL_OWNERSHIP) or ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} has invalid ownership JSON: {exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != MATERIAL_OWNERSHIP_SCHEMA:
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} has unsupported ownership metadata."
        )
    if value.get("channel") not in {"physical", "appearance"}:
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} has an invalid ownership channel."
        )
    return value


def _material_carrier_target(obj: Any, ownership: Mapping[str, Any]) -> Any:
    target = getattr(obj, PROP_MATERIAL_TARGET, None)
    expected = dict(ownership.get("target") or {})
    if target is None or str(getattr(target, "Name", "") or "") != str(
        expected.get("object_name") or ""
    ):
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} lost its stable target link."
        )
    document = getattr(obj, "Document", None)
    if document is None or str(getattr(document, "Uid", "") or "") != str(
        expected.get("document_uid") or ""
    ):
        raise RuntimeError(
            f"Material carrier {getattr(obj, 'Name', '')!r} target belongs to another document."
        )
    return target


def _preflight_material_carrier(obj: Any) -> tuple[dict[str, Any], Any]:
    ownership = _material_ownership(obj)
    target = _material_carrier_target(obj, ownership)
    channel = str(ownership["channel"])
    if channel == "physical":
        if not {
            PROP_MATERIAL_BASELINE,
            PROP_MATERIAL_ACCEPTED,
        } <= _properties(obj):
            raise RuntimeError(
                f"Material carrier {getattr(obj, 'Name', '')!r} lost native material state."
            )
        expected = dict(ownership.get("accepted_material") or {})
        live = _material_card_state(getattr(target, "ShapeMaterial", None))
        stored = _material_card_state(getattr(obj, PROP_MATERIAL_ACCEPTED))
        if live != expected or stored != expected:
            raise RuntimeError(
                f"Cannot change Material output {getattr(obj, contracts.PROP_PROGRAM_OUTPUT, '')!r}: "
                f"target {target.Name!r}.ShapeMaterial changed outside its accepted VibeScript "
                "revision. Restore the accepted material or remove the conflicting edit."
            )
    else:
        controlled = list(ownership.get("controlled_properties") or [])
        if not controlled or any(
            name not in {"ShapeAppearance", *_MATERIAL_SIMPLE_VIEW_PROPERTIES}
            for name in controlled
        ):
            raise RuntimeError(
                f"Material carrier {getattr(obj, 'Name', '')!r} has invalid appearance ownership."
            )
        view = getattr(target, "ViewObject", None)
        if view is None:
            raise RuntimeError(
                f"Appearance target {target.Name!r} has no live view provider."
            )
        if "ShapeAppearance" in controlled:
            if not {
                PROP_APPEARANCE_BASELINE,
                PROP_APPEARANCE_ACCEPTED,
            } <= _properties(obj):
                raise RuntimeError(
                    f"Appearance carrier {getattr(obj, 'Name', '')!r} lost native baseline state."
                )
            live_digest = _shape_appearance_sha256(view.ShapeAppearance)
            stored_digest = _shape_appearance_sha256(
                getattr(obj, PROP_APPEARANCE_ACCEPTED)
            )
            accepted_digest = str(
                ownership.get("accepted_shape_appearance_sha256") or ""
            )
            if live_digest != accepted_digest or stored_digest != accepted_digest:
                raise RuntimeError(
                    f"Cannot change Material appearance output "
                    f"{getattr(obj, contracts.PROP_PROGRAM_OUTPUT, '')!r}: target "
                    f"{target.Name!r}.ShapeAppearance changed outside its accepted VibeScript "
                    "revision. Restore or explicitly remove the manual display edit."
                )
        simple_names = [name for name in controlled if name != "ShapeAppearance"]
        live_simple = _capture_simple_view_state(view, simple_names)
        accepted_simple = dict(ownership.get("accepted_simple") or {})
        if not _material_state_equal(live_simple, accepted_simple):
            raise RuntimeError(
                f"Cannot change Material appearance output "
                f"{getattr(obj, contracts.PROP_PROGRAM_OUTPUT, '')!r}: one or more "
                f"controlled display properties on {target.Name!r} changed outside the "
                "accepted VibeScript revision."
            )
    return ownership, target


def _restore_material_baseline(
    obj: Any, ownership: Mapping[str, Any], target: Any
) -> None:
    if ownership["channel"] == "physical":
        _set_physical_material_preserving_view(
            target, getattr(obj, PROP_MATERIAL_BASELINE)
        )
        return
    view = getattr(target, "ViewObject", None)
    if view is None:
        raise RuntimeError(
            f"Appearance target {target.Name!r} has no live view provider."
        )
    controlled = list(ownership.get("controlled_properties") or [])
    if "ShapeAppearance" in controlled:
        view.ShapeAppearance = list(getattr(obj, PROP_APPEARANCE_BASELINE))
    _set_simple_view_state(view, dict(ownership.get("baseline_simple") or {}))


def _material_target_snapshot(
    target: Any,
    *,
    required_after_abort: bool = True,
) -> dict[str, Any]:
    document = getattr(target, "Document", None)
    if document is None:
        raise RuntimeError(
            f"Material target {getattr(target, 'Name', '')!r} has no owning document."
        )
    return {
        "document": document,
        "identity": _deletion_object_identity(
            target,
            context="A material presentation target",
        ),
        "type_id": str(getattr(target, "TypeId", "") or ""),
        "required_after_abort": bool(required_after_abort),
        "material": getattr(target, "ShapeMaterial", None),
        "view": _view_state_values(_capture_complete_view_state(target)),
    }


def _restore_material_target_snapshots(states: list[dict[str, Any]]) -> None:
    failures: list[str] = []
    resolved: list[tuple[dict[str, Any], Any]] = []
    for state in states:
        identity = tuple(state["identity"])
        target = _resolve_timeline_identity(state["document"], identity)
        if target is None:
            if state.get("required_after_abort", True):
                failures.append(
                    f"{identity[0]}: native object identity was not restored"
                )
            continue
        resolved.append((state, target))
    # Restore implementation objects before App::Links. Link view properties
    # proxy their target and should normally become equal without being set.
    resolved.sort(key=lambda item: item[0].get("type_id") == "App::Link")
    for state, target in resolved:
        try:
            material = state.get("material")
            if material is not None and hasattr(target, "ShapeMaterial"):
                current = getattr(target, "ShapeMaterial")
                if current != material:
                    target.ShapeMaterial = material
            _restore_view_state_values(target, state.get("view"))
        except Exception as exc:
            failures.append(
                f"{getattr(target, 'Name', '<target>')}: {type(exc).__name__}: {exc}"
            )
    if failures:
        raise RuntimeError(
            "Material target rollback was incomplete: " + "; ".join(failures)
        )


def _material_baseline_for_desired(
    obj: Any | None,
    previous: Mapping[str, Any] | None,
    target: Any,
    *,
    channel: str,
    controlled: list[str],
) -> dict[str, Any]:
    same_owner = bool(
        obj is not None
        and previous is not None
        and previous.get("channel") == channel
        and str(dict(previous.get("target") or {}).get("object_name") or "")
        == str(target.Name)
    )
    if channel == "physical":
        material = (
            getattr(obj, PROP_MATERIAL_BASELINE)
            if same_owner and PROP_MATERIAL_BASELINE in _properties(obj)
            else getattr(target, "ShapeMaterial")
        )
        return {"material": material}

    view = getattr(target, "ViewObject", None)
    if view is None:
        raise RuntimeError(
            f"Appearance target {target.Name!r} has no live view provider."
        )
    previous_controlled = (
        set(previous.get("controlled_properties") or []) if same_owner else set()
    )
    previous_baseline_simple = (
        dict(previous.get("baseline_simple") or {}) if same_owner else {}
    )
    simple_names = [name for name in controlled if name != "ShapeAppearance"]
    current_simple = _capture_simple_view_state(view, simple_names)
    baseline_simple = {
        name: (
            previous_baseline_simple[name]
            if name in previous_controlled and name in previous_baseline_simple
            else current_simple[name]
        )
        for name in simple_names
    }
    baseline_shape = None
    if "ShapeAppearance" in controlled:
        baseline_shape = (
            list(getattr(obj, PROP_APPEARANCE_BASELINE))
            if same_owner
            and "ShapeAppearance" in previous_controlled
            and PROP_APPEARANCE_BASELINE in _properties(obj)
            else list(view.ShapeAppearance)
        )
    return {"simple": baseline_simple, "shape_appearance": baseline_shape}


def _appearance_property_view(
    target: Any,
    native_name: str,
    property_views: Mapping[str, Any] | None,
) -> Any:
    view = (
        property_views.get(native_name)
        if property_views is not None
        else getattr(target, "ViewObject", None)
    )
    if view is None:
        raise RuntimeError(
            f"Appearance target {target.Name!r} has no live view provider."
        )
    return view


def _effective_appearance_controlled_properties(
    view: Any,
    controlled: list[str],
) -> list[str]:
    result = list(dict.fromkeys(str(name) for name in controlled))
    if (
        "ShapeAppearance" in result
        and hasattr(view, "OverrideMaterial")
        and "OverrideMaterial" not in result
    ):
        result.append("OverrideMaterial")
    return sorted(result)


def _apply_requested_appearance(
    target: Any,
    requested: Mapping[str, Any],
    *,
    property_views: Mapping[str, Any] | None = None,
) -> None:
    shape_material = requested.get("shape_material")
    shape_color = requested.get("shape_color")
    transparency = requested.get("transparency")
    if (
        shape_material is not None
        or shape_color is not None
        or transparency is not None
    ):
        view = _appearance_property_view(
            target,
            "ShapeAppearance",
            property_views,
        )
        if not hasattr(view, "ShapeAppearance"):
            raise RuntimeError(
                f"Appearance target {target.Name!r} no longer supports ShapeAppearance."
            )
        if not isinstance(shape_material, Mapping):
            if shape_material is not None:
                raise RuntimeError(
                    "Validated card appearance is not a material mapping."
                )
        allowed = {
            "ambient_color": "AmbientColor",
            "diffuse_color": "DiffuseColor",
            "specular_color": "SpecularColor",
            "emissive_color": "EmissiveColor",
            "shininess": "Shininess",
            "transparency": "Transparency",
        }
        if shape_material is not None and (
            not shape_material or not set(shape_material) <= set(allowed)
        ):
            raise RuntimeError(
                "Validated card appearance has unsupported material fields."
            )
        import FreeCAD as App

        existing = list(view.ShapeAppearance)
        if not existing:
            existing = [App.Material()]
        updated = []
        for current in existing:
            values = {
                "AmbientColor": tuple(current.AmbientColor),
                "DiffuseColor": tuple(current.DiffuseColor),
                "SpecularColor": tuple(current.SpecularColor),
                "EmissiveColor": tuple(current.EmissiveColor),
                "Shininess": float(current.Shininess),
                "Transparency": float(current.Transparency),
            }
            for field, value in dict(shape_material or {}).items():
                native_name = allowed[str(field)]
                values[native_name] = (
                    tuple(float(channel) for channel in list(value))
                    if str(field).endswith("_color")
                    else float(value)
                )
            if shape_color is not None:
                previous = list(values["DiffuseColor"])
                values["DiffuseColor"] = tuple(
                    [
                        *[float(channel) for channel in list(shape_color)],
                        float(previous[3]) if len(previous) == 4 else 1.0,
                    ]
                )
            if transparency is not None:
                values["Transparency"] = float(transparency) / 100.0
            updated.append(App.Material(**values))
        view.ShapeAppearance = updated
        if hasattr(view, "OverrideMaterial"):
            view.OverrideMaterial = True
    assignments = (
        ("line_color", "LineColor"),
        ("point_color", "PointColor"),
        ("line_width", "LineWidth"),
        ("point_size", "PointSize"),
        ("display_mode", "DisplayMode"),
        ("visibility", "Visibility"),
        ("selectable", "Selectable"),
    )
    for key, native_name in assignments:
        value = requested.get(key)
        if value is None:
            continue
        view = _appearance_property_view(target, native_name, property_views)
        if not hasattr(view, native_name):
            raise RuntimeError(
                f"Appearance target {target.Name!r} no longer supports {native_name}."
            )
        if key.endswith("_color"):
            value = tuple(float(channel) for channel in list(value))
        if key == "display_mode":
            getter = getattr(view, "getEnumerationsOfProperty", None)
            modes = (
                [str(item) for item in list(getter("DisplayMode") or [])]
                if callable(getter)
                else []
            )
            if value not in modes:
                raise RuntimeError(
                    f"Appearance target {target.Name!r} does not support display mode "
                    f"{value!r}; available modes: {modes!r}."
                )
        setattr(view, native_name, value)


def _verify_requested_appearance(
    target: Any,
    requested: Mapping[str, Any],
    *,
    property_views: Mapping[str, Any] | None = None,
) -> None:
    shape_material = requested.get("shape_material")
    shape_color = requested.get("shape_color")
    transparency = requested.get("transparency")
    materials = []
    if (
        shape_material is not None
        or shape_color is not None
        or transparency is not None
    ):
        view = _appearance_property_view(
            target,
            "ShapeAppearance",
            property_views,
        )
        materials = list(view.ShapeAppearance)
        if not materials:
            raise RuntimeError(
                f"Appearance target {target.Name!r} has no ShapeAppearance readback."
            )
        if hasattr(view, "OverrideMaterial") and not bool(view.OverrideMaterial):
            raise RuntimeError(
                f"Appearance target {target.Name!r} did not enable its native "
                "material override."
            )
    if shape_material is not None:
        native_names = {
            "ambient_color": "AmbientColor",
            "diffuse_color": "DiffuseColor",
            "specular_color": "SpecularColor",
            "emissive_color": "EmissiveColor",
            "shininess": "Shininess",
            "transparency": "Transparency",
        }
        for index, material in enumerate(materials):
            for field, expected in shape_material.items():
                observed = getattr(material, native_names[str(field)])
                if str(field).endswith("_color"):
                    observed = [float(channel) for channel in tuple(observed)]
                    expected = [float(channel) for channel in list(expected)]
                    equal = len(observed) == len(expected) and all(
                        abs(left - right) <= (1.0 / 255.0) + 2.0e-6
                        for left, right in zip(observed, expected)
                    )
                else:
                    equal = _material_state_equal(float(observed), float(expected))
                if not equal:
                    raise RuntimeError(
                        f"Appearance target {target.Name!r}.ShapeAppearance[{index}]."
                        f"{native_names[str(field)]} read back as {observed!r}, expected "
                        f"{expected!r}."
                    )
    if shape_color is not None:
        expected = [float(value) for value in list(shape_color)]
        for index, material in enumerate(materials):
            observed = [
                float(value) for value in tuple(material.DiffuseColor)[:3]
            ]
            if not _material_state_equal(observed, expected):
                raise RuntimeError(
                    f"Appearance target {target.Name!r}.ShapeAppearance[{index}]."
                    f"DiffuseColor read back as {observed!r}, expected {expected!r}."
                )
    if transparency is not None:
        expected = float(transparency)
        for index, material in enumerate(materials):
            observed = float(material.Transparency) * 100.0
            if not _material_state_equal(observed, expected):
                raise RuntimeError(
                    f"Appearance target {target.Name!r}.ShapeAppearance[{index}]."
                    f"Transparency read back as {observed!r}, expected {expected!r}."
                )

    simple_views = {
        native_name: _appearance_property_view(
            target,
            native_name,
            property_views,
        )
        for key, native_name in (
            ("line_color", "LineColor"),
            ("point_color", "PointColor"),
            ("line_width", "LineWidth"),
            ("point_size", "PointSize"),
            ("display_mode", "DisplayMode"),
            ("visibility", "Visibility"),
            ("selectable", "Selectable"),
        )
        if requested.get(key) is not None
    }
    readbacks = {
        "line_color": (
            [
                float(value)
                for value in tuple(simple_views["LineColor"].LineColor)[:3]
            ]
            if requested.get("line_color") is not None
            else None
        ),
        "point_color": (
            [
                float(value)
                for value in tuple(simple_views["PointColor"].PointColor)[:3]
            ]
            if requested.get("point_color") is not None
            else None
        ),
        "line_width": (
            float(simple_views["LineWidth"].LineWidth)
            if requested.get("line_width") is not None
            else None
        ),
        "point_size": (
            float(simple_views["PointSize"].PointSize)
            if requested.get("point_size") is not None
            else None
        ),
        "display_mode": (
            str(simple_views["DisplayMode"].DisplayMode)
            if requested.get("display_mode") is not None
            else None
        ),
        "visibility": (
            bool(simple_views["Visibility"].Visibility)
            if requested.get("visibility") is not None
            else None
        ),
        "selectable": (
            bool(simple_views["Selectable"].Selectable)
            if requested.get("selectable") is not None
            else None
        ),
    }
    for key, requested_value in requested.items():
        if key in {"shape_material", "shape_color", "transparency"} or requested_value is None:
            continue
        if not _material_state_equal(readbacks[key], requested_value):
            raise RuntimeError(
                f"Appearance target {target.Name!r}.{key} read back as "
                f"{readbacks[key]!r}, expected {requested_value!r}."
            )


def _configure_material_carrier(
    obj: Any,
    item: Mapping[str, Any],
    target: Any,
    baseline: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    output_type = str(item["type"])
    validation = item.get("material_validation")
    if not isinstance(validation, dict):
        raise RuntimeError(f"Material output {item.get('name')!r} has no validation.")
    channel = "physical" if output_type == "material_assignment" else "appearance"
    _add_property(
        obj,
        "App::PropertyLink",
        PROP_MATERIAL_TARGET,
        "Native target owned by this output.",
    )
    _add_string_property(
        obj, PROP_MATERIAL_OWNERSHIP, "Accepted reversible ownership state as JSON."
    )
    _add_string_property(
        obj,
        PROP_MATERIAL_VALIDATION,
        "Isolated and host-authenticated validation JSON.",
    )
    _add_string_property(obj, "VibeCADTargetObject", "Assigned target internal name.")
    setattr(obj, PROP_MATERIAL_TARGET, target)
    obj.VibeCADTargetObject = str(target.Name)

    target_reference = {
        "document_uid": str(getattr(target.Document, "Uid", "") or ""),
        "object_name": str(target.Name),
    }
    ownership: dict[str, Any] = {
        "schema": MATERIAL_OWNERSHIP_SCHEMA,
        "channel": channel,
        "target": target_reference,
    }
    if channel == "physical":
        native_material = item.get("native_material")
        if native_material is None:
            raise RuntimeError(
                "A validated physical assignment lost its native material card."
            )
        _add_property(
            obj,
            "Materials::PropertyMaterial",
            PROP_MATERIAL_BASELINE,
            "Native material restored when this output is retired or deleted.",
        )
        _add_property(
            obj,
            "Materials::PropertyMaterial",
            PROP_MATERIAL_ACCEPTED,
            "Native material authenticated for the accepted revision.",
        )
        setattr(obj, PROP_MATERIAL_BASELINE, baseline["material"])
        _set_physical_material_preserving_view(target, native_material)
        assigned = getattr(target, "ShapeMaterial")
        requested_card = dict(validation.get("material_card") or {})
        accepted_state = _material_card_state(assigned)
        if accepted_state != {
            "uuid": str(requested_card.get("uuid") or ""),
            "name": str(requested_card.get("name") or ""),
            "card_sha256": str(requested_card.get("card_sha256") or ""),
        }:
            raise RuntimeError(
                f"Physical material readback on {target.Name!r} differs from the validated card."
            )
        setattr(obj, PROP_MATERIAL_ACCEPTED, assigned)
        ownership["baseline_material"] = _material_card_state(baseline["material"])
        ownership["accepted_material"] = accepted_state
    else:
        requested = dict(validation.get("resolved") or {})
        view = getattr(target, "ViewObject", None)
        if view is None:
            raise RuntimeError(
                f"Appearance target {target.Name!r} has no live view provider."
            )
        controlled = _effective_appearance_controlled_properties(
            view,
            list(validation.get("controlled_properties") or []),
        )
        _add_property(
            obj,
            "App::PropertyMaterialList",
            PROP_APPEARANCE_BASELINE,
            "Complete native ShapeAppearance restored on retirement or deletion.",
        )
        _add_property(
            obj,
            "App::PropertyMaterialList",
            PROP_APPEARANCE_ACCEPTED,
            "Complete native ShapeAppearance for the accepted revision.",
        )
        baseline_shape = baseline.get("shape_appearance")
        setattr(obj, PROP_APPEARANCE_BASELINE, list(baseline_shape or []))
        _apply_requested_appearance(target, requested)
        _verify_requested_appearance(target, requested)
        accepted_simple_names = [
            name for name in controlled if name != "ShapeAppearance"
        ]
        accepted_simple = _capture_simple_view_state(view, accepted_simple_names)
        accepted_shape = (
            list(view.ShapeAppearance) if "ShapeAppearance" in controlled else []
        )
        setattr(obj, PROP_APPEARANCE_ACCEPTED, accepted_shape)
        ownership.update(
            {
                "controlled_properties": controlled,
                "baseline_simple": dict(baseline.get("simple") or {}),
                "accepted_simple": accepted_simple,
                "baseline_shape_appearance_sha256": (
                    _shape_appearance_sha256(baseline_shape)
                    if "ShapeAppearance" in controlled
                    else ""
                ),
                "accepted_shape_appearance_sha256": (
                    _shape_appearance_sha256(accepted_shape)
                    if "ShapeAppearance" in controlled
                    else ""
                ),
            }
        )
    setattr(
        obj,
        PROP_MATERIAL_OWNERSHIP,
        json.dumps(ownership, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )
    setattr(
        obj,
        PROP_MATERIAL_VALIDATION,
        json.dumps(
            validation, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ),
    )
    setattr(obj, PROP_INPUT_OBJECTS, [target])
    return ownership


def _set_native_property(obj: Any, name: str, value: Any) -> None:
    if name in _properties(obj):
        setattr(obj, name, value)


def _require_native_property(obj: Any, name: str, value: Any) -> None:
    if name not in _properties(obj):
        raise RuntimeError(
            f"Native {getattr(obj, 'TypeId', '<object>')} Draft proxy "
            f"{type(getattr(obj, 'Proxy', None)).__name__} has no {name!r} property."
        )
    setattr(obj, name, value)


def _matrix_values(placement: Any) -> list[float]:
    matrix = placement.toMatrix()
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


def _assert_matrix(actual: Any, expected: Any, label: str) -> None:
    observed = _matrix_values(actual)
    requested = _matrix_values(expected)
    if any(
        not math.isclose(left, right, rel_tol=1.0e-9, abs_tol=1.0e-9)
        for left, right in zip(observed, requested)
    ):
        raise RuntimeError(f"Published {label} changed its validated placement.")


def _draft_array_target(
    doc: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> Any:
    data = item.get("draft_data")
    if not isinstance(data, dict) or not isinstance(data.get("source"), dict):
        raise RuntimeError("A Draft array has no validated Base resolution.")
    source = dict(data["source"])
    if source.get("kind") == "program_output":
        output_name = str(source.get("output_name") or "")
        target = outputs.get(output_name)
        if target is None:
            raise RuntimeError(
                f"Draft array Base output {output_name!r} disappeared before publication."
            )
        return target
    if source.get("kind") == "document_reference":
        return _reference_target(
            doc,
            {
                "document_uid": str(source.get("document_uid") or ""),
                "object_name": str(source.get("object_name") or ""),
            },
            "Draft array source",
        )
    raise RuntimeError("A Draft array Base resolution has an unsupported source kind.")


def _configure_draft(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> None:
    definition = _definition(item)
    output_type = str(item["type"])
    data = item.get("draft_data")
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Draft output {item.get('name')!r} has no native validation data."
        )
    validated_placement = _placement(data.get("placement"))
    if output_type in {"wire", "bspline"}:
        raw_points = _definition_argument(definition, 0, "points", default=[])
        _require_native_property(
            obj,
            "Points",
            [
                _native_vector(point, f"{output_type} point")
                for point in list(raw_points or [])
            ],
        )
        _require_native_property(obj, "Closed", bool(data["closed"]))
        _require_native_property(obj, "MakeFace", bool(data["make_face"]))
        if output_type == "bspline":
            _require_native_property(
                obj, "Parameterization", float(data["parameterization"])
            )
        else:
            _require_native_property(
                obj, "FilletRadius", float(data["fillet_radius"])
            )
            _require_native_property(
                obj, "ChamferSize", float(data["chamfer_size"])
            )
            _require_native_property(obj, "Subdivisions", int(data["subdivisions"]))
        obj.Placement = validated_placement
    elif output_type == "circle":
        _require_native_property(obj, "Radius", float(data["radius"]))
        _require_native_property(obj, "FirstAngle", float(data["start_angle"]))
        _require_native_property(obj, "LastAngle", float(data["end_angle"]))
        _require_native_property(obj, "MakeFace", bool(data["make_face"]))
        obj.Placement = validated_placement
    elif output_type == "rectangle":
        _require_native_property(obj, "Length", float(data["length"]))
        _require_native_property(obj, "Height", float(data["height"]))
        _require_native_property(obj, "MakeFace", bool(data["make_face"]))
        _require_native_property(obj, "FilletRadius", float(data["fillet_radius"]))
        _require_native_property(obj, "ChamferSize", float(data["chamfer_size"]))
        obj.Placement = validated_placement
    elif output_type == "text":
        _require_native_property(obj, "Text", [str(line) for line in data["lines"]])
        obj.Placement = validated_placement
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            if not all(
                hasattr(view, name)
                for name in ("DisplayMode", "FontSize", "LineSpacing")
            ):
                raise RuntimeError("The native Draft Text view provider is incomplete.")
            view.DisplayMode = "Screen" if bool(data["screen"]) else "World"
            view.FontSize = float(data["height"])
            view.LineSpacing = float(data["line_spacing"])
    elif output_type == "array":
        expected_mode = bool(data["use_link"])
        live_mode = bool(getattr(getattr(obj, "Proxy", None), "use_link", False))
        if live_mode != expected_mode:
            raise RuntimeError(
                "A stable Draft array cannot change between link and copied-shape modes."
            )
        _require_native_property(obj, "ArrayType", str(data["array_kind"]))
        _require_native_property(obj, "NumberX", int(data["number_x"]))
        _require_native_property(obj, "NumberY", int(data["number_y"]))
        _require_native_property(obj, "NumberZ", int(data["number_z"]))
        _require_native_property(
            obj,
            "IntervalX",
            _native_vector(data["interval_x"], "array interval_x"),
        )
        _require_native_property(
            obj,
            "IntervalY",
            _native_vector(data["interval_y"], "array interval_y"),
        )
        _require_native_property(
            obj,
            "IntervalZ",
            _native_vector(data["interval_z"], "array interval_z"),
        )
        _require_native_property(obj, "NumberPolar", int(data["number_polar"]))
        _require_native_property(obj, "Angle", float(data["angle_degrees"]))
        _require_native_property(
            obj,
            "Center",
            _native_vector(data["center"], "polar array center"),
        )
        _require_native_property(
            obj,
            "Axis",
            _native_vector(data["axis"], "array axis"),
        )
        _require_native_property(
            obj,
            "IntervalAxis",
            _native_vector(data["interval_axis"], "polar array axial interval"),
        )
        _require_native_property(
            obj, "RadialDistance", float(data["radial_distance"])
        )
        _require_native_property(
            obj, "TangentialDistance", float(data["tangential_distance"])
        )
        _require_native_property(obj, "NumberCircles", int(data["number_circles"]))
        _require_native_property(obj, "Symmetry", int(data["symmetry"]))
        _require_native_property(obj, "Fuse", bool(data["fuse"]))
        _require_native_property(obj, "Base", _draft_array_target(doc, item, outputs))
        placements = [
            _placement_from_matrix(values)
            for values in list(data["placement_matrices"])
        ]
        if hasattr(obj, "setPropertyStatus"):
            obj.setPropertyStatus("PlacementList", "-Immutable")
        _require_native_property(obj, "PlacementList", placements)
        if hasattr(obj, "setPropertyStatus") and expected_mode:
            obj.setPropertyStatus("PlacementList", "Immutable")
        _require_native_property(obj, "Count", int(data["count"]))
        obj.Placement = validated_placement
    detached = item.get("detached_shape")
    if output_type == "text":
        if detached is not None:
            raise RuntimeError("A native Draft Text output cannot receive a Shape.")
    elif detached is None or not hasattr(obj, "Shape"):
        raise RuntimeError(f"Draft output {item.get('name')!r} has no detached Shape.")
    else:
        obj.Shape = detached

    from draftutils.utils import get_type

    if str(get_type(obj) or "") != str(data["draft_type"]):
        raise RuntimeError(
            f"Published Draft output {item.get('name')!r} changed native proxy type."
        )
    if type(getattr(obj, "Proxy", None)).__name__ != str(data["proxy_class"]):
        raise RuntimeError(
            f"Published Draft output {item.get('name')!r} changed proxy class."
        )
    _assert_matrix(
        obj.Placement, validated_placement, f"Draft output {item.get('name')!r}"
    )
    if output_type == "array":
        if getattr(obj, "Base", None) is not _draft_array_target(doc, item, outputs):
            raise RuntimeError("Published Draft array changed its validated Base link.")
        if int(obj.Count) != int(data["count"]):
            raise RuntimeError(
                "Published Draft array changed its validated element count."
            )
        live_placements = list(getattr(obj, "PlacementList", []) or [])
        if len(live_placements) != len(data["placement_matrices"]):
            raise RuntimeError("Published Draft array changed its placement count.")
        for index, (actual, matrix) in enumerate(
            zip(live_placements, data["placement_matrices"])
        ):
            _assert_matrix(
                actual,
                _placement_from_matrix(matrix),
                f"Draft array element {index}",
            )
    if output_type != "text" and (
        obj.Shape.isNull()
        or not obj.Shape.isValid()
        or str(obj.Shape.ShapeType) != str(item["facts"]["shape_type"])
    ):
        raise RuntimeError(
            f"Published Draft output {item.get('name')!r} changed its validated Shape."
        )
    _add_string_property(
        obj,
        "VibeCADDraftValidation",
        "Isolated native Draft object, property, placement, and Base-link readback.",
    )
    obj.VibeCADDraftValidation = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _configure_surface(obj: Any, item: Mapping[str, Any]) -> None:
    """Apply one detached Surface result without recompute or live OCC construction."""

    data = item.get("surface_data")
    detached = item.get("detached_shape")
    facts = item.get("facts")
    if not isinstance(data, dict) or not isinstance(facts, dict):
        raise RuntimeError(
            f"Surface output {item.get('name')!r} has no validated native readback."
        )
    if detached is None or not hasattr(obj, "Shape"):
        raise RuntimeError(
            f"Surface output {item.get('name')!r} has no detached Shape."
        )
    expected_shape_type = str(facts.get("shape_type") or "")
    if str(getattr(detached, "ShapeType", "") or "") != expected_shape_type:
        raise RuntimeError(
            f"Surface output {item.get('name')!r} detached Shape changed type."
        )
    obj.Shape = detached
    if str(getattr(obj.Shape, "ShapeType", "") or "") != expected_shape_type:
        raise RuntimeError(
            f"Published Surface output {item.get('name')!r} changed OCC ShapeType."
        )
    _add_string_property(
        obj,
        "VibeCADSurfaceOperation",
        "Exact Surface API operation accepted by the isolated worker.",
    )
    _add_string_property(
        obj,
        "VibeCADSurfaceEngine",
        "Native OCC or Surface engine used in the isolated worker.",
    )
    _add_string_property(
        obj,
        "VibeCADSurfaceValidation",
        "Bounded isolated Surface operation and typed-shape readback.",
    )
    obj.VibeCADSurfaceOperation = str(data.get("operation") or "")
    obj.VibeCADSurfaceEngine = str(data.get("engine") or "")
    obj.VibeCADSurfaceValidation = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _configure_sketch(doc: Any, obj: Any, item: Mapping[str, Any]) -> None:
    import FreeCAD as App

    from vibescript_sketcher_worker import (
        populate_sketch_without_solving,
        sketch_external_reference_records,
        sketch_expression_map,
    )

    definition = _definition(item)
    properties = _definition_properties(item)
    validation = item.get("sketch_validation")
    if not isinstance(validation, dict):
        raise RuntimeError("A sketch output has no isolated solver validation.")
    support = properties.get("support")
    if hasattr(obj, "AttachmentSupport"):
        if support is None:
            obj.AttachmentSupport = None
        else:
            if not isinstance(support, dict):
                raise RuntimeError("Validated Sketch support is malformed.")
            support_validation = item.get("sketch_validation", {}).get("support")
            if not isinstance(support_validation, dict):
                raise RuntimeError("Validated Sketch support has no worker resolution.")
            target = _reference_target(doc, support.get("reference"), "Sketch support")
            subelements = [
                str(value)
                for value in list(support_validation.get("resolved_subelements") or [])
            ]
            obj.AttachmentSupport = (target, subelements)
    if properties.get("map_mode") is not None and hasattr(obj, "MapMode"):
        obj.MapMode = str(properties["map_mode"])
    attachment = properties.get("attachment_offset")
    if attachment is not None and hasattr(obj, "AttachmentOffset"):
        if not isinstance(attachment, dict):
            raise RuntimeError("Validated Sketch attachment offset is malformed.")
        position = list(attachment.get("position") or [])
        rotation = list(attachment.get("rotation") or [])
        if len(position) != 3 or len(rotation) != 4:
            raise RuntimeError(
                "Validated Sketch attachment offset has the wrong dimensions."
            )
        obj.AttachmentOffset = App.Placement(
            App.Vector(*(float(value) for value in position)),
            App.Rotation(*(float(value) for value in rotation)),
        )

    external_by_graph = {
        str(value.get("graph_id") or ""): dict(value)
        for value in list(validation.get("external_geometry") or [])
        if isinstance(value, dict) and str(value.get("graph_id") or "")
    }

    def resolve_external(value: Mapping[str, Any]) -> tuple[Any, str, dict[str, Any]]:
        definition_properties = value.get("properties")
        if not isinstance(definition_properties, Mapping):
            raise RuntimeError("Validated external geometry properties are malformed.")
        graph_id = str(definition_properties.get("graph_id") or "")
        expected = external_by_graph.get(graph_id)
        if expected is None:
            raise RuntimeError(
                f"External Sketcher geometry {graph_id!r} has no worker resolution."
            )
        target = _reference_target(
            doc,
            expected.get("reference"),
            f"External Sketcher geometry {graph_id}",
        )
        subelement = str(expected.get("resolved_subelement") or "")
        if not re.fullmatch(r"(?:Edge|Vertex)[1-9][0-9]*", subelement):
            raise RuntimeError(
                f"External Sketcher geometry {graph_id!r} resolved an invalid subelement."
            )
        return target, subelement, dict(expected)

    geometry, constraints, _geometry_indexes, published_external = (
        populate_sketch_without_solving(
            obj,
            definition,
            replace_existing=True,
            external_resolver=resolve_external,
        )
    )
    if published_external != list(validation.get("external_geometry") or []):
        raise RuntimeError(
            "Published Sketcher external geometry differs from worker validation."
        )
    if int(getattr(obj, "GeometryCount", -1)) != int(
        validation.get("native_geometry_count", -2)
    ):
        raise RuntimeError(
            "Published Sketcher geometry count differs from worker validation."
        )
    external_references = sketch_external_reference_records(obj)
    if len(external_references) != int(validation.get("external_geometry_count", -1)):
        raise RuntimeError(
            "Published Sketcher external geometry count differs from worker validation."
        )
    unmatched_references = list(external_references)
    for index, expected in enumerate(published_external):
        expected_target = _reference_target(
            doc,
            expected.get("reference"),
            f"External Sketcher geometry {index}",
        )
        expected_subelement = str(expected.get("resolved_subelement") or "")
        match = next(
            (
                record
                for record in unmatched_references
                if record[0] is expected_target and record[1] == expected_subelement
            ),
            None,
        )
        if match is None:
            raise RuntimeError(
                f"Published Sketcher external geometry {index} changed its native link."
            )
        unmatched_references.remove(match)
    if unmatched_references:
        raise RuntimeError(
            "Published Sketcher external geometry has undeclared native links."
        )
    if int(getattr(obj, "ConstraintCount", -1)) != len(constraints):
        raise RuntimeError(
            "Published Sketcher constraint count differs from worker validation."
        )
    expression_bindings = sketch_expression_map(obj)
    for index, expected in enumerate(list(validation.get("constraints") or [])):
        native = obj.Constraints[index]
        if str(getattr(native, "Type", "") or "") != str(
            expected.get("native_type") or ""
        ):
            raise RuntimeError(
                f"Published Sketcher constraint {index} changed native type."
            )
        if str(getattr(native, "Name", "") or "") != str(expected.get("name") or ""):
            raise RuntimeError(f"Published Sketcher constraint {index} changed name.")
        if bool(getattr(native, "Driving", True)) != bool(
            expected.get("driving", True)
        ):
            raise RuntimeError(
                f"Published Sketcher constraint {index} changed driving state."
            )
        if bool(getattr(native, "IsActive", True)) != bool(
            expected.get("active", True)
        ):
            raise RuntimeError(
                f"Published Sketcher constraint {index} changed active state."
            )
        if bool(getattr(native, "InVirtualSpace", False)) != bool(
            expected.get("virtual", False)
        ):
            raise RuntimeError(
                f"Published Sketcher constraint {index} changed virtual state."
            )
        expression_bound = f"Constraints[{index}]" in expression_bindings
        if expression_bound != bool(expected.get("expression_bound")):
            raise RuntimeError(
                f"Published Sketcher constraint {index} changed expression binding."
            )
    _add_string_property(
        obj,
        "VibeCADSketchValidation",
        "Isolated Sketcher solver, DoF, conflict, and profile diagnostics.",
    )
    obj.VibeCADSketchValidation = json.dumps(
        validation,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _configure_solver_diagnostics(obj: Any, item: Mapping[str, Any]) -> None:
    diagnostics = item.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise RuntimeError("A solver diagnostics output has no validated diagnostics.")
    _add_string_property(obj, "VibeCADSolverStatus", "Isolated native solver status.")
    _add_property(
        obj,
        "App::PropertyInteger",
        "VibeCADSolverCode",
        "Native solver return code from the isolated worker.",
    )
    _add_property(
        obj,
        "App::PropertyInteger",
        "VibeCADJointCount",
        "Validated joint count in the isolated assembly.",
    )
    _add_property(
        obj,
        "App::PropertyInteger",
        "VibeCADComponentCount",
        "Validated component count in the isolated assembly.",
    )
    _add_string_property(
        obj,
        "VibeCADSolverDiagnostics",
        "Complete bounded isolated solver diagnostics as JSON.",
    )
    obj.VibeCADSolverStatus = str(diagnostics.get("status") or "")
    obj.VibeCADSolverCode = int(diagnostics.get("solver_code") or 0)
    obj.VibeCADJointCount = int(diagnostics.get("joint_count") or 0)
    obj.VibeCADComponentCount = int(diagnostics.get("component_count") or 0)
    obj.VibeCADSolverDiagnostics = json.dumps(
        diagnostics,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _configure_reverse_engineering(obj: Any, item: Mapping[str, Any]) -> None:
    data = item.get("reverse_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("A Reverse Engineering output has no validated native data.")
    output_type = str(item.get("type") or "")
    if output_type in _BREP_OUTPUT_TYPES:
        shape = item.get("detached_shape")
        if shape is None or shape.isNull() or not shape.isValid():
            raise RuntimeError("A Reverse Engineering BREP output is not valid.")
        obj.Shape = shape
    elif output_type == "mesh":
        mesh = item.get("detached_mesh")
        if mesh is None or int(mesh.CountFacets) <= 0:
            raise RuntimeError("A Reverse Engineering mesh output is empty.")
        obj.Mesh = mesh
    elif output_type == "fit_metrics":
        metrics = data.get("fit_metrics")
        if not isinstance(metrics, Mapping):
            raise RuntimeError("A fit-metrics output has no validated report.")
        properties = (
            (
                "App::PropertyString",
                "VibeCADTargetOutput",
                str(data.get("target_output") or ""),
                "Stable output name whose fit report is published here.",
            ),
            (
                "App::PropertyString",
                "VibeCADTargetOperation",
                str(data.get("target_operation") or ""),
                "Canonical Reverse Engineering operation measured by this report.",
            ),
            (
                "App::PropertyString",
                "VibeCADTargetOutputType",
                str(data.get("target_output_type") or ""),
                "Declared output type measured by this report.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADSourcePointCount",
                int(metrics.get("source_point_count") or 0),
                "Authenticated source point count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADEvaluatedPointCount",
                int(metrics.get("evaluated_point_count") or 0),
                "Deterministically evaluated source sample count.",
            ),
            (
                "App::PropertyLength",
                "VibeCADMeanFitDistance",
                float(metrics.get("mean_distance") or 0.0),
                "Mean native source-to-result distance.",
            ),
            (
                "App::PropertyLength",
                "VibeCADRMSFitDistance",
                float(metrics.get("rms_distance") or 0.0),
                "Root-mean-square native source-to-result distance.",
            ),
            (
                "App::PropertyLength",
                "VibeCADMaximumFitDistance",
                float(metrics.get("maximum_distance") or 0.0),
                "Maximum native source-to-result distance.",
            ),
            (
                "App::PropertyLength",
                "VibeCADFitTolerance",
                float(metrics.get("tolerance") or 0.0),
                "Tolerance used for the pass fraction.",
            ),
            (
                "App::PropertyPercent",
                "VibeCADWithinTolerance",
                int(
                    round(
                        float(metrics.get("within_tolerance_fraction") or 0.0)
                        * 100.0
                    )
                ),
                "Rounded display percentage of evaluated source points within tolerance.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADWithinToleranceFraction",
                float(metrics.get("within_tolerance_fraction") or 0.0),
                "Full-precision fraction of evaluated source points within tolerance.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADSegmentCount",
                int(metrics.get("segment_count") or 0),
                "Validated segmentation count, or zero for a fitting output.",
            ),
        )
        for property_type, name, value, description in properties:
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    else:
        raise RuntimeError(
            f"No Reverse Engineering publisher exists for output type {output_type!r}."
        )
    _add_string_property(
        obj,
        PROP_REVERSE_VALIDATION,
        "Authenticated Reverse Engineering operation, native facts, and fit metrics.",
    )
    setattr(
        obj,
        PROP_REVERSE_VALIDATION,
        json.dumps(
            dict(data),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _configure_inspection(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> None:
    """Apply precomputed native Inspection state without running its solver."""

    data = item.get("inspection_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("An Inspection output has no validated native data.")
    output_type = str(item.get("type") or "")
    if output_type == "inspection_feature":
        definition = _definition(item)
        arguments = list(definition.get("arguments") or [])
        if len(arguments) != 2 or not isinstance(arguments[1], list):
            raise RuntimeError("An Inspection comparison definition is malformed.")
        actual = _reference_target(doc, arguments[0], "Inspection actual")
        nominals = [
            _reference_target(doc, reference, f"Inspection nominal {index}")
            for index, reference in enumerate(arguments[1])
        ]
        distances = item.get("detached_distances")
        if not isinstance(distances, list) or not distances:
            raise RuntimeError("An Inspection comparison has no detached distances.")
        for property_name in ("Actual", "Nominals", "SearchRadius", "Thickness"):
            setter = getattr(obj, "setPropertyStatus", None)
            if callable(setter):
                setter(property_name, "NoRecompute")
        obj.Actual = actual
        obj.Nominals = nominals
        obj.SearchRadius = float(data["search_radius"])
        obj.Thickness = float(data["thickness"])
        obj.Distances = [float(value) for value in distances]
        observed = [float(value) for value in list(obj.Distances)]
        if observed != distances:
            raise RuntimeError(
                "The live Inspection::Feature changed its precomputed float32 distances."
            )
        summary = data.get("distance_summary")
        if not isinstance(summary, Mapping):
            raise RuntimeError("An Inspection comparison has no distance summary.")
        typed = (
            (
                "App::PropertyFloat",
                "VibeCADToleranceLower",
                float(data["tolerance"][0]),
                "Accepted lower signed deviation tolerance in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADToleranceUpper",
                float(data["tolerance"][1]),
                "Accepted upper signed deviation tolerance in millimetres.",
            ),
            (
                "App::PropertyBool",
                "VibeCADRequireComplete",
                bool(data["require_complete"]),
                "Whether every actual sample must find a nominal within the search radius.",
            ),
            (
                "App::PropertyBool",
                "VibeCADPassed",
                bool(summary["passed"]),
                "Validated aggregate tolerance verdict.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADSampleCount",
                int(summary["sample_count"]),
                "Native actual sample count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADMeasuredCount",
                int(summary["measured_count"]),
                "Samples with a nominal result inside the search radius.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADUnmeasuredCount",
                int(summary["unmeasured_count"]),
                "Samples without a nominal result inside the search radius.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADMinimumDistance",
                float(summary["minimum"] or 0.0),
                "Minimum measured signed distance in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADMaximumDistance",
                float(summary["maximum"] or 0.0),
                "Maximum measured signed distance in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADMeanDistance",
                float(summary["mean"] or 0.0),
                "Mean measured signed distance in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADRMSDistance",
                float(summary["rms"] or 0.0),
                "Root-mean-square measured distance in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADAbsoluteMaximumDistance",
                float(summary["absolute_maximum"] or 0.0),
                "Largest absolute measured deviation in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADWithinToleranceFraction",
                float(summary["within_tolerance_fraction"]),
                "Fraction of measured samples inside the accepted tolerance.",
            ),
        )
        for property_type, name, value, description in typed:
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    elif output_type == "inspection_group":
        member_names = list(data.get("member_outputs") or [])
        members = []
        for name in member_names:
            member = outputs.get(str(name))
            if member is None or str(getattr(member, "TypeId", "")) != "Inspection::Feature":
                raise RuntimeError(
                    f"Inspection group member {name!r} is missing or has the wrong native type."
                )
            members.append(member)
        for current in list(getattr(obj, "Group", []) or []):
            if not any(current is member for member in members):
                obj.removeObject(current)
        for member in members:
            if not any(current is member for current in list(obj.Group or [])):
                obj.addObject(member)
        if [str(member.Name) for member in list(obj.Group or [])] != [
            str(member.Name) for member in members
        ]:
            raise RuntimeError("The live Inspection::Group changed member order.")
        for property_type, name, value, description in (
            (
                "App::PropertyInteger",
                "VibeCADComparisonCount",
                int(data["comparison_count"]),
                "Stable comparison member count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADPassedCount",
                int(data["passed_count"]),
                "Passing comparison count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADFailedCount",
                int(data["failed_count"]),
                "Failing comparison count.",
            ),
            (
                "App::PropertyBool",
                "VibeCADPassed",
                bool(data["passed"]),
                "Aggregate group verdict.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    elif output_type == "measurement":
        target_name = str(data.get("target_output") or "")
        target = outputs.get(target_name)
        if target is None or str(getattr(target, "TypeId", "")) != "Inspection::Feature":
            raise RuntimeError("An Inspection measurement target is unavailable.")
        for property_type, name, value, description in (
            (
                "App::PropertyLink",
                "VibeCADComparison",
                target,
                "Stable comparison supplying this scalar.",
            ),
            (
                "App::PropertyString",
                "VibeCADMetric",
                str(data["metric"]),
                "Canonical scalar metric name.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADValue",
                float(data["value"]),
                "Validated scalar value.",
            ),
            (
                "App::PropertyString",
                "VibeCADUnit",
                str(data["unit"]),
                "Scalar unit: mm, ratio, or count.",
            ),
            (
                "App::PropertyBool",
                "VibeCADPassed",
                bool(data["passed"]),
                "Verdict of the source comparison.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    elif output_type == "report":
        group_name = str(data.get("group_output") or "")
        group = outputs.get(group_name)
        if group is None or str(getattr(group, "TypeId", "")) != "Inspection::Group":
            raise RuntimeError("An Inspection report group is unavailable.")
        for property_type, name, value, description in (
            (
                "App::PropertyLink",
                "VibeCADInspectionGroup",
                group,
                "Stable native Inspection group summarized by this report.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADComparisonCount",
                int(data["comparison_count"]),
                "Reported comparison count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADPassedCount",
                int(data["passed_count"]),
                "Passing comparison count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADFailedCount",
                int(data["failed_count"]),
                "Failing comparison count.",
            ),
            (
                "App::PropertyBool",
                "VibeCADPassed",
                bool(data["passed"]),
                "Aggregate report verdict.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
        _add_string_property(
            obj,
            "VibeCADInspectionEntries",
            "Complete bounded per-comparison report entries as JSON.",
        )
        obj.VibeCADInspectionEntries = json.dumps(
            list(data["entries"]),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    else:
        raise RuntimeError(
            f"No Inspection publisher exists for output type {output_type!r}."
        )
    validation = dict(data)
    if output_type == "inspection_feature":
        validation.update(
            {
                "distance_artifact_schema": str(item.get("artifact_schema") or ""),
                "distance_artifact_sha256": str(item.get("artifact_sha256") or ""),
                "distance_artifact_bytes": int(item.get("artifact_bytes") or 0),
            }
        )
    _add_string_property(
        obj,
        PROP_INSPECTION_VALIDATION,
        "Authenticated native Inspection graph, trace, distances, and verdict.",
    )
    setattr(
        obj,
        PROP_INSPECTION_VALIDATION,
        json.dumps(
            validation,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _robot_placement_matches(actual: Any, expected: Mapping[str, Any]) -> bool:
    position = expected.get("position")
    rotation = expected.get("rotation")
    if (
        not isinstance(position, (list, tuple))
        or len(position) != 3
        or not isinstance(rotation, (list, tuple))
        or len(rotation) != 4
    ):
        return False
    observed_position = [float(value) for value in actual.Base]
    observed_rotation = [float(value) for value in actual.Rotation.Q]
    expected_position = [float(value) for value in position]
    expected_rotation = [float(value) for value in rotation]
    if not all(
        math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)
        for left, right in zip(observed_position, expected_position)
    ):
        return False
    observed_norm = math.sqrt(sum(value * value for value in observed_rotation))
    expected_norm = math.sqrt(sum(value * value for value in expected_rotation))
    if observed_norm <= 1.0e-15 or expected_norm <= 1.0e-15:
        return False
    dot = sum(
        left * right
        for left, right in zip(observed_rotation, expected_rotation)
    ) / (observed_norm * expected_norm)
    return math.isclose(abs(dot), 1.0, rel_tol=1.0e-10, abs_tol=1.0e-10)


def _robot_kinematic_rows(value: Any) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 6:
        raise RuntimeError("A Robot output must contain exactly six kinematic rows.")
    rows: list[list[float]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, list) or len(raw) != 8:
            raise RuntimeError(
                f"Robot kinematic row {index} must contain exactly eight numbers."
            )
        row = [float(item) for item in raw]
        if not all(math.isfinite(item) for item in row):
            raise RuntimeError(f"Robot kinematic row {index} contains a non-finite value.")
        if row[4] not in {-1.0, 1.0} or row[6] > row[5] or row[7] <= 0.0:
            raise RuntimeError(f"Robot kinematic row {index} is inconsistent.")
        rows.append(row)
    return rows


def _robot_trajectory_summary(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "waypoint_count",
        "length",
        "duration",
    }:
        raise RuntimeError(f"{label} has a malformed native trajectory summary.")
    count = value.get("waypoint_count")
    length = value.get("length")
    duration = value.get("duration")
    if (
        type(count) is not int
        or count < 0
        or isinstance(length, bool)
        or not isinstance(length, (int, float))
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(length))
        or not math.isfinite(float(duration))
        or float(length) < 0.0
        or float(duration) < 0.0
    ):
        raise RuntimeError(f"{label} has invalid native trajectory values.")
    return {
        "waypoint_count": count,
        "length": float(length),
        "duration": float(duration),
    }


def _swap_robot_trajectory(obj: Any, trajectory: Any) -> dict[str, dict[str, Any]]:
    import Robot

    raw = Robot.swapPrecomputedTrajectory(obj, trajectory)
    try:
        if not isinstance(raw, Mapping) or set(raw) != {"installed", "displaced"}:
            raise RuntimeError(
                "The native Robot trajectory swap returned malformed state."
            )
        return {
            "installed": _robot_trajectory_summary(
                raw["installed"], "Installed trajectory"
            ),
            "displaced": _robot_trajectory_summary(
                raw["displaced"], "Displaced trajectory"
            ),
        }
    except Exception as validation_error:
        try:
            Robot.swapPrecomputedTrajectory(obj, trajectory)
        except Exception as rollback_error:
            raise RuntimeError(
                f"{validation_error} Native Robot trajectory swap rollback failed: "
                f"{type(rollback_error).__name__}: {rollback_error}"
            ) from validation_error
        raise


def _robot_summary_matches(summary: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    return (
        int(summary.get("waypoint_count", -1)) == int(data.get("waypoint_count", -2))
        and math.isclose(
            float(summary.get("length", -1.0)),
            float(data.get("length", -2.0)),
            rel_tol=1.0e-10,
            abs_tol=1.0e-8,
        )
        and math.isclose(
            float(summary.get("duration", -1.0)),
            float(data.get("duration", -2.0)),
            rel_tol=1.0e-10,
            abs_tol=1.0e-8,
        )
    )


def _configure_robot(
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    trajectory_swaps: list[dict[str, Any]],
) -> None:
    """Install validated native Robot state without recompute or path generation."""

    data = item.get("robot_data")
    if not isinstance(data, Mapping):
        raise RuntimeError("A Robot output has no validated native data.")
    data = dict(data)
    output_type = str(item.get("type") or "")
    if str(data.get("native_type") or getattr(obj, "TypeId", "")) != str(
        getattr(obj, "TypeId", "")
    ) and output_type != "simulation":
        raise RuntimeError("A Robot output changed its validated native type.")

    if output_type == "robot":
        rows = _robot_kinematic_rows(data.get("kinematics"))
        setter = getattr(obj, "setKinematic", None)
        if not callable(setter):
            raise RuntimeError(
                "This FreeCAD build cannot apply in-memory Robot kinematics."
            )
        setter(rows)
        obj.Base = _placement(data["base"])
        obj.Tool = _placement(data["tool"])
        obj.Home = [float(value) for value in data["home"]]
        expected_axes = [float(value) for value in data["axis_positions"]]
        if len(expected_axes) != 6:
            raise RuntimeError("A Robot output must contain exactly six axis positions.")
        for axis, value in enumerate(expected_axes, start=1):
            setattr(obj, f"Axis{axis}", value)
        native = obj.getRobot()
        observed_axes = [
            float(getattr(native, f"Axis{axis}")) for axis in range(1, 7)
        ]
        if (
            any(
                not math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)
                for left, right in zip(observed_axes, expected_axes)
            )
            or not _robot_placement_matches(obj.Base, data["base"])
            or not _robot_placement_matches(obj.Tool, data["tool"])
            or not _robot_placement_matches(obj.Tcp, data["tcp"])
        ):
            raise RuntimeError("The live native Robot state differs from worker validation.")
    elif output_type in {"trajectory", "dressup"}:
        if output_type == "dressup":
            source_name = str(data.get("source_output") or "")
            source = outputs.get(source_name)
            if source is None or str(getattr(source, "TypeId", "")) != (
                "Robot::TrajectoryObject"
            ):
                raise RuntimeError("A Robot dress-up source is unavailable.")
            obj.Source = source
            speed = data.get("speed")
            acceleration = data.get("acceleration")
            continuous = data.get("continuous")
            obj.UseSpeed = speed is not None
            if speed is not None:
                obj.Speed = float(speed)
            obj.UseAcceleration = acceleration is not None
            if acceleration is not None:
                obj.Acceleration = float(acceleration)
            obj.ContType = (
                "DontChange"
                if continuous is None
                else ("Continues" if continuous else "Discontinues")
            )
            obj.AddType = {
                "none": "DontChange",
                "use_orientation": "UseOrientation",
                "add_position": "AddPosition",
                "add_orientation": "AddOrintation",
                "add_position_and_orientation": "AddPositionAndOrientation",
            }[str(data["offset_mode"])]
            obj.PosAdd = _placement(data.get("offset"))
        obj.Base = _placement(data["base"])
        trajectory = item.get("detached_trajectory")
        if trajectory is None:
            raise RuntimeError("A Robot path has no detached precomputed trajectory.")
        swapped = _swap_robot_trajectory(obj, trajectory)
        trajectory_swaps.append(
            {
                "object_name": str(obj.Name),
                "object": obj,
                "holder": trajectory,
                "accepted_summary": dict(swapped["displaced"]),
            }
        )
        if not _robot_summary_matches(swapped["installed"], data):
            raise RuntimeError(
                "The installed native Robot trajectory differs from worker validation."
            )
        for property_type, name, value, description in (
            (
                "App::PropertyInteger",
                "VibeCADWaypointCount",
                int(data["waypoint_count"]),
                "Validated native waypoint count.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADTrajectoryLength",
                float(data["length"]),
                "Validated native trajectory length in millimetres.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADTrajectoryDuration",
                float(data["duration"]),
                "Validated native trajectory duration in seconds.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    elif output_type == "simulation":
        robot_name = str(data.get("robot_output") or "")
        trajectory_name = str(data.get("trajectory_output") or "")
        robot = outputs.get(robot_name)
        trajectory = outputs.get(trajectory_name)
        if robot is None or str(getattr(robot, "TypeId", "")) != "Robot::RobotObject":
            raise RuntimeError("A Robot simulation robot is unavailable.")
        if trajectory is None or str(getattr(trajectory, "TypeId", "")) not in (
            _ROBOT_TRAJECTORY_TYPES
        ):
            raise RuntimeError("A Robot simulation trajectory is unavailable.")
        for property_type, name, value, description in (
            ("App::PropertyLink", "VibeCADRobot", robot, "Simulated native robot."),
            (
                "App::PropertyLink",
                "VibeCADTrajectory",
                trajectory,
                "Simulated native trajectory or dress-up.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADDuration",
                float(data["duration"]),
                "Validated simulation duration in seconds.",
            ),
            (
                "App::PropertyFloat",
                "VibeCADLength",
                float(data["length"]),
                "Validated simulated path length in millimetres.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADSampleCount",
                int(data["sample_count"]),
                "Authenticated simulation sample count.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADReachableCount",
                int(data["reachable_count"]),
                "Samples solved by native inverse kinematics.",
            ),
            (
                "App::PropertyInteger",
                "VibeCADUnreachableCount",
                int(data["unreachable_count"]),
                "Samples rejected by native inverse kinematics.",
            ),
            (
                "App::PropertyBool",
                "VibeCADSamplesLimited",
                bool(data["samples_limited"]),
                "Whether the requested simulation was capped by its sample budget.",
            ),
            (
                "App::PropertyString",
                "VibeCADArtifactSHA256",
                str(item.get("artifact_sha256") or ""),
                "SHA-256 of the authenticated worker simulation samples.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
    else:
        raise RuntimeError(f"No Robot publisher exists for output type {output_type!r}.")

    validation = dict(data)
    if output_type == "simulation":
        validation.update(
            {
                "artifact_schema": str(item.get("artifact_schema") or ""),
                "artifact_sha256": str(item.get("artifact_sha256") or ""),
                "artifact_bytes": int(item.get("artifact_bytes") or 0),
                "sample_width": int(item.get("sample_width") or 0),
            }
        )
    _add_string_property(
        obj,
        PROP_ROBOT_VALIDATION,
        "Authenticated native Robot graph, trajectory facts, and simulation diagnostics.",
    )
    setattr(
        obj,
        PROP_ROBOT_VALIDATION,
        json.dumps(
            validation,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _mesh_assigned_facts(mesh: Any) -> dict[str, Any]:
    box = mesh.BoundBox
    return {
        "points": int(mesh.CountPoints),
        "facets": int(mesh.CountFacets),
        "edges": int(mesh.CountEdges),
        "area_mm2": float(mesh.Area),
        "volume_mm3": float(mesh.Volume),
        "bounds": [
            float(box.XMin),
            float(box.YMin),
            float(box.ZMin),
            float(box.XMax),
            float(box.YMax),
            float(box.ZMax),
        ],
    }


def _mesh_local_facts(mesh: Any) -> dict[str, Any]:
    """Inspect the assigned kernel independently of its document Placement."""

    import FreeCAD as App

    local = mesh.copy()
    local.Placement = App.Placement()
    return _mesh_assigned_facts(local)


def _mesh_assigned_facts_match(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    if any(
        observed.get(key) != expected.get(key) for key in ("points", "facets", "edges")
    ):
        return False
    for key in ("area_mm2", "volume_mm3"):
        if not math.isclose(
            float(observed.get(key, math.nan)),
            float(expected.get(key, math.nan)),
            rel_tol=1.0e-9,
            abs_tol=1.0e-9,
        ):
            return False
    left = list(observed.get("bounds") or [])
    right = list(expected.get("bounds") or [])
    return len(left) == len(right) == 6 and all(
        math.isclose(float(first), float(second), rel_tol=1.0e-9, abs_tol=1.0e-9)
        for first, second in zip(left, right)
    )


def _configure_mesh(
    obj: Any,
    item: Mapping[str, Any],
    *,
    data_key: str = "mesh_data",
    validation_property: str = PROP_MESH_VALIDATION,
) -> None:
    if str(getattr(obj, "TypeId", "") or "") != "Mesh::Feature":
        raise RuntimeError("A stable Mesh output changed native type.")
    detached = item.get("detached_mesh")
    data = item.get(data_key)
    facts = item.get("facts")
    if detached is None or not isinstance(data, dict) or not isinstance(facts, dict):
        raise RuntimeError("A Mesh output has no validated detached native state.")
    import FreeCAD as App

    preserved_placement = App.Placement(obj.Placement)
    obj.Mesh = detached
    obj.Placement = preserved_placement
    expected = {
        "points": int(facts["points"]),
        "facets": int(facts["facets"]),
        "edges": int(facts["edges"]),
        "area_mm2": float(facts["area_mm2"]),
        "volume_mm3": float(facts["volume_mm3"]),
        "bounds": [
            *[float(value) for value in facts["bounds"]["minimum"]],
            *[float(value) for value in facts["bounds"]["maximum"]],
        ],
    }
    if not _mesh_assigned_facts_match(_mesh_local_facts(obj.Mesh), expected):
        raise RuntimeError(
            "Published native Mesh state differs from isolated worker validation."
        )
    _assert_matrix(obj.Placement, preserved_placement, "Mesh output placement")
    _add_string_property(
        obj,
        validation_property,
        "Validated isolated native mesh topology and conversion diagnostics.",
    )
    setattr(
        obj,
        validation_property,
        json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )


def _configure_meshpart_shape(obj: Any, item: Mapping[str, Any]) -> None:
    if str(getattr(obj, "TypeId", "") or "") != "Part::Feature":
        raise RuntimeError("A stable MeshPart BREP output changed native type.")
    detached = item.get("detached_shape")
    data = item.get("meshpart_data")
    if detached is None or not isinstance(data, dict):
        raise RuntimeError("A MeshPart BREP output has no validated detached state.")
    import FreeCAD as App

    preserved_placement = App.Placement(obj.Placement)
    candidate = detached.copy()
    candidate.Placement = preserved_placement
    obj.Shape = candidate
    obj.Placement = preserved_placement
    if (
        obj.Shape.isNull()
        or not obj.Shape.isValid()
        or str(obj.Shape.ShapeType) != str(candidate.ShapeType)
        or not bool(obj.Shape.isSame(candidate))
    ):
        raise RuntimeError(
            "Published MeshPart BREP differs from isolated worker validation."
        )
    _assert_matrix(obj.Placement, preserved_placement, "MeshPart output placement")
    _add_string_property(
        obj,
        PROP_MESHPART_VALIDATION,
        "Validated isolated native MeshPart conversion diagnostics.",
    )
    setattr(
        obj,
        PROP_MESHPART_VALIDATION,
        json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )


def _points_kernel_facts(
    kernel: Any,
    sample_indices: list[int],
) -> dict[str, Any]:
    """Read bounded native facts without materializing the complete point array."""

    count = int(kernel.CountPoints)
    if any(
        isinstance(index, bool) or type(index) is not int or not 1 <= index <= count
        for index in sample_indices
    ):
        raise RuntimeError("Points validation contains an invalid sample index.")
    box = kernel.BoundBox
    sampled = (
        list(kernel.fromSegment([index - 1 for index in sample_indices]).Points)
        if sample_indices
        else []
    )
    return {
        "points": count,
        "bounds": {
            "minimum": [float(box.XMin), float(box.YMin), float(box.ZMin)],
            "maximum": [float(box.XMax), float(box.YMax), float(box.ZMax)],
            "size": [float(box.XLength), float(box.YLength), float(box.ZLength)],
        },
        "sample": [
            [float(point.x), float(point.y), float(point.z)] for point in sampled
        ],
        "sample_indices": list(sample_indices),
    }


def _points_bounded_facts_match(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    if observed.get("points") != expected.get("points"):
        return False
    if observed.get("sample_indices") != expected.get("sample_indices"):
        return False

    def vectors_match(left: Any, right: Any) -> bool:
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right) == 3
            and all(
                math.isclose(
                    float(first),
                    float(second),
                    rel_tol=1.0e-9,
                    abs_tol=1.0e-7,
                )
                for first, second in zip(left, right)
            )
        )

    observed_bounds = observed.get("bounds")
    expected_bounds = expected.get("bounds")
    if not isinstance(observed_bounds, Mapping) or not isinstance(
        expected_bounds, Mapping
    ):
        return False
    if not all(
        vectors_match(observed_bounds.get(name), expected_bounds.get(name))
        for name in ("minimum", "maximum", "size")
    ):
        return False
    left_sample = observed.get("sample")
    right_sample = expected.get("sample")
    return (
        isinstance(left_sample, list)
        and isinstance(right_sample, list)
        and len(left_sample) == len(right_sample)
        and all(
            vectors_match(first, second)
            for first, second in zip(left_sample, right_sample)
        )
    )


def _ensure_points_property(
    obj: Any,
    name: str,
    property_type: str,
    description: str,
) -> None:
    if name not in _properties(obj):
        obj.addProperty(property_type, name, "VibeCAD", description)
    observed_type = str(obj.getTypeIdOfProperty(name) or "")
    if observed_type != property_type:
        raise RuntimeError(
            f"Stable Points output property {name!r} has native type "
            f"{observed_type!r}; expected {property_type!r}."
        )


def _configure_points(obj: Any, item: Mapping[str, Any]) -> None:
    if str(getattr(obj, "TypeId", "") or "") != "Points::Feature":
        raise RuntimeError("A stable Points output changed native type.")
    detached = item.get("detached_points")
    attributes = item.get("point_attributes")
    facts = item.get("facts")
    data = item.get("points_data")
    if (
        detached is None
        or not isinstance(attributes, dict)
        or not isinstance(facts, dict)
        or not isinstance(data, dict)
    ):
        raise RuntimeError("A Points output has no validated detached native state.")
    import FreeCAD as App

    preserved_placement = App.Placement(obj.Placement)
    obj.Points = detached
    obj.Placement = App.Placement()
    try:
        observed = _points_kernel_facts(
            obj.Points,
            list(facts.get("sample_indices") or []),
        )
    finally:
        obj.Placement = preserved_placement
    if not _points_bounded_facts_match(observed, facts):
        raise RuntimeError(
            "Published native Points state differs from isolated worker validation."
        )
    _assert_matrix(obj.Placement, preserved_placement, "Points output placement")

    property_contracts = {
        "colors": (
            "Color",
            "App::PropertyColorList",
            "Validated per-point RGBA colors.",
        ),
        "intensities": (
            "Intensity",
            "Points::PropertyGreyValueList",
            "Validated per-point scalar intensities.",
        ),
        "normals": (
            "Normal",
            "Points::PropertyNormalList",
            "Validated per-point unit normals.",
        ),
    }
    for attribute_name, (property_name, property_type, description) in (
        property_contracts.items()
    ):
        values = list(attributes.get(attribute_name) or [])
        if values or property_name in _properties(obj):
            _ensure_points_property(obj, property_name, property_type, description)
        if property_name not in _properties(obj):
            continue
        if attribute_name == "normals":
            setattr(
                obj,
                property_name,
                [App.Vector(*(float(component) for component in value)) for value in values],
            )
        else:
            setattr(obj, property_name, values)
        if len(getattr(obj, property_name)) != len(values):
            raise RuntimeError(
                f"Published Points attribute {attribute_name!r} changed length."
            )

    structured = facts.get("structured")
    for property_name in ("Width", "Height"):
        if structured is not None or property_name in _properties(obj):
            _ensure_points_property(
                obj,
                property_name,
                "App::PropertyInteger",
                "Validated structured point-cloud dimension; zero means unstructured.",
            )
        if property_name in _properties(obj):
            setattr(
                obj,
                property_name,
                int(dict(structured or {}).get(property_name.lower()) or 0),
            )
    if structured is not None and int(obj.Width) * int(obj.Height) != int(
        facts["points"]
    ):
        raise RuntimeError("Published Points structured dimensions are inconsistent.")

    _add_string_property(
        obj,
        PROP_POINTS_VALIDATION,
        "Validated isolated point-cloud source, pipeline, attributes, and native facts.",
    )
    setattr(
        obj,
        PROP_POINTS_VALIDATION,
        json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
    )


def _points_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Capture complete accepted Points::Feature state for explicit rollback."""

    states = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "") or "") != "Points::Feature":
            continue
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_POINTS_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"Points object {obj.Name!r} has {len(property_names)} properties; "
                f"the rollback limit is {_MAX_POINTS_ROLLBACK_PROPERTIES}."
            )
        properties = {}
        property_bytes = 0
        controlled_properties = {}
        for name in ("Color", "Intensity", "Normal", "Width", "Height"):
            if name not in _properties(obj):
                continue
            raw = getattr(obj, name)
            if name == "Normal":
                value = [
                    (float(item.x), float(item.y), float(item.z))
                    for item in list(raw or [])
                ]
            elif name == "Color":
                value = [
                    tuple(float(component) for component in item)
                    for item in list(raw or [])
                ]
            elif name == "Intensity":
                value = [float(item) for item in list(raw or [])]
            else:
                value = int(raw)
            controlled_properties[name] = {
                "type": str(obj.getTypeIdOfProperty(name) or ""),
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "value": value,
            }
        for name in property_names:
            if name in {
                "Points",
                "ExpressionEngine",
                "Color",
                "Intensity",
                "Normal",
                "Width",
                "Height",
            }:
                continue
            try:
                content = bytes(obj.dumpPropertyContent(name))
            except Exception as exc:
                raise RuntimeError(
                    f"Points object {obj.Name!r} property {name!r} cannot be "
                    f"captured for rollback: {type(exc).__name__}: {exc}"
                ) from exc
            property_bytes += len(content)
            if property_bytes > _MAX_POINTS_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"Points object {obj.Name!r} rollback properties exceed "
                    f"{_MAX_POINTS_ROLLBACK_PROPERTY_BYTES} serialized bytes."
                )
            properties[name] = {
                "type": str(obj.getTypeIdOfProperty(name) or ""),
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
            }
        kernel = obj.Points.copy()
        count = int(kernel.CountPoints)
        sample_indices = list(range(1, min(4, count) + 1))
        sample_indices.extend(
            index
            for index in range(max(1, count - 3), count + 1)
            if index not in sample_indices
        )
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "label": str(obj.Label),
                "points": kernel,
                "facts": _points_kernel_facts(kernel, sample_indices),
                "properties": properties,
                "controlled_properties": controlled_properties,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _restore_points_rollback_states(states: list[dict[str, Any]]) -> list[str]:
    failures = []
    restored = []
    resolved = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        obj = document.getObject(name)
        try:
            if obj is None:
                obj = document.addObject("Points::Feature", name)
            if (
                obj is None
                or str(obj.Name) != name
                or str(obj.TypeId) != "Points::Feature"
            ):
                raise RuntimeError("native Points identity could not be restored")
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for obj, state in resolved:
        name = str(state["name"])
        try:
            obj.Points = state["points"].copy()
            for property_name, captured in state["properties"].items():
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            controlled = dict(state["controlled_properties"])
            for property_name in ("Color", "Intensity", "Normal", "Width", "Height"):
                captured = controlled.get(property_name)
                if captured is None:
                    if property_name in _properties(obj):
                        obj.removeProperty(property_name)
                    continue
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
                if str(obj.getTypeIdOfProperty(property_name) or "") != str(
                    captured["type"]
                ):
                    raise RuntimeError(
                        f"controlled property {property_name!r} changed native type"
                    )
                value = captured["value"]
                if property_name == "Normal":
                    import FreeCAD as App

                    value = [App.Vector(*item) for item in value]
                setattr(obj, property_name, value)
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError(
                    "restored Points expressions do not match accepted state"
                )
            if not _points_bounded_facts_match(
                _points_kernel_facts(
                    obj.Points,
                    list(state["facts"]["sample_indices"]),
                ),
                state["facts"],
            ):
                raise RuntimeError(
                    "restored native Points do not match accepted state"
                )
            restored.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Points operation failed and accepted state could not be fully restored: "
            f"{'; '.join(failures)}"
        )
    return restored


def _mesh_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    states = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "") or "") != "Mesh::Feature":
            continue
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_MESH_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"Mesh object {obj.Name!r} has {len(property_names)} properties; "
                f"the rollback limit is {_MAX_MESH_ROLLBACK_PROPERTIES}."
            )
        properties = {}
        property_bytes = 0
        for name in property_names:
            if name in {"Mesh", "ExpressionEngine"}:
                continue
            try:
                content = bytes(obj.dumpPropertyContent(name))
            except Exception as exc:
                raise RuntimeError(
                    f"Mesh object {obj.Name!r} property {name!r} cannot be captured "
                    f"for rollback: {type(exc).__name__}: {exc}"
                ) from exc
            property_bytes += len(content)
            if property_bytes > _MAX_MESH_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"Mesh object {obj.Name!r} rollback properties exceed "
                    f"{_MAX_MESH_ROLLBACK_PROPERTY_BYTES} serialized bytes."
                )
            properties[name] = {
                "type": str(obj.getTypeIdOfProperty(name) or ""),
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
            }
        missing_managed = [
            name
            for name in _MESH_ROLLBACK_PROPERTIES
            if name in _properties(obj) and name not in properties
        ]
        if missing_managed:
            raise RuntimeError(
                f"Mesh object {obj.Name!r} managed properties were not captured: "
                f"{missing_managed}."
            )
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "label": str(obj.Label),
                "mesh": obj.Mesh.copy(),
                "facts": _mesh_assigned_facts(obj.Mesh),
                "properties": properties,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _restore_mesh_rollback_states(states: list[dict[str, Any]]) -> list[str]:
    failures = []
    restored = []
    resolved = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        obj = document.getObject(name)
        try:
            if obj is None:
                obj = document.addObject("Mesh::Feature", name)
            if (
                obj is None
                or str(obj.Name) != name
                or str(obj.TypeId) != "Mesh::Feature"
            ):
                raise RuntimeError("native Mesh identity could not be restored")
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for obj, state in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            obj.Mesh = state["mesh"].copy()
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError(
                    "restored Mesh expressions do not match accepted state"
                )
            if not _mesh_assigned_facts_match(
                _mesh_assigned_facts(obj.Mesh), state["facts"]
            ):
                raise RuntimeError("restored native Mesh does not match accepted state")
            restored.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Mesh operation failed and accepted assigned state could not be fully "
            f"restored: {'; '.join(failures)}"
        )
    return restored


def _meshpart_shape_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Capture complete accepted Part::Feature state for explicit rollback."""

    states = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "") or "") != "Part::Feature":
            continue
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_MESH_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"MeshPart object {obj.Name!r} has {len(property_names)} properties; "
                f"the rollback limit is {_MAX_MESH_ROLLBACK_PROPERTIES}."
            )
        properties = {}
        property_bytes = 0
        for name in property_names:
            if name in {"Shape", "ExpressionEngine"}:
                continue
            try:
                content = bytes(obj.dumpPropertyContent(name))
            except Exception as exc:
                raise RuntimeError(
                    f"MeshPart object {obj.Name!r} property {name!r} cannot be "
                    f"captured for rollback: {type(exc).__name__}: {exc}"
                ) from exc
            property_bytes += len(content)
            if property_bytes > _MAX_MESH_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"MeshPart object {obj.Name!r} rollback properties exceed "
                    f"{_MAX_MESH_ROLLBACK_PROPERTY_BYTES} serialized bytes."
                )
            properties[name] = {
                "type": str(obj.getTypeIdOfProperty(name) or ""),
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
            }
        shape = obj.Shape.copy()
        if shape.isNull() or not shape.isValid():
            raise RuntimeError(
                f"MeshPart object {obj.Name!r} has no valid accepted Shape to roll back."
            )
        brep = shape.exportBrepToString()
        brep_bytes = brep.encode("utf-8")
        if len(brep_bytes) > _MAX_SHAPE_ROLLBACK_BREP_BYTES:
            raise RuntimeError(
                f"MeshPart object {obj.Name!r} accepted BREP exceeds the bounded "
                "rollback serialization limit."
            )
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "label": str(obj.Label),
                "shape": shape,
                "shape_type": str(shape.ShapeType),
                "shape_brep_sha256": hashlib.sha256(brep_bytes).hexdigest(),
                "properties": properties,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _restore_meshpart_shape_rollback_states(
    states: list[dict[str, Any]],
) -> list[str]:
    failures = []
    restored = []
    resolved = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        obj = document.getObject(name)
        try:
            if obj is None:
                obj = document.addObject("Part::Feature", name)
            if (
                obj is None
                or str(obj.Name) != name
                or str(obj.TypeId) != "Part::Feature"
            ):
                raise RuntimeError("native Part identity could not be restored")
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    for obj, state in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            obj.Shape = state["shape"].copy()
            restored_brep_sha256 = hashlib.sha256(
                obj.Shape.exportBrepToString().encode("utf-8")
            ).hexdigest()
            if (
                obj.Shape.isNull()
                or not obj.Shape.isValid()
                or str(obj.Shape.ShapeType) != state["shape_type"]
                or restored_brep_sha256 != state["shape_brep_sha256"]
            ):
                raise RuntimeError("restored native Shape does not match accepted state")
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError(
                    "restored MeshPart expressions do not match accepted state"
                )
            restored.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "MeshPart operation failed and accepted Part state could not be fully "
            f"restored: {'; '.join(failures)}"
        )
    return restored


def _reverse_feature_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Capture stable fit-metrics carriers for explicit rollback/recreation."""

    states = []
    for obj in objects:
        if str(getattr(obj, "TypeId", "") or "") != "App::FeaturePython":
            continue
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_MESH_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"Reverse Engineering metrics object {obj.Name!r} has too many "
                "properties for bounded rollback."
            )
        properties = {}
        property_bytes = 0
        for name in property_names:
            if name == "ExpressionEngine":
                continue
            content = bytes(obj.dumpPropertyContent(name))
            property_bytes += len(content)
            if property_bytes > _MAX_MESH_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"Reverse Engineering metrics object {obj.Name!r} rollback "
                    "properties exceed the bounded serialization limit."
                )
            properties[name] = {
                "type": str(obj.getTypeIdOfProperty(name) or ""),
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
                "content_sha256": _property_content_sha256(content),
            }
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "label": str(obj.Label),
                "properties": properties,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _restore_reverse_feature_rollback_states(
    states: list[dict[str, Any]],
) -> list[str]:
    failures = []
    restored = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        try:
            obj = document.getObject(name)
            if obj is None:
                obj = document.addObject("App::FeaturePython", name)
            if (
                obj is None
                or str(obj.Name) != name
                or str(obj.TypeId) != "App::FeaturePython"
            ):
                raise RuntimeError("native fit-metrics identity could not be restored")
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
            accepted_names = set(state["properties"])
            for property_name in list(_properties(obj)):
                if (
                    property_name not in accepted_names
                    and property_name != "ExpressionEngine"
                    and str(obj.getGroupOfProperty(property_name) or "") == "VibeCAD"
                ):
                    obj.removeProperty(property_name)
            for property_name, captured in state["properties"].items():
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            for property_name, captured in state["properties"].items():
                if _property_content_sha256(
                    bytes(obj.dumpPropertyContent(property_name))
                ) != str(captured["content_sha256"]):
                    raise RuntimeError(
                        f"restored property {property_name!r} differs from accepted state"
                    )
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError("restored fit-metrics expressions do not match")
            restored.append(name)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Reverse Engineering fit-metrics state could not be fully restored: "
            + "; ".join(failures)
        )
    return restored


def _inspection_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Capture every accepted Inspection object for explicit bounded rollback."""

    states = []
    for obj in objects:
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id not in {
            "Inspection::Feature",
            "Inspection::Group",
            "App::FeaturePython",
        }:
            continue
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_INSPECTION_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"Inspection object {obj.Name!r} has {len(property_names)} "
                "properties; the rollback limit is "
                f"{_MAX_INSPECTION_ROLLBACK_PROPERTIES}."
            )
        excluded = {"ExpressionEngine", "Label"}
        if type_id == "Inspection::Feature":
            excluded.update(_INSPECTION_FEATURE_KERNEL_PROPERTIES)
        elif type_id == "Inspection::Group":
            excluded.add("Group")
        properties = {}
        property_bytes = 0
        for name in property_names:
            if name in excluded:
                continue
            try:
                content = bytes(obj.dumpPropertyContent(name))
            except Exception as exc:
                raise RuntimeError(
                    f"Inspection object {obj.Name!r} property {name!r} cannot be "
                    f"captured for rollback: {type(exc).__name__}: {exc}"
                ) from exc
            property_bytes += len(content)
            if property_bytes > _MAX_INSPECTION_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"Inspection object {obj.Name!r} rollback properties exceed "
                    f"{_MAX_INSPECTION_ROLLBACK_PROPERTY_BYTES} serialized bytes."
                )
            property_type = str(obj.getTypeIdOfProperty(name) or "")
            captured = {
                "type": property_type,
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
                "content_sha256": _property_content_sha256(content),
                "deferred_link": property_type.startswith("App::PropertyLink"),
            }
            if property_type == "App::PropertyFloat":
                captured["exact_value"] = float(getattr(obj, name))
            elif property_type == "App::PropertyInteger":
                captured["exact_value"] = int(getattr(obj, name))
            elif property_type == "App::PropertyBool":
                captured["exact_value"] = bool(getattr(obj, name))
            elif property_type == "App::PropertyString":
                captured["exact_value"] = str(getattr(obj, name))
            properties[name] = captured

        kernel: dict[str, Any] = {}
        if type_id == "Inspection::Feature":
            if not _inspection_feature_is_frozen(obj):
                raise RuntimeError(
                    f"Inspection feature {obj.Name!r} is not protected from "
                    "synchronous recompute."
                )
            distances = array("f", (float(value) for value in obj.Distances))
            if not 1 <= len(distances) <= _MAX_INSPECTION_ROLLBACK_DISTANCES:
                raise RuntimeError(
                    f"Inspection feature {obj.Name!r} has {len(distances)} distances; "
                    "the rollback limit is "
                    f"{_MAX_INSPECTION_ROLLBACK_DISTANCES}."
                )
            actual = getattr(obj, "Actual", None)
            nominals = list(getattr(obj, "Nominals", []) or [])
            if actual is None or not nominals:
                raise RuntimeError(
                    f"Inspection feature {obj.Name!r} has no accepted actual/nominal graph."
                )
            kernel = {
                "actual": str(actual.Name),
                "nominals": [str(item.Name) for item in nominals],
                "search_radius": float(obj.SearchRadius),
                "thickness": float(obj.Thickness),
                "distances": distances,
                "distance_sha256": hashlib.sha256(distances.tobytes()).hexdigest(),
                "frozen": True,
            }
        elif type_id == "Inspection::Group":
            kernel = {
                "members": [
                    str(item.Name) for item in list(getattr(obj, "Group", []) or [])
                ]
            }
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "type_id": type_id,
                "label": str(obj.Label),
                "properties": properties,
                "kernel": kernel,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _restore_inspection_rollback_states(
    states: list[dict[str, Any]],
) -> list[str]:
    """Restore/recreate an accepted Inspection graph without native recompute."""

    import Inspection

    del Inspection
    failures: list[str] = []
    resolved: list[tuple[Any, dict[str, Any]]] = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        type_id = str(state["type_id"])
        try:
            obj = document.getObject(name)
            if obj is None:
                obj = document.addObject(type_id, name)
            if (
                obj is None
                or str(obj.Name) != name
                or str(obj.TypeId) != type_id
            ):
                raise RuntimeError(
                    f"native identity/type {type_id!r} could not be restored"
                )
            if type_id == "Inspection::Feature":
                _unfreeze_inspection_feature(obj)
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
                if str(obj.getTypeIdOfProperty(property_name) or "") != str(
                    captured["type"]
                ):
                    raise RuntimeError(
                        f"property {property_name!r} changed native type"
                    )
            accepted_names = set(state["properties"])
            for property_name in list(_properties(obj)):
                if (
                    property_name not in accepted_names
                    and property_name not in _INSPECTION_FEATURE_KERNEL_PROPERTIES
                    and property_name not in {"ExpressionEngine", "Group", "Label"}
                    and str(obj.getGroupOfProperty(property_name) or "") == "VibeCAD"
                ):
                    obj.removeProperty(property_name)
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                if captured["deferred_link"]:
                    continue
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                if "exact_value" in captured:
                    setattr(obj, property_name, captured["exact_value"])
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state in resolved:
        name = str(state["name"])
        try:
            if str(state["type_id"]) == "Inspection::Feature":
                kernel = state["kernel"]
                actual = obj.Document.getObject(str(kernel["actual"]))
                nominals = [
                    obj.Document.getObject(str(item)) for item in kernel["nominals"]
                ]
                if actual is None or any(item is None for item in nominals):
                    raise RuntimeError("accepted actual/nominal links disappeared")
                for property_name in (
                    "Actual",
                    "Nominals",
                    "SearchRadius",
                    "Thickness",
                ):
                    obj.setPropertyStatus(property_name, "NoRecompute")
                obj.Actual = actual
                obj.Nominals = nominals
                obj.SearchRadius = float(kernel["search_radius"])
                obj.Thickness = float(kernel["thickness"])
                obj.Distances = kernel["distances"].tolist()
            for property_name, captured in state["properties"].items():
                if not captured["deferred_link"]:
                    continue
                obj.restorePropertyContent(
                    property_name,
                    bytearray(captured["content"]),
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state in resolved:
        name = str(state["name"])
        try:
            if str(state["type_id"]) == "Inspection::Group":
                members = [
                    obj.Document.getObject(str(item))
                    for item in state["kernel"]["members"]
                ]
                if any(item is None for item in members):
                    raise RuntimeError("accepted group membership disappeared")
                for current in list(getattr(obj, "Group", []) or []):
                    if not any(current is member for member in members):
                        obj.removeObject(current)
                for member in members:
                    if not any(current is member for current in list(obj.Group or [])):
                        obj.addObject(member)
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            if str(state["type_id"]) == "Inspection::Feature":
                _freeze_inspection_feature(obj)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                if _property_content_sha256(
                    bytes(obj.dumpPropertyContent(property_name))
                ) != str(captured["content_sha256"]):
                    raise RuntimeError(
                        f"restored property {property_name!r} differs from accepted state"
                    )
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError("restored expressions differ from accepted state")
            if str(obj.Label) != str(state["label"]):
                raise RuntimeError("restored label differs from accepted state")
            if str(state["type_id"]) == "Inspection::Feature":
                kernel = state["kernel"]
                observed = array("f", (float(value) for value in obj.Distances))
                if (
                    str(getattr(obj.Actual, "Name", "")) != kernel["actual"]
                    or [str(item.Name) for item in list(obj.Nominals or [])]
                    != kernel["nominals"]
                    or float(obj.SearchRadius) != float(kernel["search_radius"])
                    or float(obj.Thickness) != float(kernel["thickness"])
                    or len(observed) != len(kernel["distances"])
                    or hashlib.sha256(observed.tobytes()).hexdigest()
                    != kernel["distance_sha256"]
                    or not _inspection_feature_is_frozen(obj)
                ):
                    raise RuntimeError(
                        "restored native distance state differs from accepted state"
                    )
            elif str(state["type_id"]) == "Inspection::Group":
                if [str(item.Name) for item in list(obj.Group or [])] != state[
                    "kernel"
                ]["members"]:
                    raise RuntimeError(
                        "restored native group membership differs from accepted state"
                    )
            restored_state = str(
                getattr(obj, reference_contracts.PROP_DERIVED_STATE, "") or ""
            )
            if restored_state and restored_state not in {"accepted", "stale"}:
                raise RuntimeError(
                    f"restored derived state {restored_state!r} is invalid"
                )
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Inspection operation failed and accepted native state could not be "
            f"fully restored: {'; '.join(failures)}"
        )
    return [str(state["name"]) for _obj, state in resolved]


def _robot_placement_state(value: Any) -> dict[str, list[float]]:
    return {
        "position": [float(item) for item in value.Base],
        "rotation": [float(item) for item in value.Rotation.Q],
    }


def _robot_definition_kinematics(value: Any, object_name: str) -> list[list[float]]:
    if not isinstance(value, Mapping):
        raise RuntimeError(
            f"Robot object {object_name!r} has no accepted declarative definition."
        )
    properties = value.get("properties")
    axes = properties.get("kinematics") if isinstance(properties, Mapping) else None
    if not isinstance(axes, list) or len(axes) != 6:
        raise RuntimeError(
            f"Robot object {object_name!r} has malformed accepted kinematics."
        )
    fields = (
        "a",
        "alpha",
        "d",
        "theta",
        "rotation_direction",
        "maximum_angle",
        "minimum_angle",
        "maximum_velocity",
    )
    rows = []
    for index, axis in enumerate(axes):
        if not isinstance(axis, Mapping) or set(axis) != set(fields):
            raise RuntimeError(
                f"Robot object {object_name!r} kinematic row {index} is malformed."
            )
        rows.append([float(axis[field]) for field in fields])
    return _robot_kinematic_rows(rows)


def _robot_exact_property_value(
    obj: Any, name: str, property_type: str
) -> Any | None:
    if property_type in {
        "App::PropertyFloat",
        "App::PropertyAngle",
        "App::PropertyDistance",
        "App::PropertyLength",
        "App::PropertySpeed",
    }:
        return {"kind": "float", "value": float(getattr(obj, name))}
    if property_type in {"App::PropertyInteger", "App::PropertyIntegerConstraint"}:
        return {"kind": "integer", "value": int(getattr(obj, name))}
    if property_type == "App::PropertyBool":
        return {"kind": "bool", "value": bool(getattr(obj, name))}
    if property_type in {
        "App::PropertyString",
        "App::PropertyEnumeration",
        "App::PropertyFile",
        "App::PropertyFileIncluded",
    }:
        return {"kind": "string", "value": str(getattr(obj, name))}
    if property_type in {"App::PropertyFloatList", "App::PropertyAngleList"}:
        return {
            "kind": "float_list",
            "value": [float(item) for item in getattr(obj, name)],
        }
    if property_type == "App::PropertyStringList":
        return {
            "kind": "string_list",
            "value": [str(item) for item in getattr(obj, name)],
        }
    if property_type == "App::PropertyPlacement":
        return {
            "kind": "placement",
            "value": _robot_placement_state(getattr(obj, name)),
        }
    if property_type == "App::PropertyVector":
        return {
            "kind": "vector",
            "value": [float(item) for item in getattr(obj, name)],
        }
    return None


def _restore_robot_exact_property(obj: Any, name: str, captured: Any) -> None:
    if not isinstance(captured, Mapping):
        return
    kind = str(captured.get("kind") or "")
    value = captured.get("value")
    if kind in {"float", "integer", "bool", "string", "float_list", "string_list"}:
        setattr(obj, name, value)
    elif kind == "placement":
        setattr(obj, name, _placement(value))
    elif kind == "vector":
        setattr(obj, name, _native_vector(value, f"rollback property {name}"))


def _robot_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    """Capture every accepted Robot-domain output for exact rollback."""

    allowed = {
        "Robot::RobotObject",
        "Robot::TrajectoryObject",
        "Robot::TrajectoryDressUpObject",
        "App::FeaturePython",
        "App::Link",
    }
    states = []
    for obj in objects:
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id not in allowed:
            raise RuntimeError(
                f"Robot program object {obj.Name!r} has unsupported native type {type_id!r}."
            )
        if type_id == "Robot::TrajectoryDressUpObject" and not _robot_dressup_is_frozen(
            obj
        ):
            raise RuntimeError(
                f"Robot dress-up {obj.Name!r} is not protected from synchronous recompute."
            )
        if type_id == "Robot::RobotObject":
            for property_name in ("RobotKinematicFile", "RobotVrmlFile"):
                if str(getattr(obj, property_name, "") or ""):
                    raise RuntimeError(
                        f"Robot object {obj.Name!r} contains an external file in "
                        f"{property_name}; VibeScript Robot objects must remain self-contained."
                    )
        property_names = list(getattr(obj, "PropertiesList", []) or [])
        if len(property_names) > _MAX_ROBOT_ROLLBACK_PROPERTIES:
            raise RuntimeError(
                f"Robot object {obj.Name!r} has {len(property_names)} properties; "
                f"the rollback limit is {_MAX_ROBOT_ROLLBACK_PROPERTIES}."
            )
        excluded = {"ExpressionEngine", "Label", "Trajectory"}
        if type_id == "Robot::RobotObject":
            excluded.update({"Tcp", *(f"Axis{axis}" for axis in range(1, 7))})
        properties: dict[str, dict[str, Any]] = {}
        property_bytes = 0
        for name in property_names:
            if name in excluded:
                continue
            try:
                content = bytes(obj.dumpPropertyContent(name))
            except Exception as exc:
                raise RuntimeError(
                    f"Robot object {obj.Name!r} property {name!r} cannot be captured "
                    f"for rollback: {type(exc).__name__}: {exc}"
                ) from exc
            property_bytes += len(content)
            if property_bytes > _MAX_ROBOT_ROLLBACK_PROPERTY_BYTES:
                raise RuntimeError(
                    f"Robot object {obj.Name!r} rollback properties exceed "
                    f"{_MAX_ROBOT_ROLLBACK_PROPERTY_BYTES} serialized bytes."
                )
            property_type = str(obj.getTypeIdOfProperty(name) or "")
            properties[name] = {
                "type": property_type,
                "group": str(obj.getGroupOfProperty(name) or ""),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "editor_modes": list(obj.getEditorMode(name) or []),
                "content": content,
                "content_sha256": _property_content_sha256(content),
                "deferred_link": property_type.startswith("App::PropertyLink"),
                "exact_value": _robot_exact_property_value(obj, name, property_type),
            }
        definition: dict[str, Any] | None = None
        if PROP_DEFINITION in _properties(obj):
            try:
                parsed = json.loads(str(getattr(obj, PROP_DEFINITION) or ""))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Robot object {obj.Name!r} has malformed accepted metadata."
                ) from exc
            if isinstance(parsed, dict):
                definition = parsed
        kernel: dict[str, Any] = {}
        if type_id == "Robot::RobotObject":
            kernel = {
                "kinematics": _robot_definition_kinematics(definition, str(obj.Name)),
                "axis_positions": [
                    float(getattr(obj, f"Axis{axis}")) for axis in range(1, 7)
                ],
                "tcp": _robot_placement_state(obj.Tcp),
            }
        states.append(
            {
                "document": obj.Document,
                "name": str(obj.Name),
                "type_id": type_id,
                "label": str(obj.Label),
                "properties": properties,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
                "kernel": kernel,
                "frozen": type_id == "Robot::TrajectoryDressUpObject",
            }
        )
    return states


def _extract_robot_trajectories(objects: list[Any]) -> list[dict[str, Any]]:
    """Move accepted trajectories into transient holders before native deletion."""

    import Robot

    extracted: list[dict[str, Any]] = []
    try:
        for obj in objects:
            if str(getattr(obj, "TypeId", "") or "") not in _ROBOT_TRAJECTORY_TYPES:
                continue
            holder = Robot.Trajectory()
            swapped = _swap_robot_trajectory(obj, holder)
            if swapped["installed"] != {
                "waypoint_count": 0,
                "length": 0.0,
                "duration": 0.0,
            }:
                raise RuntimeError(
                    f"Robot trajectory {obj.Name!r} did not enter an empty transfer state."
                )
            extracted.append(
                {
                    "object_name": str(obj.Name),
                    "object": obj,
                    "holder": holder,
                    "accepted_summary": dict(swapped["displaced"]),
                }
            )
    except Exception as extraction_error:
        failures = []
        for entry in reversed(extracted):
            try:
                restored = _swap_robot_trajectory(entry["object"], entry["holder"])
                if restored["installed"] != entry["accepted_summary"]:
                    raise RuntimeError("restored trajectory summary differs")
            except Exception as rollback_error:
                failures.append(
                    f"{entry['object_name']}: {type(rollback_error).__name__}: "
                    f"{rollback_error}"
                )
        if failures:
            raise RuntimeError(
                f"{extraction_error} Robot trajectory extraction rollback failed: "
                + "; ".join(failures)
            ) from extraction_error
        raise
    return extracted


def _restore_robot_rollback_states(
    states: list[dict[str, Any]],
    trajectory_holders: list[dict[str, Any]],
) -> list[str]:
    """Restore/recreate an accepted Robot graph using constant-time path swaps."""

    import Robot

    del Robot

    holder_by_name: dict[str, dict[str, Any]] = {}
    for entry in trajectory_holders:
        name = str(entry["object_name"])
        if name in holder_by_name:
            raise RuntimeError(f"Robot rollback has duplicate trajectory holder {name!r}.")
        holder_by_name[name] = entry

    failures: list[str] = []
    resolved: list[tuple[Any, dict[str, Any], bool]] = []
    for state in states:
        document = state["document"]
        name = str(state["name"])
        type_id = str(state["type_id"])
        try:
            obj = document.getObject(name)
            recreated = obj is None
            if obj is None:
                obj = document.addObject(type_id, name)
            if obj is None or str(obj.Name) != name or str(obj.TypeId) != type_id:
                raise RuntimeError(f"native identity/type {type_id!r} could not be restored")
            if type_id == "Robot::TrajectoryDressUpObject":
                _unfreeze_robot_dressup(obj)
            elif type_id == "Robot::RobotObject":
                _freeze_object(obj, "Robot rollback")
            for property_name, captured in state["properties"].items():
                if property_name not in _properties(obj):
                    obj.addProperty(
                        str(captured["type"]),
                        property_name,
                        str(captured["group"]),
                        str(captured["documentation"]),
                    )
                if str(obj.getTypeIdOfProperty(property_name) or "") != str(
                    captured["type"]
                ):
                    raise RuntimeError(f"property {property_name!r} changed native type")
            accepted_names = set(state["properties"])
            protected = {
                "ExpressionEngine",
                "Label",
                "Trajectory",
                "Tcp",
                *(f"Axis{axis}" for axis in range(1, 7)),
            }
            for property_name in list(_properties(obj)):
                if (
                    property_name not in accepted_names
                    and property_name not in protected
                    and str(obj.getGroupOfProperty(property_name) or "") == "VibeCAD"
                ):
                    obj.removeProperty(property_name)
            if recreated and type_id in _ROBOT_TRAJECTORY_TYPES and name not in holder_by_name:
                raise RuntimeError("accepted trajectory data has no rollback holder")
            resolved.append((obj, state, recreated))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state, _recreated in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                if captured["deferred_link"]:
                    continue
                obj.restorePropertyContent(property_name, bytearray(captured["content"]))
                _restore_robot_exact_property(
                    obj, property_name, captured.get("exact_value")
                )
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            if str(state["type_id"]) == "Robot::RobotObject":
                for axis, value in enumerate(
                    state["kernel"]["axis_positions"], start=1
                ):
                    setattr(obj, f"Axis{axis}", float(value))
                obj.setKinematic(state["kernel"]["kinematics"])
                obj.Tcp = _placement(state["kernel"]["tcp"])
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state, _recreated in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                if not captured["deferred_link"]:
                    continue
                obj.restorePropertyContent(property_name, bytearray(captured["content"]))
                for mode in list(captured["editor_modes"]):
                    obj.setPropertyStatus(property_name, str(mode))
            holder = holder_by_name.get(name)
            if holder is not None:
                swapped = _swap_robot_trajectory(obj, holder["holder"])
                if swapped["installed"] != holder["accepted_summary"]:
                    raise RuntimeError("restored native trajectory summary differs")
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path).lstrip("."), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path).lstrip("."), str(expression))
            obj.Label = str(state["label"])
            if str(state["type_id"]) == "Robot::TrajectoryDressUpObject":
                _freeze_robot_dressup(obj)
            elif str(state["type_id"]) == "Robot::RobotObject":
                obj.unfreeze(True)
                obj.purgeTouched()
                if obj.isFrozen():
                    raise RuntimeError("restored robot remained frozen")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    for obj, state, _recreated in resolved:
        name = str(state["name"])
        try:
            for property_name, captured in state["properties"].items():
                if _property_content_sha256(
                    bytes(obj.dumpPropertyContent(property_name))
                ) != str(captured["content_sha256"]):
                    raise RuntimeError(
                        f"restored property {property_name!r} differs from accepted state"
                    )
            if [
                [str(path), str(expression)]
                for path, expression in list(obj.ExpressionEngine or [])
            ] != state["expressions"]:
                raise RuntimeError("restored expressions differ from accepted state")
            if str(obj.Label) != str(state["label"]):
                raise RuntimeError("restored label differs from accepted state")
            if str(state["type_id"]) == "Robot::RobotObject":
                expected_axes = state["kernel"]["axis_positions"]
                observed_axes = [
                    float(getattr(obj, f"Axis{axis}")) for axis in range(1, 7)
                ]
                if any(
                    not math.isclose(left, right, rel_tol=1.0e-10, abs_tol=1.0e-8)
                    for left, right in zip(observed_axes, expected_axes)
                ) or not _robot_placement_matches(obj.Tcp, state["kernel"]["tcp"]):
                    raise RuntimeError("restored robot axes/TCP differ from accepted state")
            if str(state["type_id"]) == "Robot::TrajectoryDressUpObject" and not (
                _robot_dressup_is_frozen(obj)
            ):
                raise RuntimeError("restored dress-up is not frozen")
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "Robot operation failed and accepted native state could not be fully "
            f"restored: {'; '.join(failures)}"
        )
    return [str(state["name"]) for _obj, state, _recreated in resolved]


_FEM_RESULT_FLOAT_LISTS = (
    "CriticalStrainRatio",
    "DisplacementLengths",
    "MassFlowRate",
    "MaxShear",
    "MohrCoulomb",
    "NetworkPressure",
    "NodeStrainXX",
    "NodeStrainXY",
    "NodeStrainXZ",
    "NodeStrainYY",
    "NodeStrainYZ",
    "NodeStrainZZ",
    "NodeStressXX",
    "NodeStressXY",
    "NodeStressXZ",
    "NodeStressYY",
    "NodeStressYZ",
    "NodeStressZZ",
    "Peeq",
    "PrincipalMax",
    "PrincipalMed",
    "PrincipalMin",
    "ReinforcementRatio_x",
    "ReinforcementRatio_y",
    "ReinforcementRatio_z",
    "Stats",
    "Temperature",
    "UserDefined",
    "vonMises",
)
_FEM_RESULT_VECTOR_LISTS = (
    "DisplacementVectors",
    "HeatFlux",
    "PS1Vector",
    "PS2Vector",
    "PS3Vector",
)
_FEM_PROPERTIES_BY_TYPE = {
    "Fem::FemAnalysis": ("Group",),
    "Fem::FemSolverObjectPython": (
        "AnalysisType",
        "MatrixSolverType",
        "GeometricalNonlinearity",
        "MaterialNonlinearity",
        "ReducedIntegration",
        "SplitInputWriter",
        "WorkingDir",
        "WorkingDirectory",
    ),
    "App::MaterialObjectPython": (
        "Category",
        "Material",
        "References",
        "MaterialName",
        "UUID",
        "Suppressed",
    ),
    "Fem::ConstraintFixed": ("References", "Suppressed"),
    "Fem::ConstraintForce": (
        "References",
        "Force",
        "Direction",
        "DirectionVector",
        "Reversed",
        "Suppressed",
    ),
    "Fem::ConstraintPressure": (
        "References",
        "Pressure",
        "Reversed",
        "Suppressed",
    ),
    "App::DocumentObjectGroup": ("Group", "VibeCADConstraints"),
    "Fem::FemMeshShapeBaseObjectPython": (
        "FemMesh",
        "Shape",
        "ElementOrder",
        "ElementDimension",
        "CharacteristicLengthMax",
        "CharacteristicLengthMin",
        "WorkingDirectory",
        "Suppressed",
    ),
    "Fem::FemResultObjectPython": (
        "Mesh",
        "NodeNumbers",
        *_FEM_RESULT_FLOAT_LISTS,
        *_FEM_RESULT_VECTOR_LISTS,
        "Time",
        "Eigenmode",
        "EigenmodeFrequency",
        "VibeCADAnalysisObjectName",
        "VibeCADFEMStatus",
        "VibeCADSolverExecuted",
        "VibeCADInputDeckSHA256",
    ),
}


def _fem_link_name(value: Any) -> str:
    return str(getattr(value, "Name", "") or "") if value is not None else ""


def _fem_capture_property(obj: Any, name: str) -> dict[str, Any]:
    property_type = str(obj.getTypeIdOfProperty(name) or "")
    value = getattr(obj, name)
    if property_type == "Fem::PropertyFemMesh":
        captured = value.copy()
    elif property_type in {"App::PropertyLink", "App::PropertyLinkGlobal"}:
        captured = _fem_link_name(value)
    elif property_type in {"App::PropertyLinkList", "App::PropertyLinkListGlobal"}:
        captured = [_fem_link_name(item) for item in list(value or [])]
    elif property_type in {"App::PropertyLinkSub", "App::PropertyLinkSubGlobal"}:
        if value is None:
            captured = None
        else:
            target, subelements = value
            captured = {
                "object": _fem_link_name(target),
                "subelements": [str(item) for item in list(subelements or [])],
            }
    elif property_type in {
        "App::PropertyLinkSubList",
        "App::PropertyLinkSubListGlobal",
    }:
        captured = [
            {
                "object": _fem_link_name(target),
                "subelements": [str(item) for item in list(subelements or [])],
            }
            for target, subelements in list(value or [])
        ]
    elif property_type == "App::PropertyMap":
        captured = dict(value or {})
    elif property_type == "App::PropertyVector":
        captured = [float(value.x), float(value.y), float(value.z)]
    elif property_type == "App::PropertyVectorList":
        captured = [[float(item.x), float(item.y), float(item.z)] for item in value]
    elif property_type in {
        "App::PropertyForce",
        "App::PropertyPressure",
        "App::PropertyLength",
        "App::PropertyFrequency",
        "App::PropertyTime",
        "App::PropertyQuantity",
    }:
        captured = str(value)
    elif property_type.endswith("List"):
        captured = list(value or [])
    elif isinstance(value, (str, bool, int, float)) or value is None:
        captured = value
    else:
        raise RuntimeError(
            f"Cannot capture FEM rollback property {obj.Name}.{name} of type "
            f"{property_type!r}."
        )
    return {"type": property_type, "value": captured}


def _fem_resolve_link(doc: Any, name: str, context: str) -> Any:
    if not name:
        return None
    target = doc.getObject(name)
    if target is None:
        raise RuntimeError(f"{context} target {name!r} is unavailable during rollback.")
    return target


def _fem_restore_property(doc: Any, obj: Any, name: str, state: Mapping[str, Any]) -> None:
    property_type = str(state["type"])
    value = state["value"]
    if name not in _properties(obj):
        if name.startswith("VibeCAD"):
            obj.addProperty(property_type, name, "VibeCAD", "Restored VibeCAD state.")
        else:
            raise RuntimeError(
                f"Restored native FEM object {obj.Name!r} has no property {name!r}."
            )
    if property_type == "Fem::PropertyFemMesh":
        restored = value.copy()
    elif property_type in {"App::PropertyLink", "App::PropertyLinkGlobal"}:
        restored = _fem_resolve_link(doc, str(value or ""), f"{obj.Name}.{name}")
    elif property_type in {"App::PropertyLinkList", "App::PropertyLinkListGlobal"}:
        restored = [
            _fem_resolve_link(doc, str(item), f"{obj.Name}.{name}") for item in value
        ]
    elif property_type in {"App::PropertyLinkSub", "App::PropertyLinkSubGlobal"}:
        restored = (
            None
            if value is None
            else (
                _fem_resolve_link(
                    doc, str(value["object"]), f"{obj.Name}.{name}"
                ),
                list(value["subelements"]),
            )
        )
    elif property_type in {
        "App::PropertyLinkSubList",
        "App::PropertyLinkSubListGlobal",
    }:
        restored = [
            (
                _fem_resolve_link(
                    doc, str(item["object"]), f"{obj.Name}.{name}"
                ),
                list(item["subelements"]),
            )
            for item in value
        ]
    elif property_type == "App::PropertyVector":
        import FreeCAD as App

        restored = App.Vector(*value)
    elif property_type == "App::PropertyVectorList":
        import FreeCAD as App

        restored = [App.Vector(*item) for item in value]
    elif property_type == "App::PropertyMap":
        restored = dict(value)
    else:
        restored = value
    setattr(obj, name, restored)


def _fem_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    states = []
    for obj in objects:
        type_id = str(getattr(obj, "TypeId", "") or "")
        output_type = str(getattr(obj, PROP_OUTPUT_TYPE, "") or "")
        if output_type not in {
            "analysis",
            "solver",
            "material",
            "constraint",
            "load_case",
            "mesh",
            "result",
        }:
            continue
        property_names = set(_FEM_PROPERTIES_BY_TYPE.get(type_id, ()))
        property_names.update(
            name
            for name in list(getattr(obj, "PropertiesList", []) or [])
            if str(obj.getGroupOfProperty(name) or "") == "VibeCAD"
        )
        captured = {}
        for name in sorted(property_names):
            if name not in _properties(obj):
                continue
            property_type = str(obj.getTypeIdOfProperty(name) or "")
            if property_type in {
                "App::PropertyPythonObject",
                "App::PropertyExpressionEngine",
            }:
                continue
            captured[name] = _fem_capture_property(obj, name)
        dynamic = {}
        for name in sorted(_properties(obj)):
            if name in captured or name in {"ExpressionEngine", "Proxy"}:
                continue
            try:
                statuses = list(obj.getPropertyStatus(name) or [])
            except Exception:
                statuses = []
            # PropDynamic is status bit 21. It is intentionally returned as an
            # integer because PropertyContainerPy exposes names only for mutable
            # status flags, not the static property-type bits.
            if 21 not in statuses and "PropDynamic" not in statuses:
                continue
            property_type = str(obj.getTypeIdOfProperty(name) or "")
            if property_type in {
                "App::PropertyPythonObject",
                "App::PropertyExpressionEngine",
            }:
                continue
            dynamic[name] = {
                **_fem_capture_property(obj, name),
                "group": str(obj.getGroupOfProperty(name) or "Human"),
                "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                "statuses": [
                    value
                    for value in statuses
                    if value not in {21, "PropDynamic"}
                ],
            }
        states.append(
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": type_id,
                "output_type": output_type,
                "definition": str(getattr(obj, PROP_DEFINITION, "{}") or "{}"),
                "properties": captured,
                "dynamic_properties": dynamic,
                "expressions": [
                    (str(path), str(expression))
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
            }
        )
    return states


def _fem_recreate_object(doc: Any, state: Mapping[str, Any]) -> Any:
    import ObjectsFem

    name = str(state["name"])
    type_id = str(state["type_id"])
    if type_id == "Fem::FemAnalysis":
        return ObjectsFem.makeAnalysis(doc, name)
    if type_id == "Fem::FemSolverObjectPython":
        return ObjectsFem.makeSolverCalculiXCcxTools(doc, name)
    if type_id == "App::MaterialObjectPython":
        return ObjectsFem.makeMaterialSolid(doc, name)
    if type_id == "Fem::ConstraintFixed":
        return ObjectsFem.makeConstraintFixed(doc, name)
    if type_id == "Fem::ConstraintForce":
        return ObjectsFem.makeConstraintForce(doc, name)
    if type_id == "Fem::ConstraintPressure":
        return ObjectsFem.makeConstraintPressure(doc, name)
    if type_id == "App::DocumentObjectGroup":
        return doc.addObject(type_id, name)
    if type_id == "Fem::FemMeshShapeBaseObjectPython":
        return ObjectsFem.makeMeshGmsh(doc, name)
    if type_id == "Fem::FemResultObjectPython":
        return ObjectsFem.makeResultMechanical(doc, name)
    raise RuntimeError(f"Cannot recreate unsupported native FEM type {type_id!r}.")


def _restore_fem_rollback_states(doc: Any, states: list[dict[str, Any]]) -> list[str]:
    resolved: list[tuple[Any, dict[str, Any]]] = []
    failures = []
    for state in states:
        name = str(state["name"])
        try:
            obj = doc.getObject(name)
            if obj is None:
                obj = _fem_recreate_object(doc, state)
            if str(getattr(obj, "TypeId", "") or "") != str(state["type_id"]):
                raise RuntimeError(
                    f"native type changed to {getattr(obj, 'TypeId', '')!r}"
                )
            if str(obj.Name) != name:
                raise RuntimeError(f"stable name changed to {obj.Name!r}")
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(f"Could not recreate FEM rollback objects: {'; '.join(failures)}")
    # Restore independent kernels/scalars first, links and groups second.
    for obj, state in resolved:
        for name, property_state in dict(state["dynamic_properties"]).items():
            if name not in _properties(obj):
                obj.addProperty(
                    str(property_state["type"]),
                    name,
                    str(property_state["group"]),
                    str(property_state["documentation"]),
                )
    for links in (False, True):
        for obj, state in resolved:
            property_states = {
                **dict(state["properties"]),
                **dict(state["dynamic_properties"]),
            }
            for name, property_state in property_states.items():
                is_link = str(property_state["type"]).startswith("App::PropertyLink")
                if is_link != links:
                    continue
                try:
                    _fem_restore_property(doc, obj, name, property_state)
                except Exception as exc:
                    failures.append(
                        f"{obj.Name}.{name}: {type(exc).__name__}: {exc}"
                    )
    for obj, state in resolved:
        try:
            obj.Label = str(state["label"])
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path), None)
            for path, expression in state["expressions"]:
                obj.setExpression(str(path), str(expression))
            for name, property_state in dict(state["dynamic_properties"]).items():
                statuses = list(property_state.get("statuses") or [])
                if statuses:
                    obj.setPropertyStatus(name, statuses)
        except Exception as exc:
            failures.append(f"{obj.Name}.expressions: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "FEM operation failed and accepted native state could not be fully "
            f"restored: {'; '.join(failures)}"
        )
    return [str(state["name"]) for _obj, state in resolved]


def _fem_data(item: Mapping[str, Any]) -> dict[str, Any]:
    data = item.get("fem_data")
    if not isinstance(data, dict):
        raise RuntimeError(f"FEM output {item.get('name')!r} has no native readback.")
    return dict(data)


def _fem_reference_target(doc: Any, value: Mapping[str, Any], label: str) -> Any:
    reference = {
        "document_uid": str(value.get("document_uid") or ""),
        "object_name": str(value.get("object_name") or ""),
    }
    target = _reference_target(doc, reference, label)
    expected_type = str(value.get("source_type_id") or "")
    if expected_type and str(getattr(target, "TypeId", "") or "") != expected_type:
        raise RuntimeError(
            f"{label} changed native type from {expected_type!r} to "
            f"{getattr(target, 'TypeId', '')!r}."
        )
    expected_revision = str(value.get("source_revision") or "")
    if expected_revision:
        live_revision = str(
            getattr(target, contracts.PROP_PROGRAM_REVISION, "")
            or getattr(target, reference_contracts.PROP_SOURCE_REVISION, "")
            or ""
        )
        if live_revision and live_revision != expected_revision:
            raise RuntimeError(
                f"{label} changed revision after isolated FEM validation."
            )
    return target


def _fem_validation_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        key: value
        for key, value in data.items()
        if key not in {"nodes", "elements", "result_values"}
    }
    if isinstance(data.get("facts"), Mapping):
        summary["facts"] = dict(data["facts"])
    result_values = data.get("result_values")
    if isinstance(result_values, Mapping):
        summary["result_summary"] = {
            "node_count": len(list(result_values.get("node_numbers") or [])),
            "float_fields": sorted(dict(result_values.get("float_lists") or {})),
            "vector_fields": sorted(dict(result_values.get("vector_lists") or {})),
            "scalar_value_count": int(result_values.get("scalar_value_count") or 0),
            "time": float(result_values.get("time") or 0.0),
            "eigenmode": int(result_values.get("eigenmode") or 0),
            "eigenmode_frequency": float(
                result_values.get("eigenmode_frequency") or 0.0
            ),
        }
    return summary


def _configure_fem(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> None:
    import FreeCAD as App

    output_type = str(item["type"])
    data = _fem_data(item)
    definition = _definition(item)
    properties = dict(definition.get("properties") or {})
    if output_type == "solver":
        obj.AnalysisType = str(data["analysis_type"])
        obj.MatrixSolverType = str(data["matrix_solver"])
        obj.GeometricalNonlinearity = bool(data["geometrical_nonlinearity"])
        obj.MaterialNonlinearity = bool(data["material_nonlinearity"])
        obj.ReducedIntegration = bool(data["reduced_integration"])
        obj.SplitInputWriter = False
        obj.WorkingDir = ""
        obj.WorkingDirectory = ""
    elif output_type == "material":
        obj.Category = "Solid"
        obj.Material = dict(data["material"])
        references = []
        for assignment_index, assignment in enumerate(data.get("assignments") or []):
            if not isinstance(assignment, Mapping):
                raise RuntimeError(
                    f"FEM material {item['name']!r} assignment {assignment_index} is malformed."
                )
            target = _fem_reference_target(
                doc,
                assignment["target"],
                f"FEM material {item['name']!r} assignment {assignment_index}",
            )
            subelements = [
                str(value) for value in assignment["resolved_subelements"]
            ]
            if not subelements:
                raise RuntimeError(
                    f"FEM material {item['name']!r} assignment {assignment_index} is empty."
                )
            references.append((target, subelements))
        obj.References = references
    elif output_type == "constraint":
        target = _fem_reference_target(
            doc,
            data["target"],
            f"FEM constraint {item['name']!r} target",
        )
        subelements = [str(value) for value in data["resolved_subelements"]]
        obj.References = [(target, subelements)]
        kind = str(data["kind"])
        if kind == "force":
            obj.Force = f"{float(data['magnitude']):.17g} N"
            obj.DirectionVector = App.Vector(*data["direction"])
            obj.Reversed = bool(data["reversed"])
        elif kind == "pressure":
            obj.Pressure = f"{float(data['magnitude']):.17g} MPa"
            obj.Reversed = bool(data["reversed"])
    elif output_type == "load_case":
        members = []
        for name in data["constraint_outputs"]:
            member = outputs.get(str(name))
            if member is None or str(getattr(member, "TypeId", "")) not in {
                "Fem::ConstraintFixed",
                "Fem::ConstraintForce",
                "Fem::ConstraintPressure",
            }:
                raise RuntimeError(
                    f"FEM load case {item['name']!r} constraint {name!r} is unavailable."
                )
            members.append(member)
        obj.Group = []
        _add_property(
            obj,
            "App::PropertyLinkList",
            "VibeCADConstraints",
            "Exact stable constraints in this VibeScript load case.",
        )
        obj.VibeCADConstraints = members
    elif output_type == "mesh":
        source = _fem_reference_target(
            doc,
            data["source"],
            f"FEM mesh {item['name']!r} source",
        )
        detached = item.get("detached_fem_mesh")
        if detached is None:
            raise RuntimeError(f"FEM mesh {item['name']!r} has no detached native mesh.")
        obj.Shape = source
        obj.FemMesh = detached
        obj.ElementOrder = "2nd" if int(data["order"]) == 2 else "1st"
        obj.WorkingDirectory = ""
        method = str(data["method"])
        if method == "gmsh":
            obj.CharacteristicLengthMax = (
                f"{float(properties['maximum_size']):.17g} mm"
            )
            obj.CharacteristicLengthMin = (
                f"{float(properties['minimum_size']):.17g} mm"
            )
        else:
            element_type = str(properties["element_type"])
            obj.ElementDimension = (
                "1D"
                if element_type.startswith("edge")
                else "2D"
                if element_type.startswith(("triangle", "quad"))
                else "3D"
            )
    elif output_type == "analysis":
        names = [
            str(data["solver_output"]),
            *[str(value) for value in data["material_outputs"]],
            *[str(value) for value in data["constraint_outputs"]],
            *[str(value) for value in data["load_case_outputs"]],
            str(data["mesh_output"]),
        ]
        members = []
        for name in names:
            member = outputs.get(name)
            if member is None:
                raise RuntimeError(
                    f"FEM analysis {item['name']!r} member {name!r} is unavailable."
                )
            if member not in members:
                members.append(member)
        obj.Group = members
    elif output_type == "result":
        mesh_name = str(data["mesh_output"])
        analysis_name = str(data["analysis_output"])
        mesh = outputs.get(mesh_name)
        analysis = outputs.get(analysis_name)
        if mesh is None or str(getattr(mesh, "TypeId", "")) != (
            "Fem::FemMeshShapeBaseObjectPython"
        ):
            raise RuntimeError(f"FEM result {item['name']!r} mesh is unavailable.")
        if analysis is None or str(getattr(analysis, "TypeId", "")) != "Fem::FemAnalysis":
            raise RuntimeError(f"FEM result {item['name']!r} analysis is unavailable.")
        values = dict(data["result_values"])
        obj.Mesh = mesh
        obj.NodeNumbers = [int(value) for value in values["node_numbers"]]
        for name, sequence in dict(values["float_lists"]).items():
            if name not in _properties(obj):
                raise RuntimeError(
                    f"Native FEM result has no validated float-list property {name!r}."
                )
            setattr(obj, name, [float(value) for value in sequence])
        for name, sequence in dict(values["vector_lists"]).items():
            if name not in _properties(obj):
                raise RuntimeError(
                    f"Native FEM result has no validated vector-list property {name!r}."
                )
            setattr(obj, name, [App.Vector(*value) for value in sequence])
        obj.Time = float(values["time"])
        obj.Eigenmode = int(values["eigenmode"])
        obj.EigenmodeFrequency = float(values["eigenmode_frequency"])
        for property_type, name, value, description in (
            (
                "App::PropertyString",
                "VibeCADAnalysisObjectName",
                str(analysis.Name),
                "Stable native FEM analysis object name for this result.",
            ),
            (
                "App::PropertyString",
                "VibeCADFEMStatus",
                str(data["status"]),
                "Authenticated worker solve or input-validation status.",
            ),
            (
                "App::PropertyBool",
                "VibeCADSolverExecuted",
                bool(data["solver_executed"]),
                "Whether CalculiX was actually executed in the worker.",
            ),
            (
                "App::PropertyString",
                "VibeCADInputDeckSHA256",
                str(data["input_deck"]["artifact_sha256"]),
                "SHA-256 of the authenticated CalculiX input deck.",
            ),
        ):
            _add_property(obj, property_type, name, description)
            setattr(obj, name, value)
        if obj not in list(analysis.Group):
            analysis.addObject(obj)
    else:
        raise RuntimeError(f"No FEM publisher exists for output type {output_type!r}.")
    _add_string_property(
        obj,
        PROP_FEM_VALIDATION,
        "Authenticated bounded native FEM graph and validation summary.",
    )
    setattr(
        obj,
        PROP_FEM_VALIDATION,
        json.dumps(
            _fem_validation_summary(data),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _configure_object(
    doc: Any,
    obj: Any,
    item: Mapping[str, Any],
    outputs: Mapping[str, Any],
    prepared: Mapping[str, Any],
    robot_trajectory_swaps: list[dict[str, Any]],
    assembly_fastener_sources: Mapping[str, Any] | None = None,
) -> list[Any]:
    output_type = str(item["type"])
    owned_resources: list[Any] = []
    if prepared["pack"].domain == "draft":
        _configure_draft(doc, obj, item, outputs)
    elif prepared["pack"].domain == "surface":
        _configure_surface(obj, item)
    elif (
        prepared["pack"].domain == "mesh"
        and isinstance(item.get("meshpart_data"), Mapping)
        and output_type == "mesh"
    ):
        _configure_mesh(
            obj,
            item,
            data_key="meshpart_data",
            validation_property=PROP_MESHPART_VALIDATION,
        )
    elif (
        prepared["pack"].domain == "mesh"
        and isinstance(item.get("meshpart_data"), Mapping)
    ):
        _configure_meshpart_shape(obj, item)
    elif prepared["pack"].domain == "mesh":
        _configure_mesh(obj, item)
    elif prepared["pack"].domain == "meshpart" and output_type == "mesh":
        _configure_mesh(
            obj,
            item,
            data_key="meshpart_data",
            validation_property=PROP_MESHPART_VALIDATION,
        )
    elif prepared["pack"].domain == "meshpart":
        _configure_meshpart_shape(obj, item)
    elif prepared["pack"].domain == "points":
        _configure_points(obj, item)
    elif prepared["pack"].domain == "reverse_engineering":
        _configure_reverse_engineering(obj, item)
    elif prepared["pack"].domain == "inspection":
        _configure_inspection(doc, obj, item, outputs)
    elif output_type == "component_link":
        owned_resources = _configure_component(
            doc,
            obj,
            item,
            outputs,
            prepared,
            assembly_fastener_sources,
        )
    elif prepared["pack"].domain == "robot":
        _configure_robot(obj, item, outputs, robot_trajectory_swaps)
    elif prepared["pack"].domain == "fem":
        _configure_fem(doc, obj, item, outputs)
    elif output_type == "sketch":
        _configure_sketch(doc, obj, item)
    elif output_type in _BREP_OUTPUT_TYPES:
        obj.Shape = item["detached_shape"]
    elif output_type == "mesh":
        obj.Mesh = item["detached_mesh"]
    elif output_type == "joint":
        _configure_joint(obj, item, outputs, prepared)
    elif (
        prepared["pack"].domain == "assembly"
        and output_type == "mechanism_verification"
    ):
        _configure_assembly_mechanism_verification(obj, item, outputs)
    elif prepared["pack"].domain == "assembly" and output_type == "motion":
        _configure_assembly_motion(obj, item, outputs)
    elif prepared["pack"].domain == "assembly" and output_type == "simulation":
        _configure_assembly_simulation(obj, item, outputs)
    elif prepared["pack"].domain == "assembly" and output_type == "exploded_view":
        owned_resources = _configure_assembly_exploded_view(
            doc,
            obj,
            item,
            outputs,
            prepared,
        )
    elif prepared["pack"].domain == "assembly" and output_type == "bom":
        owned_resources = _configure_assembly_bom(
            obj,
            item,
            outputs,
            prepared,
        )
    elif output_type == "sheet":
        _configure_sheet(obj, item)
    elif output_type == "solver_diagnostics":
        _configure_solver_diagnostics(obj, item)
    return owned_resources


def _surface_still_matches(service: Any, prepared: Mapping[str, Any]) -> None:
    from VibeCADModelingSurface import resolve_service_surface

    live = resolve_service_surface(service, service.active_workbench_name())
    if not live.available:
        raise RuntimeError(
            live.unavailable_reason
            or "The live modeling surface is unavailable for domain publication."
        )
    expected = prepared["surface"]
    observed_tuple = (live.workbench, live.engine, live.surface_id)
    expected_tuple = (
        str(expected.get("workbench") or ""),
        str(expected.get("engine") or ""),
        str(expected.get("surface_id") or ""),
    )
    if observed_tuple != expected_tuple:
        raise RuntimeError(
            "The active workbench changed while the VibeScript worker ran."
        )


def _draft_object_compatible(obj: Any, item: Mapping[str, Any]) -> bool:
    data = item.get("draft_data")
    if not isinstance(data, dict):
        return False
    if str(getattr(obj, "TypeId", "") or "") != str(data.get("native_type") or ""):
        return False
    if type(getattr(obj, "Proxy", None)).__name__ != str(data.get("proxy_class") or ""):
        return False
    try:
        from draftutils.utils import get_type

        if str(get_type(obj) or "") != str(data.get("draft_type") or ""):
            return False
    except Exception:
        return False
    if str(item.get("type") or "") == "array":
        return bool(getattr(getattr(obj, "Proxy", None), "use_link", False)) == bool(
            data.get("use_link")
        )
    return True


def _draft_configure_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order native Base outputs before every dependent Draft array."""

    by_name = {str(item["name"]): item for item in items}
    remaining = list(items)
    configured: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while remaining:
        progress = False
        deferred: list[dict[str, Any]] = []
        for item in remaining:
            data = item.get("draft_data")
            source = data.get("source") if isinstance(data, dict) else None
            dependency = (
                str(source.get("output_name") or "")
                if isinstance(source, dict) and source.get("kind") == "program_output"
                else ""
            )
            if dependency and dependency not in configured:
                if dependency not in by_name:
                    raise RuntimeError(
                        f"Draft array {item.get('name')!r} refers to missing output "
                        f"{dependency!r}."
                    )
                deferred.append(item)
                continue
            ordered.append(item)
            configured.add(str(item["name"]))
            progress = True
        if not progress:
            raise RuntimeError("Draft publication contains a cyclic Base dependency.")
        remaining = deferred
    return ordered


def _publish_material_candidate(
    service: Any,
    prepared: dict[str, Any],
    validated: dict[str, Any],
    doc: Any,
) -> dict[str, Any]:
    """Atomically transfer reversible physical/display ownership to stable carriers."""

    existing = _objects_by_output(doc, prepared)
    desired_names = {str(item["name"]) for item in validated["outputs"]}
    retired = _retired_program_objects(doc, prepared, desired_names)
    internal = _program_objects(doc, str(prepared["program_id"]), "material")
    retired_deletion = _prepare_timeline_deletion(doc, retired)
    retired_targets = list(retired_deletion["delete_objects"])
    retired_uses = _external_uses(
        doc,
        retired_targets,
        [*internal, *retired_targets],
    )
    if retired_uses:
        raise _reference_error(
            "Cannot retire Material VibeScript outputs while human-created or "
            "foreign document objects still reference them",
            retired_uses,
        )
    updated = [
        existing[str(item["name"])]
        for item in validated["outputs"]
        if str(item["name"]) in existing
    ]
    downstream_uses = _preflight_output_updates(doc, updated, internal)

    previous: dict[str, tuple[dict[str, Any], Any]] = {}
    for obj in internal:
        output_name = str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        if not output_name:
            raise RuntimeError(
                f"Managed Material carrier {getattr(obj, 'Name', '')!r} lost its output name."
            )
        previous[output_name] = _preflight_material_carrier(obj)

    desired_targets: dict[str, Any] = {}
    desired_keys: set[tuple[str, str]] = set()
    for item in validated["outputs"]:
        name = str(item["name"])
        target = _material_definition_target(doc, item)
        if str(getattr(target, contracts.PROP_PROGRAM_DOMAIN, "") or "") == "material":
            raise RuntimeError(
                f"Material output {name!r} cannot target another managed Material carrier."
            )
        channel = "physical" if item["type"] == "material_assignment" else "appearance"
        inline_presentation = _partdesign_presentation_state(target)
        if inline_presentation is not None and inline_presentation.get(channel) is not None:
            raise RuntimeError(
                f"Material output {name!r} cannot own {channel} state on Part Design "
                f"output {target.Name!r}; that channel is already source-owned by its "
                "Part Design program."
            )
        key = (str(target.Name), channel)
        if key in desired_keys:
            raise RuntimeError(
                f"Material candidate duplicates {channel} ownership of target {target.Name!r}."
            )
        desired_keys.add(key)
        desired_targets[name] = target

    current_program_ids = {id(obj) for obj in internal}
    for obj in list(getattr(doc, "Objects", []) or []):
        if id(obj) in current_program_ids:
            continue
        if str(getattr(obj, contracts.PROP_PROGRAM_DOMAIN, "") or "") != "material":
            continue
        target = getattr(obj, PROP_MATERIAL_TARGET, None)
        if target is None:
            continue
        output_type = str(getattr(obj, PROP_OUTPUT_TYPE, "") or "")
        channel = "physical" if output_type == "material_assignment" else "appearance"
        key = (str(getattr(target, "Name", "") or ""), channel)
        if key not in desired_keys:
            continue
        try:
            ownership = _material_ownership(obj)
        except Exception as exc:
            raise RuntimeError(
                f"Target {key[0]!r} is linked by malformed foreign Material carrier "
                f"{getattr(obj, 'Name', '')!r}: {exc}"
            ) from exc
        raise RuntimeError(
            f"Target {key[0]!r} {channel} state is already owned by Material program "
            f"{getattr(obj, contracts.PROP_PROGRAM_ID, '')!r}, output "
            f"{getattr(obj, contracts.PROP_PROGRAM_OUTPUT, '')!r}. Delete or retarget that "
            "owner before publishing this candidate."
        )

    targets_to_snapshot: dict[int, Any] = {
        id(target): target for _ownership, target in previous.values()
    }
    targets_to_snapshot.update(
        {id(target): target for target in desired_targets.values()}
    )
    rollback_states = [
        _material_target_snapshot(target) for target in targets_to_snapshot.values()
    ]

    outputs: dict[str, Any] = {}
    created: list[Any] = []
    removed: list[str] = []
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish {prepared['pack'].title} VibeScript: {prepared['program_name']}"
            )
            transaction_open = True

        for channel in ("physical", "appearance"):
            for output_name, (ownership, target) in previous.items():
                if ownership["channel"] == channel:
                    _restore_material_baseline(existing[output_name], ownership, target)

        configure_order = sorted(
            list(validated["outputs"]),
            key=lambda item: 0 if item["type"] == "material_assignment" else 1,
        )
        for item in configure_order:
            output_name = str(item["name"])
            output_type = str(item["type"])
            obj = existing.get(output_name)
            if obj is None:
                obj = _create_object(
                    doc,
                    prepared,
                    output_name,
                    output_type,
                    _definition(item),
                    None,
                )
                created.append(obj)
            elif str(getattr(obj, "TypeId", "") or "") != "App::FeaturePython":
                raise RuntimeError(
                    f"Stable Material output {output_name!r} changed native carrier type."
                )
            target = desired_targets[output_name]
            prior = previous.get(output_name)
            validation = item.get("material_validation")
            controlled = (
                list(validation.get("controlled_properties") or [])
                if isinstance(validation, dict)
                else []
            )
            if output_type == "appearance":
                view = getattr(target, "ViewObject", None)
                if view is None:
                    raise RuntimeError(
                        f"Appearance target {target.Name!r} has no live view provider."
                    )
                controlled = _effective_appearance_controlled_properties(
                    view,
                    controlled,
                )
            channel = (
                "physical" if output_type == "material_assignment" else "appearance"
            )
            baseline = _material_baseline_for_desired(
                obj if prior is not None else None,
                prior[0] if prior is not None else None,
                target,
                channel=channel,
                controlled=controlled,
            )
            obj.Label = _label(item, output_name)
            _set_metadata(obj, prepared, output_name, output_type, _definition(item))
            _configure_material_carrier(obj, item, target, baseline, prepared)
            _mark_timeline_operation(
                obj,
                context=f"Material output {output_name!r}",
            )
            if _is_current_transaction_timeline_object(doc, obj):
                _publish_new_timeline_resource_block(
                    doc,
                    obj,
                    [],
                    context=f"Material output {output_name!r}",
                )
            outputs[output_name] = obj

        downstream_refresh = _refresh_external_consumers(
            downstream_uses,
            revision=str(prepared["revision"]),
        )
        removed = _remove_timeline_deletion(doc, retired_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        try:
            _restore_material_target_snapshots(rollback_states)
        except Exception as rollback_error:
            raise RuntimeError(
                f"{publication_error} Explicit Material rollback failure: {rollback_error}"
            ) from publication_error
        raise

    live_outputs: dict[str, dict[str, Any]] = {}
    published_outputs: list[dict[str, Any]] = []
    for item in validated["outputs"]:
        name = str(item["name"])
        obj = outputs[name]
        validation = dict(item.get("material_validation") or {})
        summary = {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_type": str(item["type"]),
            "derived_state": str(
                getattr(obj, reference_contracts.PROP_DERIVED_STATE, "") or ""
            ),
            "stale_reason": str(
                getattr(obj, reference_contracts.PROP_STALE_REASON, "") or ""
            ),
            "source_revision": str(
                getattr(obj, reference_contracts.PROP_SOURCE_REVISION, "") or ""
            ),
            "target": dict(validation.get("target") or {}),
            "channel": str(validation.get("channel") or ""),
            "validation": validation,
        }
        live_outputs[name] = summary
        published_outputs.append({"name": name, **summary})
    return {
        "ok": True,
        "outputs": published_outputs,
        "live_outputs": live_outputs,
        "created_objects": [str(obj.Name) for obj in created],
        "retired_objects": removed,
        "downstream_references": {
            "safe_whole_object_uses": scripted_publication.json_reference_uses(
                downstream_uses
            ),
            **downstream_refresh,
        },
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


def _techdraw_data(item: Mapping[str, Any]) -> dict[str, Any]:
    data = item.get("techdraw_data")
    if not isinstance(data, dict):
        raise RuntimeError(
            f"TechDraw output {item.get('name')!r} has no validated native state."
        )
    return dict(data)


def _techdraw_publication_checkpoint(stage: str, output_key: str, obj: Any) -> None:
    """Fault-injection seam used by lifecycle rollback tests."""

    del stage, output_key, obj


def _techdraw_projection_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    from vibescript_techdraw_worker import _dimension_reference_inventory

    summary = {
        key: data.get(key)
        for key in (
            "native_type",
            "direction",
            "x_direction",
            "position_mm",
            "scale",
            "edge_count",
            "face_count",
            "vertex_count",
            "bounds_2d",
            "centroid",
            "source_identities",
            "edges_artifact",
            "faces_artifact",
        )
    }
    summary["dimension_reference_inventory"] = _dimension_reference_inventory(
        data,
        sample_limit=24,
    )
    return summary


def _techdraw_validation_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    output_type = str(item["type"])
    data = _techdraw_data(item)
    if output_type == "view":
        return {
            "operation": "view",
            "orientation": data["orientation"],
            "hidden_lines": data["hidden_lines"],
            "smooth_lines": data["smooth_lines"],
            "projection": _techdraw_projection_summary(data),
        }
    if output_type == "projection":
        return {
            "operation": "projection",
            "native_type": data["native_type"],
            "convention": data["convention"],
            "position_mm": data["position_mm"],
            "scale": data["scale"],
            "spacing_mm": data["spacing_mm"],
            "directions": data["directions"],
            "children": {
                direction: _techdraw_projection_summary(child)
                for direction, child in dict(data["children"]).items()
            },
        }
    if output_type == "dimension":
        return {
            key: data.get(key)
            for key in (
                "operation",
                "native_type",
                "kind",
                "measure",
                "source_output",
                "projection_direction",
                "position_mm",
                "raw_value",
                "display_text",
                "references",
                "native_state",
                "format_spec",
                "over_tolerance",
                "under_tolerance",
                "show_units",
            )
        }
    return {
        key: value
        for key, value in data.items()
        if key not in {"native_members", "native_template"}
    }


def _techdraw_set_validation(obj: Any, item: Mapping[str, Any]) -> None:
    _add_string_property(
        obj,
        PROP_TECHDRAW_VALIDATION,
        "Authenticated worker-precomputed TechDraw publication summary.",
    )
    setattr(
        obj,
        PROP_TECHDRAW_VALIDATION,
        json.dumps(
            _techdraw_validation_summary(item),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _techdraw_source_objects(
    doc: Any,
    definition: Mapping[str, Any],
    *,
    output_name: str,
) -> list[Any]:
    arguments = list(definition.get("arguments") or [])
    if len(arguments) != 1 or not isinstance(arguments[0], list):
        raise RuntimeError(f"TechDraw output {output_name!r} has malformed sources.")
    result = []
    for index, reference in enumerate(arguments[0]):
        if not isinstance(reference, dict) or set(reference) != {
            "document_uid",
            "object_name",
        }:
            raise RuntimeError(
                f"TechDraw output {output_name!r} source {index} is malformed."
            )
        target = doc.getObject(str(reference["object_name"]))
        if target is None:
            raise RuntimeError(
                f"TechDraw source object {reference['object_name']!r} disappeared "
                "before publication."
            )
        result.append(target)
    return result


def _techdraw_create_output(
    doc: Any,
    prepared: Mapping[str, Any],
    output_name: str,
    output_type: str,
) -> Any:
    native_type = _native_type(output_type, "techdraw")
    return doc.addObject(native_type, _internal_name(prepared, output_name))


def _techdraw_configure_style(
    obj: Any,
    data: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> None:
    import FreeCAD as App

    obj.Direction = App.Vector(*data["direction"])
    obj.XDirection = App.Vector(*data["x_direction"])
    obj.ScaleType = "Custom"
    obj.Scale = float(data["scale"])
    obj.X = float(data["position_mm"][0])
    obj.Y = float(data["position_mm"][1])
    hidden = bool(properties["hidden_lines"])
    smooth = bool(properties["smooth_lines"])
    for name in ("HardHidden", "SmoothHidden", "SeamHidden", "IsoHidden"):
        if name in _properties(obj):
            setattr(obj, name, hidden)
    for name in ("SmoothVisible", "SeamVisible", "IsoVisible"):
        if name in _properties(obj):
            setattr(obj, name, smooth)


def _techdraw_projection_child_map(group: Any) -> dict[str, Any]:
    result = {}
    for child in list(group.Views or []):
        direction = str(getattr(child, "Type", "") or "")
        if not direction or direction in result:
            raise RuntimeError(
                f"Projection group {group.Name!r} has duplicate or malformed children."
            )
        result[direction] = child
    return result


def _techdraw_projection_type(direction: str) -> str:
    return {
        "front": "Front",
        "left": "Left",
        "right": "Right",
        "rear": "Rear",
        "top": "Top",
        "bottom": "Bottom",
        "front_top_left": "FrontTopLeft",
        "front_top_right": "FrontTopRight",
        "front_bottom_left": "FrontBottomLeft",
        "front_bottom_right": "FrontBottomRight",
    }[direction]


def _cam_data(item: Mapping[str, Any]) -> dict[str, Any]:
    data = item.get("cam_data")
    if not isinstance(data, dict):
        raise RuntimeError(f"CAM output {item.get('name')!r} has no native readback.")
    return data


def _cam_validation_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    """Persist bounded CAM facts without command streams or artifact paths."""

    data = _cam_data(item)
    summary = {
        key: data[key]
        for key in (
            "native_type",
            "proxy_module",
            "proxy_class",
            "kind",
            "strategy",
            "job_output",
            "stock_output",
            "tool_output",
            "tool_outputs",
            "operation_outputs",
            "toolpath_output",
            "path_summary",
            "combined_path_summary",
            "collision_free",
            "simulation_resolution_mm",
            "require_collision_free",
        )
        if key in data
    }
    if isinstance(data.get("simulation"), dict):
        simulation = data["simulation"]
        collision = dict(simulation.get("collision") or {})
        stock = dict(simulation.get("stock") or {})
        summary["simulation"] = {
            "complete": bool(simulation.get("complete")),
            "stage": str(simulation.get("stage") or ""),
            "simulation_scope": str(simulation.get("simulation_scope") or ""),
            "command_count": int(simulation.get("command_count") or 0),
            "executed_sweeps": int(simulation.get("executed_sweeps") or 0),
            "cutting_sweeps": int(simulation.get("cutting_sweeps") or 0),
            "resolution_mm": float(stock.get("resolution_mm") or 0.0),
            "grid": list(stock.get("grid") or []),
            "initial_volume_mm3": float(stock.get("initial_volume_mm3") or 0.0),
            "removed_volume_mm3": float(stock.get("removed_volume_mm3") or 0.0),
            "remaining_volume_mm3": float(
                stock.get("remaining_volume_mm3") or 0.0
            ),
            "modified_cells": int(stock.get("modified_cells") or 0),
            "removed_bounds": stock.get("removed_bounds"),
            "protected_model_checked": bool(
                collision.get("protected_model_checked")
            ),
            "protected_model_collision": bool(
                collision.get("protected_model_collision")
            ),
            "protected_model_volume_mm3": float(
                collision.get("protected_model_volume_mm3") or 0.0
            ),
            "protected_model_volume_aggregation": str(
                collision.get("protected_model_volume_aggregation") or ""
            ),
            "holder_checked": bool(collision.get("holder_checked")),
            "fixture_checked": bool(collision.get("fixture_checked")),
            "unavailable_checks": list(collision.get("unavailable_checks") or []),
        }
    if isinstance(data.get("postprocess"), dict):
        postprocess = data["postprocess"]
        summary["postprocess"] = {
            key: postprocess[key]
            for key in (
                "artifact_sha256",
                "artifact_bytes",
                "line_count",
                "processor",
                "processor_module",
                "processor_class",
                "units",
                "comments",
                "line_numbers",
                "machine_configured",
                "machine_name",
                "machine_limits_checked",
                "configuration_scope",
            )
        }
    if isinstance(item.get("facts"), dict):
        summary["shape_facts"] = dict(item["facts"])
    return summary


def _cam_reference_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return str(value.get("document_uid") or ""), str(value.get("object_name") or "")


def _cam_clear_expressions(obj: Any, property_names: set[str]) -> None:
    for path, _expression in list(getattr(obj, "ExpressionEngine", []) or []):
        if str(path) in property_names:
            obj.setExpression(str(path), None)


def _cam_publication_checkpoint(stage: str, output_key: str, obj: Any) -> None:
    """Instrumentation seam for deterministic publication fault injection."""

    del stage, output_key, obj


def _cam_auxiliary_objects(
    doc: Any,
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    result = {}
    for obj in _program_objects(doc, str(prepared["program_id"]), "cam"):
        key = str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or "")
        if not key or key in result:
            if key:
                raise RuntimeError(f"Multiple native CAM objects claim key {key!r}.")
            continue
        result[key] = obj
    return result


def _cam_link_name(value: Any) -> str:
    return str(getattr(value, "Name", "") or "") if value is not None else ""


def _cam_matrix_values(value: Any) -> list[float]:
    matrix = value.toMatrix() if hasattr(value, "toMatrix") else value
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


def _cam_capture_property(obj: Any, name: str) -> dict[str, Any] | None:
    property_type = str(obj.getTypeIdOfProperty(name) or "")
    if property_type in {"App::PropertyPythonObject", "App::PropertyExpressionEngine"}:
        return None
    value = getattr(obj, name)
    if property_type == "Part::PropertyPartShape":
        # Keep the detached Python wrapper alive for the duration of the
        # transaction.  TopoShape.copy() creates a new OCC identity even when
        # the BREP is identical, which needlessly changes stable in-document
        # topology hashes during rollback.
        captured = value
    elif property_type == "Part::PropertyShapeCache":
        # This native property is explicitly Prop_NoPersist and its Python
        # setter can only invalidate entries.  Treat it as what it is: a
        # derived acceleration cache, record the keys for diagnostics, and
        # restore it to a cold (invalidated) state.  Rebuilding it here would
        # perform shape resolution on the document thread and violate the CAM
        # publication boundary.
        captured = {
            "restore": "invalidate",
            "keys": [str(key) for key, _shape in list(value or [])],
        }
    elif property_type in {
        "Materials::PropertyMaterial",
        "App::PropertyMaterial",
    }:
        # Native material wrappers are immutable value objects for assignment;
        # retaining the wrapper preserves the full physical card without a
        # lossy dict conversion.
        captured = value
    elif property_type == "App::PropertyMaterialList":
        captured = list(value or [])
    elif property_type == "Path::PropertyPath":
        from vibescript_cam_worker import path_to_records

        captured = (
            path_to_records(value)
            if list(getattr(value, "Commands", []) or [])
            else []
        )
    elif "PropertyLinkSubList" in property_type:
        captured = [
            {
                "object": _cam_link_name(target),
                "subelements": [str(item) for item in list(subelements or [])],
            }
            for target, subelements in list(value or [])
        ]
    elif "PropertyLinkSub" in property_type:
        captured = (
            None
            if value is None
            else {
                "object": _cam_link_name(value[0]),
                "subelements": [str(item) for item in list(value[1] or [])],
            }
        )
    elif "PropertyLinkList" in property_type:
        captured = [_cam_link_name(item) for item in list(value or [])]
    elif "PropertyLink" in property_type:
        captured = _cam_link_name(value)
    elif property_type == "App::PropertyPlacement":
        captured = _cam_matrix_values(value)
    elif property_type == "App::PropertyMatrix":
        captured = _cam_matrix_values(value)
    elif property_type in {"App::PropertyVector", "App::PropertyPosition"}:
        captured = [float(value.x), float(value.y), float(value.z)]
    elif property_type == "App::PropertyVectorList":
        captured = [[float(item.x), float(item.y), float(item.z)] for item in value]
    elif property_type == "App::PropertyMap":
        captured = dict(value or {})
    elif property_type == "App::PropertyColor":
        captured = [float(item) for item in tuple(value)]
    elif any(
        marker in property_type
        for marker in (
            "PropertyLength",
            "PropertyDistance",
            "PropertySpeed",
            "PropertyAngle",
            "PropertyQuantity",
        )
    ):
        captured = str(value)
    elif property_type.endswith("List"):
        captured = list(value or [])
    elif isinstance(value, (str, bool, int, float)) or value is None:
        captured = value
    else:
        raise RuntimeError(
            f"Cannot capture CAM rollback property {obj.Name}.{name} of type "
            f"{property_type!r}."
        )
    return {"type": property_type, "value": captured}


def _cam_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    states = []
    for obj in objects:
        properties = {}
        dynamic = {}
        for name in sorted(_properties(obj)):
            if name in {"ExpressionEngine", "Proxy", "Label"}:
                continue
            captured = _cam_capture_property(obj, name)
            if captured is None:
                continue
            properties[name] = captured
            try:
                statuses = list(obj.getPropertyStatus(name) or [])
            except Exception:
                statuses = []
            if 21 in statuses or "PropDynamic" in statuses:
                dynamic[name] = {
                    "group": str(obj.getGroupOfProperty(name) or "Human"),
                    "documentation": str(obj.getDocumentationOfProperty(name) or ""),
                    "statuses": [
                        value
                        for value in statuses
                        if value not in {21, "PropDynamic"}
                    ],
                }
        states.append(
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
                "proxy_kind": str(
                    getattr(obj, "VibeCADCAMProxyKind", "") or ""
                ),
                "properties": properties,
                "dynamic_properties": dynamic,
                "expressions": [
                    [str(path), str(expression)]
                    for path, expression in list(obj.ExpressionEngine or [])
                ],
                "frozen": _object_is_frozen(obj, "CAM"),
            }
        )
    return states


def _cam_resolve_link(doc: Any, name: str, context: str) -> Any:
    if not name:
        return None
    target = doc.getObject(str(name))
    if target is None:
        raise RuntimeError(f"{context} link target {name!r} is unavailable.")
    return target


def _cam_restore_property(
    doc: Any,
    obj: Any,
    name: str,
    state: Mapping[str, Any],
) -> None:
    property_type = str(state["type"])
    value = state["value"]
    if property_type == "Part::PropertyPartShape":
        restored = value
    elif property_type == "Part::PropertyShapeCache":
        if not isinstance(value, dict) or value.get("restore") != "invalidate":
            raise RuntimeError(
                f"CAM rollback cache state for {obj.Name}.{name} is malformed."
            )
        # None is the documented native setter operation that clears the
        # non-persistent cache without resolving or generating geometry.
        restored = None
    elif property_type in {
        "Materials::PropertyMaterial",
        "App::PropertyMaterial",
    }:
        restored = value
    elif property_type == "App::PropertyMaterialList":
        restored = list(value or [])
    elif property_type == "Path::PropertyPath":
        if value:
            from vibescript_cam_worker import path_from_records

            restored = path_from_records(value)
        else:
            import Path

            restored = Path.Path()
    elif "PropertyLinkSubList" in property_type:
        restored = [
            (
                _cam_resolve_link(doc, item["object"], f"{obj.Name}.{name}"),
                list(item["subelements"]),
            )
            for item in value
        ]
    elif "PropertyLinkSub" in property_type:
        restored = (
            None
            if value is None
            else (
                _cam_resolve_link(doc, value["object"], f"{obj.Name}.{name}"),
                list(value["subelements"]),
            )
        )
    elif "PropertyLinkList" in property_type:
        restored = [
            _cam_resolve_link(doc, item, f"{obj.Name}.{name}") for item in value
        ]
    elif "PropertyLink" in property_type:
        restored = _cam_resolve_link(doc, str(value or ""), f"{obj.Name}.{name}")
    elif property_type in {"App::PropertyPlacement", "App::PropertyMatrix"}:
        import FreeCAD as App

        matrix = App.Matrix()
        for matrix_name, matrix_value in zip(
            (
                "A11",
                "A12",
                "A13",
                "A14",
                "A21",
                "A22",
                "A23",
                "A24",
                "A31",
                "A32",
                "A33",
                "A34",
                "A41",
                "A42",
                "A43",
                "A44",
            ),
            value,
            strict=True,
        ):
            setattr(matrix, matrix_name, float(matrix_value))
        restored = App.Placement(matrix) if property_type.endswith("Placement") else matrix
    elif property_type in {"App::PropertyVector", "App::PropertyPosition"}:
        import FreeCAD as App

        restored = App.Vector(*value)
    elif property_type == "App::PropertyVectorList":
        import FreeCAD as App

        restored = [App.Vector(*item) for item in value]
    elif property_type == "App::PropertyMap":
        restored = dict(value)
    elif property_type == "App::PropertyColor":
        restored = tuple(value)
    else:
        restored = value
    setattr(obj, name, restored)


def _restore_cam_rollback_states(
    doc: Any,
    states: list[dict[str, Any]],
) -> list[str]:
    import VibeCADVibeScriptCAM as cam

    resolved: list[tuple[Any, dict[str, Any]]] = []
    failures = []
    for state in states:
        name = str(state["name"])
        try:
            obj = doc.getObject(name)
            if obj is None:
                obj = doc.addObject(str(state["type_id"]), name)
                if obj is None:
                    raise RuntimeError("FreeCAD returned no recreated object")
            if str(obj.TypeId) != str(state["type_id"]):
                raise RuntimeError(f"native type changed to {obj.TypeId!r}")
            _unfreeze_object(obj, "CAM")
            proxy_kind = str(state.get("proxy_kind") or "")
            if proxy_kind:
                cam.attach_proxy_kind(obj, proxy_kind)
            resolved.append((obj, state))
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(f"Could not recreate CAM rollback objects: {'; '.join(failures)}")

    for obj, state in resolved:
        for name, metadata in dict(state["dynamic_properties"]).items():
            property_state = dict(state["properties"])[name]
            if name not in _properties(obj):
                obj.addProperty(
                    str(property_state["type"]),
                    name,
                    str(metadata["group"]),
                    str(metadata["documentation"]),
                )
    for restore_links in (False, True):
        for obj, state in resolved:
            for name, property_state in dict(state["properties"]).items():
                is_link = "PropertyLink" in str(property_state["type"])
                if is_link != restore_links:
                    continue
                try:
                    _cam_restore_property(doc, obj, name, property_state)
                except Exception as exc:
                    failures.append(f"{obj.Name}.{name}: {type(exc).__name__}: {exc}")
    for obj, state in resolved:
        try:
            obj.Label = str(state["label"])
            for path, _expression in list(obj.ExpressionEngine or []):
                obj.setExpression(str(path), None)
            for path, expression in list(state["expressions"]):
                obj.setExpression(str(path), str(expression))
            for name, metadata in dict(state["dynamic_properties"]).items():
                statuses = list(metadata.get("statuses") or [])
                if statuses:
                    obj.setPropertyStatus(name, statuses)
            if state["frozen"]:
                _freeze_object(obj, "CAM")
        except Exception as exc:
            failures.append(f"{obj.Name}.finalize: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError(
            "CAM publication failed and accepted native state could not be fully "
            f"restored: {'; '.join(failures)}"
        )
    return [str(state["name"]) for _obj, state in resolved]


def _publish_cam_candidate(
    service: Any,
    prepared: dict[str, Any],
    validated: dict[str, Any],
    doc: Any,
) -> dict[str, Any]:
    """Atomically apply only worker-precomputed frozen native CAM state."""

    import VibeCADVibeScriptCAM as cam

    items = [dict(item) for item in validated["outputs"]]
    by_name = {str(item["name"]): item for item in items}
    if len(by_name) != len(items):
        raise RuntimeError("The CAM publication graph contains duplicate output names.")
    existing = _objects_by_output(doc, prepared)
    internal_before = _program_objects(doc, str(prepared["program_id"]), "cam")
    rollback_states = _cam_rollback_states(internal_before)
    rollback_names = {str(state["name"]) for state in rollback_states}
    downstream_uses = _preflight_output_updates(
        doc,
        list(internal_before),
        list(internal_before),
    )
    publication_state = validated.get("cam_publication_state")
    references = (
        list(publication_state.get("references") or [])
        if isinstance(publication_state, dict)
        else []
    )
    if not references:
        raise RuntimeError("CAM publication has no authenticated detached model inputs.")
    reference_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, reference in enumerate(references):
        if not isinstance(reference, dict) or reference.get("shape") is None:
            raise RuntimeError(f"CAM publication reference {index} is malformed.")
        key = _cam_reference_key(reference)
        if not all(key) or key in reference_by_key:
            raise RuntimeError("CAM publication references are ambiguous.")
        reference_by_key[key] = reference

    job_items = [item for item in items if item["type"] == "job"]
    stock_items = [item for item in items if item["type"] == "stock"]
    toolpath_items = [item for item in items if item["type"] == "toolpath"]
    tool_items = [item for item in items if item["type"] == "tool"]
    operation_items = [item for item in items if item["type"] == "operation"]
    if not (
        len(job_items) == len(stock_items) == len(toolpath_items) == 1
        and tool_items
        and operation_items
    ):
        raise RuntimeError("CAM publication requires one exact validated job graph.")
    job_item = job_items[0]
    stock_item = stock_items[0]
    toolpath_item = toolpath_items[0]
    job_name = str(job_item["name"])

    desired_keys = set(by_name)
    desired_keys.update(
        {
            f"{job_name}.operations",
            f"{job_name}.setup_sheet",
            f"{job_name}.model",
            f"{job_name}.tools",
        }
    )
    model_key_by_reference: dict[tuple[str, str], str] = {}
    for reference_key in reference_by_key:
        digest = hashlib.sha256(
            f"{reference_key[0]}\0{reference_key[1]}".encode("utf-8")
        ).hexdigest()[:16]
        key = f"{job_name}.model.{digest}"
        model_key_by_reference[reference_key] = key
        desired_keys.add(key)
    for item in tool_items:
        desired_keys.add(f"{item['name']}.bit")

    owned_by_key = _cam_auxiliary_objects(doc, prepared)
    job = existing.get(job_name)
    previously_replaced_sources = tuple(
        getattr(job, "VibeCADTimelineReplacedInputs", ()) or ()
    ) if job is not None else ()
    replace_source_keys: set[tuple[str, str]] = set()
    for reference_key in reference_by_key:
        source = doc.getObject(reference_key[1])
        if (
            source is None
            or getattr(source, "Document", None) is not doc
            or str(getattr(doc, "Uid", "") or "") != reference_key[0]
        ):
            raise RuntimeError(
                f"CAM source object {reference_key[1]!r} disappeared before publication."
            )
        view = getattr(source, "ViewObject", None)
        if (
            bool(getattr(view, "Visibility", False))
            or any(source is previous for previous in previously_replaced_sources)
        ):
            replace_source_keys.add(reference_key)
    job_reconciliation = (
        _capture_timeline_resource_reconciliation(
            doc,
            job,
            context=f"CAM Job {job_name!r}",
        )
        if job is not None
        else None
    )
    existing_job_resource_identities = {
        tuple(identity)
        for identity in (
            list(job_reconciliation["resource_identities"])
            if job_reconciliation is not None
            else []
        )
    }
    operation_names = {str(item["name"]) for item in operation_items}
    intended_job_resource_keys = (
        desired_keys - operation_names - {job_name}
    )

    def semantic_root(candidate: Any) -> Any:
        current = candidate
        visited: set[tuple[str, int]] = set()
        while _timeline_role(
            current,
            context=f"CAM object {candidate.Name!r}",
        ) == "resource":
            identity = _deletion_object_identity(
                current,
                context=f"CAM object {candidate.Name!r}",
            )
            if identity in visited:
                raise RuntimeError("The accepted CAM resource graph is cyclic.")
            visited.add(identity)
            owner = _timeline_owner(
                current,
                context=f"CAM object {candidate.Name!r}",
            )
            if owner is None:
                raise RuntimeError(
                    f"CAM resource {candidate.Name!r} has no semantic owner."
                )
            current = owner
        return current

    # Earlier development builds published Stock, controllers, tool bits, and
    # model clones as separate History roots.  They cannot be silently
    # re-parented because that would bypass native creation provenance.
    # Recreate only those exact identities, while preserving the Job and its
    # public machining operations.
    replacement_roots: list[Any] = []
    for key in sorted(intended_job_resource_keys):
        candidate = owned_by_key.get(key)
        if candidate is None:
            continue
        identity = _deletion_object_identity(
            candidate,
            context=f"CAM resource {key!r}",
        )
        if identity in existing_job_resource_identities:
            continue
        root_candidate = semantic_root(candidate)
        root_key = str(
            getattr(root_candidate, contracts.PROP_PROGRAM_OUTPUT, "") or ""
        )
        if root_candidate is job or root_key in operation_names:
            raise RuntimeError(
                f"CAM resource {key!r} has incompatible accepted History "
                "ownership and cannot be migrated safely."
            )
        if all(root_candidate is not existing_root for existing_root in replacement_roots):
            replacement_roots.append(root_candidate)

    replacement_root_set = set(replacement_roots)
    for key, candidate in list(owned_by_key.items()):
        if semantic_root(candidate) not in replacement_root_set:
            continue
        owned_by_key.pop(key, None)
        existing.pop(key, None)

    retired_job_resource_identities = {
        tuple(identity)
        for identity, key in zip(
            (
                list(job_reconciliation["resource_identities"])
                if job_reconciliation is not None
                else []
            ),
            (
                list(job_reconciliation["resource_keys"])
                if job_reconciliation is not None
                else []
            ),
        )
        if str(key) not in desired_keys
    }
    retired_roots = list(replacement_roots)
    for key, obj in list(owned_by_key.items()):
        if key in desired_keys:
            continue
        identity = _deletion_object_identity(
            obj,
            context=f"Retired CAM object {key!r}",
        )
        if identity in retired_job_resource_identities:
            continue
        root_candidate = semantic_root(obj)
        if root_candidate is job:
            raise RuntimeError(
                f"Retired CAM object {key!r} is absent from the captured Job "
                "resource graph."
            )
        if all(root_candidate is not existing_root for existing_root in retired_roots):
            retired_roots.append(root_candidate)

    retired = retired_roots
    retired_deletion = _prepare_timeline_deletion(doc, retired)
    retired_targets = list(retired_deletion["delete_objects"])
    retired_uses = _external_uses(
        doc,
        retired_targets,
        [*internal_before, *retired_targets],
    )
    if retired_uses:
        raise _reference_error(
            "Cannot retire native CAM objects still referenced by human-created or foreign objects",
            retired_uses,
        )

    roots: dict[str, Any] = {}
    auxiliary: dict[str, Any] = {}
    created: list[Any] = []
    removed: list[str] = []
    transaction_open = False

    def ensure_auxiliary(
        key: str,
        native_type: str,
        factory,
    ) -> Any:
        obj = owned_by_key.get(key)
        if obj is None:
            obj = factory(_internal_name(prepared, key))
            created.append(obj)
        if str(getattr(obj, "TypeId", "") or "") != native_type:
            raise RuntimeError(
                f"Stable CAM object {key!r} changed native type to {obj.TypeId!r}."
            )
        auxiliary[key] = obj
        return obj

    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish CAM VibeScript: {prepared['program_name']}"
            )
            transaction_open = True
        if job_reconciliation is not None:
            _stage_timeline_resource_reconciliation(
                doc,
                job,
                job_reconciliation,
                context=f"CAM Job {job_name!r}",
            )
        removed.extend(_remove_timeline_deletion(doc, retired_deletion))

        operations_group = ensure_auxiliary(
            f"{job_name}.operations",
            "App::DocumentObjectGroup",
            lambda name: doc.addObject("App::DocumentObjectGroup", name),
        )
        model_group = ensure_auxiliary(
            f"{job_name}.model",
            "App::DocumentObjectGroup",
            lambda name: doc.addObject("App::DocumentObjectGroup", name),
        )
        tools_group = ensure_auxiliary(
            f"{job_name}.tools",
            "App::DocumentObjectGroup",
            lambda name: doc.addObject("App::DocumentObjectGroup", name),
        )
        for obj, kind in (
            (operations_group, "group:operations"),
            (model_group, "group:model"),
            (tools_group, "group:tools"),
        ):
            _unfreeze_object(obj, "CAM")
            cam.mark_proxy_kind(obj, kind)

        model_objects: dict[tuple[str, str], Any] = {}
        for reference_key, key in model_key_by_reference.items():
            reference = reference_by_key[reference_key]
            clone = ensure_auxiliary(
                key,
                "Part::Feature",
                lambda name: doc.addObject("Part::Feature", name),
            )
            _unfreeze_object(clone, "CAM")
            cam.mark_proxy_kind(clone, "model_clone")
            shape = reference["shape"]
            if shape.isNull() or not shape.isValid():
                raise RuntimeError(f"CAM model snapshot {reference_key[1]!r} is invalid.")
            clone.Shape = shape
            clone.Label = str(reference.get("label") or reference_key[1])
            _add_property(
                clone,
                "App::PropertyLink",
                "VibeCADCAMOriginal",
                "Human source object represented by this frozen model snapshot.",
            )
            source = doc.getObject(reference_key[1])
            if source is None:
                raise RuntimeError(
                    f"CAM source object {reference_key[1]!r} disappeared before publication."
                )
            clone.VibeCADCAMOriginal = source
            _add_string_property(
                clone,
                "VibeCADCAMReferenceIdentity",
                "Authenticated source identity for this frozen model snapshot.",
            )
            clone.VibeCADCAMReferenceIdentity = json.dumps(
                dict(reference.get("identity") or {}),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            model_objects[reference_key] = clone

        tool_bits: dict[str, Any] = {}
        for item in tool_items:
            name = str(item["name"])
            data = _cam_data(item)
            kind = str(data["kind"])
            key = f"{name}.bit"
            bit = ensure_auxiliary(
                key,
                "Part::FeaturePython",
                lambda internal_name, tool_kind=kind: cam.create_tool_bit(
                    doc, internal_name, tool_kind
                ),
            )
            _unfreeze_object(bit, "CAM")
            if not cam.tool_bit_is_compatible(bit, kind):
                cam.attach_tool_bit_proxy(bit, kind)
            bit.Label = str(data["tool_bit_label"])
            bit.Shape = item["detached_shape"]
            bit.ToolBitID = f"{prepared['program_id']}:{name}"
            bit.ShapeID = str(data["shape_id"])
            bit.ShapeType = str(data["shape_type"])
            geometry = dict(data["geometry"])
            for property_name in (
                "Diameter",
                "Length",
                "Flutes",
                "SpindleDirection",
                "CuttingEdgeHeight",
                "ShankDiameter",
                "TipAngle",
                "CuttingEdgeAngle",
                "TipDiameter",
            ):
                value = geometry.get(property_name, 0)
                if property_name in {"TipAngle", "CuttingEdgeAngle"}:
                    value = f"{float(value):.17g} deg"
                elif property_name not in {"Flutes", "SpindleDirection"}:
                    value = f"{float(value):.17g} mm"
                setattr(bit, property_name, value)
            tool_bits[name] = bit

        # A CAM Job is one exact History block containing its native setup
        # resources.  Public machining operations are separate later blocks.
        # Create the complete Job block first so native creation provenance is
        # contiguous; each operation is created and published only after the
        # Job block has consumed that prefix.
        job_resource_root_items = [
            stock_item,
            *tool_items,
            toolpath_item,
        ]
        if (
            len(job_resource_root_items) + len(operation_items) + 1
            != len(items)
            or {
                str(item["name"])
                for item in [
                    *job_resource_root_items,
                    *operation_items,
                    job_item,
                ]
            }
            != set(by_name)
        ):
            raise RuntimeError(
                "CAM publication root chronology does not cover the exact "
                "validated job graph."
            )
        for item in job_resource_root_items:
            name = str(item["name"])
            output_type = str(item["type"])
            data = _cam_data(item)
            obj = existing.get(name)
            if obj is None:
                obj = cam.create_root(
                    doc,
                    _internal_name(prepared, name),
                    output_type,
                    data,
                )
                created.append(obj)
            elif str(getattr(obj, "TypeId", "") or "") != cam.root_type(
                output_type
            ):
                raise RuntimeError(
                    f"Stable CAM output {name!r} cannot change native type."
                )
            _unfreeze_object(obj, "CAM")
            if output_type != "toolpath" and not cam.proxy_is_compatible(
                obj,
                output_type,
                data,
            ):
                cam.attach_root_proxy(obj, output_type, data)
            roots[name] = obj

        setup_sheet = ensure_auxiliary(
            f"{job_name}.setup_sheet",
            "App::FeaturePython",
            lambda name: cam.create_setup_sheet(doc, name),
        )
        _unfreeze_object(setup_sheet, "CAM")
        if str(getattr(setup_sheet, cam.PROP_PROXY_KIND, "") or "") != "setup_sheet":
            cam.attach_proxy_kind(setup_sheet, "setup_sheet")

        job_data = _cam_data(job_item)
        job = existing.get(job_name)
        if job is None:
            job = cam.create_root(
                doc,
                _internal_name(prepared, job_name),
                "job",
                job_data,
            )
            created.append(job)
        elif str(getattr(job, "TypeId", "") or "") != cam.root_type("job"):
            raise RuntimeError(
                f"Stable CAM output {job_name!r} cannot change native type."
            )
        _unfreeze_object(job, "CAM")
        if not cam.proxy_is_compatible(job, "job", job_data):
            cam.attach_root_proxy(job, "job", job_data)
        roots[job_name] = job

        stock = roots[str(stock_item["name"])]
        toolpath = roots[str(toolpath_item["name"])]
        from Path.Base import Util as path_timeline

        stock_data = _cam_data(stock_item)
        postprocess = dict(_cam_data(toolpath_item)["postprocess"])
        _cam_clear_expressions(job, {"GeometryTolerance"})
        job.Label = str(job_data["label"])
        job.GeometryTolerance = f"{float(job_data['geometry_tolerance_mm']):.17g} mm"
        job.Fixtures = list(job_data["fixtures"])
        job.Description = str(job_data["description"])
        job.SplitOutput = False
        job.JobType = "2.5D"
        job.OrderOutputBy = "Operation"
        job.PostProcessor = str(postprocess["processor"])
        job.PostProcessorArgs = " ".join(postprocess["arguments"])
        job.PostProcessorOutputFile = ""
        job.LastPostProcessOutput = ""
        job.Stock = stock
        job.Operations = operations_group
        job.SetupSheet = setup_sheet
        job.Model = model_group
        job.Tools = tools_group
        job.Path = toolpath_item["detached_path"]

        stock.Label = str(stock_data["label"])
        stock.Shape = stock_item["detached_shape"]
        stock.Base = model_group
        margins = dict(stock_data["margins_mm"])
        for property_name, key in (
            ("ExtXneg", "x_negative"),
            ("ExtXpos", "x_positive"),
            ("ExtYneg", "y_negative"),
            ("ExtYpos", "y_positive"),
            ("ExtZneg", "z_negative"),
            ("ExtZpos", "z_positive"),
        ):
            setattr(stock, property_name, f"{float(margins[key]):.17g} mm")

        for item in tool_items:
            name = str(item["name"])
            obj = roots[name]
            data = _cam_data(item)
            controller = dict(data["controller"])
            _cam_clear_expressions(
                obj,
                {"VertFeed", "HorizFeed", "RampFeed", "LeadInFeed", "LeadOutFeed"},
            )
            obj.Label = str(data["label"])
            obj.ToolNumber = int(controller["tool_number"])
            obj.SpindleSpeed = float(controller["spindle_rpm"])
            obj.SpindleDir = str(controller["spindle_direction"])
            obj.HorizFeed = f"{float(controller['horizontal_feed_mm_per_min']):.17g} mm/min"
            obj.VertFeed = f"{float(controller['vertical_feed_mm_per_min']):.17g} mm/min"
            obj.RampFeed = obj.HorizFeed
            obj.LeadInFeed = obj.HorizFeed
            obj.LeadOutFeed = obj.HorizFeed
            obj.Tool = tool_bits[name]

        toolpath.Label = str(_cam_data(toolpath_item)["label"])
        toolpath.Path = toolpath_item["detached_path"]
        operations_group.Label = "Operations"
        model_group.Label = "Model"
        model_group.Group = list(model_objects.values())
        tools_group.Label = "Tools"
        tools_group.Group = [roots[str(item["name"])] for item in tool_items]
        setup_sheet.Label = "Setup Sheet"

        definition_by_root = {
            str(item["name"]): _definition(item) for item in items
        }
        type_by_root = {str(item["name"]): str(item["type"]) for item in items}

        def set_publication_metadata(key: str, obj: Any) -> None:
            root = key.partition(".")[0]
            definition = definition_by_root[root]
            output_type = type_by_root[root] if key == root else "cam_auxiliary"
            _set_metadata(obj, prepared, key, output_type, definition)
            root_item = by_name[root]
            _add_string_property(
                obj,
                PROP_CAM_VALIDATION,
                "Authenticated bounded native CAM publication summary.",
            )
            setattr(
                obj,
                PROP_CAM_VALIDATION,
                json.dumps(
                    _cam_validation_summary(root_item),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )

        # Metadata is part of the exact resource identity used by History and
        # therefore precedes semantic publication.  Freezing remains the final
        # step, after the Operations group receives its public members.
        for key, obj in {**roots, **auxiliary}.items():
            set_publication_metadata(key, obj)

        job_resources = [
            *list(model_objects.values()),
            model_group,
            stock,
            *[
                resource
                for item in tool_items
                for resource in (
                    tool_bits[str(item["name"])],
                    roots[str(item["name"])],
                )
            ],
            tools_group,
            operations_group,
            setup_sheet,
            toolpath,
        ]
        path_timeline.markTimelineOperation(job)
        for resource in job_resources:
            path_timeline.markTimelineResource(resource, job)

        replaced_sources: list[Any] = []
        for clone in model_objects.values():
            source = getattr(clone, "VibeCADCAMOriginal", None)
            if (
                source is None
                or getattr(source, "Document", None) is not doc
                or doc.getObject(source.Name) is not source
            ):
                raise RuntimeError(
                    f"CAM model clone {clone.Name!r} lost its exact public source."
                )
            source_key = (str(getattr(doc, "Uid", "") or ""), str(source.Name))
            if (
                source_key in replace_source_keys
                and all(
                    source is not existing_source
                    for existing_source in replaced_sources
                )
            ):
                replaced_sources.append(source)
        path_timeline.markTimelineReplacedInputs(job, replaced_sources)

        retired_job_resources: list[Any]
        if job_reconciliation is None:
            _publish_new_timeline_resource_block(
                doc,
                job,
                job_resources,
                context=f"CAM Job {job_name!r}",
            )
            retired_job_resources = []
        else:
            retired_job_resources = (
                _finalize_timeline_resource_reconciliation(
                    doc,
                    job,
                    job_reconciliation,
                    job_resources,
                    context=f"CAM Job {job_name!r}",
                )
            )
        removed.extend(
            _remove_reconciled_timeline_resources(
                doc,
                retired_job_resources,
                context=f"CAM Job {job_name!r}",
            )
        )
        # Publication verifies that pre-existing History state is unchanged
        # while it consumes the new semantic block.  Apply the declared visual
        # replacement only after that atomic check succeeds.
        for source in replaced_sources:
            view = getattr(source, "ViewObject", None)
            if view is not None:
                view.Visibility = False

        # Each public machining operation is a later, independently editable
        # History step.  Create, configure, and publish it before creating the
        # next one so provisional creation generations cannot interleave.
        for item in operation_items:
            name = str(item["name"])
            data = _cam_data(item)
            obj = existing.get(name)
            if obj is None:
                obj = cam.create_root(
                    doc,
                    _internal_name(prepared, name),
                    "operation",
                    data,
                )
                created.append(obj)
            elif str(getattr(obj, "TypeId", "") or "") != cam.root_type(
                "operation"
            ):
                raise RuntimeError(
                    f"Stable CAM output {name!r} cannot change native type."
                )
            _unfreeze_object(obj, "CAM")
            if not cam.proxy_is_compatible(obj, "operation", data):
                cam.attach_root_proxy(obj, "operation", data)
            roots[name] = obj

            properties = dict(data["properties"])
            _cam_clear_expressions(
                obj,
                {"StartDepth", "FinalDepth", "StepDown", "PeckDepth"},
            )
            obj.Label = str(data["label"])
            obj.ToolController = roots[str(data["tool_output"])]
            grouped: dict[tuple[str, str], list[str]] = {}
            for descriptor in list(data["selections"]):
                key = _cam_reference_key(dict(descriptor["source"]))
                grouped.setdefault(key, []).append(str(descriptor["face"]))
            obj.Base = [
                (model_objects[key], faces) for key, faces in grouped.items()
            ]
            obj.StartDepth = f"{float(properties['start_depth_mm']):.17g} mm"
            obj.FinalDepth = f"{float(properties['final_depth_mm']):.17g} mm"
            obj.StepDown = (
                f"{float(properties.get('step_down_mm', 0.0)):.17g} mm"
            )
            obj.StepOver = int(properties.get("step_over_percent", 0))
            if "side" in properties:
                obj.Side = str(properties["side"])
            if "boundary" in properties:
                obj.BoundaryShape = str(properties["boundary"])
            obj.PeckEnabled = bool(properties.get("peck_enabled", False))
            obj.PeckDepth = (
                f"{float(properties.get('peck_depth_mm', 0.0)):.17g} mm"
            )
            obj.Strategy = str(data["strategy"]).title()
            obj.CoolantMode = str(properties["coolant"])
            obj.Path = item["detached_path"]
            set_publication_metadata(name, obj)
            path_timeline.markTimelineOperation(obj)
            if _is_created_timeline_object(doc, obj, created):
                _publish_new_timeline_resource_block(
                    doc,
                    obj,
                    [],
                    context=f"CAM machining operation {obj.Name!r}",
                )

        operations_group.Group = [
            roots[str(item["name"])] for item in operation_items
        ]
        all_objects = {**roots, **auxiliary}
        for key, obj in all_objects.items():
            _cam_publication_checkpoint("before_freeze", key, obj)
            _freeze_object(obj, "CAM")

        downstream_refresh = _refresh_external_consumers(
            downstream_uses,
            revision=str(prepared["revision"]),
        )
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        created_names = [str(getattr(obj, "Name", "") or "") for obj in created]
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        rollback_failures = []
        if rollback_states:
            try:
                _restore_cam_rollback_states(doc, rollback_states)
            except Exception as rollback_error:
                rollback_failures.append(str(rollback_error))
        try:
            _remove_failed_domain_creations(
                doc,
                [
                    name
                    for name in created_names
                    if name and name not in rollback_names
                ],
            )
        except Exception as cleanup_error:
            rollback_failures.append(
                "failed candidate objects could not be removed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        if rollback_failures:
            raise RuntimeError(
                f"{publication_error} Explicit CAM rollback failure: "
                f"{' | '.join(rollback_failures)}"
            ) from publication_error
        raise

    live_outputs = {}
    published_outputs = []
    for item in items:
        name = str(item["name"])
        obj = roots[name]
        summary = {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_type": str(item["type"]),
            "derived_state": str(
                getattr(obj, reference_contracts.PROP_DERIVED_STATE, "") or ""
            ),
            "stale_reason": str(
                getattr(obj, reference_contracts.PROP_STALE_REASON, "") or ""
            ),
            "source_revision": str(
                getattr(obj, reference_contracts.PROP_SOURCE_REVISION, "") or ""
            ),
            "cam_data": _cam_validation_summary(item),
            "frozen": _object_is_frozen(obj, "CAM"),
        }
        live_outputs[name] = summary
        published_outputs.append({"name": name, **summary})
    return {
        "ok": True,
        "outputs": published_outputs,
        "live_outputs": live_outputs,
        "created_objects": [str(obj.Name) for obj in created],
        "retired_objects": removed,
        "downstream_references": {
            "safe_whole_object_uses": scripted_publication.json_reference_uses(
                downstream_uses
            ),
            **downstream_refresh,
        },
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
        "geometry_generation_on_document_thread": False,
        "simulation_on_document_thread": False,
        "postprocessing_on_document_thread": False,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


_TECHDRAW_METADATA_PROPERTIES = (
    contracts.PROP_PROGRAM_ID,
    contracts.PROP_PROGRAM_DOMAIN,
    contracts.PROP_PROGRAM_WORKBENCH,
    contracts.PROP_PROGRAM_REVISION,
    contracts.PROP_PROGRAM_OUTPUT,
    PROP_OUTPUT_TYPE,
    PROP_DEFINITION,
    PROP_INPUT_OBJECTS,
    PROP_INPUT_SNAPSHOTS,
    PROP_TECHDRAW_VALIDATION,
    reference_contracts.PROP_DERIVED_STATE,
    reference_contracts.PROP_STALE_REASON,
    reference_contracts.PROP_SOURCE_REVISION,
)


def _techdraw_names(values: Any) -> list[str]:
    return [str(getattr(value, "Name", "") or "") for value in list(values or [])]


def _techdraw_capture_state(obj: Any) -> dict[str, Any]:
    type_id = str(obj.TypeId)
    state: dict[str, Any] = {
        "name": str(obj.Name),
        "type_id": type_id,
        "label": str(obj.Label),
        "frozen": _object_is_frozen(obj, "TechDraw"),
        "metadata": {},
        "core": {},
    }
    for name in _TECHDRAW_METADATA_PROPERTIES:
        if name not in _properties(obj):
            continue
        value = getattr(obj, name)
        if name == PROP_INPUT_OBJECTS:
            state["metadata"][name] = _techdraw_names(value)
        else:
            state["metadata"][name] = str(value)
    core = state["core"]
    if type_id == "TechDraw::DrawTemplate":
        core.update(
            {
                "width": float(obj.Width),
                "height": float(obj.Height),
                "orientation": str(obj.Orientation),
                "editable_texts": dict(obj.EditableTexts),
            }
        )
    elif type_id == "TechDraw::DrawPage":
        core.update(
            {
                "template": str(getattr(getattr(obj, "Template", None), "Name", "")),
                "views": _techdraw_names(obj.Views),
                "projection_type": str(obj.ProjectionType),
                "scale": float(obj.Scale),
                "keep_updated": bool(obj.KeepUpdated),
            }
        )
    elif type_id == "TechDraw::DrawProjGroup":
        core.update(
            {
                "sources": _techdraw_names(obj.Source),
                "projection_type": str(obj.ProjectionType),
                "scale_type": str(obj.ScaleType),
                "scale": float(obj.Scale),
                "x": float(obj.X),
                "y": float(obj.Y),
                "spacing_x": float(obj.spacingX),
                "spacing_y": float(obj.spacingY),
                "auto_distribute": bool(obj.AutoDistribute),
                "views": _techdraw_names(obj.Views),
            }
        )
    elif type_id in {"TechDraw::DrawViewPart", "TechDraw::DrawProjGroupItem"}:
        core.update(
            {
                "sources": _techdraw_names(obj.Source),
                "direction": [float(value) for value in obj.Direction],
                "x_direction": [float(value) for value in obj.XDirection],
                "scale_type": str(obj.ScaleType),
                "scale": float(obj.Scale),
                "x": float(obj.X),
                "y": float(obj.Y),
                "line_flags": {
                    name: bool(getattr(obj, name))
                    for name in (
                        "HardHidden",
                        "SmoothHidden",
                        "SeamHidden",
                        "IsoHidden",
                        "SmoothVisible",
                        "SeamVisible",
                        "IsoVisible",
                    )
                    if name in _properties(obj)
                },
                "snapshot": obj.getPrecomputedProjection(),
            }
        )
    elif type_id == "TechDraw::DrawViewDimension":
        core.update(
            {
                "dimension_type": str(obj.Type),
                "measure_type": str(obj.MeasureType),
                "references": [
                    (
                        str(value[0].Name),
                        [
                            str(subelement)
                            for subelement in (
                                value[1]
                                if isinstance(value[1], (tuple, list))
                                else (value[1],)
                            )
                        ],
                    )
                    for value in list(obj.References2D or [])
                ],
                "x": float(obj.X),
                "y": float(obj.Y),
                "format_spec": str(obj.FormatSpec),
                "over_tolerance": float(obj.OverTolerance),
                "under_tolerance": float(obj.UnderTolerance),
                "show_units": bool(obj.ShowUnits),
                "snapshot": obj.getPrecomputedDimension(),
            }
        )
    elif type_id == "TechDraw::DrawViewAnnotation":
        core.update(
            {
                "text": [str(value) for value in obj.Text],
                "x": float(obj.X),
                "y": float(obj.Y),
                "text_size": float(obj.TextSize),
                "text_alignment": str(obj.TextAlignment),
            }
        )
    return state


def _techdraw_rollback_states(objects: list[Any]) -> list[dict[str, Any]]:
    return [_techdraw_capture_state(obj) for obj in objects]


def _techdraw_resolve(doc: Any, name: str, *, context: str) -> Any:
    obj = doc.getObject(str(name or ""))
    if obj is None:
        raise RuntimeError(f"{context} refers to missing object {name!r}.")
    return obj


def _restore_techdraw_rollback_states(
    doc: Any,
    states: list[dict[str, Any]],
) -> list[str]:
    import FreeCAD as App

    restored = []
    pages = []
    for state in states:
        obj = _techdraw_resolve(doc, state["name"], context="TechDraw rollback")
        if str(obj.TypeId) != state["type_id"]:
            raise RuntimeError(
                f"TechDraw rollback object {obj.Name!r} changed native type."
            )
        _unfreeze_object(obj, "TechDraw")
        obj.Label = state["label"]
        core = state["core"]
        type_id = state["type_id"]
        if type_id == "TechDraw::DrawTemplate":
            obj.Width = core["width"]
            obj.Height = core["height"]
            obj.Orientation = core["orientation"]
            obj.EditableTexts = dict(core["editable_texts"])
        elif type_id == "TechDraw::DrawPage":
            pages.append((obj, core))
        elif type_id == "TechDraw::DrawProjGroup":
            obj.Source = [
                _techdraw_resolve(doc, name, context=f"{obj.Name}.Source")
                for name in core["sources"]
            ]
            obj.ProjectionType = core["projection_type"]
            obj.ScaleType = core["scale_type"]
            obj.Scale = core["scale"]
            obj.X = core["x"]
            obj.Y = core["y"]
            obj.spacingX = core["spacing_x"]
            obj.spacingY = core["spacing_y"]
            obj.AutoDistribute = core["auto_distribute"]
        elif type_id in {"TechDraw::DrawViewPart", "TechDraw::DrawProjGroupItem"}:
            obj.Source = [
                _techdraw_resolve(doc, name, context=f"{obj.Name}.Source")
                for name in core["sources"]
            ]
            obj.Direction = App.Vector(*core["direction"])
            obj.XDirection = App.Vector(*core["x_direction"])
            obj.ScaleType = core["scale_type"]
            obj.Scale = core["scale"]
            obj.X = core["x"]
            obj.Y = core["y"]
            for name, value in core["line_flags"].items():
                setattr(obj, name, value)
            obj.setPrecomputedProjection(core["snapshot"])
        elif type_id == "TechDraw::DrawViewDimension":
            obj.Type = core["dimension_type"]
            obj.MeasureType = core["measure_type"]
            obj.References2D = [
                (
                    _techdraw_resolve(doc, name, context=f"{obj.Name}.References2D"),
                    tuple(subelements),
                )
                for name, subelements in core["references"]
            ]
            obj.X = core["x"]
            obj.Y = core["y"]
            obj.FormatSpec = core["format_spec"]
            obj.OverTolerance = core["over_tolerance"]
            obj.UnderTolerance = core["under_tolerance"]
            obj.ShowUnits = core["show_units"]
            obj.setPrecomputedDimension(core["snapshot"])
        elif type_id == "TechDraw::DrawViewAnnotation":
            obj.Text = list(core["text"])
            obj.X = core["x"]
            obj.Y = core["y"]
            obj.TextSize = core["text_size"]
            obj.TextAlignment = core["text_alignment"]
        for name, value in state["metadata"].items():
            if name == PROP_INPUT_OBJECTS:
                setattr(
                    obj,
                    name,
                    [
                        _techdraw_resolve(doc, target, context=f"{obj.Name}.{name}")
                        for target in value
                    ],
                )
            else:
                setattr(obj, name, value)
        restored.append(str(obj.Name))
    for page, core in pages:
        page.Template = _techdraw_resolve(
            doc, core["template"], context=f"{page.Name}.Template"
        )
        page.ProjectionType = core["projection_type"]
        page.Scale = core["scale"]
        page.KeepUpdated = core["keep_updated"]
        for view in list(page.Views or []):
            page.removeView(view)
        for name in core["views"]:
            page.addView(
                _techdraw_resolve(doc, name, context=f"{page.Name}.Views")
            )
    for state in states:
        if state["frozen"]:
            obj = _techdraw_resolve(doc, state["name"], context="TechDraw freeze")
            _freeze_object(obj, "TechDraw")
    return restored


def _remove_techdraw_objects(doc: Any, objects: list[Any]) -> list[str]:
    """Remove a native drawing graph in TechDraw dependency order."""

    targets = {
        str(obj.Name): obj
        for obj in objects
        if getattr(obj, "Name", None) and doc.getObject(str(obj.Name)) is not None
    }
    target_types = {name: str(obj.TypeId) for name, obj in targets.items()}
    removed: list[str] = []

    def remove_name(name: str) -> None:
        if doc.getObject(name) is not None:
            doc.removeObject(name)
        if name not in removed:
            removed.append(name)

    for obj in targets.values():
        _unfreeze_object(obj, "TechDraw")

    # Dimensions own link-subelement references into projected children.
    for name, obj in list(targets.items()):
        if target_types[name] == "TechDraw::DrawViewDimension":
            remove_name(name)

    # A projection group's native purge API clears Anchor and Views safely.
    for name, group in list(targets.items()):
        if target_types[name] != "TechDraw::DrawProjGroup":
            continue
        child_names = [str(child.Name) for child in list(group.Views or [])]
        unmanaged = [child for child in child_names if child not in targets]
        if unmanaged:
            raise RuntimeError(
                f"Cannot remove projection group {name!r}; it contains unmanaged "
                f"children {unmanaged!r}."
            )
        group.purgeProjections()
        for child_name in child_names:
            if doc.getObject(child_name) is not None:
                raise RuntimeError(
                    f"Projection child {child_name!r} survived native group purge."
                )
            if child_name not in removed:
                removed.append(child_name)

    # A direction retired from a surviving group must also use the native API.
    for name, child in list(targets.items()):
        if (
            doc.getObject(name) is None
            or target_types[name] != "TechDraw::DrawProjGroupItem"
        ):
            continue
        # DrawProjGroup.Views is the native ownership contract.  A projection
        # item's generic InList is not authoritative after History has staged
        # its resource block for deletion and may already omit the collection.
        parents = [
            candidate
            for candidate in list(doc.Objects)
            if str(getattr(candidate, "TypeId", "") or "")
            == "TechDraw::DrawProjGroup"
            and child in list(candidate.Views or [])
        ]
        if len(parents) != 1:
            raise RuntimeError(
                f"Projection child {name!r} does not have exactly one native group."
            )
        parent = parents[0]
        direction = str(child.Type)
        parent.removeProjection(direction)
        if doc.getObject(name) is not None:
            raise RuntimeError(
                f"Projection child {name!r} survived native direction removal."
            )
        removed.append(name)

    # Pages are removed before their remaining views and templates so no live
    # collection retains a dangling link during document teardown.
    for name, obj in list(targets.items()):
        if (
            target_types[name] == "TechDraw::DrawPage"
            and doc.getObject(name) is not None
        ):
            remove_name(name)

    rank = {
        "TechDraw::DrawViewAnnotation": 0,
        "TechDraw::DrawViewPart": 1,
        "TechDraw::DrawProjGroup": 2,
        "TechDraw::DrawTemplate": 3,
    }
    for name, obj in sorted(
        targets.items(),
        key=lambda item: rank.get(target_types[item[0]], 2),
    ):
        if doc.getObject(name) is not None:
            remove_name(name)
    return removed


def _remove_techdraw_timeline_deletion(
    doc: Any,
    deletion: Mapping[str, Any],
) -> list[str]:
    """Consume one native deletion plan using TechDraw's required APIs."""

    # Delete public owners before their internal resources.  Transaction undo
    # then restores resources first, so Page.Template, projection-group Views,
    # and dimension references can resolve their exact targets in one pass.
    removed = _remove_techdraw_objects(
        doc,
        [
            *list(deletion["root_objects"]),
            *list(deletion["resource_objects"]),
        ],
    )
    _finish_timeline_deletion(doc, deletion)
    return removed


def _publish_techdraw_candidate(
    service: Any,
    prepared: Mapping[str, Any],
    validated: Mapping[str, Any],
    doc: Any,
) -> dict[str, Any]:
    items = [dict(item) for item in list(validated["outputs"])]
    by_name = {str(item["name"]): item for item in items}
    if len(by_name) != len(items):
        raise RuntimeError("TechDraw publication received duplicate output names.")
    existing = _objects_by_output(doc, prepared)
    internal_before = _program_objects(
        doc, str(prepared["program_id"]), "techdraw"
    )
    rollback_states = _techdraw_rollback_states(internal_before)
    rollback_names = {str(state["name"]) for state in rollback_states}
    desired_names = set(by_name)
    retired = _retired_program_objects(doc, prepared, desired_names)
    outputs: dict[str, Any] = {}
    created: list[Any] = []

    expected_native = {
        output_type: _native_type(output_type, "techdraw")
        for output_type in (
            "page",
            "template",
            "view",
            "projection",
            "dimension",
            "annotation",
        )
    }
    updated = []
    for item in items:
        name = str(item["name"])
        output_type = str(item["type"])
        obj = existing.get(name)
        if obj is not None:
            if str(obj.TypeId) != expected_native[output_type]:
                raise RuntimeError(
                    f"Stable TechDraw output {name!r} cannot change native type "
                    f"from {obj.TypeId!r} to {expected_native[output_type]!r}."
                )
            updated.append(obj)

    page_owner = {}
    page_reconciliations: dict[str, dict[str, Any]] = {}
    page_template_output: dict[str, str] = {}
    for item in items:
        if str(item["type"]) != "page":
            continue
        page_name = str(item["name"])
        page_template_output[page_name] = str(
            _techdraw_data(item)["template_output"]
        )
        page = existing.get(page_name)
        if page is not None:
            page_reconciliations[page_name] = (
                _capture_timeline_resource_reconciliation(
                    doc,
                    page,
                    context=f"TechDraw Page {page_name!r}",
                )
            )
        for content_name in list(_techdraw_data(item)["content_outputs"]):
            page_owner[str(content_name)] = page_name

    desired_child_maps: dict[str, dict[str, Any]] = {}
    retired_projection_children: dict[str, list[Any]] = {}
    projection_reconciliations: dict[str, dict[str, Any]] = {}
    for item in items:
        if str(item["type"]) != "projection":
            continue
        name = str(item["name"])
        group = existing.get(name)
        if group is None:
            continue
        projection_reconciliations[name] = (
            _capture_timeline_resource_reconciliation(
                doc,
                group,
                context=f"TechDraw Projection {name!r}",
            )
        )
        existing_children = _techdraw_projection_child_map(group)
        desired_types = {
            _techdraw_projection_type(direction): direction
            for direction in list(_techdraw_data(item)["directions"])
        }
        child_map = {}
        for native_direction, child in existing_children.items():
            if child not in internal_before:
                raise RuntimeError(
                    f"Projection group {name!r} contains unmanaged child {child.Name!r}."
                )
            direction = desired_types.get(native_direction)
            if direction is None:
                retired_projection_children.setdefault(name, []).append(child)
            else:
                child_map[direction] = child
                updated.append(child)
        desired_child_maps[name] = child_map

    # A Template is implementation state of its Page.  Recreate templates
    # authored by older builds as standalone History roots; exact existing
    # Page resources remain stable and are reconciled in place.
    forced_retired_roots: list[Any] = []
    for page_name, template_name in page_template_output.items():
        template = existing.get(template_name)
        if template is None:
            continue
        capture = page_reconciliations.get(page_name)
        captured_identities = {
            tuple(identity)
            for identity in (
                list(capture["resource_identities"]) if capture else []
            )
        }
        identity = _deletion_object_identity(
            template,
            context=f"TechDraw Template {template_name!r}",
        )
        if identity in captured_identities:
            continue
        forced_retired_roots.append(template)
        existing.pop(template_name, None)

    surviving_resource_identities = {
        tuple(identity)
        for capture in [
            *page_reconciliations.values(),
            *projection_reconciliations.values(),
        ]
        for identity in list(capture["resource_identities"])
    }
    retired = [
        obj
        for obj in retired
        if _deletion_object_identity(
            obj,
            context="A retired TechDraw object",
        )
        not in surviving_resource_identities
    ]
    for root in forced_retired_roots:
        if all(root is not existing_root for existing_root in retired):
            retired.append(root)

    downstream_uses = _preflight_output_updates(doc, updated, internal_before)
    retired_deletion = _prepare_timeline_deletion(doc, retired)
    retired_targets = list(retired_deletion["delete_objects"])
    retired_uses = _external_uses(
        doc,
        retired_targets,
        [*internal_before, *retired_targets],
    )
    if retired_uses:
        raise _reference_error(
            "Cannot retire TechDraw VibeScript outputs while human-created or "
            "foreign objects still reference them",
            retired_uses,
        )

    transaction_open = False
    removed: list[str] = []
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish TechDraw VibeScript: {prepared['program_name']}"
            )
            transaction_open = True
        removed.extend(
            _remove_techdraw_timeline_deletion(
                doc,
                retired_deletion,
            )
        )
        for obj in internal_before:
            if doc.getObject(str(getattr(obj, "Name", "") or "")) is obj:
                _unfreeze_object(obj, "TechDraw")

        def ensure_output(item: Mapping[str, Any]) -> Any:
            name = str(item["name"])
            obj = outputs.get(name)
            if obj is None:
                obj = existing.get(name)
            if obj is None:
                obj = _techdraw_create_output(
                    doc, prepared, name, str(item["type"])
                )
                created.append(obj)
            outputs[name] = obj
            return obj

        # Each Page and its Template form one contiguous native History block.
        # Publish that prerequisite before creating any page content; content
        # is linked into the still-live Page after its own blocks are accepted.
        for item in items:
            if str(item["type"]) != "page":
                continue
            data = _techdraw_data(item)
            page_name = str(item["name"])
            template_name = str(data["template_output"])
            template_item = by_name[template_name]
            template = ensure_output(template_item)
            page = ensure_output(item)
            reconciliation = page_reconciliations.get(page_name)
            if reconciliation is not None:
                _stage_timeline_resource_reconciliation(
                    doc,
                    page,
                    reconciliation,
                    context=f"TechDraw Page {page_name!r}",
                )
            template_data = _techdraw_data(template_item)
            template_properties = dict(
                _definition(template_item)["properties"]
            )
            template.Label = str(template_properties["label"])
            template.Width = float(template_data["width_mm"])
            template.Height = float(template_data["height_mm"])
            template.Orientation = str(template_data["orientation"])
            template.EditableTexts = dict(template_data["editable_texts"])
            _set_metadata(
                template,
                prepared,
                template_name,
                "template",
                _definition(template_item),
            )
            _techdraw_set_validation(template, template_item)

            page.Template = template
            page.ProjectionType = (
                "First angle"
                if data["convention"] == "first_angle"
                else "Third angle"
            )
            page.Scale = float(data["scale"])
            page.KeepUpdated = False
            for view in list(page.Views or []):
                page.removeView(view)
            _set_metadata(
                page,
                prepared,
                page_name,
                "page",
                _definition(item),
            )
            _techdraw_set_validation(page, item)
            _mark_timeline_operation(
                page,
                context=f"TechDraw Page {page_name!r}",
            )
            _mark_timeline_resource(
                template,
                page,
                context=f"TechDraw Template {template_name!r}",
            )
            if reconciliation is None:
                _publish_new_timeline_resource_block(
                    doc,
                    page,
                    [template],
                    context=f"TechDraw Page {page_name!r}",
                )
            else:
                retired_page_resources = (
                    _finalize_timeline_resource_reconciliation(
                        doc,
                        page,
                        reconciliation,
                        [template],
                        context=f"TechDraw Page {page_name!r}",
                    )
                )
                removed.extend(
                    _remove_reconciled_timeline_resources(
                        doc,
                        retired_page_resources,
                        context=f"TechDraw Page {page_name!r}",
                    )
                )

        for projection_name, page_name in page_owner.items():
            item = by_name[projection_name]
            if str(item["type"]) != "projection":
                continue
            page = outputs[page_name]
            group = ensure_output(item)
            if group not in list(page.Views or []):
                page.addPrecomputedView(group)
            reconciliation = projection_reconciliations.get(projection_name)
            if reconciliation is not None:
                _stage_timeline_resource_reconciliation(
                    doc,
                    group,
                    reconciliation,
                    context=f"TechDraw Projection {projection_name!r}",
                )
            retiring_children = list(
                retired_projection_children.get(projection_name, [])
            )
            retiring_child_set = set(retiring_children)
            if retiring_child_set:
                for dimension_item in items:
                    if str(dimension_item["type"]) != "dimension":
                        continue
                    dimension_name = str(dimension_item["name"])
                    dimension = outputs.get(dimension_name)
                    if dimension is None:
                        dimension = existing.get(dimension_name)
                    if dimension is None:
                        continue
                    if any(
                        (
                            isinstance(reference, tuple)
                            and bool(reference)
                            and reference[0] in retiring_child_set
                        )
                        for reference in list(
                            getattr(dimension, "References2D", ()) or ()
                        )
                    ):
                        dimension.References2D = []
                removed.extend(
                    name
                    for name in _remove_techdraw_objects(
                        doc,
                        retiring_children,
                    )
                    if name not in removed
                )
            child_map = desired_child_maps.setdefault(projection_name, {})
            new_children: list[Any] = []
            for direction in list(_techdraw_data(item)["directions"]):
                if direction in child_map:
                    continue
                child = group.addPrecomputedProjection(
                    _techdraw_projection_type(direction)
                )
                if child is None:
                    raise RuntimeError(
                        f"Could not create precomputed projection child {direction!r}."
                    )
                child_map[direction] = child
                created.append(child)
                new_children.append(child)

            if new_children:
                definition = _definition(item)
                properties = dict(definition["properties"])
                data = _techdraw_data(item)
                sources = _techdraw_source_objects(
                    doc,
                    definition,
                    output_name=projection_name,
                )
                group.Label = str(properties["label"])
                group.Source = sources
                group.ProjectionType = (
                    "First angle"
                    if data["convention"] == "first_angle"
                    else "Third angle"
                )
                group.ScaleType = "Custom"
                group.Scale = float(data["scale"])
                group.X = float(data["position_mm"][0])
                group.Y = float(data["position_mm"][1])
                group.spacingX = float(data["spacing_mm"][0])
                group.spacingY = float(data["spacing_mm"][1])
                group.AutoDistribute = False
                detached_children = item.get("detached_projection_children")
                if not isinstance(detached_children, dict):
                    raise RuntimeError(
                        f"Projection group {projection_name!r} has no detached "
                        "child state."
                    )
                for direction in list(data["directions"]):
                    child = child_map[direction]
                    child_data = dict(data["children"][direction])
                    child.Source = sources
                    _techdraw_configure_style(child, child_data, properties)
                    child.setPrecomputedProjection(
                        detached_children[direction]
                    )
                    child.Label = (
                        f"{group.Label}: "
                        f"{direction.replace('_', ' ').title()}"
                    )
                    _set_metadata(
                        child,
                        prepared,
                        f"{projection_name}.{direction}",
                        "projection_item",
                        {
                            "operation": "projection_item",
                            "parent_output": projection_name,
                            "direction": direction,
                        },
                    )
                    _add_string_property(
                        child,
                        PROP_TECHDRAW_VALIDATION,
                        "Authenticated worker-precomputed TechDraw projection "
                        "summary.",
                    )
                    setattr(
                        child,
                        PROP_TECHDRAW_VALIDATION,
                        json.dumps(
                            _techdraw_projection_summary(child_data),
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    )
                _set_metadata(
                    group,
                    prepared,
                    projection_name,
                    "projection",
                    definition,
                )
                _techdraw_set_validation(group, item)

            final_children = [
                child_map[direction]
                for direction in list(_techdraw_data(item)["directions"])
            ]
            _mark_timeline_operation(
                group,
                context=f"TechDraw Projection {projection_name!r}",
            )
            for direction, child in zip(
                list(_techdraw_data(item)["directions"]),
                final_children,
            ):
                _mark_timeline_resource(
                    child,
                    group,
                    context=(
                        f"TechDraw Projection {projection_name!r} "
                        f"item {direction!r}"
                    ),
                )
            if reconciliation is None:
                _publish_new_timeline_resource_block(
                    doc,
                    group,
                    final_children,
                    context=f"TechDraw Projection {projection_name!r}",
                )
            else:
                retired_projection_resources = (
                    _finalize_timeline_resource_reconciliation(
                        doc,
                        group,
                        reconciliation,
                        final_children,
                        context=f"TechDraw Projection {projection_name!r}",
                    )
                )
                removed.extend(
                    _remove_reconciled_timeline_resources(
                        doc,
                        retired_projection_resources,
                        context=f"TechDraw Projection {projection_name!r}",
                    )
                )

        def configure_and_publish_simple_output(
            item: Mapping[str, Any],
        ) -> None:
            name = str(item["name"])
            output_type = str(item["type"])
            if output_type not in {"view", "annotation", "dimension"}:
                raise RuntimeError(
                    f"TechDraw output {name!r} is not a simple page operation."
                )
            definition = _definition(item)
            properties = dict(definition["properties"])
            data = _techdraw_data(item)
            obj = ensure_output(item)
            page = outputs[page_owner[name]]
            if obj not in list(page.Views or []):
                page.addPrecomputedView(obj)

            obj.Label = str(properties["label"])
            if output_type == "view":
                obj.Source = _techdraw_source_objects(
                    doc, definition, output_name=name
                )
                _techdraw_configure_style(obj, data, properties)
                snapshot = item.get("detached_projection")
                if not isinstance(snapshot, dict):
                    raise RuntimeError(
                        f"TechDraw view {name!r} has no detached projection state."
                    )
                obj.setPrecomputedProjection(snapshot)
            elif output_type == "annotation":
                obj.Text = list(data["text"])
                obj.X = float(data["position_mm"][0])
                obj.Y = float(data["position_mm"][1])
                obj.TextSize = float(data["text_size_mm"])
                obj.TextAlignment = str(data["alignment"]).title()
            else:
                source = outputs[str(data["source_output"])]
                if str(data["projection_direction"]):
                    source = desired_child_maps[str(data["source_output"])][
                        str(data["projection_direction"])
                    ]
                obj.Type = {
                    "distance": "Distance",
                    "distance_x": "DistanceX",
                    "distance_y": "DistanceY",
                    "radius": "Radius",
                    "diameter": "Diameter",
                    "angle": "Angle",
                    "angle_3_point": "Angle3Pt",
                    "area": "Area",
                }[str(data["kind"])]
                obj.MeasureType = (
                    "True" if data["measure"] == "true" else "Projected"
                )
                obj.References2D = [
                    (source, str(reference))
                    for reference in list(properties["references"])
                ]
                obj.X = float(data["position_mm"][0])
                obj.Y = float(data["position_mm"][1])
                obj.FormatSpec = str(data["format_spec"])
                obj.OverTolerance = float(data["over_tolerance"])
                obj.UnderTolerance = float(data["under_tolerance"])
                obj.ShowUnits = bool(data["show_units"])
                snapshot = item.get("detached_dimension")
                if not isinstance(snapshot, dict):
                    raise RuntimeError(
                        f"TechDraw dimension {name!r} has no detached dimension state."
                    )
                obj.setPrecomputedDimension(snapshot)

            _set_metadata(obj, prepared, name, output_type, definition)
            _techdraw_set_validation(obj, item)
            _techdraw_publication_checkpoint("after_apply", name, obj)
            _mark_timeline_operation(
                obj,
                context=f"TechDraw output {name!r}",
            )
            if _is_created_timeline_object(doc, obj, created):
                _publish_new_timeline_resource_block(
                    doc,
                    obj,
                    [],
                    context=f"TechDraw output {name!r}",
                )

        # Views and annotations have no page-content dependency.  Dimensions
        # follow them so every exact projected reference exists before the
        # dimension's own contiguous History block is created.
        for output_type in ("view", "annotation", "dimension"):
            for item in items:
                if str(item["type"]) == output_type:
                    configure_and_publish_simple_output(item)

        for item in items:
            name = str(item["name"])
            output_type = str(item["type"])
            if output_type in {"view", "annotation", "dimension"}:
                continue
            definition = _definition(item)
            properties = dict(definition["properties"])
            data = _techdraw_data(item)
            obj = outputs[name]
            obj.Label = str(properties["label"])
            if output_type == "template":
                obj.Width = float(data["width_mm"])
                obj.Height = float(data["height_mm"])
                obj.Orientation = str(data["orientation"])
                obj.EditableTexts = dict(data["editable_texts"])
            elif output_type == "projection":
                sources = _techdraw_source_objects(doc, definition, output_name=name)
                obj.Source = sources
                obj.ProjectionType = (
                    "First angle"
                    if data["convention"] == "first_angle"
                    else "Third angle"
                )
                obj.ScaleType = "Custom"
                obj.Scale = float(data["scale"])
                obj.X = float(data["position_mm"][0])
                obj.Y = float(data["position_mm"][1])
                obj.spacingX = float(data["spacing_mm"][0])
                obj.spacingY = float(data["spacing_mm"][1])
                obj.AutoDistribute = False
                detached_children = item.get("detached_projection_children")
                if not isinstance(detached_children, dict):
                    raise RuntimeError(
                        f"Projection group {name!r} has no detached child state."
                    )
                for direction in list(data["directions"]):
                    child = desired_child_maps[name][direction]
                    child_data = dict(data["children"][direction])
                    child.Source = sources
                    _techdraw_configure_style(child, child_data, properties)
                    child.setPrecomputedProjection(detached_children[direction])
                    child.Label = f"{obj.Label}: {direction.replace('_', ' ').title()}"
                    child_definition = {
                        "operation": "projection_item",
                        "parent_output": name,
                        "direction": direction,
                    }
                    _set_metadata(
                        child,
                        prepared,
                        f"{name}.{direction}",
                        "projection_item",
                        child_definition,
                    )
                    _add_string_property(
                        child,
                        PROP_TECHDRAW_VALIDATION,
                        "Authenticated worker-precomputed TechDraw projection summary.",
                    )
                    setattr(
                        child,
                        PROP_TECHDRAW_VALIDATION,
                        json.dumps(
                            _techdraw_projection_summary(child_data),
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    )
            elif output_type != "page":
                raise RuntimeError(
                    f"TechDraw output {name!r} has an unsupported publication type."
                )
            _set_metadata(obj, prepared, name, output_type, definition)
            _techdraw_set_validation(obj, item)
            _techdraw_publication_checkpoint("after_apply", name, obj)

        # Native page membership is presentation order, not object-creation
        # provenance.  Restore the source-declared order after every content
        # operation exists and has published its own exact History block.
        for item in items:
            if str(item["type"]) != "page":
                continue
            page = outputs[str(item["name"])]
            for view in list(page.Views or []):
                page.removeView(view)
            for output_name in list(_techdraw_data(item)["content_outputs"]):
                page.addPrecomputedView(outputs[str(output_name)])

        desired_objects = list(outputs.values())
        for child_map in desired_child_maps.values():
            desired_objects.extend(child_map.values())
        desired_ids = {id(obj) for obj in desired_objects}
        retired_targets = list(retired_deletion["delete_objects"])
        overlap = [
            str(obj.Name) for obj in retired_targets if id(obj) in desired_ids
        ]
        if overlap:
            raise RuntimeError(
                "A native TechDraw history resource is also a desired output: "
                + ", ".join(sorted(overlap))
            )
        downstream_refresh = _refresh_external_consumers(
            downstream_uses,
            revision=str(prepared["revision"]),
        )
        freeze_order = {
            "TechDraw::DrawViewPart": 0,
            "TechDraw::DrawProjGroupItem": 0,
            "TechDraw::DrawViewDimension": 1,
            "TechDraw::DrawViewAnnotation": 1,
            "TechDraw::DrawProjGroup": 2,
            "TechDraw::DrawPage": 3,
            "TechDraw::DrawTemplate": 4,
        }
        for obj in sorted(
            {id(value): value for value in desired_objects}.values(),
            key=lambda value: freeze_order.get(str(value.TypeId), 5),
        ):
            output_key = str(
                getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or obj.Name
            )
            _techdraw_publication_checkpoint("before_freeze", output_key, obj)
            _freeze_object(obj, "TechDraw")
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        created_names = [str(getattr(obj, "Name", "") or "") for obj in created]
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        rollback_failures = []
        try:
            _remove_failed_domain_creations(
                doc,
                [
                    name
                    for name in created_names
                    if name and name not in rollback_names
                ],
            )
        except Exception as cleanup_error:
            rollback_failures.append(
                "failed candidate objects could not be removed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        if rollback_states:
            try:
                _restore_techdraw_rollback_states(doc, rollback_states)
            except Exception as rollback_error:
                rollback_failures.append(str(rollback_error))
        if rollback_failures:
            raise RuntimeError(
                f"{publication_error} Explicit TechDraw rollback failure: "
                f"{' | '.join(rollback_failures)}"
            ) from publication_error
        raise

    live_outputs = {}
    published_outputs = []
    for item in items:
        name = str(item["name"])
        obj = outputs[name]
        summary = {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_type": str(item["type"]),
            "derived_state": str(
                getattr(obj, reference_contracts.PROP_DERIVED_STATE, "") or ""
            ),
            "stale_reason": str(
                getattr(obj, reference_contracts.PROP_STALE_REASON, "") or ""
            ),
            "source_revision": str(
                getattr(obj, reference_contracts.PROP_SOURCE_REVISION, "") or ""
            ),
            "techdraw_data": _techdraw_validation_summary(item),
            "frozen": _object_is_frozen(obj, "TechDraw"),
        }
        if str(item["type"]) == "projection":
            summary["projection_children"] = {
                direction: str(child.Name)
                for direction, child in desired_child_maps[name].items()
            }
        live_outputs[name] = summary
        published_outputs.append({"name": name, **summary})
    return {
        "ok": True,
        "outputs": published_outputs,
        "live_outputs": live_outputs,
        "created_objects": [str(obj.Name) for obj in created],
        "retired_objects": removed,
        "downstream_references": {
            "safe_whole_object_uses": scripted_publication.json_reference_uses(
                downstream_uses
            ),
            **downstream_refresh,
        },
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
        "artifact_io_on_document_thread": False,
        "geometry_generation_on_document_thread": False,
        "projection_generation_on_document_thread": False,
        "dimension_evaluation_on_document_thread": False,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


def _assembly_model_evidence(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep accepted Assembly evidence useful without exposing generated names."""

    data = item.get("assembly_data")
    if not isinstance(data, Mapping):
        return None
    output_type = str(item.get("type") or "")
    if output_type == "bom":
        rows = [dict(row) for row in list(data.get("rows") or [])]
        preview = rows[:128]
        all_paths = [
            str(path)
            for row in rows
            for path in list(row.get("occurrence_paths") or [])
        ]
        return {
            "schema": str(data.get("schema") or ""),
            "assembly_output": str(data.get("assembly_output") or ""),
            "columns": [dict(column) for column in list(data.get("columns") or [])],
            "settings": dict(data.get("settings") or {}),
            "row_count": int(data.get("row_count", 0)),
            "occurrence_path_count": int(data.get("occurrence_path_count", 0)),
            "table_sha256": str(data.get("table_sha256") or ""),
            "used_range": list(data.get("used_range") or []),
            "rows": preview,
            "rows_truncated": len(rows) > len(preview),
            "rows_omitted": max(0, len(rows) - len(preview)),
            "available_row_override_paths": all_paths[:256],
            "override_paths_truncated": len(all_paths) > 256,
            "override_paths_omitted": max(0, len(all_paths) - 256),
        }
    if output_type == "component_link":
        raw_states = list(data.get("solved_occurrences") or [])
        states = [
            {
                key: state.get(key)
                for key in (
                    "occurrence_path",
                    "source_kind",
                    "source_label",
                    "native_target_mode",
                    "live_occurrence",
                    "local_placement",
                    "global_placement",
                )
                if key in state
            }
            for state in raw_states[:128]
            if isinstance(state, Mapping)
        ]
        paths = [str(path) for path in list(data.get("occurrence_paths") or [])]
        return {
            "assembly_output": str(data.get("assembly_output") or ""),
            "source": dict(data.get("source") or {}),
            "source_kind": str(data.get("source_kind") or ""),
            "grounded": bool(data.get("grounded")),
            "flexible": bool(data.get("flexible")),
            "hierarchy_sha256": str(data.get("hierarchy_sha256") or ""),
            "occurrence_path_count": int(data.get("occurrence_path_count", 0)),
            "occurrence_paths": paths[:256],
            "occurrence_paths_truncated": len(paths) > 256,
            "occurrence_paths_omitted": max(0, len(paths) - 256),
            "solved_placement": dict(data.get("solved_placement") or {}),
            "authored_to_solved_delta": dict(
                data.get("authored_to_solved_delta") or {}
            ),
            "solved_occurrences": states,
            "solved_occurrences_truncated": len(raw_states) > len(states),
            "solved_occurrences_omitted": max(0, len(raw_states) - len(states)),
        }
    if output_type == "joint":
        connectors = []
        for connector in list(data.get("connectors") or []):
            if not isinstance(connector, Mapping):
                continue
            connectors.append(
                {
                    key: connector.get(key)
                    for key in (
                        "component_output",
                        "occurrence_path",
                        "selection",
                        "semantic_selection",
                        "geometry_type",
                        "offset",
                        "local_frame",
                        "global_frame",
                    )
                    if key in connector
                }
            )
        return {
            "assembly_output": str(data.get("assembly_output") or ""),
            "kind": str(data.get("kind") or ""),
            "suppressed": bool(data.get("suppressed")),
            "parameters": dict(data.get("parameters") or {}),
            "length_limits_mm": data.get("length_limits_mm"),
            "angle_limits_degrees": data.get("angle_limits_degrees"),
            "connectors": connectors,
        }
    if output_type == "mechanism_verification":
        report = data.get("report")
        if not isinstance(report, Mapping):
            return None
        compact_results = []
        for raw in list(report.get("results") or []):
            if not isinstance(raw, Mapping):
                continue
            evidence = raw.get("evidence")
            body = (
                dict(evidence.get("body") or {})
                if isinstance(evidence, Mapping)
                else {}
            )
            interface_evidence = (
                dict(evidence.get("interfaces") or {})
                if isinstance(evidence, Mapping)
                else {}
            )
            interface_section = dict(
                interface_evidence.get("section") or {}
            )
            compact_results.append(
                {
                    key: raw.get(key)
                    for key in (
                        "id",
                        "declaration_kind",
                        "assertion",
                        "first_component",
                        "second_component",
                        "verdict",
                        "reason_code",
                        "message",
                        "tolerance_mm",
                        "minimum_clearance_mm",
                        "first_interface",
                        "second_interface",
                    )
                }
                | {
                    "minimum_distance_mm": body.get(
                        "minimum_distance_mm"
                    ),
                    "common_volume_mm3": body.get("common_volume_mm3"),
                    "witnesses": list(body.get("witnesses") or [])[:4],
                }
                | (
                    {
                        "interface_minimum_distance_mm": interface_evidence.get(
                            "minimum_distance_mm"
                        ),
                        "contact_locus_on_interfaces": interface_evidence.get(
                            "contact_locus_on_interfaces"
                        ),
                        "body_witnesses_on_interfaces": interface_evidence.get(
                            "body_witnesses_on_interfaces"
                        ),
                        "section_all_on_interfaces": interface_section.get(
                            "all_on_interfaces"
                        ),
                    }
                    if interface_evidence
                    else {}
                )
            )
        return {
            "assembly_output": str(data.get("assembly_output") or ""),
            "schema": str(report.get("schema") or ""),
            "verdict": str(report.get("verdict") or ""),
            "scope": dict(report.get("scope") or {}),
            "summary": dict(report.get("summary") or {}),
            "scenario_sha256": str(report.get("scenario_sha256") or ""),
            "solve_report_sha256": str(
                report.get("solve_report_sha256") or ""
            ),
            "static_check_sha256": str(
                report.get("static_check_sha256") or ""
            ),
            "results": compact_results,
            "first_failure": (
                {
                    key: report["first_failure"].get(key)
                    for key in (
                        "id",
                        "assertion",
                        "first_component",
                        "second_component",
                        "verdict",
                        "reason_code",
                        "message",
                    )
                }
                if isinstance(report.get("first_failure"), Mapping)
                else None
            ),
        }
    if output_type in {"assembly", "solver_diagnostics", "motion", "simulation"}:
        evidence = dict(data)
        if output_type == "solver_diagnostics":
            diagnostics = item.get("diagnostics")
            validation_scope = (
                diagnostics.get("validation_scope")
                if isinstance(diagnostics, Mapping)
                else None
            )
            if isinstance(validation_scope, Mapping):
                # This is authenticated against the native solver result before
                # publication. Keep the solver's deliberately narrow claim in
                # every durable source/result view instead of reducing a clean
                # constraint solve to the misleading word "accepted".
                evidence["validation_scope"] = dict(validation_scope)
        return evidence
    if output_type == "exploded_view":
        return {
            key: data.get(key)
            for key in (
                "schema",
                "assembly_output",
                "moves",
                "assembly_bounds",
                "final_component_placements",
                "line_count",
            )
            if key in data
        }
    return None


def _partdesign_presentation_state(obj: Any) -> dict[str, Any] | None:
    if PROP_PARTDESIGN_PRESENTATION_STATE not in _properties(obj):
        return None
    try:
        state = json.loads(
            str(getattr(obj, PROP_PARTDESIGN_PRESENTATION_STATE, "") or "")
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Part Design output {getattr(obj, 'Name', '')!r} has invalid "
            f"presentation state: {exc}"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("schema") != PARTDESIGN_PRESENTATION_OWNERSHIP_SCHEMA
        or set(state) != {"schema", "physical", "appearance"}
        or (
            state["physical"] is not None
            and not isinstance(state["physical"], dict)
        )
        or (
            state["appearance"] is not None
            and not isinstance(state["appearance"], dict)
        )
    ):
        raise RuntimeError(
            f"Part Design output {getattr(obj, 'Name', '')!r} has malformed "
            "presentation ownership state."
        )
    return state


def _partdesign_appearance_property_views(
    obj: Any,
    controlled: list[str],
    *,
    expected_locations: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve each display property to the stable link or its private shape."""

    publication_view = getattr(obj, "ViewObject", None)
    if publication_view is None:
        raise RuntimeError(
            f"Part Design output {obj.Name!r} has no live view provider."
        )
    implementation = scripted_publication.publication_target(obj)
    implementation_view = getattr(implementation, "ViewObject", None)
    views = {
        "publication": publication_view,
        "implementation": implementation_view,
    }
    property_views: dict[str, Any] = {}
    locations: dict[str, str] = {}
    implementation_preferred = {"LineColor", "PointColor", "DisplayMode"}
    for name in controlled:
        expected = (
            str(expected_locations.get(name) or "")
            if expected_locations is not None
            else ""
        )
        if expected:
            if expected not in views:
                raise RuntimeError(
                    f"Part Design output {obj.Name!r} has invalid appearance "
                    f"location {expected!r} for {name!r}."
                )
            candidates = [(expected, views[expected])]
        elif name in implementation_preferred:
            candidates = [
                ("implementation", implementation_view),
                ("publication", publication_view),
            ]
        else:
            candidates = [
                ("publication", publication_view),
                ("implementation", implementation_view),
            ]
        for location, view in candidates:
            if view is not None and hasattr(view, name):
                property_views[name] = view
                locations[name] = location
                break
        else:
            raise RuntimeError(
                f"Part Design output {obj.Name!r} does not support requested "
                f"appearance property {name!r}."
            )
    return property_views, locations


def _capture_mapped_simple_view_state(
    property_views: Mapping[str, Any],
    names: list[str],
) -> dict[str, Any]:
    return {
        name: _capture_simple_view_state(property_views[name], [name])[name]
        for name in names
    }


def _set_mapped_simple_view_state(
    property_views: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    for name, value in state.items():
        _set_simple_view_state(property_views[name], {name: value})


def _preflight_partdesign_presentation(obj: Any) -> dict[str, Any] | None:
    """Validate persisted ownership without blocking source reconciliation.

    The VibeScript program is the editable source of truth for a Part Design
    output.  A live material or display-property change may differ from the
    last accepted revision, but that must not make the source impossible to
    edit.  Publication restores the persisted baseline and then applies the
    newly validated source state; rollback snapshots preserve the live state
    if publication fails.
    """

    state = _partdesign_presentation_state(obj)
    if state is None:
        return None
    physical = state.get("physical")
    if physical is not None:
        if (
            set(physical) != {"baseline", "accepted"}
            or not isinstance(physical.get("baseline"), dict)
            or not isinstance(physical.get("accepted"), dict)
        ):
            raise RuntimeError(
                f"Part Design output {obj.Name!r} has malformed physical "
                "material ownership state."
            )
        if not {
            PROP_PARTDESIGN_MATERIAL_BASELINE,
            PROP_PARTDESIGN_MATERIAL_ACCEPTED,
        } <= _properties(obj):
            raise RuntimeError(
                f"Part Design output {obj.Name!r} lost its material baseline."
            )
        stored = _material_card_state(
            getattr(obj, PROP_PARTDESIGN_MATERIAL_ACCEPTED)
        )
        if stored != physical.get("accepted"):
            raise RuntimeError(
                f"Part Design output {obj.Name!r} has inconsistent persisted "
                "material ownership state."
            )

    appearance = state.get("appearance")
    if appearance is not None:
        controlled = list(appearance.get("controlled_properties") or [])
        if not controlled or any(
            name not in {"ShapeAppearance", *_MATERIAL_SIMPLE_VIEW_PROPERTIES}
            for name in controlled
        ):
            raise RuntimeError(
                f"Part Design output {obj.Name!r} has invalid appearance ownership."
            )
        property_views, _locations = _partdesign_appearance_property_views(
            obj,
            controlled,
            expected_locations=dict(
                appearance.get("property_locations") or {}
            ),
        )
        if "ShapeAppearance" in controlled:
            if not {
                PROP_PARTDESIGN_APPEARANCE_BASELINE,
                PROP_PARTDESIGN_APPEARANCE_ACCEPTED,
            } <= _properties(obj):
                raise RuntimeError(
                    f"Part Design output {obj.Name!r} lost its appearance baseline."
                )
            stored_digest = _shape_appearance_sha256(
                getattr(obj, PROP_PARTDESIGN_APPEARANCE_ACCEPTED)
            )
            expected_digest = str(
                appearance.get("accepted_shape_appearance_sha256") or ""
            )
            if stored_digest != expected_digest:
                raise RuntimeError(
                    f"Part Design output {obj.Name!r} has inconsistent persisted "
                    "ShapeAppearance ownership state."
                )
        simple_names = [
            name for name in controlled if name != "ShapeAppearance"
        ]
        _capture_mapped_simple_view_state(property_views, simple_names)
    return state


def _restore_partdesign_presentation_baseline(
    obj: Any,
    state: Mapping[str, Any] | None,
) -> None:
    if state is None:
        return
    if state.get("physical") is not None:
        _set_physical_material_preserving_view(
            obj,
            getattr(obj, PROP_PARTDESIGN_MATERIAL_BASELINE),
        )
    appearance = state.get("appearance")
    if appearance is None:
        return
    controlled = list(appearance.get("controlled_properties") or [])
    property_views, _locations = _partdesign_appearance_property_views(
        obj,
        controlled,
        expected_locations=dict(appearance.get("property_locations") or {}),
    )
    if "ShapeAppearance" in controlled:
        property_views["ShapeAppearance"].ShapeAppearance = list(
            getattr(obj, PROP_PARTDESIGN_APPEARANCE_BASELINE)
        )
    _set_mapped_simple_view_state(
        property_views,
        dict(appearance.get("baseline_simple") or {}),
    )


def _configure_partdesign_presentation(
    obj: Any,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply and persist one output's validated material and appearance state."""

    presentation = item.get("partdesign_presentation")
    if (
        not isinstance(presentation, Mapping)
        or presentation.get("schema") != "vibecad-partdesign-presentation-v1"
    ):
        raise RuntimeError(
            f"Part Design output {item.get('name')!r} lost validated "
            "presentation evidence."
        )
    physical_record = presentation.get("physical_material")
    appearance_validation = presentation.get("appearance")
    ownership: dict[str, Any] = {
        "schema": PARTDESIGN_PRESENTATION_OWNERSHIP_SCHEMA,
        "physical": None,
        "appearance": None,
    }

    if physical_record is not None:
        if not isinstance(physical_record, Mapping):
            raise RuntimeError("Validated Part Design material state is malformed.")
        native_material = item.get("partdesign_native_material")
        if native_material is None:
            raise RuntimeError(
                f"Part Design output {item.get('name')!r} lost its resolved "
                "native material card."
            )
        if not hasattr(obj, "ShapeMaterial"):
            raise RuntimeError(
                f"Part Design output {obj.Name!r} does not support ShapeMaterial."
            )
        baseline_material = getattr(obj, "ShapeMaterial")
        _add_property(
            obj,
            "Materials::PropertyMaterial",
            PROP_PARTDESIGN_MATERIAL_BASELINE,
            "Native material restored when source material ownership is removed.",
        )
        _add_property(
            obj,
            "Materials::PropertyMaterial",
            PROP_PARTDESIGN_MATERIAL_ACCEPTED,
            "Native material authenticated for the accepted Part Design revision.",
        )
        setattr(
            obj,
            PROP_PARTDESIGN_MATERIAL_BASELINE,
            baseline_material,
        )
        _set_physical_material_preserving_view(obj, native_material)
        assigned = getattr(obj, "ShapeMaterial")
        accepted = _material_card_state(assigned)
        expected = {
            "uuid": str(physical_record.get("uuid") or ""),
            "name": str(physical_record.get("name") or ""),
            "card_sha256": str(physical_record.get("card_sha256") or ""),
        }
        if accepted != expected:
            raise RuntimeError(
                f"Part Design output {obj.Name!r} physical material readback "
                "differs from the validated catalog card."
            )
        setattr(obj, PROP_PARTDESIGN_MATERIAL_ACCEPTED, assigned)
        ownership["physical"] = {
            "baseline": _material_card_state(baseline_material),
            "accepted": accepted,
        }

    if appearance_validation is not None:
        if not isinstance(appearance_validation, Mapping):
            raise RuntimeError("Validated Part Design appearance state is malformed.")
        requested = dict(appearance_validation.get("resolved") or {})
        view = getattr(obj, "ViewObject", None)
        if view is None:
            raise RuntimeError(
                f"Part Design output {obj.Name!r} has no live view provider."
            )
        controlled = _effective_appearance_controlled_properties(
            view,
            list(appearance_validation.get("controlled_properties") or []),
        )
        if not controlled:
            raise RuntimeError(
                f"Part Design output {obj.Name!r} has an empty appearance request."
            )
        property_views, property_locations = (
            _partdesign_appearance_property_views(obj, controlled)
        )
        simple_names = [
            name for name in controlled if name != "ShapeAppearance"
        ]
        baseline_simple = _capture_mapped_simple_view_state(
            property_views,
            simple_names,
        )
        baseline_shape = (
            list(property_views["ShapeAppearance"].ShapeAppearance)
            if "ShapeAppearance" in controlled
            else []
        )
        _add_property(
            obj,
            "App::PropertyMaterialList",
            PROP_PARTDESIGN_APPEARANCE_BASELINE,
            "Complete ShapeAppearance restored when source appearance is removed.",
        )
        _add_property(
            obj,
            "App::PropertyMaterialList",
            PROP_PARTDESIGN_APPEARANCE_ACCEPTED,
            "Complete ShapeAppearance authenticated for the accepted revision.",
        )
        setattr(
            obj,
            PROP_PARTDESIGN_APPEARANCE_BASELINE,
            baseline_shape,
        )
        _apply_requested_appearance(
            obj,
            requested,
            property_views=property_views,
        )
        _verify_requested_appearance(
            obj,
            requested,
            property_views=property_views,
        )
        accepted_simple = _capture_mapped_simple_view_state(
            property_views,
            simple_names,
        )
        accepted_shape = (
            list(property_views["ShapeAppearance"].ShapeAppearance)
            if "ShapeAppearance" in controlled
            else []
        )
        setattr(
            obj,
            PROP_PARTDESIGN_APPEARANCE_ACCEPTED,
            accepted_shape,
        )
        ownership["appearance"] = {
            "controlled_properties": controlled,
            "property_locations": property_locations,
            "baseline_simple": baseline_simple,
            "accepted_simple": accepted_simple,
            "baseline_shape_appearance_sha256": (
                _shape_appearance_sha256(baseline_shape)
                if "ShapeAppearance" in controlled
                else ""
            ),
            "accepted_shape_appearance_sha256": (
                _shape_appearance_sha256(accepted_shape)
                if "ShapeAppearance" in controlled
                else ""
            ),
        }

    _add_string_property(
        obj,
        PROP_PARTDESIGN_PRESENTATION_STATE,
        "Reversible source-owned Part Design material and appearance state.",
    )
    setattr(
        obj,
        PROP_PARTDESIGN_PRESENTATION_STATE,
        json.dumps(
            ownership,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return ownership


def _partdesign_presentation_channels(
    presentation: Mapping[str, Any],
) -> set[str]:
    channels: set[str] = set()
    if presentation.get("physical_material") is not None:
        channels.add("physical")
    if presentation.get("appearance") is not None:
        channels.add("appearance")
    return channels


class _PartDesignShapeCarrier:
    """Detached validated shape presented to the shared publication service."""

    def __init__(self, item: Mapping[str, Any]) -> None:
        import FreeCAD as App

        self.Name = str(item["name"])
        self.Label = str(
            dict(item.get("partdesign_data") or {}).get("body_label")
            or item["name"]
        )
        self.Shape = item["detached_shape"]
        # OCC stores rigid transforms in the TopoShape location. Assigning that
        # Shape to Part::Feature extracts local geometry, so the carrier must
        # expose the same placement separately or publication drops it.
        self.Placement = App.Placement(self.Shape.Placement)
        self.ViewObject = None

    def getGlobalPlacement(self) -> Any:
        return self.Placement


def _partdesign_program_root(doc: Any, program_id: str) -> Any | None:
    matches = []
    for obj in list(getattr(doc, "Objects", []) or []):
        v2_id = str(getattr(obj, contracts.PROP_PROGRAM_ID, "") or "")
        v1_id = str(getattr(obj, "VibeCADVibeScriptModelId", "") or "")
        publication_id = str(
            getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
        )
        if program_id not in {v2_id, v1_id, publication_id}:
            continue
        if (
            scripted_publication.role_of(obj) == scripted_publication.ROLE_MODEL
            or (
                str(getattr(obj, "TypeId", "") or "") == "App::Part"
                and not str(
                    getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""
                )
                and not str(
                    getattr(obj, "VibeCADVibeScriptOutputKey", "") or ""
                )
            )
        ):
            matches.append(obj)
    unique = {str(obj.Name): obj for obj in matches}
    if len(unique) > 1:
        raise RuntimeError(
            f"Multiple Part Design program roots claim id {program_id}: "
            f"{sorted(unique)}."
        )
    return next(iter(unique.values()), None)


def _partdesign_publications(
    doc: Any,
    root: Any | None,
    program_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    candidates = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if scripted_publication.is_publication(obj)
        and str(getattr(obj, scripted_publication.PROP_MODEL_ID, "") or "")
        == program_id
    ]
    if root is not None:
        candidates.extend(
            obj
            for obj in list(getattr(root, "Group", []) or [])
            if obj not in candidates
            and (
                scripted_publication.is_publication(obj)
                or "VibeCADVibeScriptOutputKey" in _properties(obj)
            )
        )
        candidates.extend(
            obj
            for obj in _partdesign_component_occurrences(root)
            if obj not in candidates
        )
    for obj in candidates:
        output_name = str(
            getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "")
            or getattr(obj, scripted_publication.PROP_OUTPUT_KEY, "")
            or getattr(obj, "VibeCADVibeScriptOutputKey", "")
            or ""
        )
        if not output_name:
            continue
        if output_name in result and result[output_name] is not obj:
            raise RuntimeError(
                f"Multiple Part Design publications claim output {output_name!r}."
            )
        result[output_name] = obj
    return result


def _tag_partdesign_root(root: Any, prepared: Mapping[str, Any]) -> None:
    scripted_publication.tag_object(
        root,
        role=scripted_publication.ROLE_MODEL,
        engine="vibescript:partdesign",
        model_id=str(prepared["program_id"]),
        revision=str(prepared["revision"]),
    )
    for name, description, value in (
        (
            contracts.PROP_PROGRAM_ID,
            "Stable VibeScript program id.",
            str(prepared["program_id"]),
        ),
        (
            contracts.PROP_PROGRAM_DOMAIN,
            "VibeScript workbench domain.",
            "partdesign",
        ),
        (
            contracts.PROP_PROGRAM_WORKBENCH,
            "Workbench owning this VibeScript program.",
            "PartDesignWorkbench",
        ),
        (
            contracts.PROP_PROGRAM_REVISION,
            "Accepted VibeScript program revision.",
            str(prepared["revision"]),
        ),
        (
            contracts.PROP_PROGRAM_LABEL,
            "Stable VibeScript program label.",
            str(prepared["program_name"]),
        ),
        (
            contracts.PROP_PROGRAM_CONTRACT,
            "Portable accepted VibeScript source, inputs, and output contract.",
            str(prepared.get("document_program_contract") or ""),
        ),
    ):
        _add_string_property(root, name, description)
        setattr(root, name, value)
    _hide_property(root, contracts.PROP_PROGRAM_CONTRACT)
    scripted_publication.ensure_string_property(
        root, scripted_publication.PROP_INTERFACES
    )


def _partdesign_interface_table(
    validated: Mapping[str, Any],
    publications: Mapping[str, Any],
) -> dict[str, Any]:
    outputs: dict[str, dict[str, Any]] = {}
    declarations: dict[str, list[dict[str, Any]]] = {}
    for item in list(validated.get("outputs") or []):
        output_name = str(item["name"])
        published = publications[output_name]
        data = (
            item.get("component_data")
            if str(item.get("type") or "") == "component_link"
            else item.get("partdesign_data")
        )
        if not isinstance(data, Mapping):
            raise RuntimeError(
                f"Part Design output {output_name!r} has no interface evidence."
            )
        output_interfaces: dict[str, Any] = {}
        for raw_name, raw in dict(data.get("interfaces") or {}).items():
            name = str(raw_name)
            if not isinstance(raw, Mapping):
                raise RuntimeError(
                    f"Part Design semantic interface {name!r} is malformed."
                )
            definition = {
                "output": output_name,
                "selection": dict(raw.get("selection") or {}),
                **(
                    {"description": str(raw.get("description") or "")}
                    if raw.get("description")
                    else {}
                ),
                **(
                    {"connector": dict(raw["connector"])}
                    if isinstance(raw.get("connector"), Mapping)
                    else {}
                ),
                "resolved": {
                    "object": str(published.Name),
                    "subelements": list(raw.get("subelements") or []),
                    "geometry": list(raw.get("geometry") or []),
                    **(
                        {"connector_frame": dict(raw["connector_frame"])}
                        if isinstance(raw.get("connector_frame"), Mapping)
                        else {}
                    ),
                },
            }
            output_interfaces[name] = definition
            declarations.setdefault(name, []).append(definition)
        outputs[output_name] = output_interfaces
    table: dict[str, Any] = {
        reference_contracts.INTERFACE_TABLE_SCHEMA_KEY: (
            reference_contracts.INTERFACE_TABLE_SCHEMA
        ),
        reference_contracts.INTERFACE_TABLE_OUTPUTS_KEY: outputs,
    }
    # Preserve the original flat lookup for names that remain program-unique.
    # Output-local names that are reused intentionally live only in _outputs.
    for name, definitions in declarations.items():
        if len(definitions) == 1:
            table[name] = definitions[0]
    return table


def _partdesign_component_occurrences(root: Any) -> list[Any]:
    properties = _properties(root)
    occurrences: list[Any]
    if PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES in properties:
        names = [
            str(name or "")
            for name in list(
                getattr(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES, []) or []
            )
        ]
        if any(not name or "." in name for name in names):
            raise RuntimeError(
                f"Part Design program {root.Name!r} has malformed component-"
                "occurrence names."
            )
        if len(set(names)) != len(names):
            raise RuntimeError(
                f"Part Design program {root.Name!r} lists a component occurrence "
                "more than once."
            )
        document = getattr(root, "Document", None)
        occurrences = [
            document.getObject(name) if document is not None else None
            for name in names
        ]
        if any(occurrence is None for occurrence in occurrences):
            missing = [
                name
                for name, occurrence in zip(names, occurrences)
                if occurrence is None
            ]
            raise RuntimeError(
                f"Part Design program {root.Name!r} references missing component "
                f"occurrence(s): {', '.join(missing)}."
            )
    elif PROP_PARTDESIGN_COMPONENT_OCCURRENCES in properties:
        # Compatibility with the first component-occurrence documents. The
        # migration below converts these live links to stable object names so
        # an earlier program root never gains forward History dependencies on
        # its later, independently placed occurrences.
        occurrences = list(
            getattr(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCES, []) or []
        )
    else:
        return []

    program_id = str(getattr(root, contracts.PROP_PROGRAM_ID, "") or "")
    for occurrence in occurrences:
        if not _is_partdesign_component_occurrence(occurrence):
            raise RuntimeError(
                f"Part Design program {root.Name!r} references an invalid "
                "component occurrence."
            )
        occurrence_program_id = str(
            getattr(occurrence, contracts.PROP_PROGRAM_ID, "") or ""
        )
        if program_id and occurrence_program_id != program_id:
            raise RuntimeError(
                f"Part Design program {root.Name!r} cannot own component "
                f"occurrence {occurrence.Name!r} from another VibeScript program."
            )
    return occurrences


def _set_partdesign_component_occurrences(root: Any, occurrences: list[Any]) -> None:
    occurrences = list(occurrences)
    program_id = str(getattr(root, contracts.PROP_PROGRAM_ID, "") or "")
    if len({id(occurrence) for occurrence in occurrences}) != len(occurrences):
        raise RuntimeError(
            "One component occurrence cannot appear more than once in a Part "
            "Design program."
        )
    for occurrence in occurrences:
        if not _is_partdesign_component_occurrence(occurrence):
            raise RuntimeError("Part Design component-occurrence metadata is invalid.")
        occurrence_program_id = str(
            getattr(occurrence, contracts.PROP_PROGRAM_ID, "") or ""
        )
        if program_id and occurrence_program_id != program_id:
            raise RuntimeError(
                f"Component occurrence {occurrence.Name!r} belongs to another "
                "VibeScript program."
            )
    _add_property(
        root,
        "App::PropertyStringList",
        PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES,
        "Stable object names of top-level reusable component occurrences owned "
        "by this Design program.",
    )
    _add_property(
        root,
        "App::PropertyLinkList",
        PROP_PARTDESIGN_COMPONENT_OCCURRENCES,
        "Legacy component-occurrence links retained for document compatibility.",
    )
    setattr(
        root,
        PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES,
        [str(occurrence.Name) for occurrence in occurrences],
    )
    # A normal PropertyLinkList participates in the modeling dependency graph.
    # Keeping later top-level occurrences here makes every subsequent History
    # operation invalid. Preserve the legacy property surface, but never store
    # ownership metadata as live modeling links.
    setattr(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCES, [])
    _hide_property(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES)
    _hide_property(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCES)


def migrate_partdesign_component_occurrence_links(doc: Any) -> dict[str, Any]:
    """Replace legacy forward dependency links with exact object-name metadata."""

    migrated: list[str] = []
    for root in list(getattr(doc, "Objects", []) or []):
        properties = _properties(root)
        if PROP_PARTDESIGN_COMPONENT_OCCURRENCES not in properties:
            continue
        if (
            str(getattr(root, contracts.PROP_PROGRAM_DOMAIN, "") or "")
            != "partdesign"
        ):
            continue
        legacy = list(
            getattr(root, PROP_PARTDESIGN_COMPONENT_OCCURRENCES, []) or []
        )
        has_names = PROP_PARTDESIGN_COMPONENT_OCCURRENCE_NAMES in properties
        if has_names:
            named = _partdesign_component_occurrences(root)
            if legacy and [str(item.Name) for item in legacy] != [
                str(item.Name) for item in named
            ]:
                raise RuntimeError(
                    f"Part Design program {root.Name!r} has conflicting legacy "
                    "and current component-occurrence metadata."
                )
            occurrences = named
        else:
            occurrences = legacy
        if not legacy and has_names:
            continue
        _set_partdesign_component_occurrences(root, occurrences)
        migrated.append(str(root.Name))
    return {"migrated_programs": migrated}


def _is_partdesign_component_occurrence(obj: Any) -> bool:
    return (
        str(getattr(obj, "TypeId", "") or "") == "App::Link"
        and str(getattr(obj, PROP_OUTPUT_TYPE, "") or "") == "component_link"
        and str(getattr(obj, contracts.PROP_PROGRAM_DOMAIN, "") or "")
        == "partdesign"
    )


def _delete_partdesign_publication(
    doc: Any,
    root: Any | None,
    published: Any,
) -> list[str]:
    if not _is_partdesign_component_occurrence(published):
        if root is not None:
            try:
                return scripted_publication.delete_publication(doc, root, published)
            except scripted_publication.PublicationError:
                # A prior interrupted deletion may have removed the program
                # container or private shape target before its stable
                # publication. Recover only objects carrying the same exact
                # source ownership identity.
                pass
        program_id = str(
            getattr(published, scripted_publication.PROP_MODEL_ID, "") or ""
        )
        deleted: list[str] = []
        target_name = str(
            getattr(published, scripted_publication.PROP_IMPLEMENTATION, "") or ""
        )
        target = doc.getObject(target_name) if target_name else None
        if target is not None:
            if (
                scripted_publication.role_of(target)
                != scripted_publication.ROLE_PUBLICATION_TARGET
                or str(
                    getattr(target, scripted_publication.PROP_MODEL_ID, "") or ""
                )
                != program_id
            ):
                raise RuntimeError(
                    "A broken Part Design publication points at an object not "
                    "owned by the same VibeScript source."
                )
            doc.removeObject(str(target.Name))
            deleted.append(str(target.Name))
        published_name = str(getattr(published, "Name", "") or "")
        if published_name and doc.getObject(published_name) is not None:
            doc.removeObject(published_name)
            deleted.append(published_name)
        return deleted
    if root is None:
        name = str(getattr(published, "Name", "") or "")
        if name and doc.getObject(name) is not None:
            doc.removeObject(name)
            return [name]
        return []
    retained = [
        item
        for item in _partdesign_component_occurrences(root)
        if item is not published
    ]
    _set_partdesign_component_occurrences(root, retained)
    name = str(getattr(published, "Name", "") or "")
    if name and doc.getObject(name) is not None:
        return _remove_timeline_deletion(
            doc,
            _prepare_timeline_deletion(doc, [published]),
        )
    return []


def _create_partdesign_component_occurrence(
    doc: Any,
    root: Any,
    prepared: Mapping[str, Any],
    item: Mapping[str, Any],
) -> Any:
    output_name = str(item["name"])
    occurrence = doc.addObject(
        "App::Link",
        _internal_name(prepared, output_name),
    )
    if occurrence is None:
        raise RuntimeError(
            f"FreeCAD did not create component occurrence {output_name!r}."
        )
    scripted_publication.tag_object(
        occurrence,
        role=scripted_publication.ROLE_IMPLEMENTATION,
        engine="vibescript:partdesign",
        model_id=str(prepared["program_id"]),
        output_key=output_name,
        revision=str(prepared["revision"]),
    )
    _update_partdesign_component_occurrence(
        doc,
        root,
        occurrence,
        prepared,
        item,
    )
    return occurrence


def _update_partdesign_component_occurrence(
    doc: Any,
    root: Any,
    occurrence: Any,
    prepared: Mapping[str, Any],
    item: Mapping[str, Any],
) -> None:
    output_name = str(item["name"])
    if str(getattr(occurrence, "TypeId", "") or "") != "App::Link":
        raise RuntimeError(
            f"Stable component output {output_name!r} is not an App::Link."
        )
    previous_type = str(getattr(occurrence, PROP_OUTPUT_TYPE, "") or "")
    if previous_type and previous_type != "component_link":
        raise RuntimeError(
            f"Stable output {output_name!r} cannot change from {previous_type!r} "
            "to 'component_link'. Return the occurrence under a new output name."
        )
    _configure_component(doc, occurrence, item, {}, prepared)
    occurrence.Label = str(
        dict(item.get("component_data") or {}).get("label") or output_name
    )
    if hasattr(occurrence, "LinkTransform"):
        occurrence.LinkTransform = True
    scripted_publication.tag_object(
        occurrence,
        role=scripted_publication.ROLE_IMPLEMENTATION,
        engine="vibescript:partdesign",
        model_id=str(prepared["program_id"]),
        output_key=output_name,
        revision=str(prepared["revision"]),
    )
    _set_metadata(
        occurrence,
        prepared,
        output_name,
        "component_link",
        _definition(item),
    )


def _migrate_partdesign_output_representation(
    doc: Any,
    root: Any,
    published: Any,
    prepared: Mapping[str, Any],
    item: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Convert one stable App::Link between occurrence and shape publication.

    FreeCAD defers object deletion until transaction commit. Deleting and
    recreating an App::Link under the same name inside that transaction can
    therefore return the pending-deletion occurrence with its old LinkedObject.
    Keep the public link and swap only its private target instead.
    """

    output_name = str(item["name"])
    if str(getattr(published, "TypeId", "") or "") != "App::Link":
        raise RuntimeError(
            f"Part Design output {output_name!r} cannot migrate representation "
            f"because its native carrier is {getattr(published, 'TypeId', '')!r}, "
            "not App::Link."
        )
    wants_component = str(item.get("type") or "") == "component_link"
    created: list[str] = []
    removed: list[str] = []
    deferred_remove: list[str] = []
    if wants_component:
        target_name = str(
            getattr(published, scripted_publication.PROP_IMPLEMENTATION, "") or ""
        )
        target = doc.getObject(target_name) if target_name else None
        if target is not None:
            deferred_remove.append(str(target.Name))
        _add_string_property(
            published,
            PROP_OUTPUT_TYPE,
            "Declared VibeScript output type.",
        )
        setattr(published, PROP_OUTPUT_TYPE, "component_link")
        return {
            "created": created,
            "removed": removed,
            "deferred_remove": deferred_remove,
        }

    target = doc.addObject(
        "Part::Feature",
        f"{_internal_name(prepared, output_name)}_Source",
    )
    if target is None:
        raise RuntimeError(
            f"FreeCAD did not create a shape target for output {output_name!r}."
        )
    scripted_publication.tag_object(
        target,
        role=scripted_publication.ROLE_PUBLICATION_TARGET,
        engine="vibescript:partdesign",
        model_id=str(prepared["program_id"]),
        output_key=output_name,
        revision=str(prepared["revision"]),
    )
    root.addObject(target)
    target_view = getattr(target, "ViewObject", None)
    if target_view is not None and hasattr(target_view, "Visibility"):
        target_view.Visibility = False
    scripted_publication.ensure_string_property(
        published, scripted_publication.PROP_IMPLEMENTATION
    )
    setattr(published, scripted_publication.PROP_IMPLEMENTATION, str(target.Name))
    import FreeCAD as App

    published.Placement = App.Placement()
    created.append(str(target.Name))
    return {
        "created": created,
        "removed": removed,
        "deferred_remove": deferred_remove,
    }


def _partdesign_history_reference(
    body: Any,
    objects: Mapping[str, Any],
    reference: Mapping[str, Any] | None,
) -> Any:
    if reference is None:
        return None
    scope = str(reference.get("scope") or "")
    if scope == "body":
        return body
    if scope == "origin":
        return body.Origin
    if scope == "history":
        name = str(reference.get("name") or "")
        if name not in objects:
            raise RuntimeError(
                f"Native Part Design history refers to missing object {name!r}."
            )
        return objects[name]
    if scope == "external_reference":
        document = getattr(body, "Document", None)
        document_uid = str(reference.get("document_uid") or "")
        if document is None or str(getattr(document, "Uid", "") or "") != document_uid:
            raise RuntimeError(
                "Native Part Design history external support belongs to another document."
            )
        object_name = str(reference.get("object_name") or "")
        target = document.getObject(object_name)
        if target is None or target is body or target in objects.values():
            raise RuntimeError(
                f"Native Part Design history cannot resolve external support {object_name!r}."
            )
        return target
    if scope == "origin_feature":
        features = list(getattr(body.Origin, "OriginFeatures", []) or [])
        index = int(reference.get("index") or 0)
        role = str(reference.get("role") or "")
        matches = [
            feature
            for feature in features
            if str(getattr(feature, "Role", "") or "") == role
        ]
        if len(matches) == 1:
            return matches[0]
        if 0 <= index < len(features):
            return features[index]
        raise RuntimeError(
            f"Native Part Design history cannot resolve origin role {role!r}."
        )
    raise RuntimeError(
        f"Native Part Design history has unsupported reference scope {scope!r}."
    )


def _apply_partdesign_history_links(
    body: Any,
    objects: Mapping[str, Any],
    obj: Any,
    links: Mapping[str, Any],
) -> None:
    for property_name, specification in links.items():
        if bool(specification.get("read_only")) or property_name == "ExternalGeometry":
            # restoreContent restores native read-only link collections such as
            # Sketcher ExternalGeometry. They cannot be assigned through Python.
            continue
        kind = str(specification.get("kind") or "")
        value = specification.get("value")
        if kind == "link":
            restored = _partdesign_history_reference(body, objects, value)
        elif kind == "link_list":
            restored = [
                _partdesign_history_reference(body, objects, item)
                for item in list(value or [])
            ]
        elif kind == "link_sub":
            restored = (
                None
                if value is None
                else (
                    _partdesign_history_reference(
                        body,
                        objects,
                        value.get("target"),
                    ),
                    list(value.get("subelements") or []),
                )
            )
        elif kind == "link_sub_list":
            restored = [
                (
                    _partdesign_history_reference(
                        body,
                        objects,
                        item.get("target"),
                    ),
                    list(item.get("subelements") or []),
                )
                for item in list(value or [])
            ]
        else:
            raise RuntimeError(
                f"Native Part Design history has unsupported link kind {kind!r}."
            )
        try:
            setattr(obj, property_name, restored)
        except Exception as exc:
            raise RuntimeError(
                f"Could not restore {obj.Name}.{property_name} from validated "
                f"Part Design history: {exc}"
            ) from exc


def _verify_partdesign_history_body(
    body: Any,
    item: Mapping[str, Any],
    *,
    output_name: str,
) -> None:
    tip = getattr(body, "Tip", None)
    if tip is None or tip not in list(getattr(body, "Group", []) or []):
        raise RuntimeError(
            f"Restored native Part Design Body for {output_name!r} has no "
            "valid Tip feature."
        )
    # The Body's aggregate Shape is refreshed by the document-level recompute
    # that follows publication.  The restored Tip already contains the
    # worker-validated result, so verify that native feature directly instead
    # of forcing a recompute on the GUI publication thread.
    shape = getattr(tip, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise RuntimeError(
            f"Restored native Part Design Tip for {output_name!r} is invalid."
        )
    facts = dict(item.get("facts") or {})
    observed = {
        "solids": len(list(getattr(shape, "Solids", []) or [])),
        "shells": len(list(getattr(shape, "Shells", []) or [])),
        "faces": len(list(getattr(shape, "Faces", []) or [])),
        "wires": len(list(getattr(shape, "Wires", []) or [])),
        "edges": len(list(getattr(shape, "Edges", []) or [])),
        "vertices": len(list(getattr(shape, "Vertexes", []) or [])),
    }
    for name, value in observed.items():
        if int(facts.get(name, -1)) != value:
            raise RuntimeError(
                f"Restored native Part Design Tip for {output_name!r} changed "
                f"{name} ({value} != {facts.get(name)!r})."
            )
    expected_volume = float(facts.get("volume_mm3") or 0.0)
    observed_volume = float(getattr(shape, "Volume", 0.0) or 0.0)
    tolerance = max(1.0e-7, abs(expected_volume) * 1.0e-9)
    if abs(observed_volume - expected_volume) > tolerance:
        raise RuntimeError(
            f"Restored native Part Design Tip for {output_name!r} changed volume "
            f"({observed_volume:g} != {expected_volume:g} mm³)."
        )


def _set_view_visibility(obj: Any, visible: bool) -> bool:
    view = getattr(obj, "ViewObject", None)
    target = view if view is not None and hasattr(view, "Visibility") else obj
    if not hasattr(target, "Visibility"):
        return False
    desired = bool(visible)
    if bool(target.Visibility) == desired:
        return False
    target.Visibility = desired
    return True


def _is_partdesign_result_feature(obj: Any) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    if not callable(checker):
        return False
    try:
        if (
            checker("PartDesign::ShapeBinder")
            or checker("PartDesign::SubShapeBinder")
            or checker("Part::Part2DObject")
            or checker("Part::BodyBase")
            or checker("Part::Datum")
        ):
            return False
        return bool(
            checker("PartDesign::Feature")
            or checker("Part::Feature")
        )
    except Exception:
        return False


def _partdesign_history_result_features(body: Any) -> list[Any]:
    return [
        obj
        for obj in list(getattr(body, "Group", []) or [])
        if _is_partdesign_result_feature(obj)
    ]


def _mark_partdesign_history_presentation(body: Any) -> bool:
    changed = PROP_PARTDESIGN_HISTORY_PRESENTATION not in _properties(body)
    _add_string_property(
        body,
        PROP_PARTDESIGN_HISTORY_PRESENTATION,
        "Internal presentation contract that makes the native Body the visible result.",
    )
    if (
        str(getattr(body, PROP_PARTDESIGN_HISTORY_PRESENTATION, "") or "")
        != PARTDESIGN_HISTORY_PRESENTATION_SCHEMA
    ):
        setattr(
            body,
            PROP_PARTDESIGN_HISTORY_PRESENTATION,
            PARTDESIGN_HISTORY_PRESENTATION_SCHEMA,
        )
        changed = True
    _hide_property(body, PROP_PARTDESIGN_HISTORY_PRESENTATION)
    return changed


def _configure_partdesign_history_presentation(
    body: Any,
    *,
    visible: bool = True,
) -> bool:
    """Render the native Body Tip and keep earlier result states hidden."""

    body_view = getattr(body, "ViewObject", None)
    result_features = _partdesign_history_result_features(body)
    if body_view is None or not hasattr(body_view, "Visibility"):
        return False
    if any(
        getattr(feature, "ViewObject", None) is None
        or not hasattr(feature.ViewObject, "Visibility")
        for feature in result_features
    ):
        return False

    tip = getattr(body, "Tip", None)
    changed = False
    for feature in result_features:
        changed = _set_view_visibility(
            feature,
            bool(visible and feature is tip),
        ) or changed
    changed = _set_view_visibility(body, visible) or changed
    return _mark_partdesign_history_presentation(body) or changed


def _copy_native_body_presentation(source: Any, body: Any) -> None:
    """Copy portable appearance without importing Link-only display modes."""

    if hasattr(source, "ShapeMaterial") and hasattr(body, "ShapeMaterial"):
        try:
            source_material = source.ShapeMaterial
            if _material_card_state(body.ShapeMaterial) != _material_card_state(
                source_material
            ):
                body.ShapeMaterial = source_material
        except Exception as exc:
            raise scripted_publication.PublicationError(
                f"Could not copy ShapeMaterial from "
                f"{getattr(source, 'Name', '<object>')} to "
                f"{getattr(body, 'Name', '<object>')}.",
                details={"native_error": str(exc)},
            ) from exc

    source_view = getattr(source, "ViewObject", None)
    body_view = getattr(body, "ViewObject", None)
    if source_view is None or body_view is None:
        return
    # App::Link and PartDesign::Body expose different DisplayMode
    # enumerations ("Link" is not a Body mode). Color and line presentation
    # are portable, while the Body keeps its native Tip renderer mode.
    for name in (
        "ShapeColor",
        "LineColor",
        "PointColor",
        "Transparency",
        "LineWidth",
        "PointSize",
    ):
        if not hasattr(source_view, name) or not hasattr(body_view, name):
            continue
        try:
            setattr(body_view, name, getattr(source_view, name))
        except Exception as exc:
            raise scripted_publication.PublicationError(
                f"Could not copy view property {name!r} from "
                f"{getattr(source, 'Name', '<object>')} to "
                f"{getattr(body, 'Name', '<object>')}.",
                details={"property": name, "native_error": str(exc)},
            ) from exc


def _partdesign_presentation_identity(obj: Any, role: str) -> tuple[str, str] | None:
    if (
        str(getattr(obj, scripted_publication.PROP_ROLE, "") or "") != role
        or str(getattr(obj, scripted_publication.PROP_ENGINE, "") or "")
        != "vibescript:partdesign"
    ):
        return None
    model_id = str(
        getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
    ).strip()
    output_key = str(
        getattr(obj, scripted_publication.PROP_OUTPUT_KEY, "") or ""
    ).strip()
    if not model_id or not output_key:
        return None
    return model_id, output_key


def _create_native_body_for_publication(
    doc: Any,
    publication: Any,
    *,
    visible: bool,
) -> Any:
    """Give an older published solid a native Body that humans can edit."""

    shape = getattr(publication, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise RuntimeError(
            f"Part Design publication {publication.Name!r} has no valid shape "
            "for native Body migration."
        )
    try:
        root = scripted_publication.model_root_for(publication)
    except scripted_publication.PublicationError as exc:
        raise RuntimeError(str(exc)) from exc

    base_name = f"{str(publication.Name)}_Body"
    body_name = (
        str(doc.getUniqueObjectName(base_name))
        if hasattr(doc, "getUniqueObjectName")
        else base_name
    )
    body = doc.addObject("PartDesign::Body", body_name)
    if body is None:
        raise RuntimeError(
            f"FreeCAD did not create a native Body for {publication.Name!r}."
        )
    root.addObject(body)
    # App::Part may uniquify a child's Label as it is adopted. Apply the
    # human-facing output label afterward; the stable publication is internal
    # and object identity remains anchored by its immutable Name.
    output_label = str(
        getattr(publication, "Label", "") or publication.Name
    )
    body.Label = output_label
    if str(getattr(body, "Label", "") or "") != output_label:
        # Duplicate labels are disabled in a default FreeCAD profile. Prefer a
        # descriptive native label over the opaque auto-generated "001".
        body.Label = f"{output_label} Body"

    result = body.newObject("PartDesign::Feature", f"{body_name}_Result")
    if result is None:
        raise RuntimeError(
            f"FreeCAD did not create a native result for {publication.Name!r}."
        )
    result.Label = "Result"
    result.Shape = shape
    _add_string_property(
        result,
        "VibeCADNativeFeatureRole",
        "Native feature role for a validated accepted result.",
    )
    result.VibeCADNativeFeatureRole = "adopted_result"
    _hide_property(result, "VibeCADNativeFeatureRole")
    body.Tip = result

    scripted_publication.tag_object(
        body,
        role=scripted_publication.ROLE_IMPLEMENTATION,
        engine="vibescript:partdesign",
        model_id=str(
            getattr(publication, scripted_publication.PROP_MODEL_ID, "") or ""
        ),
        output_key=str(
            getattr(publication, scripted_publication.PROP_OUTPUT_KEY, "") or ""
        ),
        revision=str(
            getattr(publication, scripted_publication.PROP_REVISION, "") or ""
        ),
    )
    scripted_publication.tag_object(
        result,
        role=scripted_publication.ROLE_IMPLEMENTATION,
        engine="vibescript:partdesign",
        model_id=str(
            getattr(publication, scripted_publication.PROP_MODEL_ID, "") or ""
        ),
        output_key=str(
            getattr(publication, scripted_publication.PROP_OUTPUT_KEY, "") or ""
        ),
        revision=str(
            getattr(publication, scripted_publication.PROP_REVISION, "") or ""
        ),
    )
    _copy_native_body_presentation(publication, body)
    _configure_partdesign_history_presentation(body, visible=visible)
    return body


def _link_publication_to_native_body(publication: Any, body: Any) -> bool:
    """Keep stable downstream references live while the native Body is edited."""

    try:
        root = scripted_publication.model_root_for(publication)
    except scripted_publication.PublicationError as exc:
        raise RuntimeError(str(exc)) from exc
    desired_subname = f"{body.Name}."
    linked = getattr(publication, "LinkedObject", None)
    if (
        isinstance(linked, (tuple, list))
        and len(linked) >= 2
        and linked[0] is root
        and str(linked[1]) == desired_subname
    ):
        return False
    publication.LinkedObject = (root, desired_subname)
    publication.LinkTransform = True
    return True


def restore_partdesign_history_presentation(doc: Any) -> dict[str, Any]:
    """Make native Bodies the sole visible Part Design results."""

    bodies: dict[tuple[str, str], list[Any]] = {}
    publications: dict[tuple[str, str], list[Any]] = {}
    publication_targets: dict[tuple[str, str], list[Any]] = {}
    program_operations: list[Any] = []
    for obj in list(getattr(doc, "Objects", []) or []):
        if (
            str(getattr(obj, "TypeId", "") or "")
            == "PartDesign::DesignScriptOperation"
            and str(
                getattr(obj, scripted_publication.PROP_ROLE, "") or ""
            )
            == scripted_publication.ROLE_IMPLEMENTATION
        ):
            program_operations.append(obj)
        if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
            identity = _partdesign_presentation_identity(
                obj,
                scripted_publication.ROLE_IMPLEMENTATION,
            )
            if identity is not None:
                bodies.setdefault(identity, []).append(obj)
        if str(getattr(obj, "TypeId", "") or "") == "App::Link":
            identity = _partdesign_presentation_identity(
                obj,
                scripted_publication.ROLE_PUBLICATION,
            )
            if identity is not None:
                publications.setdefault(identity, []).append(obj)
        target_identity = _partdesign_presentation_identity(
            obj,
            scripted_publication.ROLE_PUBLICATION_TARGET,
        )
        if target_identity is not None:
            publication_targets.setdefault(target_identity, []).append(obj)

    changed_objects: set[str] = set()
    for operation in program_operations:
        before = {
            name: str(getattr(operation, name, "") or "")
            for name in (
                "VibeCADTimelineEditCommand",
                "VibeCADTimelineDeleteCommand",
            )
        }
        _set_partdesign_program_history_commands(operation)
        if before != {
            name: str(getattr(operation, name, "") or "")
            for name in before
        }:
            changed_objects.add(str(operation.Name))

    # Private detached-shape carriers are never a human viewport result. Hide
    # every explicitly tagged target, including orphaned or duplicate targets
    # that cannot be paired unambiguously with a publication. Their document
    # identity remains intact for reference repair and diagnostics.
    for target_matches in publication_targets.values():
        for target in target_matches:
            if _set_view_visibility(target, False):
                target_name = str(getattr(target, "Name", "") or "")
                if target_name:
                    changed_objects.add(target_name)

    migrated_bodies: list[str] = []
    skipped_identities: list[str] = []
    for identity in sorted(set(publications) - set(bodies)):
        publication_matches = publications[identity]
        if len(publication_matches) != 1:
            skipped_identities.append(f"{identity[0]}:{identity[1]}")
            continue
        publication = publication_matches[0]
        publication_visible = bool(
            getattr(
                getattr(publication, "ViewObject", None),
                "Visibility",
                False,
            )
        )
        body = _create_native_body_for_publication(
            doc,
            publication,
            visible=publication_visible,
        )
        bodies[identity] = [body]
        body_name = str(getattr(body, "Name", "") or "")
        publication_name = str(getattr(publication, "Name", "") or "")
        changed_objects.update((body_name, publication_name))
        migrated_bodies.append(body_name)

    for identity in sorted(set(bodies).intersection(publications)):
        body_matches = bodies[identity]
        publication_matches = publications[identity]
        if len(body_matches) != 1 or len(publication_matches) != 1:
            skipped_identities.append(f"{identity[0]}:{identity[1]}")
            continue
        body = body_matches[0]
        publication = publication_matches[0]
        body_name = str(getattr(body, "Name", "") or "")
        publication_name = str(getattr(publication, "Name", "") or "")
        body_tip = getattr(body, "Tip", None)
        if (
            str(getattr(body_tip, "TypeId", "") or "")
            == "PartDesign::DesignBodyPublication"
        ):
            # A global Design Body is already the stable viewport result. Its
            # Tip publishes one exact Body-state chain and the VibeScript
            # App::Link remains only the source program's compatibility
            # boundary. Relinking that App::Link through the old program
            # container would recreate legacy Body/container ownership and
            # make the saved graph differ from the graph which was accepted.
            body_visible = bool(
                getattr(
                    getattr(body, "ViewObject", None),
                    "Visibility",
                    False,
                )
            )
            _copy_native_body_presentation(publication, body)
            if _configure_partdesign_history_presentation(
                body,
                visible=body_visible,
            ):
                changed_objects.add(body_name)
            if _set_view_visibility(publication, False):
                changed_objects.add(publication_name)
            continue
        if _link_publication_to_native_body(publication, body):
            changed_objects.add(publication_name)
        current_schema = str(
            getattr(body, PROP_PARTDESIGN_HISTORY_PRESENTATION, "") or ""
        )
        if current_schema != PARTDESIGN_HISTORY_PRESENTATION_SCHEMA:
            body_view = getattr(body, "ViewObject", None)
            publication_view = getattr(publication, "ViewObject", None)
            if current_schema == _LEGACY_PARTDESIGN_HISTORY_PRESENTATION_SCHEMA:
                output_visible = bool(
                    getattr(publication_view, "Visibility", False)
                    or any(
                        bool(
                            getattr(
                                getattr(feature, "ViewObject", None),
                                "Visibility",
                                False,
                            )
                        )
                        for feature in _partdesign_history_result_features(body)
                    )
                )
            else:
                output_visible = bool(
                    getattr(body_view, "Visibility", False)
                    or getattr(publication_view, "Visibility", False)
                )
            _copy_native_body_presentation(publication, body)
            if _configure_partdesign_history_presentation(
                body,
                visible=output_visible,
            ):
                changed_objects.add(body_name)
            if _set_view_visibility(publication, False):
                changed_objects.add(publication_name)
            if body_name not in migrated_bodies:
                migrated_bodies.append(body_name)
            continue

        body_visible = bool(
            getattr(getattr(body, "ViewObject", None), "Visibility", False)
        )
        if _configure_partdesign_history_presentation(
            body,
            visible=body_visible,
        ):
            changed_objects.add(body_name)
        if _set_view_visibility(publication, False):
            changed_objects.add(publication_name)

    return {
        "changed_objects": sorted(name for name in changed_objects if name),
        "migrated_bodies": sorted(name for name in migrated_bodies if name),
        "skipped_identities": skipped_identities,
    }


def _partdesign_history_key(obj: Any, *, context: str) -> str:
    properties = _properties(obj)
    if PROP_PARTDESIGN_HISTORY_KEY in properties:
        if (
            obj.getTypeIdOfProperty(PROP_PARTDESIGN_HISTORY_KEY)
            != "App::PropertyString"
        ):
            raise RuntimeError(f"{context} has an invalid authored history identity.")
        key = str(getattr(obj, PROP_PARTDESIGN_HISTORY_KEY, "") or "")
        if key:
            return key

    # Native-history artifacts author document-safe object names.  Documents
    # produced before the explicit key property therefore already carry the
    # exact authored key in Name; this is identity data, not a label/type
    # inference.
    key = str(getattr(obj, "Name", "") or "")
    if not key:
        raise RuntimeError(f"{context} has no stable authored history identity.")
    return key


def _partdesign_timeline_blocks(
    authored_objects: list[Any],
    *,
    output_name: str,
) -> list[dict[str, Any]]:
    """Return exact resource-first/root-last blocks for one authored Body."""

    from PartDesign.PartDesignTimeline import mark_operation, mark_resource

    authored_set = set(authored_objects)
    keys: dict[Any, str] = {}
    key_objects: dict[str, Any] = {}
    operations: list[Any] = []
    resources: list[Any] = []
    for obj in authored_objects:
        context = f"Part Design output {output_name!r} object {obj.Name!r}"
        key = _partdesign_history_key(obj, context=context)
        if key in key_objects and key_objects[key] is not obj:
            raise RuntimeError(
                f"Part Design output {output_name!r} duplicates authored "
                f"history identity {key!r}."
            )
        key_objects[key] = obj
        keys[obj] = key
        role = _timeline_role(obj, context=context)
        if role in {"", "operation"}:
            mark_operation(obj)
            operations.append(obj)
            continue
        if role == "resource":
            owner = _timeline_owner(obj, context=context)
            if owner not in authored_set:
                raise RuntimeError(
                    f"Part Design resource {obj.Name!r} has an owner outside "
                    f"output {output_name!r}."
                )
            mark_resource(obj, owner)
            resources.append(obj)
            continue
        raise RuntimeError(
            f"Part Design authored object {obj.Name!r} cannot be internal."
        )

    blocks: list[dict[str, Any]] = []
    assigned_resources: set[Any] = set()
    for operation in operations:
        owned = []
        for resource in resources:
            current = resource
            visited: set[Any] = set()
            while current in authored_set and _timeline_role(
                current,
                context=f"Part Design resource {resource.Name!r}",
            ) == "resource":
                if current in visited:
                    raise RuntimeError(
                        f"Part Design output {output_name!r} has cyclic "
                        "resource ownership."
                    )
                visited.add(current)
                current = _timeline_owner(
                    current,
                    context=f"Part Design resource {resource.Name!r}",
                )
            if current is operation:
                owned.append(resource)
        ordered_resources, resource_owners = _canonical_timeline_resource_graph(
            operation,
            owned,
            context=(
                f"Part Design output {output_name!r} operation "
                f"{operation.Name!r}"
            ),
        )
        assigned_resources.update(ordered_resources)
        blocks.append(
            {
                "operation": operation,
                "resources": ordered_resources,
                "resource_owners": resource_owners,
                "ordered": [*ordered_resources, operation],
                "keys": [
                    *(keys[resource] for resource in ordered_resources),
                    keys[operation],
                ],
                "root_key": keys[operation],
            }
        )
    if assigned_resources != set(resources):
        raise RuntimeError(
            f"Part Design output {output_name!r} contains a resource which "
            "does not resolve to an authored operation."
        )
    return blocks


def _materialize_partdesign_native_history(
    doc: Any,
    root: Any,
    prepared: Mapping[str, Any],
    validated: Mapping[str, Any],
    *,
    existing_bodies: Mapping[str, Any] | None = None,
    preserve_existing_tips: bool = False,
    internalize_restored_objects: bool = False,
    build_timeline_blocks: bool = True,
) -> dict[str, Any]:
    history = validated.get("partdesign_native_history")
    if not isinstance(history, Mapping):
        return {"available": False, "bodies": {}, "created_objects": []}

    output_items = {
        str(item["name"]): item for item in list(validated.get("outputs") or [])
    }
    bodies: dict[str, Any] = {}
    timeline_blocks: dict[str, list[dict[str, Any]]] = {}
    created_objects: list[Any] = []
    program_id = str(prepared["program_id"])
    revision = str(prepared["revision"])
    for body_specification in list(history.get("outputs") or []):
        object_specs = list(body_specification.get("objects") or [])
        output_name = str(body_specification["output_name"])
        if existing_bodies is not None and output_name not in existing_bodies:
            # The Design-global publisher creates physical Bodies only for
            # api.body solid outputs. Standalone publish outputs retain their
            # stable publication boundary and do not gain a synthetic Body.
            continue
        body = (
            existing_bodies.get(output_name)
            if existing_bodies is not None
            else None
        )
        existing_tip = getattr(body, "Tip", None) if body is not None else None
        if body is None:
            body = doc.addObject(
                "PartDesign::Body",
                str(body_specification["body_name"]),
            )
            if body is None:
                raise RuntimeError(
                    f"FreeCAD did not restore the native Body for {output_name!r}."
                )
            body.Label = str(body_specification["body_label"])
            created_objects.append(body)
            provisional_query = getattr(
                doc,
                "isProvisionallyEnrolledInTimelineByCurrentTransaction",
                None,
            )
            classify_internal = getattr(
                doc,
                "classifyProvisionalTimelineInternalObject",
                None,
            )
            if (
                callable(provisional_query)
                and provisional_query(body)
                and callable(classify_internal)
            ):
                classify_internal(body)
            root.addObject(body)
        elif str(getattr(body, "TypeId", "") or "") != "PartDesign::Body":
            raise RuntimeError(
                f"Existing Part Design output {output_name!r} is not a Body."
            )

        objects: dict[str, Any] = {}
        for specification in object_specs:
            original_name = str(specification["name"])
            if existing_bodies is None:
                obj = body.newObject(
                    str(specification["type_id"]),
                    original_name,
                )
            else:
                obj = doc.addObject(
                    str(specification["type_id"]),
                    original_name,
                )
                if obj is not None:
                    root.addObject(obj)
            if obj is None:
                raise RuntimeError(
                    f"FreeCAD did not restore native history object {original_name!r}."
                )
            if str(getattr(obj, "TypeId", "") or "") != str(
                specification["type_id"]
            ):
                raise RuntimeError(
                    f"FreeCAD created the wrong native history type for "
                    f"{original_name!r}."
                )
            created_objects.append(obj)
            objects[original_name] = obj
            if internalize_restored_objects:
                provisional_query = getattr(
                    doc,
                    "isProvisionallyEnrolledInTimelineByCurrentTransaction",
                    None,
                )
                classify_internal = getattr(
                    doc,
                    "classifyProvisionalTimelineInternalObject",
                    None,
                )
                if (
                    callable(provisional_query)
                    and provisional_query(obj)
                    and callable(classify_internal)
                ):
                    classify_internal(obj)

        for specification in object_specs:
            original_name = str(specification["name"])
            obj = objects[original_name]
            try:
                obj.restoreContent(bytearray(specification["content"]))
            except Exception as exc:
                raise RuntimeError(
                    f"Could not restore native history object {original_name!r}: {exc}"
                ) from exc
            obj.Label = str(specification.get("label") or original_name)
            _add_string_property(
                obj,
                PROP_PARTDESIGN_HISTORY_KEY,
                "Stable authored identity for native Part Design history.",
            )
            if (
                obj.getTypeIdOfProperty(PROP_PARTDESIGN_HISTORY_KEY)
                != "App::PropertyString"
            ):
                raise RuntimeError(
                    f"Native Part Design history object {original_name!r} has "
                    "an invalid authored identity property."
                )
            setattr(obj, PROP_PARTDESIGN_HISTORY_KEY, original_name)
            _hide_property(obj, PROP_PARTDESIGN_HISTORY_KEY)

        for specification in object_specs:
            original_name = str(specification["name"])
            obj = objects[original_name]
            _apply_partdesign_history_links(
                body,
                objects,
                obj,
                dict(specification.get("links") or {}),
            )
            view = getattr(obj, "ViewObject", None)
            if view is not None and hasattr(view, "Visibility"):
                view.Visibility = bool(specification.get("visible"))
            if str(
                getattr(obj, "VibeCADFastenerSchema", "") or ""
            ) == "vibecad-standard-fastener-v1":
                from VibeCADFasteners import install_fastener_view_provider

                install_fastener_view_provider(obj)

            scripted_publication.tag_object(
                obj,
                role=scripted_publication.ROLE_IMPLEMENTATION,
                engine="vibescript:partdesign",
                model_id=program_id,
                output_key=output_name,
                revision=revision,
            )
        tip_name = str(body_specification.get("tip_name") or "")
        if preserve_existing_tips and existing_tip is not None:
            body.Tip = existing_tip
        elif tip_name and tip_name in objects:
            body.Tip = objects[tip_name]
        tip = getattr(body, "Tip", None)
        tip_shape = getattr(tip, "Shape", None)
        if (
            not (preserve_existing_tips and existing_tip is not None)
            and (
                str(body_specification.get("representation") or "") != "body"
                or tip_shape is None
                or tip_shape.isNull()
                or not tip_shape.isValid()
            )
        ):
            accepted = body.newObject(
                "PartDesign::Feature",
                f"{str(body.Name)}_Result",
            )
            if accepted is None:
                raise RuntimeError(
                    f"FreeCAD did not create the accepted native result for "
                    f"{output_name!r}."
                )
            accepted.Label = "Result"
            accepted.Shape = output_items[output_name]["detached_shape"]
            created_objects.append(accepted)
            _add_string_property(
                accepted,
                "VibeCADNativeFeatureRole",
                "Native feature role for a validated accepted result.",
            )
            accepted.VibeCADNativeFeatureRole = "adopted_result"
            _hide_property(accepted, "VibeCADNativeFeatureRole")
            _add_string_property(
                accepted,
                PROP_PARTDESIGN_HISTORY_KEY,
                "Stable authored identity for native Part Design history.",
            )
            setattr(
                accepted,
                PROP_PARTDESIGN_HISTORY_KEY,
                f"{output_name}.__accepted_result__",
            )
            _hide_property(accepted, PROP_PARTDESIGN_HISTORY_KEY)
            scripted_publication.tag_object(
                accepted,
                role=scripted_publication.ROLE_IMPLEMENTATION,
                engine="vibescript:partdesign",
                model_id=program_id,
                output_key=output_name,
                revision=revision,
            )
            body.Tip = accepted
        scripted_publication.tag_object(
            body,
            role=scripted_publication.ROLE_IMPLEMENTATION,
            engine="vibescript:partdesign",
            model_id=program_id,
            output_key=output_name,
            revision=revision,
        )
        _configure_partdesign_history_presentation(body)
        bodies[output_name] = body
        authored_objects = [
            objects[str(specification["name"])]
            for specification in object_specs
        ]
        if body.Tip is not None and all(
            body.Tip is not candidate for candidate in authored_objects
        ):
            authored_objects.append(body.Tip)
        if build_timeline_blocks:
            timeline_blocks[output_name] = _partdesign_timeline_blocks(
                authored_objects,
                output_name=output_name,
            )

    for body_specification in list(history.get("outputs") or []):
        output_name = str(body_specification["output_name"])
        if existing_bodies is not None and output_name not in existing_bodies:
            continue
        body = bodies.get(output_name)
        if body is None:
            raise RuntimeError(
                f"Native Part Design Body history for {output_name!r} is empty."
            )
        _verify_partdesign_history_body(
            body,
            output_items[output_name],
            output_name=output_name,
        )

    return {
        "available": True,
        "bodies": bodies,
        "created_objects": [
            str(getattr(obj, "Name", "") or "") for obj in created_objects
        ],
        "created_object_refs": created_objects,
        "timeline_blocks": timeline_blocks,
        "artifact_sha256": str(history.get("artifact_sha256") or ""),
    }


def _timeline_object_identity(obj: Any) -> tuple[str, int]:
    name = str(getattr(obj, "Name", "") or "")
    object_id = getattr(obj, "ID", None)
    if (
        not name
        or isinstance(object_id, bool)
        or not isinstance(object_id, int)
        or object_id <= 0
    ):
        raise RuntimeError(
            "The document timeline contains an object without a stable native identity."
        )
    return name, int(object_id)


def _document_timeline(doc: Any) -> Any | None:
    matches = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "App::DocumentTimeline"
    ]
    if len(matches) > 1:
        raise RuntimeError("The document contains multiple native operation timelines.")
    timeline = matches[0] if matches else None
    if timeline is None:
        return None
    required = {
        "Operations",
        "Position",
        "VisibilityAtEnd",
        "SuppressionAtEnd",
    }
    missing = required.difference(_properties(timeline))
    if missing:
        raise RuntimeError(
            "The native operation timeline is missing required properties: "
            + ", ".join(sorted(missing))
            + "."
        )
    return timeline


def _partdesign_timeline_body_identity(
    body: Any,
) -> tuple[str, str, str] | None:
    if (
        str(getattr(body, "TypeId", "") or "") != "PartDesign::Body"
        or scripted_publication.role_of(body)
        != scripted_publication.ROLE_IMPLEMENTATION
    ):
        return None
    engine = str(
        getattr(body, scripted_publication.PROP_ENGINE, "") or ""
    ).strip()
    model_id = str(
        getattr(body, scripted_publication.PROP_MODEL_ID, "") or ""
    ).strip()
    output_key = str(
        getattr(body, scripted_publication.PROP_OUTPUT_KEY, "") or ""
    ).strip()
    if engine != "vibescript:partdesign" or not model_id or not output_key:
        return None
    return engine, model_id, output_key


def _capture_partdesign_timeline_replacement(
    doc: Any,
    previous_implementation: list[Any],
) -> dict[str, Any] | None:
    """Capture exact authored Body segments before transactional replacement."""

    timeline = _document_timeline(doc)
    if timeline is None or not previous_implementation:
        return None
    operations = list(getattr(timeline, "Operations", []) or [])
    visibility = [
        bool(value)
        for value in list(getattr(timeline, "VisibilityAtEnd", []) or [])
    ]
    suppression = [
        bool(value)
        for value in list(getattr(timeline, "SuppressionAtEnd", []) or [])
    ]
    if len(visibility) != len(operations) or len(suppression) != len(operations):
        raise RuntimeError(
            "The native operation timeline has mismatched operation and state counts."
        )
    position = int(getattr(timeline, "Position", 0) or 0)
    if not 0 <= position <= len(operations):
        raise RuntimeError("The native operation timeline position is out of range.")

    bodies: dict[tuple[str, str, str], Any] = {}
    for obj in previous_implementation:
        identity = _partdesign_timeline_body_identity(obj)
        if identity is None:
            continue
        if identity in bodies and bodies[identity] is not obj:
            raise RuntimeError(
                "Multiple generated Part Design Bodies claim timeline identity "
                f"{identity[1]}:{identity[2]}."
            )
        bodies[identity] = obj
    if not bodies:
        return None

    member_identity_to_body: dict[tuple[str, int], tuple[str, str, str]] = {}
    member_key_by_identity: dict[tuple[str, int], str] = {}
    for identity, body in bodies.items():
        members = [body, *list(getattr(body, "Group", []) or [])]
        for member in members:
            member_identity = _timeline_object_identity(member)
            existing = member_identity_to_body.get(member_identity)
            if existing is not None and existing != identity:
                raise RuntimeError(
                    "A native operation belongs to multiple generated Part Design "
                    "Body segments."
                )
            member_identity_to_body[member_identity] = identity
            member_key_by_identity[member_identity] = (
                f"{identity[2]}.__body__"
                if member is body
                else _partdesign_history_key(
                    member,
                    context=(
                        f"Existing Part Design output {identity[2]!r} "
                        f"object {member.Name!r}"
                    ),
                )
            )

    operation_identities = [
        _timeline_object_identity(operation) for operation in operations
    ]
    segments: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, operation_identity in enumerate(operation_identities):
        body_identity = member_identity_to_body.get(operation_identity)
        if body_identity is None:
            continue
        segment = segments.setdefault(
            body_identity,
            {
                "indices": [],
                "objects": [],
                "operation_identities": [],
                "operation_keys": [],
            },
        )
        segment["indices"].append(index)
        segment["objects"].append(operations[index])
        segment["operation_identities"].append(operation_identity)
        segment["operation_keys"].append(member_key_by_identity[operation_identity])

    for identity, segment in segments.items():
        indices = list(segment["indices"])
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise RuntimeError(
                "Generated Part Design history is interleaved with unrelated "
                f"operations for {identity[1]}:{identity[2]}; regeneration cannot "
                "replace that history without changing document chronology."
            )
        segment["first_index"] = indices[0]
        segment["last_index"] = indices[-1]
        segment["before_position"] = sum(index < position for index in indices)
        if len(set(segment["operation_keys"])) != len(segment["operation_keys"]):
            raise RuntimeError(
                f"Generated Part Design history for {identity[1]}:{identity[2]} "
                "has duplicate authored identities."
            )

        segment_objects = list(segment["objects"])
        segment_set = set(segment_objects)
        root_objects: list[Any] = []
        root_keys: list[str] = []
        root_end_offsets: list[int] = []
        for local_index, candidate in enumerate(segment_objects):
            role = _timeline_role(
                candidate,
                context=(
                    f"Existing Part Design output {identity[2]!r} "
                    f"History member {candidate.Name!r}"
                ),
            )
            if role == "internal":
                raise RuntimeError(
                    f"Internal Part Design object {candidate.Name!r} appears "
                    "in native History."
                )
            if role == "resource":
                current = candidate
                visited: set[Any] = set()
                while _timeline_role(
                    current,
                    context=f"Part Design resource {candidate.Name!r}",
                ) == "resource":
                    if current in visited:
                        raise RuntimeError(
                            f"Part Design output {identity[2]!r} has cyclic "
                            "resource ownership."
                        )
                    visited.add(current)
                    current = _timeline_owner(
                        current,
                        context=f"Part Design resource {candidate.Name!r}",
                    )
                    if current is None:
                        break
                if current not in segment_set:
                    raise RuntimeError(
                        f"Part Design resource {candidate.Name!r} resolves "
                        "outside its generated Body segment."
                    )
                continue
            root_objects.append(candidate)
            root_keys.append(segment["operation_keys"][local_index])
            root_end_offsets.append(local_index + 1)
        if not root_objects:
            raise RuntimeError(
                f"Generated Part Design history for {identity[1]}:{identity[2]} "
                "has no semantic operations."
            )
        before_position = int(segment["before_position"])
        if (
            before_position not in {0, len(segment_objects)}
            and before_position not in set(root_end_offsets)
        ):
            raise RuntimeError(
                f"Generated Part Design history for {identity[1]}:{identity[2]} "
                "has a native History position inside a semantic operation."
            )
        segment["root_objects"] = root_objects
        segment["root_keys"] = root_keys
        segment["root_end_offsets"] = root_end_offsets

    if not segments:
        return None
    ordered_segments = [
        segment
        for _identity, segment in sorted(
            segments.items(),
            key=lambda item: int(item[1]["first_index"]),
        )
    ]
    for identity, segment in segments.items():
        segment["body_identity"] = identity
    return {
        "timeline_identity": _timeline_object_identity(timeline),
        "segments": ordered_segments,
    }


def _stage_partdesign_timeline_replacement(
    doc: Any,
    captured: Mapping[str, Any] | None,
) -> None:
    if not captured:
        return
    timeline = _document_timeline(doc)
    if timeline is None or _timeline_object_identity(timeline) != tuple(
        captured["timeline_identity"]
    ):
        raise RuntimeError(
            "The native operation History changed identity before Part Design "
            "regeneration."
        )
    stage = getattr(doc, "stageTimelineOperationSegmentReplacement", None)
    if not callable(stage):
        raise RuntimeError(
            "The native staged History segment-replacement API is unavailable."
        )
    stage(
        [
            list(segment["root_objects"])
            for segment in list(captured["segments"])
        ]
    )


def _replace_partdesign_timeline_segments(
    doc: Any,
    captured: Mapping[str, Any] | None,
    native_history: Mapping[str, Any],
) -> None:
    """Finalize exact staged replacement and publish wholly new Body history."""

    blocks_by_output = {
        str(name): [dict(block) for block in list(blocks)]
        for name, blocks in dict(native_history.get("timeline_blocks") or {}).items()
    }
    new_bodies: dict[tuple[str, str, str], Any] = {}
    for body in dict(native_history.get("bodies") or {}).values():
        identity = _partdesign_timeline_body_identity(body)
        if identity is None:
            raise RuntimeError(
                "A regenerated Part Design Body has no stable scripted output identity."
            )
        if identity in new_bodies and new_bodies[identity] is not body:
            raise RuntimeError(
                "Multiple regenerated Part Design Bodies claim History identity "
                f"{identity[1]}:{identity[2]}."
            )
        new_bodies[identity] = body

    replaced_outputs: set[str] = set()
    if captured:
        timeline = _document_timeline(doc)
        if timeline is None or _timeline_object_identity(timeline) != tuple(
            captured["timeline_identity"]
        ):
            raise RuntimeError(
                "The native operation History changed identity during Part Design "
                "publication."
            )
        mappings = []
        for segment_index, raw_segment in enumerate(
            list(captured["segments"])
        ):
            segment = dict(raw_segment)
            identity = tuple(segment["body_identity"])
            output_name = str(identity[2])
            replaced_outputs.add(output_name)
            blocks = (
                blocks_by_output.get(output_name, [])
                if identity in new_bodies
                else []
            )
            ordered_new_blocks = [
                list(block["ordered"]) for block in blocks
            ]
            flattened_new = [
                obj for block in ordered_new_blocks for obj in block
            ]
            flattened_new_keys = [
                str(key)
                for block in blocks
                for key in list(block["keys"])
            ]
            if len(flattened_new) != len(flattened_new_keys):
                raise RuntimeError(
                    f"Part Design output {output_name!r} has inconsistent "
                    "authored History keys."
                )
            if len(set(flattened_new_keys)) != len(flattened_new_keys):
                raise RuntimeError(
                    f"Part Design output {output_name!r} duplicates an "
                    "authored History key."
                )

            old_keys = [
                str(key) for key in list(segment["operation_keys"])
            ]
            old_index_by_key = {
                key: index for index, key in enumerate(old_keys)
            }
            state_sources = [
                old_index_by_key.get(key, -1)
                for key in flattened_new_keys
            ]
            consumer_replacements = [-1] * len(old_keys)

            before_position = int(segment["before_position"])
            old_member_count = len(old_keys)
            active_root_count = -1
            if before_position not in {0, old_member_count}:
                old_root_keys = [
                    str(key) for key in list(segment["root_keys"])
                ]
                old_active_by_key = {
                    key: int(end_offset) <= before_position
                    for key, end_offset in zip(
                        old_root_keys,
                        list(segment["root_end_offsets"]),
                    )
                }
                new_root_keys = [
                    str(block["root_key"]) for block in blocks
                ]
                if any(key not in old_active_by_key for key in new_root_keys):
                    raise RuntimeError(
                        f"Part Design output {output_name!r} changed operations "
                        "across the active native History boundary. Move History "
                        "to either side of that Body before regenerating it."
                    )
                active_states = [
                    old_active_by_key[key] for key in new_root_keys
                ]
                if any(
                    not active_states[index - 1] and active_states[index]
                    for index in range(1, len(active_states))
                ):
                    raise RuntimeError(
                        f"Part Design output {output_name!r} cannot preserve one "
                        "coherent active native History boundary."
                    )
                active_root_count = sum(active_states)

            mappings.append(
                (
                    segment_index,
                    ordered_new_blocks,
                    state_sources,
                    consumer_replacements,
                    active_root_count,
                )
            )

        finalize = getattr(
            doc,
            "finalizeProvisionalTimelineOperationSegmentReplacement",
            None,
        )
        if not callable(finalize):
            raise RuntimeError(
                "The native staged History segment-replacement finalizer is unavailable."
            )
        finalize(mappings)

    for output_name, blocks in blocks_by_output.items():
        if output_name in replaced_outputs:
            continue
        for block in blocks:
            _publish_new_timeline_resource_block(
                doc,
                block["operation"],
                list(block["resources"]),
                context=(
                    f"New Part Design output {output_name!r} operation "
                    f"{block['operation'].Name!r}"
                ),
            )


def _partdesign_design_program_operation(
    doc: Any,
    program_id: str,
) -> Any | None:
    matches = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "")
        == "PartDesign::DesignScriptOperation"
        and str(getattr(obj, "ProgramId", "") or "") == program_id
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple Design History operations claim VibeScript program "
            f"{program_id!r}."
        )
    return matches[0] if matches else None


def _partdesign_implementation_bodies(
    doc: Any,
    program_id: str,
) -> dict[str, Any]:
    claims: dict[str, list[Any]] = {}
    for obj in list(getattr(doc, "Objects", []) or []):
        if (
            str(getattr(obj, "TypeId", "") or "") != "PartDesign::Body"
            or scripted_publication.role_of(obj)
            != scripted_publication.ROLE_IMPLEMENTATION
            or str(
                getattr(obj, scripted_publication.PROP_ENGINE, "") or ""
            )
            != "vibescript:partdesign"
            or str(
                getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
            )
            != program_id
        ):
            continue
        key = str(
            getattr(obj, scripted_publication.PROP_OUTPUT_KEY, "") or ""
        ).strip()
        if not key:
            raise RuntimeError(
                f"Managed Part Design Body {obj.Name!r} has no output key."
            )
        claims.setdefault(key, []).append(obj)
    duplicates = {
        key: values for key, values in claims.items() if len(values) > 1
    }
    if duplicates:
        details = "; ".join(
            f"{key!r}: {', '.join(repr(str(body.Name)) for body in bodies)}"
            for key, bodies in sorted(duplicates.items())
        )
        raise RuntimeError(
            "Legacy Part Design output ownership is ambiguous because no native "
            f"Design History operation can choose the authoritative Body ({details})."
        )
    return {key: values[0] for key, values in claims.items()}


def _repair_partdesign_implementation_body_claims(
    doc: Any,
    program_id: str,
    authoritative: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Remove managed Bodies no longer backed by the native operation."""

    repairs: list[dict[str, str]] = []
    obsolete: list[Any] = []
    for obj in list(getattr(doc, "Objects", []) or []):
        if (
            str(getattr(obj, "TypeId", "") or "") != "PartDesign::Body"
            or scripted_publication.role_of(obj)
            != scripted_publication.ROLE_IMPLEMENTATION
            or str(
                getattr(obj, scripted_publication.PROP_ENGINE, "") or ""
            )
            != "vibescript:partdesign"
            or str(
                getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
            )
            != program_id
        ):
            continue
        key = str(
            getattr(obj, scripted_publication.PROP_OUTPUT_KEY, "") or ""
        ).strip()
        expected = authoritative.get(key)
        if expected is obj:
            continue
        obsolete.append(obj)
        repairs.append(
            {
                "object_name": str(obj.Name),
                "claimed_output": key,
                "authoritative_object": (
                    str(expected.Name) if expected is not None else ""
                ),
            }
        )
    if not obsolete:
        return repairs

    deletion_candidates: list[Any] = []
    candidate_ids: set[int] = set()
    for body in obsolete:
        owned = [body]
        pending = list(getattr(body, "Group", []) or [])
        while pending:
            candidate = pending.pop()
            if candidate in owned:
                continue
            owned.append(candidate)
            pending.extend(list(getattr(candidate, "Group", []) or []))
        for candidate in owned:
            if id(candidate) in candidate_ids:
                continue
            deletion_candidates.append(candidate)
            candidate_ids.add(id(candidate))
    deletion = _prepare_timeline_deletion(doc, deletion_candidates)
    deletion_targets = list(deletion["delete_objects"])
    program_internal = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(
            getattr(obj, contracts.PROP_PROGRAM_ID, "")
            or getattr(obj, scripted_publication.PROP_MODEL_ID, "")
            or ""
        )
        == program_id
    ]
    external = _external_uses(
        doc,
        deletion_targets,
        [*program_internal, *deletion_targets],
    )
    if external:
        raise _reference_error(
            "Cannot retire obsolete managed Part Design Bodies while foreign "
            "document objects still reference them",
            external,
        )
    _remove_timeline_deletion(doc, deletion)
    return repairs


def _partdesign_body_output(item: Mapping[str, Any]) -> bool:
    data = item.get("partdesign_data")
    return (
        isinstance(data, Mapping)
        and str(data.get("representation") or "") == "body"
        and str(item.get("type") or "") == "solid"
    )


def _partdesign_source_evidence(root: Any, program_id: str) -> list[Any]:
    """Return exact worker-native objects retained under one source root."""

    if root is None:
        return []
    return [
        obj
        for obj in list(getattr(root, "Group", []) or [])
        if PROP_PARTDESIGN_HISTORY_KEY in _properties(obj)
        and str(
            getattr(obj, scripted_publication.PROP_ENGINE, "") or ""
        )
        == "vibescript:partdesign"
        and str(
            getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
        )
        == program_id
    ]


def _publish_partdesign_design_candidate(
    service: Any,
    prepared: Mapping[str, Any],
    validated: Mapping[str, Any],
    doc: Any,
) -> dict[str, Any]:
    """Publish one complete program as one Design-global History operation."""

    import PartDesign

    items = [dict(item) for item in list(validated["outputs"])]
    if not items:
        raise RuntimeError(
            "A Part Design VibeScript program must publish at least one output."
        )
    body_items = [item for item in items if _partdesign_body_output(item)]
    program_id = str(prepared["program_id"])
    revision = str(prepared["revision"])
    root = _partdesign_program_root(doc, program_id)
    publications = (
        _partdesign_publications(doc, root, program_id)
        if root is not None
        else {}
    )
    operation = _partdesign_design_program_operation(doc, program_id)
    legacy_bodies = (
        _partdesign_implementation_bodies(doc, program_id)
        if operation is None
        else {}
    )
    previous_source_evidence = _partdesign_source_evidence(root, program_id)
    source_evidence_uses = _external_uses(
        doc,
        previous_source_evidence,
        [
            *previous_source_evidence,
            *([root] if root is not None else []),
            *([operation] if operation is not None else []),
        ],
    )
    if source_evidence_uses:
        raise _reference_error(
            "Cannot regenerate Part Design source evidence while downstream "
            "objects reference it; reference the published Body instead",
            source_evidence_uses,
        )

    previous_presentation = {
        name: _preflight_partdesign_presentation(obj)
        for name, obj in publications.items()
        if not _is_partdesign_component_occurrence(obj)
    }
    for item in items:
        if str(item.get("type") or "") == "component_link":
            continue
        output_name = str(item["name"])
        published = publications.get(output_name)
        if published is None:
            continue
        presentation = item.get("partdesign_presentation")
        if not isinstance(presentation, Mapping):
            raise RuntimeError(
                f"Part Design output {output_name!r} has no validated "
                "presentation state."
            )
        desired_channels = _partdesign_presentation_channels(presentation)
        if not desired_channels:
            continue
        for candidate in list(getattr(doc, "Objects", []) or []):
            if (
                str(
                    getattr(
                        candidate,
                        contracts.PROP_PROGRAM_DOMAIN,
                        "",
                    )
                    or ""
                )
                != "material"
                or getattr(candidate, PROP_MATERIAL_TARGET, None)
                is not published
            ):
                continue
            ownership = _material_ownership(candidate)
            if str(ownership.get("channel") or "") in desired_channels:
                raise RuntimeError(
                    f"Part Design output {output_name!r} cannot replace a "
                    "presentation channel owned by a Material program."
                )

    previous_interfaces: dict[str, Any] = {}
    if root is not None:
        try:
            previous_interfaces = json.loads(
                str(
                    getattr(
                        root,
                        scripted_publication.PROP_INTERFACES,
                        "{}",
                    )
                    or "{}"
                )
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Part Design program {program_id} has invalid interface "
                f"metadata: {exc}"
            ) from exc
        if not isinstance(previous_interfaces, dict):
            raise RuntimeError(
                f"Part Design program {program_id} has a non-object "
                "interface table."
            )

    existing_values = list(publications.values())
    existing_shape_publications = [
        obj
        for obj in existing_values
        if not _is_partdesign_component_occurrence(obj)
    ]
    reference_preflight = (
        reference_contracts.preflight_regeneration(
            service,
            existing_shape_publications,
            model_root=root,
        )
        if root is not None and existing_shape_publications
        else None
    )
    desired = {str(item["name"]) for item in items}
    retired_names = sorted(set(publications) - desired)
    for name in retired_names:
        uses = scripted_publication.external_reference_uses(
            doc,
            [publications[name]],
            internal_objects=(
                [root, *existing_values]
                if root is not None
                else existing_values
            ),
        )
        if uses:
            raise _reference_error(
                f"Cannot retire Part Design VibeScript output {name!r} "
                "while downstream objects reference it",
                uses,
            )

    # A stable VibeScript result key is the public identity. Its App::Link can
    # change between a lightweight occurrence and a shape publication when the
    # source contract is deliberately edited. Convert it only when no foreign
    # object references its current representation.
    replacement_names: list[str] = []
    replacement_internal = [
        *([root] if root is not None else []),
        *existing_values,
    ]
    for candidate in existing_shape_publications:
        target = scripted_publication.publication_target(candidate, root)
        if target is not None and target not in replacement_internal:
            replacement_internal.append(target)
    for item in items:
        name = str(item["name"])
        published = publications.get(name)
        if published is None:
            continue
        wants_component = str(item.get("type") or "") == "component_link"
        has_component = _is_partdesign_component_occurrence(published)
        if wants_component == has_component:
            continue
        uses = scripted_publication.external_reference_uses(
            doc,
            [published],
            internal_objects=replacement_internal,
        )
        if uses:
            raise _reference_error(
                f"Cannot change Part Design VibeScript output {name!r} between "
                "a shape publication and a linked occurrence while downstream "
                "objects reference its current native carrier",
                uses,
            )
        replacement_names.append(name)

    # Older documents stored each accepted worker feature graph in a generated
    # Body under the program root. Replace that implementation exactly once;
    # stable public links remain the downstream compatibility boundary.
    legacy_objects: list[Any] = []
    if operation is None:
        for body in legacy_bodies.values():
            for obj in [
                body,
                *list(getattr(body, "OutListRecursive", []) or []),
            ]:
                if obj not in legacy_objects:
                    legacy_objects.append(obj)
    legacy_deletion = _prepare_timeline_deletion(doc, legacy_objects)
    legacy_targets = list(legacy_deletion["delete_objects"])
    legacy_uses = _external_uses(
        doc,
        legacy_targets,
        [
            *legacy_targets,
            *existing_values,
            *([root] if root is not None else []),
        ],
    )
    if legacy_uses:
        raise _reference_error(
            "Cannot migrate generated Part Design history while foreign "
            "objects reference its private implementation; reference the "
            "stable published output instead",
            legacy_uses,
        )

    rollback_targets: dict[int, Any] = {}
    for obj in publications.values():
        rollback_targets[id(obj)] = obj
        if not _is_partdesign_component_occurrence(obj):
            implementation = scripted_publication.publication_target(obj, root)
            rollback_targets[id(implementation)] = implementation
    rollback_states = [
        _material_target_snapshot(obj)
        for obj in rollback_targets.values()
    ]

    transaction_open = False
    created: list[str] = []
    removed: list[str] = []
    removed_implementation: list[str] = []
    created_bodies: list[str] = []
    ownership_repairs: list[dict[str, str]] = []
    deferred_representation_targets: list[str] = []
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish Part Design VibeScript: "
                f"{prepared['program_name']}"
            )
            transaction_open = True
        if root is None:
            root = doc.addObject(
                "App::Part",
                _internal_name(prepared, "Program"),
            )
            if root is None:
                raise RuntimeError(
                    "FreeCAD did not create the Part Design program metadata "
                    "object."
                )
            created.append(str(root.Name))
        root.Label = str(prepared["program_name"])
        _tag_partdesign_root(root, prepared)
        if previous_source_evidence:
            removed_implementation.extend(
                _remove_objects_dependency_order(
                    doc,
                    previous_source_evidence,
                )
            )
        # Component occurrences are top-level History operations. Temporarily
        # remove the root's semantic LinkList while the native Design
        # operation replaces its own resources, otherwise the timeline sees a
        # later occurrence as an implementation dependency of an earlier
        # consumer. The exact LinkList is restored after all outputs update.
        if _partdesign_component_occurrences(root):
            _set_partdesign_component_occurrences(root, [])

        for name in retired_names:
            removed.extend(
                _delete_partdesign_publication(
                    doc,
                    root,
                    publications.pop(name),
                )
            )
        items_by_name = {str(item["name"]): item for item in items}
        for name in replacement_names:
            migration = _migrate_partdesign_output_representation(
                doc,
                root,
                publications[name],
                prepared,
                items_by_name[name],
            )
            created.extend(migration["created"])
            removed.extend(migration["removed"])
            deferred_representation_targets.extend(
                migration["deferred_remove"]
            )
        if legacy_targets:
            removed_implementation.extend(
                _remove_timeline_deletion(doc, legacy_deletion)
            )

        if operation is None:
            operation = doc.addObject(
                "PartDesign::DesignScriptOperation",
                _internal_name(prepared, "ProgramOperation"),
            )
            if operation is None:
                raise RuntimeError(
                    "FreeCAD did not create the Design VibeScript History "
                    "operation."
                )
            created.append(str(operation.Name))
        operation.Label = str(prepared["program_name"])
        _set_partdesign_program_history_commands(operation)

        output_keys = [str(item["name"]) for item in body_items]
        output_labels = [
            str(
                dict(item.get("partdesign_data") or {}).get(
                    "body_label"
                )
                or item["name"]
            )
            for item in body_items
        ]
        before_body_names = {
            str(obj.Name)
            for obj in list(getattr(doc, "Objects", []) or [])
            if str(getattr(obj, "TypeId", "") or "")
            == "PartDesign::Body"
        }
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            str(root.Name),
            program_id,
            revision,
            output_keys,
            output_labels,
            [item["detached_shape"] for item in body_items],
            [None] * len(body_items),
            [str(item["name"]) for item in items],
            [str(item["type"]) for item in items],
        )
        bodies = list(PartDesign.finalizeDesignScriptOperationEdit(edit))
        if len(bodies) != len(body_items):
            raise RuntimeError(
                "The Design VibeScript operation did not publish one Body per "
                "api.body output."
            )
        created_bodies = [
            str(body.Name)
            for body in bodies
            if str(body.Name) not in before_body_names
        ]
        scripted_publication.tag_object(
            operation,
            role=scripted_publication.ROLE_IMPLEMENTATION,
            engine="vibescript:partdesign",
            model_id=program_id,
            revision=revision,
        )

        bodies_by_output = {
            str(item["name"]): body
            for item, body in zip(body_items, bodies)
        }
        restored_native_history = _materialize_partdesign_native_history(
            doc,
            root,
            prepared,
            validated,
            existing_bodies=bodies_by_output,
            preserve_existing_tips=True,
            internalize_restored_objects=True,
            build_timeline_blocks=False,
        )
        for item in items:
            name = str(item["name"])
            output_type = str(item["type"])
            body = bodies_by_output.get(name)
            if body is not None:
                scripted_publication.tag_object(
                    body,
                    role=scripted_publication.ROLE_IMPLEMENTATION,
                    engine="vibescript:partdesign",
                    model_id=program_id,
                    output_key=name,
                    revision=revision,
                )
            published = publications.get(name)
            if output_type == "component_link":
                created_occurrence = published is None
                if published is None:
                    published = _create_partdesign_component_occurrence(
                        doc,
                        root,
                        prepared,
                        item,
                    )
                    publications[name] = published
                    created.append(str(published.Name))
                    rollback_targets[id(published)] = published
                    rollback_states.append(
                        _material_target_snapshot(
                            published,
                            required_after_abort=False,
                        )
                    )
                else:
                    _update_partdesign_component_occurrence(
                        doc,
                        root,
                        published,
                        prepared,
                        item,
                    )
                if name in replacement_names:
                    scripted_publication.ensure_string_property(
                        published, scripted_publication.PROP_IMPLEMENTATION
                    )
                    setattr(
                        published,
                        scripted_publication.PROP_IMPLEMENTATION,
                        "",
                    )
                if created_occurrence:
                    # api.component creates one placed occurrence of a reusable
                    # definition. The definition is not an implicit second
                    # occurrence at its authoring origin. Users can still show
                    # it explicitly, and later builds preserve both independent
                    # visibility choices.
                    target = getattr(published, "LinkedObject", None)
                    if target is not None:
                        if id(target) not in rollback_targets:
                            rollback_targets[id(target)] = target
                            rollback_states.append(
                                _material_target_snapshot(target)
                            )
                        _set_view_visibility(target, False)
                    _set_view_visibility(published, True)
                continue
            carrier = _PartDesignShapeCarrier(item)
            if published is None:
                published = scripted_publication.create_publication(
                    doc,
                    root,
                    carrier,
                    internal_name=_internal_name(prepared, name),
                    label=carrier.Label,
                    engine="vibescript:partdesign",
                    model_id=program_id,
                    output_key=name,
                    revision=revision,
                )
                publications[name] = published
                created.append(str(published.Name))
                for rollback_target in (
                    published,
                    scripted_publication.publication_target(
                        published,
                        root,
                    ),
                ):
                    if id(rollback_target) in rollback_targets:
                        continue
                    rollback_targets[id(rollback_target)] = rollback_target
                    rollback_states.append(
                        _material_target_snapshot(
                            rollback_target,
                            required_after_abort=False,
                        )
                    )
            else:
                _restore_partdesign_presentation_baseline(
                    published,
                    previous_presentation.get(name),
                )
                scripted_publication.tag_object(
                    published,
                    role=scripted_publication.ROLE_PUBLICATION,
                    engine="vibescript:partdesign",
                    model_id=program_id,
                    output_key=name,
                    revision=revision,
                )
                scripted_publication.update_publication(
                    published,
                    root,
                    carrier,
                    revision=revision,
                )
                published.Label = carrier.Label
            _set_metadata(
                published,
                prepared,
                name,
                output_type,
                _definition(item),
            )
            _configure_partdesign_presentation(published, item)
            if body is not None:
                _copy_native_body_presentation(published, body)
                _configure_partdesign_history_presentation(
                    body,
                    visible=True,
                )
                _set_view_visibility(published, False)
            else:
                # Non-solid outputs retain the stable publication as their
                # sole viewport result. They belong to the same source
                # operation but intentionally have no physical Body identity.
                _set_view_visibility(published, True)

        _set_partdesign_component_occurrences(
            root,
            [
                publications[str(item["name"])]
                for item in items
                if str(item.get("type") or "") == "component_link"
            ],
        )

        ownership_repairs = _repair_partdesign_implementation_body_claims(
            doc,
            program_id,
            bodies_by_output,
        )

        interface_table = _partdesign_interface_table(
            validated,
            publications,
        )
        reference_contracts.validate_removed_interfaces(
            doc,
            [
                obj
                for obj in publications.values()
                if not _is_partdesign_component_occurrence(obj)
            ],
            program_id,
            reference_contracts.interface_identities(previous_interfaces),
            reference_contracts.interface_identities(interface_table),
            preflight=reference_preflight,
        )
        setattr(
            root,
            scripted_publication.PROP_INTERFACES,
            json.dumps(
                interface_table,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        downstream = reference_contracts.refresh_after_publication(
            service,
            program_id,
            list(publications.values()),
            revision=revision,
            preflight=reference_preflight,
        )
        for target_name in deferred_representation_targets:
            target = doc.getObject(target_name)
            if target is None:
                continue
            doc.removeObject(target_name)
            removed.append(target_name)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        abort_error = None
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
                transaction_open = False
            except Exception as exc:
                abort_error = exc
        rollback_error = None
        try:
            _restore_material_target_snapshots(rollback_states)
        except Exception as exc:
            rollback_error = exc
        if abort_error is not None or rollback_error is not None:
            raise RuntimeError(
                f"{publication_error} Explicit Part Design presentation "
                "rollback failed: "
                + "; ".join(
                    message
                    for message in (
                        (
                            f"transaction abort: {type(abort_error).__name__}: "
                            f"{abort_error}"
                            if abort_error is not None
                            else ""
                        ),
                        (
                            f"presentation restore: {rollback_error}"
                            if rollback_error is not None
                            else ""
                        ),
                    )
                    if message
                )
            ) from publication_error
        raise

    live_outputs: dict[str, Any] = {}
    output_rows: list[dict[str, Any]] = []
    body_objects = {
        str(item["name"]): str(body.Name)
        for item, body in zip(body_items, bodies)
    }
    for item in items:
        name = str(item["name"])
        output_type = str(item["type"])
        published = publications[name]
        partdesign_data = dict(item.get("partdesign_data") or {})
        component_data = dict(item.get("component_data") or {})
        authored_label = str(
            (
                component_data.get("label")
                if output_type == "component_link"
                else partdesign_data.get("body_label")
            )
            or published.Label
        )
        row = {
            "object_name": str(published.Name),
            "reference": {
                "document_uid": str(getattr(doc, "Uid", "") or ""),
                "object_name": str(published.Name),
            },
            # The stable hidden App::Link can receive FreeCAD's duplicate-label
            # suffix because the visible native Body already owns the authored
            # label. Tool results describe the authored output, not that hidden
            # carrier's presentation detail.
            "label": authored_label,
            "type_id": str(published.TypeId),
            "output_type": output_type,
            "facts": dict(item.get("facts") or {}),
            "partdesign_data": partdesign_data,
            **(
                {"component_data": component_data}
                if component_data
                else {}
            ),
            "derived_state": str(
                getattr(
                    published,
                    reference_contracts.PROP_DERIVED_STATE,
                    "",
                )
                or ""
            ),
            "stale_reason": str(
                getattr(
                    published,
                    reference_contracts.PROP_STALE_REASON,
                    "",
                )
                or ""
            ),
            "source_revision": str(
                getattr(
                    published,
                    reference_contracts.PROP_SOURCE_REVISION,
                    "",
                )
                or ""
            ),
        }
        live_outputs[name] = row
        output_rows.append({"name": name, **row})
    return {
        "ok": True,
        "outputs": output_rows,
        "live_outputs": live_outputs,
        "interfaces": interface_table,
        "created_objects": created,
        "retired_objects": removed,
        "native_history": {
            "available": True,
            "strategy": "design_program_operation",
            "operation_object": str(operation.Name),
            "body_objects": body_objects,
            "created_objects": [
                *created_bodies,
                *list(restored_native_history.get("created_objects") or []),
            ],
            "restored_source_objects": list(
                restored_native_history.get("created_objects") or []
            ),
            "retired_objects": removed_implementation,
            "ownership_repairs": ownership_repairs,
            "artifact_sha256": str(
                restored_native_history.get("artifact_sha256") or ""
            ),
        },
        "downstream_references": downstream,
        "recompute_deferred": True,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


def _publish_partdesign_legacy_candidate(
    service: Any,
    prepared: Mapping[str, Any],
    validated: Mapping[str, Any],
    doc: Any,
) -> dict[str, Any]:
    """Publish one v2 Part Design candidate through the shared stable boundary."""

    program_id = str(prepared["program_id"])
    root = _partdesign_program_root(doc, program_id)
    publications = (
        _partdesign_publications(doc, root, program_id) if root is not None else {}
    )
    native_history_available = isinstance(
        validated.get("partdesign_native_history"),
        Mapping,
    )
    previous_implementation = (
        scripted_publication.implementation_closure(root)
        if root is not None and native_history_available
        else []
    )
    timeline_replacement = _capture_partdesign_timeline_replacement(
        doc,
        previous_implementation,
    )
    if previous_implementation:
        publication_targets = [
            scripted_publication.publication_target(obj, root)
            for obj in publications.values()
        ]
        implementation_uses = scripted_publication.external_reference_uses(
            doc,
            previous_implementation,
            internal_objects=[
                root,
                *previous_implementation,
                *publications.values(),
                *publication_targets,
            ],
        )
        if implementation_uses:
            raise _reference_error(
                "Cannot regenerate native Part Design history while downstream "
                "objects reference its generated implementation",
                implementation_uses,
            )
    previous_presentation = {
        name: _preflight_partdesign_presentation(obj)
        for name, obj in publications.items()
    }
    for item in validated["outputs"]:
        output_name = str(item["name"])
        published = publications.get(output_name)
        if published is None:
            continue
        presentation = item.get("partdesign_presentation")
        if not isinstance(presentation, Mapping):
            raise RuntimeError(
                f"Part Design output {output_name!r} has no validated presentation state."
            )
        desired_channels = _partdesign_presentation_channels(presentation)
        if not desired_channels:
            continue
        for candidate in list(getattr(doc, "Objects", []) or []):
            if (
                str(getattr(candidate, contracts.PROP_PROGRAM_DOMAIN, "") or "")
                != "material"
                or getattr(candidate, PROP_MATERIAL_TARGET, None) is not published
            ):
                continue
            ownership = _material_ownership(candidate)
            if str(ownership.get("channel") or "") in desired_channels:
                raise RuntimeError(
                    f"Part Design output {output_name!r} cannot own "
                    f"{ownership['channel']} presentation while Material program "
                    f"{getattr(candidate, contracts.PROP_PROGRAM_ID, '')!r} owns "
                    "the same channel."
                )
    previous_interfaces: dict[str, Any] = {}
    if root is not None:
        try:
            previous_interfaces = json.loads(
                str(
                    getattr(root, scripted_publication.PROP_INTERFACES, "{}")
                    or "{}"
                )
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Part Design program {program_id} has invalid interface metadata: {exc}"
            ) from exc
        if not isinstance(previous_interfaces, dict):
            raise RuntimeError(
                f"Part Design program {program_id} has a non-object interface table."
            )
    existing_values = list(publications.values())
    reference_preflight = (
        reference_contracts.preflight_regeneration(
            service,
            existing_values,
            model_root=root,
        )
        if root is not None and existing_values
        else None
    )
    desired = {str(item["name"]) for item in validated["outputs"]}
    retired_names = sorted(set(publications) - desired)
    for name in retired_names:
        uses = scripted_publication.external_reference_uses(
            doc,
            [publications[name]],
            internal_objects=[root, *existing_values] if root is not None else existing_values,
        )
        if uses:
            raise _reference_error(
                f"Cannot retire Part Design VibeScript output {name!r} while "
                "downstream objects reference it",
                uses,
            )
    transaction_open = False
    created: list[str] = []
    removed: list[str] = []
    removed_implementation: list[str] = []
    native_history = {
        "available": False,
        "bodies": {},
        "created_objects": [],
        "created_object_refs": [],
        "timeline_blocks": {},
    }
    rollback_targets: dict[int, Any] = {}
    for obj in publications.values():
        rollback_targets[id(obj)] = obj
        implementation = scripted_publication.publication_target(obj, root)
        rollback_targets[id(implementation)] = implementation
    rollback_states = [
        _material_target_snapshot(obj) for obj in rollback_targets.values()
    ]
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish Part Design VibeScript: {prepared['program_name']}"
            )
            transaction_open = True
        _stage_partdesign_timeline_replacement(
            doc,
            timeline_replacement,
        )
        if root is None:
            root = doc.addObject(
                "App::Part", _internal_name(prepared, "Program")
            )
            if root is None:
                raise RuntimeError("FreeCAD did not create the Part Design program root.")
            created.append(str(root.Name))
        root.Label = str(prepared["program_name"])
        _tag_partdesign_root(root, prepared)
        if native_history_available:
            removed_implementation = scripted_publication.delete_implementation(
                doc,
                root,
            )
            native_history = _materialize_partdesign_native_history(
                doc,
                root,
                prepared,
                validated,
            )
        for name in retired_names:
            removed.extend(
                scripted_publication.delete_publication(
                    doc, root, publications.pop(name)
                )
            )
        for item in validated["outputs"]:
            name = str(item["name"])
            output_type = str(item["type"])
            carrier = _PartDesignShapeCarrier(item)
            published = publications.get(name)
            if published is None:
                published = scripted_publication.create_publication(
                    doc,
                    root,
                    carrier,
                    internal_name=_internal_name(prepared, name),
                    label=carrier.Label,
                    engine="vibescript:partdesign",
                    model_id=program_id,
                    output_key=name,
                    revision=str(prepared["revision"]),
                )
                publications[name] = published
                created.append(str(published.Name))
                for rollback_target in (
                    published,
                    scripted_publication.publication_target(published, root),
                ):
                    if id(rollback_target) in rollback_targets:
                        continue
                    rollback_targets[id(rollback_target)] = rollback_target
                    rollback_states.append(
                        _material_target_snapshot(
                            rollback_target,
                            required_after_abort=False,
                        )
                    )
            else:
                _restore_partdesign_presentation_baseline(
                    published,
                    previous_presentation.get(name),
                )
                scripted_publication.tag_object(
                    published,
                    role=scripted_publication.ROLE_PUBLICATION,
                    engine="vibescript:partdesign",
                    model_id=program_id,
                    output_key=name,
                    revision=str(prepared["revision"]),
                )
                scripted_publication.update_publication(
                    published,
                    root,
                    carrier,
                    revision=str(prepared["revision"]),
                )
                published.Label = carrier.Label
            _set_metadata(
                published,
                prepared,
                name,
                output_type,
                _definition(item),
            )
            body = dict(native_history.get("bodies") or {}).get(name)
            previous_state = previous_presentation.get(name)
            defer_publication_presentation = (
                body is not None and previous_state is not None
            )
            if not defer_publication_presentation:
                _configure_partdesign_presentation(published, item)
            if body is not None:
                _configure_partdesign_presentation(body, item)
                _configure_partdesign_history_presentation(body, visible=True)
                published.LinkedObject = (root, f"{body.Name}.")
                published.LinkTransform = True
                _set_view_visibility(published, False)
                if defer_publication_presentation:
                    # Relinking can refresh link-proxied material and display
                    # properties from the regenerated Body. Reconcile the
                    # persisted baseline and newly validated source state only
                    # after the stable publication points at that Body.
                    _restore_partdesign_presentation_baseline(
                        published,
                        previous_state,
                    )
                    _configure_partdesign_presentation(published, item)
        interface_table = _partdesign_interface_table(validated, publications)
        reference_contracts.validate_removed_interfaces(
            doc,
            list(publications.values()),
            program_id,
            reference_contracts.interface_identities(previous_interfaces),
            reference_contracts.interface_identities(interface_table),
            preflight=reference_preflight,
        )
        setattr(
            root,
            scripted_publication.PROP_INTERFACES,
            json.dumps(
                interface_table,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
        downstream = reference_contracts.refresh_after_publication(
            service,
            program_id,
            list(publications.values()),
            revision=str(prepared["revision"]),
            preflight=reference_preflight,
        )
        _replace_partdesign_timeline_segments(
            doc,
            timeline_replacement,
            native_history,
        )
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        abort_error = None
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
                transaction_open = False
            except Exception as exc:
                abort_error = exc
        rollback_error = None
        try:
            _restore_material_target_snapshots(rollback_states)
        except Exception as exc:
            rollback_error = exc
        if abort_error is not None or rollback_error is not None:
            raise RuntimeError(
                f"{publication_error} Explicit Part Design presentation rollback "
                "failed: "
                + "; ".join(
                    message
                    for message in (
                        (
                            f"transaction abort: {type(abort_error).__name__}: "
                            f"{abort_error}"
                            if abort_error is not None
                            else ""
                        ),
                        (
                            f"presentation restore: {rollback_error}"
                            if rollback_error is not None
                            else ""
                        ),
                    )
                    if message
                )
            ) from publication_error
        raise
    live_outputs: dict[str, Any] = {}
    output_rows: list[dict[str, Any]] = []
    for item in validated["outputs"]:
        name = str(item["name"])
        output_type = str(item["type"])
        published = publications[name]
        row = {
            "object_name": str(published.Name),
            "label": str(published.Label),
            "type_id": str(published.TypeId),
            "output_type": output_type,
            "facts": dict(item.get("facts") or {}),
            "partdesign_data": dict(item.get("partdesign_data") or {}),
            "derived_state": str(
                getattr(published, reference_contracts.PROP_DERIVED_STATE, "")
                or ""
            ),
            "stale_reason": str(
                getattr(published, reference_contracts.PROP_STALE_REASON, "")
                or ""
            ),
            "source_revision": str(
                getattr(published, reference_contracts.PROP_SOURCE_REVISION, "")
                or ""
            ),
        }
        live_outputs[name] = row
        output_rows.append({"name": name, **row})
    return {
        "ok": True,
        "outputs": output_rows,
        "live_outputs": live_outputs,
        "interfaces": interface_table,
        "created_objects": created,
        "retired_objects": removed,
        "native_history": {
            "available": bool(native_history.get("available")),
            "body_objects": {
                name: str(body.Name)
                for name, body in dict(native_history.get("bodies") or {}).items()
            },
            "created_objects": list(native_history.get("created_objects") or []),
            "retired_objects": removed_implementation,
            "artifact_sha256": str(
                native_history.get("artifact_sha256") or ""
            ),
        },
        "downstream_references": downstream,
        "recompute_deferred": True,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


def _publish_partdesign_candidate(
    service: Any,
    prepared: Mapping[str, Any],
    validated: Mapping[str, Any],
    doc: Any,
) -> dict[str, Any]:
    outputs = list(validated.get("outputs") or [])
    if not outputs:
        raise RuntimeError(
            "A Part Design VibeScript program must publish at least one output."
        )
    return _publish_partdesign_design_candidate(
        service,
        prepared,
        validated,
        doc,
    )


def publish_candidate(
    service: Any,
    prepared: dict[str, Any],
    validated: dict[str, Any],
) -> dict[str, Any]:
    """Apply detached, validated values without process waits or artifact I/O."""

    _surface_still_matches(service, prepared)
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "") or "") != prepared["document_name"]:
        raise RuntimeError("The active document changed while the domain worker ran.")
    if str(getattr(doc, "Uid", "") or "") != prepared["document_uid"]:
        raise RuntimeError(
            "The active document identity changed while the domain worker ran."
        )
    if str(service.provider_document_revision()) != prepared["document_revision"]:
        raise RuntimeError(
            "The document changed while the domain worker ran; regenerate on the live state."
        )
    # Older Part Design component programs stored top-level occurrences in a
    # normal LinkList on their earlier program root. Repair that bookkeeping
    # before any domain finalizes another History operation; otherwise the
    # legacy forward dependency invalidates unrelated Model and Assembly work.
    migrate_partdesign_component_occurrence_links(doc)
    domain = str(prepared["pack"].domain)
    if domain not in _TIMELINE_PUBLICATION_STRATEGY_BY_DOMAIN:
        raise RuntimeError(
            f"VibeScript domain {domain!r} has no semantic History publication "
            "strategy."
        )
    if domain == "partdesign":
        return _publish_partdesign_candidate(service, prepared, validated, doc)
    if domain == "material":
        return _publish_material_candidate(service, prepared, validated, doc)
    if domain == "cam":
        return _publish_cam_candidate(service, prepared, validated, doc)
    if domain == "techdraw":
        return _publish_techdraw_candidate(service, prepared, validated, doc)
    public_output_names = [str(item["name"]) for item in validated["outputs"]]
    if domain == "assembly" and validated.get("assembly_members"):
        validated = {
            **validated,
            "outputs": [
                *list(validated["outputs"]),
                *list(validated["assembly_members"]),
            ],
        }
    existing = _objects_by_output(doc, prepared)
    desired_output_names = {str(item["name"]) for item in validated["outputs"]}
    retired = _retired_program_objects(doc, prepared, desired_output_names)
    internal_objects = _program_objects(
        doc,
        str(prepared["program_id"]),
        prepared["pack"].domain,
    )
    if prepared["pack"].domain == "assembly":
        for candidate in list(internal_objects):
            if str(getattr(candidate, "TypeId", "")) != "Assembly::AssemblyObject":
                continue
            for group in (
                _assembly_joint_group(candidate),
                _assembly_simulation_group(candidate),
                _assembly_view_group(candidate),
                _assembly_bom_group(candidate),
                _assembly_verification_group(candidate),
            ):
                if group is not None and group not in internal_objects:
                    internal_objects.append(group)
    retired_deletion = _prepare_timeline_deletion(doc, retired)
    retired_targets = list(retired_deletion["delete_objects"])
    retired_uses = _external_uses(
        doc,
        retired_targets,
        [*internal_objects, *retired_targets],
    )
    if retired_uses:
        raise _reference_error(
            f"Cannot retire {prepared['pack'].title} VibeScript outputs while "
            "human-created or foreign document objects still reference them",
            retired_uses,
        )
    updated_objects = [
        existing[str(item["name"])]
        for item in validated["outputs"]
        if str(item["name"]) in existing
    ]
    if prepared["pack"].domain == "assembly":
        updated_names = {str(item["name"]) for item in validated["outputs"]}
        updated_objects.extend(
            obj
            for obj in internal_objects
            if ".move." in str(
                getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""
            )
            and str(
                getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""
            ).partition(".")[0]
            in updated_names
        )
    spreadsheet_rollbacks = (
        _spreadsheet_rollback_states(updated_objects)
        if prepared["pack"].domain == "spreadsheet"
        else []
    )
    assembly_bom_rollbacks = (
        _assembly_bom_rollback_states(updated_objects)
        if prepared["pack"].domain == "assembly"
        else []
    )
    mesh_rollbacks = (
        _mesh_rollback_states(internal_objects)
        if prepared["pack"].domain in {"mesh", "meshpart", "reverse_engineering"}
        else []
    )
    meshpart_shape_rollbacks = (
        _meshpart_shape_rollback_states(internal_objects)
        if prepared["pack"].domain in {"mesh", "meshpart", "reverse_engineering"}
        else []
    )
    points_rollbacks = (
        _points_rollback_states(internal_objects)
        if prepared["pack"].domain == "points"
        else []
    )
    reverse_feature_rollbacks = (
        _reverse_feature_rollback_states(internal_objects)
        if prepared["pack"].domain == "reverse_engineering"
        else []
    )
    inspection_rollbacks = (
        _inspection_rollback_states(internal_objects)
        if prepared["pack"].domain == "inspection"
        else []
    )
    robot_rollbacks = (
        _robot_rollback_states(internal_objects)
        if prepared["pack"].domain == "robot"
        else []
    )
    fem_rollbacks = (
        _fem_rollback_states(internal_objects)
        if prepared["pack"].domain == "fem"
        else []
    )
    downstream_uses = _preflight_output_updates(
        doc,
        updated_objects,
        internal_objects,
    )
    outputs: dict[str, Any] = {}
    created: list[Any] = []
    removed: list[str] = []
    assembly_dependency_anchor: Any | None = None
    assembly_previous_adoptions: dict[str, Any] = {}
    assembly_adoptions: dict[str, Any] = {}
    assembly_fastener_sources: dict[str, Any] = {}
    assembly_replaced_fastener_source_identities: list[tuple[str, int]] = []
    robot_trajectory_swaps: list[dict[str, Any]] = []
    retired_robot_trajectories: list[dict[str, Any]] = []
    pending_output_publications: dict[int, tuple[Any, str]] = {}
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(
                f"Publish {prepared['pack'].title} VibeScript: {prepared['program_name']}"
            )
            transaction_open = True

        def ensure_output_object(item: Mapping[str, Any], owner: Any | None) -> Any:
            output_name = str(item["name"])
            output_type = str(item["type"])
            adoption_target = (
                _assembly_adoption_target(doc, item)
                if prepared["pack"].domain == "assembly"
                and owner is not None
                else None
            )
            previous_adoption = assembly_previous_adoptions.get(output_name)
            if previous_adoption is not None and adoption_target is not previous_adoption:
                raise RuntimeError(
                    f"Assembly output {output_name!r} already adopts Model occurrence "
                    f"{previous_adoption.Name!r}. Keep that source or use a new output "
                    "name for a different occurrence."
                )
            obj = outputs.get(output_name) or existing.get(output_name)
            if adoption_target is not None:
                if obj is not None and obj is not adoption_target:
                    raise RuntimeError(
                        f"Assembly output {output_name!r} already owns a different native "
                        "occurrence. Use a new output name when adopting an existing "
                        "Model occurrence."
                    )
                obj = adoption_target
                _adopt_assembly_occurrence(
                    owner,
                    obj,
                    output_name=output_name,
                )
                assembly_adoptions[output_name] = obj
            if obj is None:
                obj = _create_object(
                    doc,
                    prepared,
                    output_name,
                    output_type,
                    _definition(item),
                    owner,
                    assembly_fastener_sources or None,
                )
                created.append(obj)
            expected_native = (
                _component_native_type(doc, item)
                if output_type == "component_link"
                else _native_type(output_type, prepared["pack"].domain)
            )
            if prepared["pack"].domain == "fem":
                expected_native = str(_fem_data(item).get("native_type") or "")
            if prepared["pack"].domain == "draft":
                compatible = _draft_object_compatible(obj, item)
            elif output_type == "component_link":
                compatible = str(getattr(obj, "TypeId", "")) == expected_native
            elif output_type == "joint":
                compatible = str(getattr(obj, "TypeId", "")) == "App::FeaturePython"
            else:
                compatible = str(getattr(obj, "TypeId", "")) == expected_native
            if not compatible:
                raise RuntimeError(
                    f"Stable output {output_name!r} cannot change from native type "
                    f"{getattr(obj, 'TypeId', '')!r} to {expected_native!r}."
                )
            outputs[output_name] = obj
            return obj

        assembly_item = next(
            (item for item in validated["outputs"] if item["type"] == "assembly"),
            None,
        )
        assembly = (
            ensure_output_object(assembly_item, None)
            if assembly_item is not None
            else None
        )
        if prepared["pack"].domain == "assembly" and assembly_item is not None:
            assembly_output = str(assembly_item["name"])
            assembly_dependency_anchor = _find_assembly_dependency_anchor(
                doc,
                str(prepared["program_id"]),
                assembly_output,
            )
            if assembly_dependency_anchor is None:
                assembly_dependency_anchor = _create_assembly_dependency_anchor(
                    doc,
                    str(prepared["program_id"]),
                    assembly_output,
                )
                created.append(assembly_dependency_anchor)
            assembly_previous_adoptions = _assembly_adopted_occurrences(
                assembly_dependency_anchor
            )
        configure_order = list(validated["outputs"])
        if prepared["pack"].domain == "assembly":
            priority = {
                "assembly": 0,
                "component_link": 1,
                "joint": 2,
                "motion": 3,
                "simulation": 4,
                "mechanism_verification": 5,
                "exploded_view": 6,
                "bom": 7,
                "solver_diagnostics": 8,
            }
            configure_order.sort(key=lambda item: priority.get(str(item["type"]), 8))
        elif prepared["pack"].domain == "draft":
            configure_order = _draft_configure_order(configure_order)
        elif prepared["pack"].domain == "inspection":
            priority = {
                "inspection_feature": 0,
                "measurement": 1,
                "inspection_group": 2,
                "report": 3,
            }
            configure_order.sort(
                key=lambda item: priority.get(str(item["type"]), 4)
            )
        elif prepared["pack"].domain == "robot":
            priority = {"robot": 0, "trajectory": 1, "dressup": 2, "simulation": 3}
            configure_order.sort(
                key=lambda item: priority.get(str(item["type"]), 4)
            )
        elif prepared["pack"].domain == "fem":
            priority = {
                "solver": 0,
                "material": 1,
                "constraint": 2,
                "load_case": 3,
                "mesh": 4,
                "analysis": 5,
                "result": 6,
            }
            configure_order.sort(
                key=lambda item: priority.get(str(item["type"]), 7)
            )
        for item in configure_order:
            output_name = str(item["name"])
            output_type = str(item["type"])
            adopted_occurrence = False
            occurrence_reconciliation: dict[str, Any] | None = None
            occurrence_reconciliation_staged = False
            if prepared["pack"].domain == "assembly":
                if (
                    output_type == "component_link"
                    and str(_definition(item).get("operation") or "") == "fastener"
                ):
                    existing_occurrence = existing.get(output_name)
                    if existing_occurrence is not None:
                        occurrence_reconciliation = (
                            _capture_timeline_resource_reconciliation(
                                doc,
                                existing_occurrence,
                                key_for_resource=_assembly_timeline_resource_key,
                                context=(
                                    f"Assembly occurrence {output_name!r} "
                                    "resource graph"
                                ),
                            )
                        )
                        _stage_timeline_resource_reconciliation(
                            doc,
                            existing_occurrence,
                            occurrence_reconciliation,
                            context=(
                                f"Assembly occurrence {output_name!r} "
                                "resource graph"
                            ),
                        )
                        occurrence_reconciliation_staged = True
                    (
                        prepared_fastener_sources,
                        fastener_sources_created,
                        replaced_fastener_sources,
                    ) = _prepare_assembly_fastener_sources(doc, prepared, [item])
                    assembly_fastener_sources.update(prepared_fastener_sources)
                    created.extend(fastener_sources_created)
                    assembly_replaced_fastener_source_identities.extend(
                        _deletion_object_identity(
                            source,
                            context=(
                                f"Assembly occurrence {output_name!r} replaced "
                                "fastener resource"
                            ),
                        )
                        for source in replaced_fastener_sources
                    )
                obj = ensure_output_object(item, assembly)
                adopted_occurrence = assembly_adoptions.get(output_name) is obj
            else:
                # Create and configure each output in dependency order. Some
                # native containers join History only when their members are
                # installed, so pre-creating later dependants can put them
                # before their owner even when the Python object list looks
                # correctly sorted.
                obj = ensure_output_object(item, assembly)
            inspection_feature = (
                prepared["pack"].domain == "inspection"
                and str(getattr(obj, "TypeId", "")) == "Inspection::Feature"
            )
            if inspection_feature:
                _unfreeze_inspection_feature(obj)
            robot_dressup = (
                prepared["pack"].domain == "robot"
                and str(getattr(obj, "TypeId", ""))
                == "Robot::TrajectoryDressUpObject"
            )
            if robot_dressup:
                _unfreeze_robot_dressup(obj)
            assembly_bom = (
                prepared["pack"].domain == "assembly"
                and str(getattr(obj, "TypeId", "")) == "Assembly::BomObject"
            )
            if assembly_bom:
                _unfreeze_object(obj, "Assembly BOM")

            assembly_reconciliation: dict[str, Any] | None = None
            assembly_operation_is_new = False
            if prepared["pack"].domain == "assembly":
                assembly_operation_is_new = _is_current_transaction_timeline_object(
                    doc,
                    obj,
                )
                if (
                    not assembly_operation_is_new
                    and output_type != "component_link"
                    and (
                        output_type == "assembly"
                        or output_type in _ASSEMBLY_RESOURCE_GRAPH_OUTPUT_TYPES
                    )
                ):
                    assembly_reconciliation = (
                        _capture_timeline_resource_reconciliation(
                            doc,
                            obj,
                            context=(
                                f"Assembly output {output_name!r} resource graph"
                            ),
                        )
                    )
                    _stage_timeline_resource_reconciliation(
                        doc,
                        obj,
                        assembly_reconciliation,
                        context=(
                            f"Assembly output {output_name!r} resource graph"
                        ),
                    )

            if adopted_occurrence:
                _configure_adopted_assembly_component(obj, item)
                configured_resources = []
            else:
                obj.Label = _label(item, output_name)
                configured_resources = _configure_object(
                    doc,
                    obj,
                    item,
                    outputs,
                    prepared,
                    robot_trajectory_swaps,
                    assembly_fastener_sources,
                )
                _set_metadata(
                    obj,
                    prepared,
                    output_name,
                    str(item["type"]),
                    _definition(item),
                )
            if inspection_feature:
                _freeze_inspection_feature(obj)
            if robot_dressup:
                _freeze_robot_dressup(obj)
            if assembly_bom:
                _freeze_object(obj, "Assembly BOM")

            if prepared["pack"].domain != "assembly":
                _mark_timeline_operation(
                    obj,
                    context=f"{prepared['pack'].title} output {output_name!r}",
                )
                if _is_current_transaction_timeline_object(doc, obj):
                    pending_output_publications[id(obj)] = (
                        obj,
                        f"{prepared['pack'].title} output {output_name!r}",
                    )
                continue

            if output_type == "assembly":
                if assembly_dependency_anchor is None:
                    raise RuntimeError(
                        "Assembly output has no dependency resource anchor."
                    )
                _mark_timeline_operation(
                    obj,
                    context=f"Assembly output {output_name!r}",
                )
                _mark_timeline_resource(
                    assembly_dependency_anchor,
                    obj,
                    context="Assembly dependency resource",
                )
                if assembly_operation_is_new:
                    _publish_new_timeline_resource_block(
                        doc,
                        obj,
                        [assembly_dependency_anchor],
                        context="Assembly dependency resource graph",
                    )
                else:
                    if assembly_reconciliation is None:
                        raise RuntimeError(
                            "Assembly output has no staged dependency resource "
                            "reconciliation."
                        )
                    released = _finalize_timeline_resource_reconciliation(
                        doc,
                        obj,
                        assembly_reconciliation,
                        [assembly_dependency_anchor],
                        context="Assembly dependency resource graph",
                    )
                    removed.extend(
                        _remove_reconciled_timeline_resources(
                            doc,
                            released,
                            context="Assembly dependency resource graph",
                        )
                    )
                continue

            if output_type == "component_link":
                import UtilsAssembly

                if adopted_occurrence:
                    grounding, retired_grounding = _configure_component_grounding(
                        doc,
                        obj,
                        item,
                        outputs,
                        prepared,
                    )
                    removed.extend(retired_grounding)
                    if grounding is not None:
                        _mark_timeline_operation(
                            grounding,
                            context=(
                                f"Assembly grounding operation for {output_name!r}"
                            ),
                        )
                        if _is_current_transaction_timeline_object(doc, grounding):
                            created.append(grounding)
                            _publish_new_timeline_resource_block(
                                doc,
                                grounding,
                                [],
                                context=(
                                    f"Assembly grounding operation for {output_name!r}"
                                ),
                            )
                    continue

                fastener_source = assembly_fastener_sources.get(output_name)
                if fastener_source is not None and getattr(
                    obj, "LinkedObject", None
                ) is not fastener_source:
                    raise RuntimeError(
                        f"Assembly fastener output {output_name!r} has no exact "
                        "catalog occurrence to own its hidden definition."
                    )
                if assembly_operation_is_new and fastener_source is not None:
                    _mark_timeline_operation(
                        obj,
                        context=f"Assembly fastener occurrence {output_name!r}",
                    )
                    _mark_timeline_resource(
                        fastener_source,
                        obj,
                        context=(
                            f"Assembly fastener output {output_name!r} definition"
                        ),
                    )
                    _publish_new_timeline_resource_block(
                        doc,
                        obj,
                        [fastener_source],
                        context=f"Assembly fastener occurrence {output_name!r}",
                    )
                elif assembly_operation_is_new:
                    UtilsAssembly.finalizeInsertedComponentTimeline(obj)
                elif str(getattr(obj, "TypeId", "") or "") == (
                    "Assembly::AssemblyLink"
                ):
                    UtilsAssembly.synchronizeAssemblyLinkTimelineResources(obj)
                if (
                    _timeline_role(
                        obj,
                        context=f"Assembly occurrence {output_name!r}",
                    )
                    != "operation"
                    or _timeline_owner(
                        obj,
                        context=f"Assembly occurrence {output_name!r}",
                    )
                    is not None
                ):
                    raise RuntimeError(
                        f"Native Assembly occurrence {output_name!r} was not "
                        "published as one independent History operation."
                    )

                if occurrence_reconciliation is None:
                    occurrence_reconciliation = (
                        _capture_timeline_resource_reconciliation(
                            doc,
                            obj,
                            key_for_resource=_assembly_timeline_resource_key,
                            context=(
                                f"Assembly occurrence {output_name!r} resource graph"
                            ),
                        )
                    )
                final_occurrence_resources = [
                    resource
                    for resource in occurrence_reconciliation["resources"]
                    if _assembly_managed_resource_identity(
                        resource,
                        context=(
                            f"Assembly occurrence {output_name!r} native resource"
                        ),
                    )
                    is not None
                ]
                if fastener_source is not None:
                    _mark_timeline_resource(
                        fastener_source,
                        obj,
                        context=(
                            f"Assembly fastener output {output_name!r} definition"
                        ),
                    )
                    final_occurrence_resources.append(fastener_source)

                if not occurrence_reconciliation_staged:
                    _stage_timeline_resource_reconciliation(
                        doc,
                        obj,
                        occurrence_reconciliation,
                        context=(
                            f"Assembly occurrence {output_name!r} resource graph"
                        ),
                    )
                released = _finalize_timeline_resource_reconciliation(
                    doc,
                    obj,
                    occurrence_reconciliation,
                    final_occurrence_resources,
                    key_for_resource=_assembly_timeline_resource_key,
                    context=(
                        f"Assembly occurrence {output_name!r} resource graph"
                    ),
                )
                removed.extend(
                    _remove_reconciled_timeline_resources(
                        doc,
                        released,
                        context=(
                            f"Assembly occurrence {output_name!r} resource graph"
                        ),
                    )
                )

                grounding, retired_grounding = _configure_component_grounding(
                    doc,
                    obj,
                    item,
                    outputs,
                    prepared,
                )
                removed.extend(retired_grounding)
                if grounding is not None:
                    _mark_timeline_operation(
                        grounding,
                        context=(
                            f"Assembly grounding operation for {output_name!r}"
                        ),
                    )
                    if _is_current_transaction_timeline_object(doc, grounding):
                        created.append(grounding)
                        _publish_new_timeline_resource_block(
                            doc,
                            grounding,
                            [],
                            context=(
                                f"Assembly grounding operation for "
                                f"{output_name!r}"
                            ),
                        )
                continue

            _mark_timeline_operation(
                obj,
                context=f"Assembly output {output_name!r}",
            )
            final_resources = list(configured_resources)
            explicit_outputs = list(outputs.values())
            seen_resource_identities: set[tuple[str, int]] = set()
            for resource in final_resources:
                identity = _deletion_object_identity(
                    resource,
                    context=(
                        f"Assembly output {output_name!r} implementation resource"
                    ),
                )
                if (
                    identity in seen_resource_identities
                    or _resolve_timeline_identity(doc, identity) is not resource
                    or resource is obj
                    or any(
                        resource is explicit and explicit is not obj
                        for explicit in explicit_outputs
                    )
                ):
                    raise RuntimeError(
                        f"Assembly output {output_name!r} has a duplicate, "
                        "detached, self-owned, or independently published resource."
                    )
                seen_resource_identities.add(identity)
                _mark_timeline_resource(
                    resource,
                    obj,
                    context=(
                        f"Assembly output {output_name!r} implementation resource"
                    ),
                )
                if _is_current_transaction_timeline_object(doc, resource):
                    created.append(resource)

            if assembly_operation_is_new:
                _publish_new_timeline_resource_block(
                    doc,
                    obj,
                    final_resources,
                    context=f"Assembly output {output_name!r} resource graph",
                )
            else:
                if assembly_reconciliation is None:
                    if final_resources:
                        raise RuntimeError(
                            f"Assembly output {output_name!r} created an "
                            "undeclared implementation resource graph."
                        )
                    # Joints, motions, diagnostics, simulations, and static
                    # checks are standalone History operations. Their native
                    # properties—including intentional suppression changes—
                    # update normally and require no resource reconciliation.
                    continue
                released = _finalize_timeline_resource_reconciliation(
                    doc,
                    obj,
                    assembly_reconciliation,
                    final_resources,
                    context=f"Assembly output {output_name!r} resource graph",
                )
                removed.extend(
                    _remove_reconciled_timeline_resources(
                        doc,
                        released,
                        context=f"Assembly output {output_name!r} resource graph",
                    )
                )
        if prepared["pack"].domain != "assembly":
            # Configure the complete output graph before publishing any new
            # operation.  Native proxies may update a dependency's
            # presentation while links are installed (Draft arrays hide their
            # Base, for example).  Publishing during configuration would turn
            # that still-in-progress state into an accepted History boundary.
            # Consume provisional outputs only after configuration, in their
            # exact document-creation order.
            published_output_ids: set[int] = set()
            for created_object in created:
                pending = pending_output_publications.get(id(created_object))
                if pending is None:
                    continue
                operation, context = pending
                _publish_new_timeline_resource_block(
                    doc,
                    operation,
                    [],
                    context=context,
                )
                published_output_ids.add(id(operation))
            missing_publications = [
                operation
                for object_id, (operation, _context) in (
                    pending_output_publications.items()
                )
                if object_id not in published_output_ids
            ]
            if missing_publications:
                raise RuntimeError(
                    "New VibeScript outputs were not present in exact document "
                    "creation order: "
                    + ", ".join(str(obj.Name) for obj in missing_publications)
                )
        if assembly_dependency_anchor is not None and assembly_item is not None:
            # Publish the anchor with the Assembly root in creation order, but
            # install its source links only after every occurrence has finished
            # synchronizing.  AssemblyLink materialization can touch source
            # groups; linking the invalidation anchor before that point would
            # incorrectly mark the revision stale during its own publication.
            _configure_assembly_dependency_anchor(
                assembly_dependency_anchor,
                prepared,
                assembly_item,
            )
            for output_name, occurrence in assembly_previous_adoptions.items():
                if output_name not in assembly_adoptions:
                    _release_assembly_occurrence(assembly, occurrence)
            _set_assembly_adopted_occurrences(
                assembly_dependency_anchor,
                assembly_adoptions,
            )
        if assembly is not None and any(obj is assembly for obj in created):
            _configure_new_assembly_presentation(
                assembly,
                list(validated["outputs"]),
                outputs,
            )
        unreconciled_fastener_source_identities = [
            identity
            for identity in assembly_replaced_fastener_source_identities
            if _resolve_timeline_identity(doc, identity) is not None
        ]
        if unreconciled_fastener_source_identities:
            raise RuntimeError(
                "Replaced Assembly fastener definitions were not released by "
                "their exact occurrence resource reconciliations: "
                + ", ".join(
                    identity[0]
                    for identity in unreconciled_fastener_source_identities
                )
            )
        downstream_refresh = _refresh_external_consumers(
            downstream_uses,
            revision=str(prepared["revision"]),
        )
        if prepared["pack"].domain == "robot":
            retired_robot_trajectories = _extract_robot_trajectories(retired)
        removed = _remove_timeline_deletion(doc, retired_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as publication_error:
        created_names = [str(getattr(obj, "Name", "") or "") for obj in created]
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        if assembly_bom_rollbacks:
            try:
                _restore_assembly_bom_rollback_states(assembly_bom_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{publication_error} Explicit Assembly BOM rollback failure: "
                    f"{rollback_error}"
                ) from publication_error
        if spreadsheet_rollbacks:
            try:
                _restore_spreadsheet_rollback_states(spreadsheet_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{publication_error} Rollback failure: {rollback_error}"
                ) from publication_error
        if prepared["pack"].domain in {
            "mesh",
            "meshpart",
            "reverse_engineering",
        }:
            rollback_failures: list[str] = []
            if mesh_rollbacks:
                try:
                    _restore_mesh_rollback_states(mesh_rollbacks)
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            if meshpart_shape_rollbacks:
                try:
                    _restore_meshpart_shape_rollback_states(
                        meshpart_shape_rollbacks
                    )
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            if reverse_feature_rollbacks:
                try:
                    _restore_reverse_feature_rollback_states(
                        reverse_feature_rollbacks
                    )
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            try:
                _remove_failed_domain_creations(
                    doc, [name for name in created_names if name]
                )
            except Exception as cleanup_error:
                rollback_failures.append(
                    "failed candidate objects could not be removed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if rollback_failures:
                raise RuntimeError(
                    f"{publication_error} Explicit {prepared['pack'].title} "
                    "rollback failure: "
                    f"{' | '.join(rollback_failures)}"
                ) from publication_error
        if prepared["pack"].domain == "points":
            rollback_failures = []
            if points_rollbacks:
                try:
                    _restore_points_rollback_states(points_rollbacks)
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            try:
                _remove_failed_domain_creations(
                    doc, [name for name in created_names if name]
                )
            except Exception as cleanup_error:
                rollback_failures.append(
                    "failed candidate objects could not be removed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if rollback_failures:
                raise RuntimeError(
                    f"{publication_error} Explicit Points rollback failure: "
                    f"{' | '.join(rollback_failures)}"
                ) from publication_error
        if prepared["pack"].domain == "inspection":
            rollback_failures: list[str] = []
            if inspection_rollbacks:
                try:
                    _restore_inspection_rollback_states(inspection_rollbacks)
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            try:
                _remove_failed_domain_creations(
                    doc, [name for name in created_names if name]
                )
            except Exception as cleanup_error:
                rollback_failures.append(
                    "failed candidate objects could not be removed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if rollback_failures:
                raise RuntimeError(
                    f"{publication_error} Explicit Inspection rollback failure: "
                    f"{' | '.join(rollback_failures)}"
                ) from publication_error
        if prepared["pack"].domain == "robot":
            rollback_failures: list[str] = []
            try:
                _restore_robot_rollback_states(
                    robot_rollbacks,
                    robot_trajectory_swaps + retired_robot_trajectories,
                )
            except Exception as rollback_error:
                rollback_failures.append(str(rollback_error))
            try:
                _remove_failed_domain_creations(
                    doc, [name for name in created_names if name]
                )
            except Exception as cleanup_error:
                rollback_failures.append(
                    "failed candidate objects could not be removed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if rollback_failures:
                raise RuntimeError(
                    f"{publication_error} Explicit Robot rollback failure: "
                    f"{' | '.join(rollback_failures)}"
                ) from publication_error
        if prepared["pack"].domain == "fem":
            rollback_failures: list[str] = []
            if fem_rollbacks:
                try:
                    _restore_fem_rollback_states(doc, fem_rollbacks)
                except Exception as rollback_error:
                    rollback_failures.append(str(rollback_error))
            try:
                _remove_failed_domain_creations(
                    doc, [name for name in created_names if name]
                )
            except Exception as cleanup_error:
                rollback_failures.append(
                    "failed candidate objects could not be removed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            if rollback_failures:
                raise RuntimeError(
                    f"{publication_error} Explicit FEM rollback failure: "
                    f"{' | '.join(rollback_failures)}"
                ) from publication_error
        raise
    live_outputs = {
        name: {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_type": str(getattr(obj, PROP_OUTPUT_TYPE, "") or ""),
            "derived_state": str(
                getattr(obj, reference_contracts.PROP_DERIVED_STATE, "") or ""
            ),
            "stale_reason": str(
                getattr(obj, reference_contracts.PROP_STALE_REASON, "") or ""
            ),
            "source_revision": str(
                getattr(obj, reference_contracts.PROP_SOURCE_REVISION, "") or ""
            ),
        }
        for name, obj in outputs.items()
    }
    for item in validated["outputs"]:
        name = str(item["name"])
        if isinstance(item.get("facts"), dict):
            live_outputs[name]["facts"] = dict(item["facts"])
        if isinstance(item.get("operation_diagnostics"), dict):
            live_outputs[name]["operation_diagnostics"] = dict(
                item["operation_diagnostics"]
            )
        if isinstance(item.get("draft_data"), dict):
            live_outputs[name]["draft_data"] = dict(item["draft_data"])
        if isinstance(item.get("surface_data"), dict):
            live_outputs[name]["surface_data"] = dict(item["surface_data"])
        if isinstance(item.get("mesh_data"), dict):
            live_outputs[name]["mesh_data"] = dict(item["mesh_data"])
        if isinstance(item.get("meshpart_data"), dict):
            live_outputs[name]["meshpart_data"] = dict(item["meshpart_data"])
        if isinstance(item.get("points_data"), dict):
            live_outputs[name]["points_data"] = dict(item["points_data"])
        if isinstance(item.get("reverse_data"), dict):
            live_outputs[name]["reverse_data"] = dict(item["reverse_data"])
        if isinstance(item.get("inspection_data"), dict):
            live_outputs[name]["inspection_data"] = dict(item["inspection_data"])
        if isinstance(item.get("robot_data"), dict):
            live_outputs[name]["robot_data"] = dict(item["robot_data"])
        if isinstance(item.get("fem_data"), dict):
            live_outputs[name]["fem_data"] = _fem_validation_summary(item["fem_data"])
        if prepared["pack"].domain == "assembly":
            evidence = _assembly_model_evidence(item)
            if evidence is not None:
                live_outputs[name]["assembly_data"] = evidence
    published_outputs = []
    for item in validated["outputs"]:
        name = str(item["name"])
        if name not in public_output_names:
            continue
        summary = {"name": name, **live_outputs[name]}
        if isinstance(item.get("diagnostics"), dict):
            summary["diagnostics"] = dict(item["diagnostics"])
        if isinstance(item.get("sketch_validation"), dict):
            summary["sketch_validation"] = dict(item["sketch_validation"])
        if isinstance(item.get("sheet_validation"), dict):
            summary["sheet_validation"] = dict(item["sheet_validation"])
        if isinstance(item.get("draft_data"), dict):
            summary["draft_data"] = dict(item["draft_data"])
        if isinstance(item.get("surface_data"), dict):
            summary["surface_data"] = dict(item["surface_data"])
        if isinstance(item.get("mesh_data"), dict):
            summary["mesh_data"] = dict(item["mesh_data"])
        if isinstance(item.get("meshpart_data"), dict):
            summary["meshpart_data"] = dict(item["meshpart_data"])
        if isinstance(item.get("points_data"), dict):
            summary["points_data"] = dict(item["points_data"])
        if isinstance(item.get("reverse_data"), dict):
            summary["reverse_data"] = dict(item["reverse_data"])
        if isinstance(item.get("inspection_data"), dict):
            summary["inspection_data"] = dict(item["inspection_data"])
        if isinstance(item.get("robot_data"), dict):
            summary["robot_data"] = dict(item["robot_data"])
        if isinstance(item.get("fem_data"), dict):
            summary["fem_data"] = _fem_validation_summary(item["fem_data"])
        published_outputs.append(summary)
    return {
        "ok": True,
        "outputs": published_outputs,
        "live_outputs": {
            name: live_outputs[name]
            for name in public_output_names
        },
        "created_objects": [str(obj.Name) for obj in created],
        "retired_objects": removed,
        "downstream_references": {
            "safe_whole_object_uses": scripted_publication.json_reference_uses(
                downstream_uses
            ),
            **downstream_refresh,
        },
        "recompute_deferred": True,
        "stdout": str(validated.get("stdout") or ""),
        "budget": dict(validated.get("budget") or {}),
    }


def _delete_material_program(
    doc: Any,
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    objects = _program_objects(doc, str(prepared["program_id"]), "material")
    timeline_deletion = _prepare_timeline_deletion(doc, objects)
    deletion_targets = list(timeline_deletion["delete_objects"])
    external = _external_uses(doc, deletion_targets, deletion_targets)
    if external:
        raise _reference_error(
            "Cannot delete this Material VibeScript program while human-created or "
            "foreign document objects reference its stable carriers",
            external,
        )
    states: list[tuple[Any, dict[str, Any], Any]] = []
    for obj in objects:
        ownership, target = _preflight_material_carrier(obj)
        states.append((obj, ownership, target))
    rollback_targets = {id(target): target for _obj, _ownership, target in states}
    rollback_states = [
        _material_target_snapshot(target) for target in rollback_targets.values()
    ]
    deleted = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""),
            "target": str(getattr(target, "Name", "") or ""),
            "channel": str(ownership["channel"]),
        }
        for obj, ownership, target in states
    ]
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete Material VibeScript program")
            transaction_open = True
        for channel in ("physical", "appearance"):
            for obj, ownership, target in states:
                if ownership["channel"] == channel:
                    _restore_material_baseline(obj, ownership, target)
        _remove_timeline_deletion(doc, timeline_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as deletion_error:
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        try:
            _restore_material_target_snapshots(rollback_states)
        except Exception as rollback_error:
            raise RuntimeError(
                f"{deletion_error} Explicit Material deletion rollback failure: "
                f"{rollback_error}"
            ) from deletion_error
        raise
    return {
        "ok": True,
        "deleted_objects": deleted,
        "restored_target_count": len(rollback_targets),
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
    }


def _delete_cam_program(
    doc: Any,
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    objects = _program_objects(doc, str(prepared["program_id"]), "cam")
    timeline_deletion = _prepare_timeline_deletion(doc, objects)
    deletion_targets = list(timeline_deletion["delete_objects"])
    external = _external_uses(doc, deletion_targets, deletion_targets)
    if external:
        raise _reference_error(
            "Cannot delete this CAM VibeScript program while human-created or foreign "
            "objects reference its stable native graph",
            external,
        )
    rollback_states = _cam_rollback_states(deletion_targets)
    deleted = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(
                getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""
            ),
        }
        for obj in objects
    ]
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete CAM VibeScript program")
            transaction_open = True
        _remove_timeline_deletion(doc, timeline_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as deletion_error:
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        try:
            _restore_cam_rollback_states(doc, rollback_states)
        except Exception as rollback_error:
            raise RuntimeError(
                f"{deletion_error} Explicit CAM deletion rollback failure: "
                f"{rollback_error}"
            ) from deletion_error
        raise
    return {
        "ok": True,
        "deleted_objects": deleted,
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
        "artifact_io_on_document_thread": False,
    }


def _delete_techdraw_program(
    doc: Any,
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    objects = _program_objects(doc, str(prepared["program_id"]), "techdraw")
    timeline_deletion = _prepare_timeline_deletion(doc, objects)
    deletion_targets = list(timeline_deletion["delete_objects"])
    external = _external_uses(doc, deletion_targets, deletion_targets)
    if external:
        raise _reference_error(
            "Cannot delete this TechDraw VibeScript program while human-created or "
            "foreign objects reference its stable native drawing graph",
            external,
        )
    rollback_states = _techdraw_rollback_states(deletion_targets)
    deleted = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(
                getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""
            ),
        }
        for obj in objects
    ]
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete TechDraw VibeScript program")
            transaction_open = True
        _remove_techdraw_timeline_deletion(doc, timeline_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as deletion_error:
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        try:
            _restore_techdraw_rollback_states(doc, rollback_states)
        except Exception as rollback_error:
            raise RuntimeError(
                f"{deletion_error} Explicit TechDraw deletion rollback failure: "
                f"{rollback_error}"
            ) from deletion_error
        raise
    return {
        "ok": True,
        "deleted_objects": deleted,
        "recompute_deferred": True,
        "catalog_access_on_document_thread": False,
        "artifact_io_on_document_thread": False,
        "projection_generation_on_document_thread": False,
    }


def _delete_partdesign_design_program(
    doc: Any,
    prepared: Mapping[str, Any],
    root: Any | None,
    operation: Any,
) -> dict[str, Any]:
    """Delete one global VibeScript operation and every owned Body output."""

    import PartDesign

    program_id = str(prepared["program_id"])
    publications = _partdesign_publications(doc, root, program_id)
    body_ids = {
        str(value)
        for value in list(getattr(operation, "OutputBodyIds", []) or [])
    }
    bodies = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body"
        and str(getattr(obj, "VibeCADBodyId", "") or "") in body_ids
    ]
    if len(bodies) != len(body_ids):
        raise RuntimeError(
            "The VibeScript Design operation lost one of its persistent "
            "Body outputs."
        )

    source_evidence = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if PROP_PARTDESIGN_HISTORY_KEY in _properties(obj)
        and str(
            getattr(obj, scripted_publication.PROP_ENGINE, "") or ""
        )
        == "vibescript:partdesign"
        and str(
            getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
        )
        == program_id
    ]

    contained: list[Any] = []
    contained_ids: set[int] = set()

    def collect_contained(obj: Any) -> None:
        if id(obj) in contained_ids:
            return
        contained_ids.add(id(obj))
        contained.append(obj)
        group = getattr(obj, "Group", None)
        if isinstance(group, (list, tuple)):
            for child in group:
                if child is not None:
                    collect_contained(child)

    for body in bodies:
        collect_contained(body)
    body_origin_objects: list[Any] = []
    for body in bodies:
        origin = getattr(body, "Origin", None)
        if origin is None:
            continue
        body_origin_objects.append(origin)
        body_origin_objects.extend(
            feature
            for feature in list(getattr(origin, "OriginFeatures", []) or [])
            if feature is not None
        )
    states = [
        obj
        for obj in list(getattr(doc, "Objects", []) or [])
        if getattr(obj, "Operation", None) is operation
        and str(getattr(obj, "TypeId", "") or "")
        == "PartDesign::DesignBodyState"
    ]
    private_objects = [
        *([root] if root is not None else []),
        *list(getattr(root, "Group", []) or []),
        *list(getattr(root, "OutListRecursive", []) or []),
        *publications.values(),
        operation,
        *states,
        *contained,
        *body_origin_objects,
        *source_evidence,
    ]
    internal: list[Any] = []
    internal_ids: set[int] = set()
    for obj in private_objects:
        if id(obj) not in internal_ids:
            internal.append(obj)
            internal_ids.add(id(obj))
    external = _external_uses(doc, internal, internal)
    if external:
        raise _reference_error(
            "Cannot delete this Part Design VibeScript program while "
            "downstream objects reference its publications or Bodies",
            external,
        )

    deleted = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(name),
        }
        for name, obj in publications.items()
    ]
    deleted_names = {item["object_name"] for item in deleted}
    deleted.extend(
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(getattr(obj, scripted_publication.PROP_OUTPUT_KEY, "") or ""),
        }
        for obj in contained
        if str(getattr(obj, "Name", "") or "")
        not in deleted_names
    )
    contained_names = [
        str(getattr(obj, "Name", "") or "")
        for obj in contained
        if str(getattr(obj, "Name", "") or "")
    ]
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete Part Design VibeScript program")
            transaction_open = True
        # Source evidence can reference output Body origin planes and earlier
        # source features. Remove that exact, program-tagged graph before the
        # operation removes its Bodies, including when a damaged document has
        # already lost the source App::Part root.
        _remove_objects_dependency_order(doc, source_evidence)
        for published in list(publications.values()):
            _delete_partdesign_publication(
                doc,
                root,
                published,
            )
        PartDesign.removeDesignOperation(operation)
        # Native operation deletion owns the Bodies, but older Body layouts can
        # leave an adopted result feature detached after their container is
        # removed. Delete the exact captured containment closure and prove no
        # source-owned native descendant survived before artifacts are purged.
        for child_name in reversed(contained_names):
            if doc.getObject(child_name) is not None:
                doc.removeObject(child_name)
        if root is not None:
            for child in reversed(list(getattr(root, "Group", []) or [])):
                child_name = str(getattr(child, "Name", "") or "")
                if child_name and doc.getObject(child_name) is not None:
                    doc.removeObject(child_name)
            root_name = str(root.Name)
            if doc.getObject(root_name) is not None:
                doc.removeObject(root_name)
        survivors = [
            name for name in contained_names if doc.getObject(name) is not None
        ]
        survivors.extend(
            str(getattr(obj, "Name", "") or "")
            for obj in list(getattr(doc, "Objects", []) or [])
            if (
                str(getattr(obj, "ProgramId", "") or "") == program_id
                or str(
                    getattr(obj, scripted_publication.PROP_MODEL_ID, "") or ""
                )
                == program_id
                or str(getattr(obj, contracts.PROP_PROGRAM_ID, "") or "")
                == program_id
            )
        )
        survivors = sorted({name for name in survivors if name})
        if survivors:
            raise RuntimeError(
                "Part Design source deletion retained owned native objects: "
                + ", ".join(survivors)
            )
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception:
        if transaction_open and hasattr(doc, "abortTransaction"):
            doc.abortTransaction()
        raise
    return {
        "ok": True,
        "deleted_objects": deleted,
        "recompute_deferred": True,
    }


def _delete_partdesign_program(
    doc: Any,
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    program_id = str(prepared["program_id"])
    root = _partdesign_program_root(doc, program_id)
    operation = _partdesign_design_program_operation(doc, program_id)
    if operation is not None:
        return _delete_partdesign_design_program(
            doc,
            prepared,
            root,
            operation,
        )
    if root is None:
        publications = _partdesign_publications(doc, None, program_id)
        if not publications:
            return {"ok": True, "deleted_objects": [], "recompute_deferred": True}
        deleted = [
            {
                "object_name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
                "output_name": str(name),
            }
            for name, obj in publications.items()
        ]
        transaction_open = False
        try:
            if hasattr(doc, "openTransaction"):
                doc.openTransaction("Delete orphaned Part Design VibeScript program")
                transaction_open = True
            for published in list(publications.values()):
                _delete_partdesign_publication(doc, None, published)
            survivors = _partdesign_publications(doc, None, program_id)
            if survivors:
                raise RuntimeError(
                    "Part Design source deletion retained stable publications: "
                    + ", ".join(sorted(str(obj.Name) for obj in survivors.values()))
                )
            if hasattr(doc, "commitTransaction") and transaction_open:
                doc.commitTransaction()
                transaction_open = False
        except Exception:
            if transaction_open and hasattr(doc, "abortTransaction"):
                doc.abortTransaction()
            raise
        return {
            "ok": True,
            "deleted_objects": deleted,
            "recompute_deferred": True,
        }
    publications = _partdesign_publications(doc, root, program_id)
    internal = [
        root,
        *list(getattr(root, "OutListRecursive", []) or []),
        *publications.values(),
    ]
    timeline_deletion = _prepare_timeline_deletion(doc, internal)
    deletion_targets = list(timeline_deletion["delete_objects"])
    external = _external_uses(doc, deletion_targets, deletion_targets)
    if external:
        raise _reference_error(
            "Cannot delete this Part Design VibeScript program while downstream "
            "objects reference its stable publications",
            external,
        )
    deleted: list[dict[str, Any]] = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(name),
        }
        for name, obj in publications.items()
    ]
    transaction_open = False
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Delete Part Design VibeScript program")
            transaction_open = True
        _remove_timeline_resources(doc, timeline_deletion)
        for published in list(publications.values()):
            published_name = str(getattr(published, "Name", "") or "")
            if not published_name or doc.getObject(published_name) is None:
                continue
            target_name = str(
                getattr(
                    published,
                    scripted_publication.PROP_IMPLEMENTATION,
                    "",
                )
                or ""
            )
            if target_name and doc.getObject(target_name) is not None:
                scripted_publication.delete_publication(doc, root, published)
            else:
                doc.removeObject(published_name)
        if doc.getObject(str(root.Name)) is not None:
            scripted_publication.delete_implementation(doc, root)
        for child in reversed(list(getattr(root, "Group", []) or [])):
            child_name = str(getattr(child, "Name", "") or "")
            if child_name and doc.getObject(child_name) is not None:
                doc.removeObject(child_name)
        root_name = str(root.Name)
        if doc.getObject(root_name) is not None:
            doc.removeObject(root_name)
        remaining_roots = [
            candidate
            for identity in (
                _deletion_object_identity(
                    candidate,
                    context="A native Part Design deletion target",
                )
                for candidate in list(timeline_deletion["root_objects"])
            )
            if (
                candidate := _resolve_timeline_identity(doc, identity)
            )
            is not None
        ]
        if remaining_roots:
            _remove_objects_dependency_order(doc, remaining_roots)
        _finish_timeline_deletion(doc, timeline_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception:
        if transaction_open and hasattr(doc, "abortTransaction"):
            doc.abortTransaction()
        raise
    return {"ok": True, "deleted_objects": deleted, "recompute_deferred": True}


def delete_live_program(service: Any, prepared: Mapping[str, Any]) -> dict[str, Any]:
    if not bool(prepared.get("history_lifecycle")):
        _surface_still_matches(service, prepared)
    doc = service._active_document()
    if doc is None or str(getattr(doc, "Name", "") or "") != prepared["document_name"]:
        raise RuntimeError("The active document changed before deletion.")
    if str(getattr(doc, "Uid", "") or "") != str(prepared.get("document_uid") or ""):
        raise RuntimeError("The active document identity changed before deletion.")
    if prepared["pack"].domain == "partdesign":
        if str(service.provider_document_revision()) != str(
            prepared.get("document_revision") or ""
        ):
            raise RuntimeError(
                "The document changed before Part Design deletion; inspect and retry."
            )
        return _delete_partdesign_program(doc, prepared)
    if prepared["pack"].domain == "material":
        if str(service.provider_document_revision()) != str(
            prepared.get("document_revision") or ""
        ):
            raise RuntimeError(
                "The document changed before Material deletion; inspect and retry on live state."
            )
        return _delete_material_program(doc, prepared)
    if prepared["pack"].domain == "cam":
        if str(service.provider_document_revision()) != str(
            prepared.get("document_revision") or ""
        ):
            raise RuntimeError(
                "The document changed before CAM deletion; inspect and retry on live state."
            )
        return _delete_cam_program(doc, prepared)
    if prepared["pack"].domain == "techdraw":
        if str(service.provider_document_revision()) != str(
            prepared.get("document_revision") or ""
        ):
            raise RuntimeError(
                "The document changed before TechDraw deletion; inspect and retry "
                "on live state."
            )
        return _delete_techdraw_program(doc, prepared)
    objects = _program_objects(
        doc, str(prepared["program_id"]), prepared["pack"].domain
    )
    assembly_borrowed_occurrences: list[tuple[Any, Any]] = []
    if prepared["pack"].domain == "assembly":
        assembly_roots = [
            obj
            for obj in objects
            if str(getattr(obj, "TypeId", "") or "")
            == "Assembly::AssemblyObject"
        ]
        if len(assembly_roots) > 1:
            raise RuntimeError(
                "One Assembly VibeScript program cannot own multiple native Assembly roots."
            )
        if assembly_roots:
            for anchor in objects:
                for occurrence in _assembly_adopted_occurrences(anchor).values():
                    assembly_borrowed_occurrences.append(
                        (assembly_roots[0], occurrence)
                    )
    timeline_deletion = _prepare_timeline_deletion(doc, objects)
    deletion_targets = list(timeline_deletion["delete_objects"])
    mesh_rollbacks = (
        _mesh_rollback_states(objects)
        if prepared["pack"].domain in {"mesh", "meshpart", "reverse_engineering"}
        else []
    )
    meshpart_shape_rollbacks = (
        _meshpart_shape_rollback_states(objects)
        if prepared["pack"].domain in {"mesh", "meshpart", "reverse_engineering"}
        else []
    )
    points_rollbacks = (
        _points_rollback_states(objects)
        if prepared["pack"].domain == "points"
        else []
    )
    reverse_feature_rollbacks = (
        _reverse_feature_rollback_states(objects)
        if prepared["pack"].domain == "reverse_engineering"
        else []
    )
    inspection_rollbacks = (
        _inspection_rollback_states(objects)
        if prepared["pack"].domain == "inspection"
        else []
    )
    robot_rollbacks = (
        _robot_rollback_states(objects)
        if prepared["pack"].domain == "robot"
        else []
    )
    fem_rollbacks = (
        _fem_rollback_states(objects)
        if prepared["pack"].domain == "fem"
        else []
    )
    internal = list(deletion_targets)
    for obj in objects:
        if str(getattr(obj, "TypeId", "")) == "Assembly::AssemblyObject":
            joint_group = _assembly_joint_group(obj)
            if joint_group is not None:
                internal.append(joint_group)
            simulation_group = _assembly_simulation_group(obj)
            if simulation_group is not None:
                internal.append(simulation_group)
            view_group = _assembly_view_group(obj)
            if view_group is not None:
                internal.append(view_group)
            bom_group = _assembly_bom_group(obj)
            if bom_group is not None:
                internal.append(bom_group)
            verification_group = _assembly_verification_group(obj)
            if verification_group is not None:
                internal.append(verification_group)
    external = _external_uses(doc, deletion_targets, internal)
    if external:
        raise _reference_error(
            "Cannot delete this VibeScript program while human-created or foreign "
            "document objects reference its stable outputs",
            external,
        )
    deleted = [
        {
            "object_name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
            "output_name": str(getattr(obj, contracts.PROP_PROGRAM_OUTPUT, "") or ""),
        }
        for obj in objects
    ]
    transaction_open = False
    robot_trajectories: list[dict[str, Any]] = []
    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction(f"Delete {prepared['pack'].title} VibeScript program")
            transaction_open = True
        if prepared["pack"].domain == "robot":
            robot_trajectories = _extract_robot_trajectories(objects)
        for assembly, occurrence in assembly_borrowed_occurrences:
            _release_assembly_occurrence(assembly, occurrence)
        _remove_timeline_deletion(doc, timeline_deletion)
        if hasattr(doc, "commitTransaction") and transaction_open:
            doc.commitTransaction()
            transaction_open = False
    except Exception as deletion_error:
        if transaction_open and hasattr(doc, "abortTransaction"):
            try:
                doc.abortTransaction()
            except Exception:
                pass
        if mesh_rollbacks:
            try:
                _restore_mesh_rollback_states(mesh_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit Mesh deletion rollback failure: "
                    f"{rollback_error}"
                ) from deletion_error
        if meshpart_shape_rollbacks:
            try:
                _restore_meshpart_shape_rollback_states(meshpart_shape_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit MeshPart deletion rollback failure: "
                    f"{rollback_error}"
                ) from deletion_error
        if points_rollbacks:
            try:
                _restore_points_rollback_states(points_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit Points deletion rollback failure: "
                    f"{rollback_error}"
                ) from deletion_error
        if reverse_feature_rollbacks:
            try:
                _restore_reverse_feature_rollback_states(
                    reverse_feature_rollbacks
                )
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit Reverse Engineering deletion "
                    f"rollback failure: {rollback_error}"
                ) from deletion_error
        if inspection_rollbacks:
            try:
                _restore_inspection_rollback_states(inspection_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit Inspection deletion rollback "
                    f"failure: {rollback_error}"
                ) from deletion_error
        if robot_rollbacks:
            try:
                _restore_robot_rollback_states(
                    robot_rollbacks,
                    robot_trajectories,
                )
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit Robot deletion rollback failure: "
                    f"{rollback_error}"
                ) from deletion_error
        if fem_rollbacks:
            try:
                _restore_fem_rollback_states(doc, fem_rollbacks)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"{deletion_error} Explicit FEM deletion rollback failure: "
                    f"{rollback_error}"
                ) from deletion_error
        raise
    return {"ok": True, "deleted_objects": deleted, "recompute_deferred": True}
