# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic discovery of reusable Assembly component definitions."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from VibeCADDocumentReferences import (
    DocumentReferenceError,
    reference_for_target,
)
import VibeCADReferenceContracts as reference_contracts

MAX_COMPONENT_FILES = 512
MAX_COMPONENT_DIRECTORIES = 4096
MAX_DOCUMENT_XML_BYTES = 16 * 1024 * 1024
MAX_COMPONENT_FILE_BYTES = 512 * 1024 * 1024
MAX_INJECTED_COMPONENTS = 64
MAX_COMPONENT_SEARCH_RESULTS = 200
MAX_COMPONENT_SEARCH_RESPONSE_BYTES = 36 * 1024
MAX_COMPONENT_REFERENCE_LABEL_CHARACTERS = 512
_SAVED_COMPONENT_TYPES = frozenset(
    {
        "App::Link",
        "App::Part",
        "Assembly::AssemblyObject",
        "Part::Feature",
        "Part::FeaturePython",
        "PartDesign::Body",
    }
)
_SEARCH_PROPERTY_NAMES = frozenset(
    {
        "Description",
        "Label",
        "PartNumber",
        "StockCode",
    }
)
_VIBESCRIPT_PROPERTY_NAMES = frozenset(
    {
        "VibeCADPublishedInterfaces",
        "VibeCADScriptedRole",
        "VibeCADVibeScriptDomain",
        "VibeCADVibeScriptOutputName",
        "VibeCADVibeScriptOutputType",
        "VibeCADVibeScriptProgramId",
        "VibeCADVibeScriptRevision",
    }
)
_NATIVE_INTERFACE_PROPERTY_NAMES = frozenset(
    {
        reference_contracts.PROP_NATIVE_INTERFACE,
        reference_contracts.PROP_NATIVE_INTERFACE_NAME,
        reference_contracts.PROP_NATIVE_INTERFACE_KIND,
        reference_contracts.PROP_NATIVE_INTERFACE_ALLOWED_JOINTS,
        reference_contracts.PROP_NATIVE_INTERFACE_COMPATIBILITY,
        "Placement",
    }
)
_SAVED_NATIVE_LCS_TYPES = frozenset(
    {
        "App::LocalCoordinateSystem",
        "PartDesign::CoordinateSystem",
        "Part::LocalCoordinateSystem",
    }
)


class ComponentCatalogError(ValueError):
    """The project component catalog could not be searched safely."""


def _assembly_component_contract(
    type_id: str,
    *,
    solid_count: int | None = None,
    output_type: str = "",
) -> dict[str, Any]:
    """Describe the exact mechanism boundary represented by one reference."""

    subassembly = type_id == "Assembly::AssemblyObject"
    contract: dict[str, Any] = {
        "schema": "vibecad-assembly-component-contract-v1",
        "default_behavior": "rigid_occurrence",
        "movable_unit_count": 1,
        "child_solids_independently_movable": False,
        "flexible_occurrence_supported": subassembly,
        "rule": (
            "One component reference creates one mechanism occurrence. Its solids "
            "cannot move independently."
        ),
    }
    if solid_count is not None:
        contract["solid_count"] = int(solid_count)
    if output_type:
        contract["vibescript_output_type"] = str(output_type)
    if subassembly:
        contract.update(
            {
                "flexible_behavior": (
                    "api.component(..., flexible=True) exposes the authenticated "
                    "native subassembly's internal occurrences and joints."
                ),
                "internal_occurrences_independently_movable": True,
            }
        )
    elif output_type == "compound":
        contract["topology_grouping"] = "compound"
        contract["authoring_rule"] = (
            "A compound publication is still one rigid occurrence. Use separate "
            "stable outputs for parts that require separate joints or motion."
        )
    if not subassembly and solid_count is not None and solid_count > 1:
        contract["authoring_correction"] = (
            "This is a rigid multi-solid definition. Publish each independently "
            "moving manufactured part as a separate stable Part Design output. "
            "For identical repeated parts, publish one single-solid master and "
            "create occurrences with Assembly api.instances."
        )
    return contract


def _object_is_derived(obj: Any, type_name: str) -> bool:
    try:
        return bool(obj.isDerivedFrom(type_name))
    except Exception:
        return False


def _inside_partdesign_body(obj: Any) -> bool:
    if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
        return False
    return any(
        str(getattr(parent, "TypeId", "") or "") == "PartDesign::Body"
        for parent in list(getattr(obj, "InList", []) or [])
    )


