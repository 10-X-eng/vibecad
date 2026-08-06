# SPDX-License-Identifier: LGPL-2.1-or-later

"""Release integration gate for the bundled standard-component catalog."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import shutil
import tempfile

import FreeCAD as App

from VibeCADFasteners import (
    CATALOG_ID,
    COMPONENT_SCHEMA,
    FastenerCatalogError,
    MAX_MODEL_THREAD_AXIAL_LENGTH_MM,
    PROP_CANONICAL_KEY,
    PROP_ERROR,
    PROP_GENERATOR_REVISION,
    PROP_SCHEMA,
    assembly_component_contract,
    catalog_index,
    create_fastener_feature,
    describe_standard,
    fastener_feature_identity,
    provenance,
    resolve_fastener,
    search_catalog,
    update_fastener_feature,
)


def _default_request(standard: str) -> dict:
    details = describe_standard(standard)
    assert details["nominal_threads"], standard
    nominal_thread = str(details["nominal_threads"][0])
    selected = describe_standard(
        standard,
        nominal_thread=nominal_thread,
    )
    length = None
    if selected["requires_length"]:
        if selected["arbitrary_length"]:
            length = float(selected["default_length_mm"])
        else:
            assert selected["lengths"], (standard, nominal_thread)
            length = float(selected["lengths"][0]["millimeters"])
    return {
        "standard": standard,
        "nominal_thread": nominal_thread,
        "length_mm": length,
        "model_thread": False,
    }


def _verify_catalog_resolution(index: dict) -> dict:
    canonical_keys = set()
    size_count = 0
    boundary_count = 0
    for row in index["standards"]:
        standard = str(row["standard"])
        details = describe_standard(standard)
        for nominal_thread in details["nominal_threads"]:
            selected = describe_standard(
                standard,
                nominal_thread=nominal_thread,
            )
            size_count += 1
            requests = [None]
            if selected["requires_length"]:
                if selected["arbitrary_length"]:
                    requests = [float(selected["default_length_mm"])]
                else:
                    lengths = [
                        float(item["millimeters"])
                        for item in selected["lengths"]
                    ]
                    requests = sorted({lengths[0], lengths[-1]})
            for length in requests:
                identity = resolve_fastener(
                    standard=standard,
                    nominal_thread=nominal_thread,
                    length_mm=length,
                )
                assert identity["schema"] == COMPONENT_SCHEMA
                assert identity["catalog"] == CATALOG_ID
                assert identity["canonical_key"] not in canonical_keys
                canonical_keys.add(identity["canonical_key"])
                boundary_count += 1
    return {
        "nominal_sizes": size_count,
        "resolved_boundaries": boundary_count,
        "canonical_keys": len(canonical_keys),
    }


def _verify_representative_geometry(index: dict) -> dict:
    document = App.newDocument("VibeCADFastenerCatalogMatrix")
    generated = []
    try:
        for row in index["standards"]:
            request = _default_request(str(row["standard"]))
            feature, identity = create_fastener_feature(
                document,
                **request,
                object_name=f"Catalog_{len(generated):03d}",
            )
            assert feature.Shape.isValid()
            assert len(feature.Shape.Solids) == 1
            assert str(getattr(feature, PROP_SCHEMA)) == COMPONENT_SCHEMA
            assert str(getattr(feature, PROP_CANONICAL_KEY)) == identity[
                "canonical_key"
            ]
            assert str(getattr(feature, PROP_ERROR)) == ""
            assert (
                str(getattr(feature, PROP_GENERATOR_REVISION))
                == provenance()["revision"]
            )
            contract = assembly_component_contract(feature.Shape, identity)
            assert "thread_axis" in contract["published_interfaces"]
            assert contract["bom_properties"]
            if identity["standard"] in {"ISO7379", "ASMEB18.3.4"}:
                shoulder_length = float(identity["length_mm"])
                interfaces = contract["published_interfaces"]
                assert interfaces["bearing_plane"]["standard_frame"][
                    "position"
                ] == [0.0, 0.0, shoulder_length]
                assert interfaces["under_head_plane"]["standard_frame"][
                    "position"
                ] == [0.0, 0.0, shoulder_length]
                assert interfaces["mounting_plane"]["standard_frame"][
                    "position"
                ] == [0.0, 0.0, shoulder_length]
            generated.append(
                {
                    "standard": identity["standard"],
                    "family": identity["family"],
                    "faces": len(feature.Shape.Faces),
                }
            )
            document.removeObject(feature.Name)
        return {
            "generated_standards": len(generated),
            "families": sorted({item["family"] for item in generated}),
        }
    finally:
        App.closeDocument(document.Name)


def _verify_model_thread_families(index: dict) -> dict:
    """Prove the native boolean changes generated BREP for every family."""

    document = App.newDocument("VibeCADModelThreadFamilies")
    generated = {}
    try:
        for row in index["standards"]:
            family = str(row["family"])
            if not row["supports_model_thread"] or family in generated:
                continue
            request = _default_request(str(row["standard"]))
            simple, simple_identity = create_fastener_feature(
                document,
                **request,
                object_name=f"Simple_{len(generated):02d}",
            )
            simple_brep = simple.Shape.exportBrepToString()
            simple_edges = len(simple.Shape.Edges)
            assert bool(simple.Thread) is False
            assert simple_identity["model_thread"] is False
            document.removeObject(simple.Name)

            request["model_thread"] = True
            feature, identity = create_fastener_feature(
                document,
                **request,
                object_name=f"RealThread_{len(generated):02d}",
            )
            assert feature.Shape.isValid()
            assert len(feature.Shape.Solids) == 1
            assert bool(feature.Thread) is True
            assert feature.getTypeIdOfProperty("Thread") == "App::PropertyBool"
            assert identity["model_thread"] is True
            real_brep = feature.Shape.exportBrepToString()
            assert hashlib.sha256(real_brep.encode()).digest() != (
                hashlib.sha256(simple_brep.encode()).digest()
            ), family
            assert len(feature.Shape.Edges) > simple_edges, family
            generated[family] = {
                "part_number": identity["part_number"],
                "simple_edges": simple_edges,
                "real_thread_edges": len(feature.Shape.Edges),
            }
            document.removeObject(feature.Name)
        expected_families = {
            str(row["family"])
            for row in index["standards"]
            if row["supports_model_thread"]
        }
        assert set(generated) == expected_families
        return {
            "families": generated,
            "family_count": len(generated),
        }
    finally:
        App.closeDocument(document.Name)


def _verify_shoulder_screw_thread_and_datum() -> dict:
    """Pin the ISO 7379 thread boolean and its non-zero bearing datum."""

    document = App.newDocument("VibeCADShoulderScrewContract")
    try:
        feature, identity = create_fastener_feature(
            document,
            standard="ISO7379",
            nominal_thread="M6",
            length_mm=30.0,
            model_thread=True,
            object_name="ThreadedShoulderScrew",
        )
        assert feature.Shape.isValid()
        assert len(feature.Shape.Solids) == 1
        assert bool(feature.Thread) is True
        contract = assembly_component_contract(feature.Shape, identity)
        interfaces = contract["published_interfaces"]
        for name in ("mounting_plane", "bearing_plane", "under_head_plane"):
            assert interfaces[name]["standard_frame"]["position"] == [
                0.0,
                0.0,
                30.0,
            ]
        return {
            "standard": identity["standard"],
            "part_number": identity["part_number"],
            "model_thread": bool(feature.Thread),
            "bearing_plane_z_mm": 30.0,
        }
    finally:
        App.closeDocument(document.Name)


def _verify_document_lifecycle(root: Path) -> dict:
    document = App.newDocument("VibeCADFastenerDocumentLifecycle")
    body = document.addObject("PartDesign::Body", "FastenerBody")
    feature, identity = create_fastener_feature(
        body,
        standard="ISO4762",
        nominal_thread="M6",
        length_mm=20,
        object_name="Fastener",
        label="Motor mount bolt",
    )
    original_name = str(feature.Name)
    simple_brep = feature.Shape.exportBrepToString()
    simple_edges = len(feature.Shape.Edges)
    threaded = update_fastener_feature(
        feature,
        standard="ISO4762",
        nominal_thread="M6",
        length_mm=20,
        model_thread=True,
        label="Motor mount bolt",
    )
    real_thread_brep = feature.Shape.exportBrepToString()
    assert bool(feature.Thread) is True
    assert threaded["model_thread"] is True
    assert real_thread_brep != simple_brep
    assert len(feature.Shape.Edges) > simple_edges
    updated = update_fastener_feature(
        feature,
        standard="ISO4762",
        nominal_thread="M6",
        length_mm=25,
        model_thread=True,
        label="Motor mount bolt",
    )
    assert feature.Name == original_name
    assert updated["canonical_key"] != identity["canonical_key"]
    assert bool(feature.Thread) is True
    save_path = root / "standard-fastener.FCStd"
    document.recompute()
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)

    reopened = App.openDocument(str(save_path))
    try:
        restored = reopened.getObject(original_name)
        assert restored is not None
        assert restored.Shape.isValid()
        assert len(restored.Shape.Solids) == 1
        assert fastener_feature_identity(restored)["canonical_key"] == updated[
            "canonical_key"
        ]
        reopened.recompute()
        assert str(getattr(restored, PROP_ERROR)) == ""
        assert restored.Proxy is not None
        assert bool(restored.Thread) is True
        assert fastener_feature_identity(restored)["model_thread"] is True
        reopened_real_brep = restored.Shape.exportBrepToString()
        reopened_real_edges = len(restored.Shape.Edges)
        restored.Thread = False
        reopened.recompute()
        assert bool(restored.Thread) is False
        assert fastener_feature_identity(restored)["model_thread"] is False
        assert restored.Shape.exportBrepToString() != reopened_real_brep
        assert len(restored.Shape.Edges) < reopened_real_edges
        restored.Thread = True
        reopened.recompute()
        assert bool(restored.Thread) is True
        assert fastener_feature_identity(restored)["model_thread"] is True
        reopened_edit = resolve_fastener(
            standard="ISO4762",
            nominal_thread="M6",
            length_mm=30,
            model_thread=True,
        )
        if restored.getTypeIdOfProperty("Length") == "App::PropertyEnumeration":
            restored.Length = str(reopened_edit["length_token"])
        else:
            restored.Length = float(reopened_edit["length_mm"])
        reopened.recompute()
        assert str(restored.Label) == "Motor mount bolt"
        assert fastener_feature_identity(restored)["canonical_key"] == (
            reopened_edit["canonical_key"]
        )
        assert str(getattr(restored, PROP_ERROR)) == ""
        assert not any(
            str(root) in str(restored.getPropertyByName(name))
            for name in restored.PropertiesList
            if restored.getTypeIdOfProperty(name) == "App::PropertyString"
        )
        cached_brep = restored.Shape.exportBrepToString()
        restored.Proxy = None
        reopened.recompute()
        assert restored.Shape.exportBrepToString() == cached_brep
        return {
            "object_name": original_name,
            "part_number": reopened_edit["part_number"],
            "restored_proxy_edit": True,
            "native_thread_toggle_changes_brep": True,
            "cached_shape_without_proxy": True,
        }
    finally:
        App.closeDocument(reopened.Name)


def _verify_search_and_failures() -> dict:
    partial_size = search_catalog("m3", limit=100)
    assert partial_size["total_matches"] > 0
    assert "M3" in partial_size["results"][0]["nominal_threads"]
    assert all(
        any(
            "m3" in str(size).casefold()
            for size in row["nominal_threads"]
        )
        for row in partial_size["results"]
    )
    cross_field = search_catalog("m3 socket", limit=100)
    assert cross_field["total_matches"] > 0
    assert "M3" in cross_field["results"][0]["nominal_threads"]
    assert all(
        "socket" in (
            f"{row['standard']} {row['family']} {row['description']}"
        ).casefold()
        and any(
            "m3" in str(size).casefold()
            for size in row["nominal_threads"]
        )
        for row in cross_field["results"]
    )
    partial_standard = search_catalog("476", limit=100)
    assert any(
        row["standard"] == "ISO4762"
        for row in partial_standard["results"]
    )
    split_family = search_catalog("press nut", limit=100)
    assert [
        row["standard"]
        for row in split_family["results"]
    ] == ["PEMPressNut"]

    result = search_catalog(
        "socket head",
        standard="ISO4762",
        nominal_thread="M6",
        length_mm=20,
    )
    assert result["total_matches"] == 1
    constructor = result["results"][0]["constructor"]
    assert constructor["standard"] == "ISO4762"
    assert constructor["nominal_thread"] == "M6"
    assert constructor["length_mm"] == 20
    unavailable = search_catalog(
        standard="ISO4762",
        nominal_thread="M6",
        length_mm=21.234,
    )
    assert unavailable["results"][0]["requested_match"] is False
    assert unavailable["results"][0]["nearest_valid_lengths_mm"]
    failures = []
    for name, arguments in (
        (
            "unknown_standard",
            {
                "standard": "NOT-A-STANDARD",
                "nominal_thread": "M6",
                "length_mm": 20,
            },
        ),
        (
            "unknown_size",
            {
                "standard": "ISO4762",
                "nominal_thread": "M6.123",
                "length_mm": 20,
            },
        ),
        (
            "unknown_length",
            {
                "standard": "ISO4762",
                "nominal_thread": "M6",
                "length_mm": 21.234,
            },
        ),
        (
            "model_thread_limit",
            {
                "standard": "ThreadedRod",
                "nominal_thread": "M6",
                "length_mm": MAX_MODEL_THREAD_AXIAL_LENGTH_MM + 1,
                "model_thread": True,
            },
        ),
        (
            "non_boolean_model_thread",
            {
                "standard": "ISO4762",
                "nominal_thread": "M6",
                "length_mm": 20,
                "model_thread": "modeled",
            },
        ),
    ):
        try:
            resolve_fastener(**arguments)
        except FastenerCatalogError as exc:
            assert str(exc)
            failures.append(name)
        else:
            raise AssertionError(f"{name} was silently accepted")
    return {
        "partial_size_matches": partial_size["total_matches"],
        "cross_field_matches": cross_field["total_matches"],
        "partial_standard_match": "ISO4762",
        "split_family_match": "PEMPressNut",
        "exact_constructor": constructor,
        "nearest_is_data_only": True,
        "hard_failures": failures,
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="vibecad-fasteners-release-"))
    try:
        assert tuple(inspect.signature(resolve_fastener).parameters) == (
            "standard",
            "nominal_thread",
            "length_mm",
            "model_thread",
            "left_handed",
            "options",
        )
        index = catalog_index()
        assert index["upstream_standard_count"] == 227
        assert len(index["standards"]) == 225
        standards = {row["standard"] for row in index["standards"]}
        assert len(standards) == 225
        assert {"PEMIUTA", "PEMIUTB", "PEMIUTC"}.issubset(standards)
        assert {
            item["standard"]
            for item in index["excluded_upstream_standards"]
        } == {"ISO8733", "ISO8735"}
        evidence = {
            "provenance": provenance(),
            "catalog": {
                "standards": len(index["standards"]),
                "upstream_standards": index["upstream_standard_count"],
                "excluded_upstream_standards": index[
                    "excluded_upstream_standards"
                ],
                "excluded_upstream_nominal_rows": sum(
                    len(row["excluded_upstream_nominal_threads"])
                    for row in index["standards"]
                ),
                **_verify_catalog_resolution(index),
            },
            "geometry": _verify_representative_geometry(index),
            "model_threads": _verify_model_thread_families(index),
            "shoulder_screw": _verify_shoulder_screw_thread_and_datum(),
            "document": _verify_document_lifecycle(root),
            "search": _verify_search_and_failures(),
        }
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "bundled_standard_fasteners",
                    **evidence,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