def _native_lcs_candidates(owner_document: Any, component: Any) -> list[dict[str, Any]]:
    """Return exact direct LCS identities without assigning semantic meaning."""

    result: list[dict[str, Any]] = []
    for candidate in list(getattr(component, "Group", []) or []):
        if not reference_contracts.is_native_coordinate_system(candidate):
            continue
        try:
            reference = reference_for_target(owner_document, candidate)
        except DocumentReferenceError:
            continue
        item = {
            "object_name": str(getattr(candidate, "Name", "") or ""),
            "label": str(getattr(candidate, "Label", "") or ""),
            "reference": reference,
        }
        properties = set(getattr(candidate, "PropertiesList", []) or [])
        if (
            reference_contracts.PROP_NATIVE_INTERFACE in properties
            and bool(
                getattr(
                    candidate,
                    reference_contracts.PROP_NATIVE_INTERFACE,
                    False,
                )
            )
        ):
            item["published_interface"] = str(
                getattr(
                    candidate,
                    reference_contracts.PROP_NATIVE_INTERFACE_NAME,
                    "",
                )
                or ""
            )
        result.append(item)
        if len(result) >= 64:
            break
    return result


def _live_component_candidate(
    owner_document: Any,
    source_document: Any,
    obj: Any,
) -> dict[str, Any] | None:
    type_id = str(getattr(obj, "TypeId", "") or "")
    scripted_role = str(getattr(obj, "VibeCADScriptedRole", "") or "")
    reusable_occurrence = (
        type_id == "App::Link"
        and str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
        == "component_link"
        and str(getattr(obj, "VibeCADVibeScriptDomain", "") or "")
        in {"partdesign", "robot"}
    )
    if (
        scripted_role in {"implementation", "model", "publication_target"}
        and not reusable_occurrence
    ):
        return None
    scripted_publication = scripted_role == "publication"
    if type_id == "Assembly::AssemblyLink" or (
        type_id == "App::Link" and not scripted_publication and not reusable_occurrence
    ):
        return None
    if _inside_partdesign_body(obj):
        return None
    supported = (
        reusable_occurrence
        or scripted_publication
        or type_id == "PartDesign::Body"
        or _object_is_derived(obj, "Assembly::AssemblyObject")
        or (
            _object_is_derived(obj, "App::Part")
            and not _object_is_derived(obj, "Part::Feature")
        )
        or _object_is_derived(obj, "Part::Feature")
    )
    if not supported:
        return None
    try:
        shape = getattr(obj, "Shape", None)
        solids = list(getattr(shape, "Solids", []) or [])
        if (
            shape is None
            or bool(shape.isNull())
            or not bool(shape.isValid())
            or len(solids) < 1
        ):
            return None
    except Exception:
        return None
    try:
        reference = reference_for_target(owner_document, obj)
    except DocumentReferenceError:
        return None
    same_document = source_document is owner_document
    portable = same_document or "document_path" in reference
    candidate = {
        "document_label": str(
            getattr(source_document, "Label", "")
            or getattr(source_document, "Name", "")
            or ""
        ),
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": type_id,
        "kind": "occurrence" if reusable_occurrence else "definition",
        "source": "open_document",
        "live_validated": True,
        "portable": portable,
        "reference": reference,
        "assembly_contract": _assembly_component_contract(
            type_id,
            solid_count=len(solids),
            output_type=str(
                getattr(obj, "VibeCADVibeScriptOutputType", "") or ""
            ),
        ),
    }
    program_id = str(getattr(obj, "VibeCADVibeScriptProgramId", "") or "").strip()
    if program_id:
        domain = str(getattr(obj, "VibeCADVibeScriptDomain", "") or "").strip()
        try:
            from VibeCADVibeScriptDomains import get_vibescript_pack_for_domain

            source_pack = get_vibescript_pack_for_domain(domain)
        except Exception:
            source_pack = None
        candidate["authoring_source"] = {
            "source_id": program_id,
            "domain": domain,
            "output_name": str(
                getattr(obj, "VibeCADVibeScriptOutputName", "") or ""
            ).strip(),
            "document_uid": str(reference.get("document_uid") or ""),
            "document_name": str(getattr(source_document, "Name", "") or ""),
            "document_path": str(
                reference.get("document_path")
                or getattr(source_document, "FileName", "")
                or ""
            ),
            "current_revision": str(
                getattr(obj, "VibeCADVibeScriptRevision", "") or ""
            ).strip(),
            "document_open": True,
            **(
                {"workbench": str(source_pack.workbench)}
                if source_pack is not None
                else {}
            ),
        }
    local_coordinate_systems = _native_lcs_candidates(owner_document, obj)
    if local_coordinate_systems:
        candidate["local_coordinate_systems"] = local_coordinate_systems
    try:
        import VibeCADReferenceContracts as reference_contracts
        import VibeCADScriptedPublication as publication

        published = reference_contracts.published_object(obj)
        if published is not None:
            root = publication.model_root_for(published)
            table = json.loads(
                str(getattr(root, publication.PROP_INTERFACES, "{}") or "{}")
            )
            output_key = str(getattr(published, publication.PROP_OUTPUT_KEY, "") or "")
            if isinstance(table, Mapping):
                descriptors = reference_contracts.published_interface_descriptors(
                    dict(table),
                    output_key,
                )
                for descriptor in descriptors:
                    try:
                        resolved = reference_contracts.resolve_interface(
                            None,
                            obj,
                            str(descriptor["name"]),
                        )
                    except reference_contracts.ReferenceContractError:
                        continue
                    frame = resolved.get("connector_frame")
                    if isinstance(frame, Mapping):
                        descriptor["frame"] = dict(frame)
                interfaces = [str(item["name"]) for item in descriptors]
                if interfaces:
                    candidate["published_interfaces"] = interfaces[:64]
                    candidate["interfaces"] = descriptors[:64]
                    candidate["interfaces_truncated"] = len(interfaces) > 64
    except Exception:
        # Catalog discovery must remain useful when optional publication metadata
        # is malformed; exact publication validation still rejects bad references.
        pass
    if not candidate.get("interfaces"):
        native_definitions = reference_contracts.native_interface_definitions(obj)
        if native_definitions:
            native_table = {
                reference_contracts.INTERFACE_TABLE_SCHEMA_KEY: (
                    reference_contracts.INTERFACE_TABLE_SCHEMA
                ),
                reference_contracts.INTERFACE_TABLE_OUTPUTS_KEY: {
                    str(getattr(obj, "Name", "") or ""): native_definitions
                },
            }
            descriptors = reference_contracts.published_interface_descriptors(
                native_table,
                str(getattr(obj, "Name", "") or ""),
            )
            candidate["published_interfaces"] = [
                str(item["name"]) for item in descriptors[:64]
            ]
            candidate["interfaces"] = descriptors[:64]
            candidate["interfaces_truncated"] = len(descriptors) > 64
    return candidate


def open_component_candidates(owner: Any) -> list[dict[str, Any]]:
    """Return live-validated component definitions from all open documents."""

    import FreeCAD as App

    open_candidates: list[dict[str, Any]] = []
    for document in list(App.listDocuments().values()):
        for obj in list(getattr(document, "Objects", []) or [])[:20_000]:
            candidate = _live_component_candidate(owner, document, obj)
            if candidate is not None:
                open_candidates.append(candidate)
    return open_candidates


def capture_component_catalog(service: Any) -> dict[str, Any]:
    """Capture open-document candidates on the FreeCAD document thread."""

    owner = service._active_document()
    if owner is None:
        raise ComponentCatalogError(
            "Component search requires an active document."
        )
    import FreeCAD as App

    open_candidates = open_component_candidates(owner)
    open_paths: set[str] = set()
    for document in list(App.listDocuments().values()):
        file_name = str(getattr(document, "FileName", "") or "").strip()
        if file_name:
            open_paths.add(str(Path(file_name).expanduser().resolve()))
    owner_file_name = str(getattr(owner, "FileName", "") or "").strip()
    owner_file = (
        Path(owner_file_name).expanduser().resolve() if owner_file_name else None
    )
    return {
        "owner_document_uid": str(getattr(owner, "Uid", "") or ""),
        "project_directory": (str(owner_file.parent) if owner_file is not None else ""),
        "owner_file": str(owner_file) if owner_file is not None else "",
        "open_document_files": sorted(open_paths),
        "open_candidates": open_candidates,
    }


def _bounded_saved_files(root: Path) -> tuple[list[Path], bool, int]:
    files: list[Path] = []
    directory_count = 0
    truncated = False
    for current, directories, names in os.walk(root, followlinks=False):
        directory_count += 1
        directories[:] = sorted(
            name for name in directories if not Path(current, name).is_symlink()
        )
        if directory_count > MAX_COMPONENT_DIRECTORIES:
            truncated = True
            break
        for name in sorted(names):
            if not name.casefold().endswith(".fcstd"):
                continue
            path = Path(current, name)
            if path.is_symlink():
                continue
            files.append(path)
            if len(files) >= MAX_COMPONENT_FILES:
                truncated = True
                return files, truncated, directory_count
    return files, truncated, directory_count


def _property_text(property_node: ET.Element | None) -> str:
    if property_node is None:
        return ""
    for child_name in ("String", "Uuid"):
        child = property_node.find(child_name)
        if child is not None:
            return str(child.attrib.get("value") or "")
    return ""


def _property_bool(property_node: ET.Element | None) -> bool | None:
    if property_node is None:
        return None
    child = property_node.find("Bool")
    if child is None:
        return None
    raw = str(child.attrib.get("value") or "").strip().casefold()
    return raw == "true" if raw in {"true", "false"} else None


def _property_links(property_node: ET.Element | None) -> list[str]:
    if property_node is None:
        return []
    link_list = property_node.find("LinkList")
    if link_list is None:
        return []
    return [
        str(link.attrib.get("value") or "").strip()
        for link in link_list.findall("Link")
        if str(link.attrib.get("value") or "").strip()
    ]


def _property_placement_frame(property_node: ET.Element | None) -> dict[str, Any] | None:
    """Read one saved placement into the exact connector-frame wire format."""

    if property_node is None:
        return None
    placement = property_node.find("PropertyPlacement")
    if placement is None:
        return None
    try:
        px, py, pz = (
            float(placement.attrib[name]) for name in ("Px", "Py", "Pz")
        )
        qx, qy, qz, qw = (
            float(placement.attrib[name]) for name in ("Q0", "Q1", "Q2", "Q3")
        )
    except (KeyError, TypeError, ValueError):
        return None
    norm = (qx * qx + qy * qy + qz * qz + qw * qw) ** 0.5
    if norm <= 0.0:
        return None
    qx, qy, qz, qw = (value / norm for value in (qx, qy, qz, qw))
    matrix = [
        1.0 - 2.0 * (qy * qy + qz * qz),
        2.0 * (qx * qy - qz * qw),
        2.0 * (qx * qz + qy * qw),
        px,
        2.0 * (qx * qy + qz * qw),
        1.0 - 2.0 * (qx * qx + qz * qz),
        2.0 * (qy * qz - qx * qw),
        py,
        2.0 * (qx * qz - qy * qw),
        2.0 * (qy * qz + qx * qw),
        1.0 - 2.0 * (qx * qx + qy * qy),
        pz,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    return {
        "schema": "vibecad-connector-frame-v1",
        "origin_mm": [matrix[3], matrix[7], matrix[11]],
        "x_direction": [matrix[0], matrix[4], matrix[8]],
        "axis_direction": [matrix[2], matrix[6], matrix[10]],
        "matrix": matrix,
    }


def _saved_native_interface_definition(
    object_name: str,
    values: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if values.get(reference_contracts.PROP_NATIVE_INTERFACE) is not True:
        return None
    name = str(values.get(reference_contracts.PROP_NATIVE_INTERFACE_NAME) or "").strip()
    kind = str(values.get(reference_contracts.PROP_NATIVE_INTERFACE_KIND) or "").strip()
    frame = values.get("Placement")
    if (
        not name
        or kind not in reference_contracts.NATIVE_INTERFACE_KINDS
        or not isinstance(frame, Mapping)
    ):
        return None
    try:
        allowed = json.loads(
            str(
                values.get(reference_contracts.PROP_NATIVE_INTERFACE_ALLOWED_JOINTS)
                or "[]"
            )
        )
    except ValueError:
        return None
    if not isinstance(allowed, list) or any(
        not isinstance(value, str) for value in allowed
    ):
        return None
    compatibility = str(
        values.get(reference_contracts.PROP_NATIVE_INTERFACE_COMPATIBILITY) or ""
    ).strip()
    connector = {
        "kind": kind,
        **({"allowed_joints": list(allowed)} if allowed else {}),
        **({"compatibility": compatibility} if compatibility else {}),
    }
    return name, {
        "selection": {"type": "frame", "native_lcs": object_name},
        "connector": connector,
        "resolved": {
            "object": "",
            "subelements": [],
            "geometry": [],
            "connector_frame": dict(frame),
        },
    }


def _document_property(root: ET.Element, name: str) -> str:
    properties = root.find("Properties")
    if properties is None:
        return ""
    for node in properties.findall("Property"):
        if str(node.attrib.get("name") or "") == name:
            return _property_text(node)
    return ""


def _saved_document_candidates(
    project_root: Path,
    path: Path,
) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ComponentCatalogError(f"Could not stat {path.name!r}: {exc}") from exc
    if not 0 < size <= MAX_COMPONENT_FILE_BYTES:
        raise ComponentCatalogError(
            f"{path.name!r} is empty or exceeds the component-file limit."
        )
    try:
        with zipfile.ZipFile(path, "r") as archive:
            info = archive.getinfo("Document.xml")
            if not 0 < info.file_size <= MAX_DOCUMENT_XML_BYTES:
                raise ComponentCatalogError(
                    f"{path.name!r} has an invalid Document.xml size."
                )
            xml = archive.read(info)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ComponentCatalogError(
            f"{path.name!r} is not a readable FreeCAD document: {exc}"
        ) from exc
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ComponentCatalogError(
            f"{path.name!r} has malformed FreeCAD metadata: {exc}"
        ) from exc
    document_uid = _document_property(root, "Uid").strip()
    if not document_uid:
        raise ComponentCatalogError(f"{path.name!r} has no document uid.")
    document_label = _document_property(root, "Label").strip() or path.stem
    declarations = root.find("Objects")
    if declarations is None:
        return []
    types = {
        str(node.attrib.get("name") or ""): str(node.attrib.get("type") or "")
        for node in declarations.findall("Object")
    }
    metadata: dict[str, dict[str, str]] = {}
    component_groups: dict[str, list[str]] = {}
    native_metadata: dict[str, dict[str, Any]] = {}
    object_data = root.find("ObjectData")
    if object_data is not None:
        for object_node in object_data.findall("Object"):
            object_name = str(object_node.attrib.get("name") or "")
            properties = object_node.find("Properties")
            if not object_name or properties is None:
                continue
            values: dict[str, str] = {}
            native_values: dict[str, Any] = {}
            for property_node in properties.findall("Property"):
                property_name = str(property_node.attrib.get("name") or "")
                if property_name in _SEARCH_PROPERTY_NAMES | _VIBESCRIPT_PROPERTY_NAMES:
                    text = _property_text(property_node).strip()
                    if text:
                        values[property_name] = text
                if property_name == "Group":
                    component_groups[object_name] = _property_links(property_node)
                if property_name in _NATIVE_INTERFACE_PROPERTY_NAMES:
                    if property_name == reference_contracts.PROP_NATIVE_INTERFACE:
                        native_values[property_name] = _property_bool(property_node)
                    elif property_name == "Placement":
                        native_values[property_name] = _property_placement_frame(
                            property_node
                        )
                    else:
                        native_values[property_name] = _property_text(
                            property_node
                        ).strip()
            metadata[object_name] = values
            if native_values:
                native_metadata[object_name] = native_values
    relative = path.relative_to(project_root)
    document_path = relative.as_posix()
    candidates = []
    for object_name, type_id in types.items():
        if not object_name or type_id not in _SAVED_COMPONENT_TYPES:
            continue
        values = metadata.get(object_name, {})
        scripted_role = values.get("VibeCADScriptedRole", "")
        reusable_occurrence = (
            type_id == "App::Link"
            and values.get("VibeCADVibeScriptOutputType") == "component_link"
            and values.get("VibeCADVibeScriptDomain") in {"partdesign", "robot"}
        )
        if (
            scripted_role in {"implementation", "model", "publication_target"}
            and not reusable_occurrence
        ):
            continue
        if (
            type_id == "App::Link"
            and scripted_role != "publication"
            and not reusable_occurrence
        ):
            continue
        label = values.get("Label") or object_name
        item = {
            "document_label": document_label,
            "object_name": object_name,
            "label": label,
            "type_id": type_id,
            "kind": "occurrence" if reusable_occurrence else "definition",
            "source": "saved_project_file",
            "live_validated": False,
            "portable": True,
            "reference": {
                "document_uid": document_uid,
                "object_name": object_name,
                "document_path": document_path,
            },
        }
        if values.get("PartNumber"):
            item["part_number"] = values["PartNumber"]
        if values.get("Description"):
            item["description"] = values["Description"]
        if values.get("StockCode"):
            item["stock_code"] = values["StockCode"]
        if values.get("VibeCADVibeScriptProgramId"):
            domain = values.get("VibeCADVibeScriptDomain", "")
            try:
                from VibeCADVibeScriptDomains import get_vibescript_pack_for_domain

                source_pack = get_vibescript_pack_for_domain(domain)
            except Exception:
                source_pack = None
            item["authoring_source"] = {
                "source_id": values["VibeCADVibeScriptProgramId"],
                "domain": domain,
                "output_name": values.get("VibeCADVibeScriptOutputName", ""),
                "document_uid": document_uid,
                "document_name": document_label,
                "document_path": document_path,
                "current_revision": values.get("VibeCADVibeScriptRevision", ""),
                "document_open": False,
                **(
                    {"workbench": str(source_pack.workbench)}
                    if source_pack is not None
                    else {}
                ),
            }
        output_type = values.get("VibeCADVibeScriptOutputType", "")
        item["assembly_contract"] = _assembly_component_contract(
            type_id,
            output_type=output_type,
        )
        output_name = values.get("VibeCADVibeScriptOutputName", "")
        if output_name:
            for table_values in metadata.values():
                raw_table = table_values.get("VibeCADPublishedInterfaces", "")
                if not raw_table:
                    continue
                try:
                    interface_table = json.loads(raw_table)
                except ValueError:
                    continue
                descriptors = reference_contracts.published_interface_descriptors(
                    interface_table,
                    output_name,
                )
                if descriptors:
                    item["published_interfaces"] = [
                        str(descriptor["name"]) for descriptor in descriptors[:64]
                    ]
                    item["interfaces"] = descriptors[:64]
                    item["interfaces_truncated"] = len(descriptors) > 64
                    break
        native_definitions: dict[str, dict[str, Any]] = {}
        local_coordinate_systems: list[dict[str, Any]] = []
        for child_name in component_groups.get(object_name, []):
            if types.get(child_name) not in _SAVED_NATIVE_LCS_TYPES:
                continue
            parsed = _saved_native_interface_definition(
                child_name,
                native_metadata.get(child_name, {}),
            )
            child_item = {
                "object_name": child_name,
                "label": metadata.get(child_name, {}).get("Label") or child_name,
                "reference": {
                    "document_uid": document_uid,
                    "object_name": child_name,
                    "document_path": document_path,
                },
            }
            if parsed is not None:
                interface_name, definition = parsed
                definition["resolved"]["object"] = object_name
                if interface_name not in native_definitions:
                    native_definitions[interface_name] = definition
                    child_item["published_interface"] = interface_name
            local_coordinate_systems.append(child_item)
            if len(local_coordinate_systems) >= 64:
                break
        if local_coordinate_systems:
            item["local_coordinate_systems"] = local_coordinate_systems
        if native_definitions and not item.get("interfaces"):
            native_table = {
                reference_contracts.INTERFACE_TABLE_SCHEMA_KEY: (
                    reference_contracts.INTERFACE_TABLE_SCHEMA
                ),
                reference_contracts.INTERFACE_TABLE_OUTPUTS_KEY: {
                    object_name: native_definitions
                },
            }
            descriptors = reference_contracts.published_interface_descriptors(
                native_table,
                object_name,
            )
            item["published_interfaces"] = [
                str(descriptor["name"]) for descriptor in descriptors[:64]
            ]
            item["interfaces"] = descriptors[:64]
            item["interfaces_truncated"] = len(descriptors) > 64
        candidates.append(item)
    return candidates


def _query_matches(candidate: Mapping[str, Any], terms: tuple[str, ...]) -> bool:
    if not terms:
        return True
    values = [
        str(candidate.get(name) or "")
        for name in (
            "document_label",
            "object_name",
            "label",
            "type_id",
            "part_number",
            "description",
            "stock_code",
        )
    ]
    values.extend(
        str(value) for value in list(candidate.get("published_interfaces") or [])
    )
    authoring = candidate.get("authoring_source")
    if isinstance(authoring, Mapping):
        values.extend(str(value) for value in authoring.values())
    text = " ".join(values).casefold()
    return all(term in text for term in terms)


def _component_reference_result(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return one compact, copy-ready identity without redundant fields."""

    label = str(candidate.get("label") or candidate.get("object_name") or "")
    result = {
        "label": label[:MAX_COMPONENT_REFERENCE_LABEL_CHARACTERS],
        "kind": str(candidate.get("kind") or "definition"),
        "reference": dict(candidate.get("reference") or {}),
    }
    if len(label) > MAX_COMPONENT_REFERENCE_LABEL_CHARACTERS:
        result["label_truncated"] = True
    return result


def _component_search_result_bytes(result: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            {"ok": True, **dict(result)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def prepare_captured_component_catalog(
    captured: Mapping[str, Any],
) -> dict[str, Any]:
    """Read saved metadata once and retain one immutable turn catalog."""

    candidates = []
    for raw_candidate in list(captured.get("open_candidates") or []):
        if not isinstance(raw_candidate, Mapping):
            continue
        candidate = dict(raw_candidate)
        # Snapshots captured before occurrence-aware catalogs remain readable.
        # Every inventory returned to an authoring model still has one explicit
        # definition/occurrence classification.
        candidate.setdefault("kind", "definition")
        candidates.append(candidate)
    project_directory = str(captured.get("project_directory") or "").strip()
    scanned_files = 0
    skipped_files = 0
    search_truncated = False
    errors: list[dict[str, str]] = []
    if project_directory:
        root = Path(project_directory).expanduser().resolve()
        owner_file = str(captured.get("owner_file") or "")
        open_files = {
            str(Path(item).expanduser().resolve())
            for item in list(captured.get("open_document_files") or [])
            if str(item or "").strip()
        }
        files, search_truncated, _directory_count = _bounded_saved_files(root)
        for path in files:
            relative = path.relative_to(root).as_posix()
            resolved = str(path.resolve())
            if resolved == owner_file or resolved in open_files:
                continue
            scanned_files += 1
            try:
                candidates.extend(_saved_document_candidates(root, path))
            except ComponentCatalogError as exc:
                skipped_files += 1
                if len(errors) < 8:
                    errors.append(
                        {
                            "document_path": relative,
                            "error": str(exc),
                        }
                    )
    return {
        "schema": "vibecad-component-catalog-snapshot-v1",
        "owner_document_uid": str(captured.get("owner_document_uid") or ""),
        "project_directory": project_directory,
        "project_file_search_available": bool(project_directory),
        "saved_documents_scanned": scanned_files,
        "saved_documents_skipped": skipped_files,
        "search_truncated": search_truncated,
        "candidates": candidates,
        "errors": errors,
    }


def search_prepared_component_catalog(
    prepared: Mapping[str, Any],
    query: str = "",
    *,
    document_path: str | None = None,
    limit: int = 25,
    offset: int = 0,
    detail: str = "full",
) -> dict[str, Any]:
    """Filter a prepared turn catalog without rescanning project files."""

    if prepared.get("schema") != "vibecad-component-catalog-snapshot-v1":
        raise ComponentCatalogError("Invalid prepared component catalog snapshot.")
    clean_query = str(query or "").strip()
    if len(clean_query) > 256:
        raise ComponentCatalogError("query exceeds 256 characters.")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_COMPONENT_SEARCH_RESULTS
    ):
        raise ComponentCatalogError(
            f"limit must be an integer between 1 and {MAX_COMPONENT_SEARCH_RESULTS}."
        )
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ComponentCatalogError("offset must be a non-negative integer.")
    clean_detail = str(detail or "").strip().casefold()
    if clean_detail not in {"references", "full"}:
        raise ComponentCatalogError("detail must be 'references' or 'full'.")
    clean_document_path = str(document_path or "").strip()
    if clean_document_path:
        from VibeCADDocumentReferences import normalize_document_path

        try:
            clean_document_path = normalize_document_path(clean_document_path)
        except DocumentReferenceError as exc:
            raise ComponentCatalogError(str(exc)) from exc
        if not prepared.get("project_file_search_available"):
            raise ComponentCatalogError(
                "Save the Assembly document before searching a project-relative file."
            )

    candidates = [
        dict(item)
        for item in list(prepared.get("candidates") or [])
        if isinstance(item, Mapping)
    ]
    terms = tuple(term for term in clean_query.casefold().split() if term)
    if clean_document_path:
        candidates = [
            item
            for item in candidates
            if str(dict(item.get("reference") or {}).get("document_path") or "")
            == clean_document_path
        ]
    matches = [item for item in candidates if _query_matches(item, terms)]
    matches.sort(
        key=lambda item: (
            str(dict(item.get("reference") or {}).get("document_path") or ""),
            str(item.get("document_label") or "").casefold(),
            str(item.get("label") or "").casefold(),
            str(item.get("object_name") or ""),
        )
    )
    requested_page = matches[offset : offset + limit]
    if clean_detail == "references":
        requested_page = [_component_reference_result(item) for item in requested_page]

    errors = [
        dict(item)
        for item in list(prepared.get("errors") or [])
        if isinstance(item, Mapping)
    ]

    def result_for(page: list[dict[str, Any]]) -> dict[str, Any]:
        next_offset = offset + len(page) if offset + len(page) < len(matches) else None
        return {
            "query": clean_query,
            "document_path": clean_document_path,
            "detail": clean_detail,
            "offset": offset,
            "limit": limit,
            "page_byte_limit": MAX_COMPONENT_SEARCH_RESPONSE_BYTES,
            "page_byte_limited": len(page) < len(requested_page),
            "project_file_search_available": bool(
                prepared.get("project_file_search_available")
            ),
            "saved_documents_scanned": int(
                prepared.get("saved_documents_scanned") or 0
            ),
            "saved_documents_skipped": int(
                prepared.get("saved_documents_skipped") or 0
            ),
            "search_truncated": bool(prepared.get("search_truncated")),
            "match_count": len(matches),
            "returned_count": len(page),
            "matches_truncated": next_offset is not None,
            "next_offset": next_offset,
            "matches": page,
            "errors": errors,
        }

    page: list[dict[str, Any]] = []
    for candidate in requested_page:
        proposed = result_for([*page, candidate])
        if (
            _component_search_result_bytes(proposed)
            > MAX_COMPONENT_SEARCH_RESPONSE_BYTES
        ):
            break
        page.append(candidate)

    if not page and requested_page:
        # A pathological full record must not make pagination stall. Preserve its
        # exact reusable identity and state explicitly that only metadata was omitted.
        source_candidate = matches[offset]
        compact = {
            **_component_reference_result(source_candidate),
            "full_metadata_omitted": True,
            "omission_reason": "component_record_byte_limit",
        }
        proposed = result_for([compact])
        if (
            _component_search_result_bytes(proposed)
            > MAX_COMPONENT_SEARCH_RESPONSE_BYTES
        ):
            raise ComponentCatalogError(
                "The next component reference exceeds the catalog response byte limit."
            )
        page.append(compact)

    return result_for(page)


def search_captured_component_catalog(
    captured: Mapping[str, Any],
    query: str = "",
    *,
    document_path: str | None = None,
    limit: int = 25,
    offset: int = 0,
    detail: str = "full",
) -> dict[str, Any]:
    """Search captured open objects and saved project metadata off-thread."""

    return search_prepared_component_catalog(
        prepare_captured_component_catalog(captured),
        query,
        document_path=document_path,
        limit=limit,
        offset=offset,
        detail=detail,
    )


def component_inventory(
    prepared: Mapping[str, Any],
    *,
    limit: int = MAX_INJECTED_COMPONENTS,
) -> dict[str, Any]:
    """Return the bounded inventory injected into component-capable turns."""

    found = search_prepared_component_catalog(prepared, limit=limit)
    included_fields = (
        "document_label",
        "object_name",
        "label",
        "kind",
        "type_id",
        "source",
        "live_validated",
        "portable",
        "reference",
        "part_number",
        "stock_code",
        "authoring_source",
        "assembly_contract",
        "published_interfaces",
        "interfaces",
        "interfaces_truncated",
        "local_coordinate_systems",
    )
    components = [
        {
            name: candidate[name]
            for name in included_fields
            if name in candidate and candidate[name] not in (None, "", [], {})
        }
        for candidate in found["matches"]
    ]
    return {
        "schema": "vibecad-available-components-v1",
        "component_count": int(found["match_count"]),
        "components_included": len(components),
        "components_truncated": bool(found["matches_truncated"]),
        "project_file_search_available": bool(found["project_file_search_available"]),
        "components": components,
        "usage": (
            "Use a definition reference with api.component or api.instances. Reuse an "
            "occurrence reference when Assembly must adopt that exact placed object. "
            "Call component_catalog.search only when the needed component is not listed "
            "or when additional catalog metadata is required. To enumerate a truncated "
            "catalog, use detail='references', limit=200, offset=0, then repeat with "
            "offset=next_offset until next_offset is null. A page may return fewer "
            "than limit so its complete matches array remains provider-safe."
        ),
    }


def search_component_catalog(
    service: Any,
    query: str = "",
    *,
    document_path: str | None = None,
    limit: int = 25,
    offset: int = 0,
    detail: str = "full",
) -> dict[str, Any]:
    """Synchronous convenience entry point for tests and direct callers."""

    return search_captured_component_catalog(
        capture_component_catalog(service),
        query,
        document_path=document_path,
        limit=limit,
        offset=offset,
        detail=detail,
    )
