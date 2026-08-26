# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace
import sys

import pytest

import VibeCADNativeDrawingSourceCatalog as drawing_catalog
import VibeCADNativeDrawingSnapshot as drawing_snapshot
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources
from VibeCADNativeDrawingSourceCatalog import drawing_source_catalog_page
from VibeCADNativeDrawingViewState import drawing_source_catalog_identity_state
from VibeCADNativeGeometrySources import (
    active_design_geometry_sources,
    drawing_source_exclusion_reason,
    is_potential_design_geometry_source,
)


class _Shape:
    Solids = (object(),)
    Faces = ()
    Edges = ()

    @staticmethod
    def isNull() -> bool:
        return False

    @staticmethod
    def isValid() -> bool:
        return True


class _Source:
    def __init__(self, object_id: int, *, analysis_domain: bool = False) -> None:
        self.ID = object_id
        self.Name = f"Source{object_id}"
        self.Label = self.Name
        self.TypeId = "Part::Feature"
        self.PropertiesList = ["Shape"]
        self.Shape = _Shape()
        self.ViewObject = SimpleNamespace(Visibility=True)
        self.VibeCADAnalysisDomain = analysis_domain
        self.AnalysisSources = ()

    @staticmethod
    def getParentGeoFeatureGroup():
        return None

    @staticmethod
    def getParentGroup():
        return None

    @staticmethod
    def isDerivedFrom(_type_id: str) -> bool:
        return False


class _CatalogShape:
    ShapeType = "Solid"
    Solids = (object(),)
    Faces = tuple(object() for _index in range(6))
    Edges = tuple(object() for _index in range(12))
    BoundBox = SimpleNamespace(XLength=10.0, YLength=20.0, ZLength=30.0)

    def __init__(self) -> None:
        self.validity_checks = 0

    @staticmethod
    def isNull() -> bool:
        return False

    def isValid(self) -> bool:
        self.validity_checks += 1
        raise AssertionError("source discovery must not validate a BREP")

    @staticmethod
    def copy():
        raise AssertionError("source discovery must not copy a BREP")


class _CatalogSource(_Source):
    def __init__(self, object_id: int) -> None:
        super().__init__(object_id)
        self.Name = "Body"
        self.Label = "Bracket"
        self.TypeId = "PartDesign::Body"
        self.PropertiesList = ["Shape"]
        self.Shape = _CatalogShape()
        self.Placement = None


def test_responsive_drawing_identity_capture_never_reads_live_shape(
    monkeypatch,
) -> None:
    class _IdentityOnlySource:
        Name = "HugeCompound"
        Label = "Huge Compound"
        TypeId = "App::Part"
        ID = 42
        PropertiesList = ["Shape"]
        VibeCADAnalysisDomain = False
        VibeCADTimelineRole = ""
        ViewObject = SimpleNamespace(Visibility=True)

        @property
        def Shape(self):
            raise AssertionError("responsive source capture must not read Shape")

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

        @staticmethod
        def getParentGroup():
            return None

    source = _IdentityOnlySource()
    document = SimpleNamespace(Uid="document-a")
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert is_potential_design_geometry_source(document, source) is True
    assert drawing_source_catalog_identity_state(source) == {
        "object_name": "HugeCompound",
        "label": "Huge Compound",
        "type_id": "App::Part",
        "shape_type": "",
        "placement": None,
        "topology": {},
        "bounds_size_mm": None,
        "geometry_details_deferred": True,
    }


@pytest.mark.parametrize("type_id", ("App::Part", "App::Link"))
def test_responsive_drawing_keeps_container_geometry_without_shape_property(
    monkeypatch,
    type_id: str,
) -> None:
    class _ContainerSource:
        Name = "ImportedAssembly"
        Label = "Imported Assembly"
        TypeId = type_id
        ID = 43
        PropertiesList = []
        VibeCADAnalysisDomain = False
        ViewObject = SimpleNamespace(Visibility=True)

        @property
        def Shape(self):
            raise AssertionError("responsive source capture must not read Shape")

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

        @staticmethod
        def getParentGroup():
            return None

    source = _ContainerSource()
    document = SimpleNamespace(Uid="document-a")
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert is_potential_design_geometry_source(document, source) is True
    assert drawing_source_catalog_identity_state(source)["object_name"] == (
        "ImportedAssembly"
    )


def test_responsive_drawing_rejects_origin_geometry_without_reading_shape(
    monkeypatch,
) -> None:
    class _Origin:
        Name = "XY_Plane"
        Label = "XY-plane"
        TypeId = "App::Plane"
        ID = 44
        PropertiesList = []
        VibeCADAnalysisDomain = False
        ViewObject = SimpleNamespace(Visibility=True)

        @property
        def Shape(self):
            raise AssertionError("responsive source capture must not read Shape")

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

        @staticmethod
        def getParentGroup():
            return None

    source = _Origin()
    document = SimpleNamespace(Uid="document-a")
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert is_potential_design_geometry_source(document, source) is False


def test_drawing_keeps_design_sources_while_analyze_uses_their_domain(monkeypatch) -> None:
    source = _Source(1)
    domain = _Source(2, analysis_domain=True)
    domain.AnalysisSources = (source,)
    document = SimpleNamespace(Uid="document-a", Objects=(source, domain))
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert active_design_geometry_sources(document) == (source,)
    assert active_analyze_geometry_sources(document) == (domain,)


def test_drawing_sources_respect_effective_visibility_and_exclude_fem(
    monkeypatch,
) -> None:
    visible = _Source(1)
    hidden = _Source(2)
    hidden.ViewObject.Visibility = False

    hidden_parent = SimpleNamespace(
        Name="HiddenAssembly",
        ViewObject=SimpleNamespace(Visibility=False),
        getParentGroup=lambda: None,
        getParentGeoFeatureGroup=lambda: None,
    )
    nested = _Source(3)
    nested.getParentGroup = lambda: hidden_parent

    fem_mesh = _Source(4)
    fem_mesh.TypeId = "Fem::FemMeshShapeBaseObjectPython"
    fem_mesh.PropertiesList = ["Shape", "FemMesh"]

    analysis_domain = _Source(5, analysis_domain=True)
    analysis_member = _Source(6)
    analysis = SimpleNamespace(
        Name="Analysis",
        TypeId="Fem::FemAnalysis",
        Group=(analysis_member,),
        ViewObject=SimpleNamespace(Visibility=False),
    )
    document = SimpleNamespace(
        Uid="document-a",
        Objects=(
            visible,
            hidden,
            hidden_parent,
            nested,
            fem_mesh,
            analysis_domain,
            analysis_member,
            analysis,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert active_design_geometry_sources(document) == (visible,)
    assert drawing_source_exclusion_reason(document, visible) is None
    assert drawing_source_exclusion_reason(document, hidden) == "hidden"
    assert drawing_source_exclusion_reason(document, nested) == "hidden"
    assert drawing_source_exclusion_reason(document, fem_mesh) == "analysis_artifact"
    assert (
        drawing_source_exclusion_reason(document, analysis_domain)
        == "analysis_artifact"
    )
    assert (
        drawing_source_exclusion_reason(document, analysis_member)
        == "analysis_artifact"
    )


def test_hidden_responsive_source_is_rejected_before_shape_access(monkeypatch) -> None:
    class _HiddenSource:
        Name = "HiddenBody"
        TypeId = "PartDesign::Body"
        ID = 7
        PropertiesList = ["Shape"]
        VibeCADAnalysisDomain = False
        ViewObject = SimpleNamespace(Visibility=False)

        @property
        def Shape(self):
            raise AssertionError("hidden Drawing sources must not read Shape")

        @staticmethod
        def getParentGeoFeatureGroup():
            return None

        @staticmethod
        def getParentGroup():
            return None

    source = _HiddenSource()
    document = SimpleNamespace(Uid="document-a", Objects=(source,))
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert is_potential_design_geometry_source(document, source) is False


def test_drawing_source_catalog_pages_one_hundred_bodies_without_guessing(
    monkeypatch,
) -> None:
    sources = tuple(SimpleNamespace(Name=f"Body{index:03d}") for index in range(100))
    monkeypatch.setattr(
        drawing_catalog,
        "active_design_geometry_sources",
        lambda _document, **_options: sources,
    )
    monkeypatch.setattr(
        drawing_catalog,
        "drawing_source_catalog_state",
        lambda source: {
            "object_name": source.Name,
            "label": source.Name,
            "type_id": "PartDesign::Body",
            "state_sha256": f"{int(source.Name[-3:]):064x}",
            "shape_type": "Solid",
            "topology": {"solids": 1, "faces": 6, "edges": 12},
            "bounds_size_mm": [1.0, 1.0, 1.0],
        },
    )

    first = drawing_source_catalog_page(object(), offset=0, page_size=48)
    second = drawing_source_catalog_page(
        object(), offset=first["next_offset"], page_size=48
    )
    third = drawing_source_catalog_page(
        object(), offset=second["next_offset"], page_size=48
    )

    assert [page["returned_count"] for page in (first, second, third)] == [48, 48, 4]
    assert [page["next_offset"] for page in (first, second, third)] == [48, 96, None]
    assert third["sources"][-1]["source_target"] == {
        "object_name": "Body099",
    }


def test_drawing_source_catalog_defers_exact_brep_validation_and_hashing(
    monkeypatch,
) -> None:
    source = _CatalogSource(1)
    document = SimpleNamespace(Uid="document-a", Objects=(source,))
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    result = drawing_source_catalog_page(document, offset=0, page_size=48)

    assert source.Shape.validity_checks == 0
    assert result == {
        "source_count": 1,
        "offset": 0,
        "returned_count": 1,
        "next_offset": None,
        "sources": [
            {
                "source_name": "Body",
                "source_target": {"object_name": "Body"},
                "type_id": "PartDesign::Body",
                "shape_type": "Solid",
                "topology": {"solids": 1, "faces": 6, "edges": 12},
                "bounds_size_mm": [10.0, 20.0, 30.0],
                "label": "Bracket",
            }
        ],
    }


def test_shared_design_source_discovery_preserves_exact_validation_by_default(
    monkeypatch,
) -> None:
    source = _CatalogSource(1)
    document = SimpleNamespace(Uid="document-a", Objects=(source,))
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    assert active_design_geometry_sources(document) == ()
    assert source.Shape.validity_checks == 1
    source.Shape.validity_checks = 0
    assert active_design_geometry_sources(document, validate_brep=False) == (source,)
    assert source.Shape.validity_checks == 0


def test_drawing_provider_context_uses_identity_only_for_selected_sources(
    monkeypatch,
) -> None:
    source = _CatalogSource(1)
    document = SimpleNamespace(
        Uid="document-a",
        Objects=(source,),
        getObject=lambda name: source if name == source.Name else None,
    )
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )
    state = {
        "object_name": "Body",
        "label": "Bracket",
        "type_id": "PartDesign::Body",
        "shape_type": "Solid",
        "placement": None,
        "topology": {"solids": 1, "faces": 6, "edges": 12},
        "bounds_size_mm": [10.0, 20.0, 30.0],
    }
    page_options = []
    monkeypatch.setattr(
        drawing_snapshot,
        "drawing_source_catalog_state_page",
        lambda _document, **options: page_options.append(options)
        or {
            "source_count": 1,
            "offset": 0,
            "returned_count": 1,
            "next_offset": None,
            "sources": [dict(state)],
        },
    )
    count, sources = drawing_snapshot._drawing_sources(document)
    selected = drawing_snapshot._selected_sources(
        document,
        {"items": [{"object": {"object_name": "Body"}}]},
    )
    selected_breaks = drawing_snapshot._selected_break_definitions(
        document,
        {"items": [{"object": {"object_name": "Body"}}]},
    )
    selected_drafts = drawing_snapshot._selected_draft_sources(
        document,
        {"items": [{"object": {"object_name": "Body"}}]},
    )

    assert page_options == [
        {"offset": 0, "page_size": 48, "structural_revision": None}
    ]
    assert count == 1
    assert sources == [state]
    identity = {
        "object_name": "Body",
        "label": "Bracket",
        "type_id": "PartDesign::Body",
        "shape_type": "",
        "placement": None,
        "topology": {},
        "bounds_size_mm": None,
        "geometry_details_deferred": True,
    }
    assert selected == [identity]
    assert selected_breaks == [{**identity, "break_details_deferred": True}]
    assert selected_drafts == [{**identity, "draft_details_deferred": True}]
    assert source.Shape.validity_checks == 0


def test_drawing_source_catalog_cache_reuses_only_the_same_document_revision(
    monkeypatch,
) -> None:
    drawing_catalog.invalidate_drawing_source_catalog_cache()
    source = _CatalogSource(1)
    document = SimpleNamespace(
        Uid="document-a",
        Objects=(source,),
        getObject=lambda name: source if name == source.Name else None,
    )
    discoveries = []
    serializations = []
    monkeypatch.setattr(
        drawing_catalog,
        "active_design_geometry_sources",
        lambda _document, **options: discoveries.append(options) or (source,),
    )
    monkeypatch.setattr(
        drawing_catalog,
        "drawing_source_catalog_state",
        lambda candidate: serializations.append(candidate)
        or {
            "object_name": "Body",
            "label": "Bracket",
            "type_id": "PartDesign::Body",
            "shape_type": "Solid",
            "placement": None,
            "topology": {"solids": 1, "faces": 6, "edges": 12},
            "bounds_size_mm": [10.0, 20.0, 30.0],
        },
    )

    first = drawing_source_catalog_page(
        document,
        offset=0,
        page_size=48,
        structural_revision=7,
    )
    first["sources"][0]["label"] = "Caller mutation"
    repeated = drawing_source_catalog_page(
        document,
        offset=0,
        page_size=48,
        structural_revision=7,
    )
    changed = drawing_source_catalog_page(
        document,
        offset=0,
        page_size=48,
        structural_revision=8,
    )

    assert repeated["sources"][0]["label"] == "Bracket"
    assert changed == repeated
    assert discoveries == [
        {"validate_brep": False},
        {"validate_brep": False},
    ]
    assert serializations == [source, source]


def test_drawing_provider_context_requests_revision_cached_source_page(
    monkeypatch,
) -> None:
    page = {
        "source_count": 1,
        "offset": 0,
        "returned_count": 1,
        "next_offset": None,
        "sources": [{"object_name": "Body"}],
    }
    observed = []
    monkeypatch.setattr(
        drawing_snapshot,
        "drawing_source_catalog_state_page",
        lambda document, **options: observed.append((document, options)) or page,
        raising=False,
    )
    document = object()

    assert drawing_snapshot._drawing_sources(
        document,
        structural_revision=12,
    ) == (1, [{"object_name": "Body"}])
    assert observed == [
        (
            document,
            {"offset": 0, "page_size": 48, "structural_revision": 12},
        )
    ]


def test_required_cached_drawing_page_never_falls_back_to_inline_discovery(
    monkeypatch,
) -> None:
    drawing_catalog.invalidate_drawing_source_catalog_cache()
    discoveries = []
    document = SimpleNamespace(Uid="document-a", Objects=())
    monkeypatch.setattr(
        drawing_catalog,
        "active_design_geometry_sources",
        lambda *_args, **_kwargs: discoveries.append(True) or (),
    )

    with pytest.raises(drawing_catalog.DrawingSourceCatalogNotReady):
        drawing_source_catalog_page(
            document,
            offset=0,
            page_size=48,
            structural_revision=7,
            require_cached=True,
        )

    assert discoveries == []


def test_completed_drawing_catalog_serves_every_page_without_rescanning(
    monkeypatch,
) -> None:
    drawing_catalog.invalidate_drawing_source_catalog_cache()
    discoveries = []
    monkeypatch.setattr(
        drawing_catalog,
        "active_design_geometry_sources",
        lambda *_args, **_kwargs: discoveries.append(True) or (),
    )
    sources = [
        {
            "object_name": f"Body{index}",
            "label": f"Body {index}",
            "type_id": "PartDesign::Body",
            "shape_type": "Solid",
            "placement": None,
            "topology": {"solids": 1, "faces": 6, "edges": 12},
            "bounds_size_mm": [1.0, 1.0, 1.0],
        }
        for index in range(100)
    ]

    drawing_catalog.cache_drawing_source_catalog_state("document-a", 7, sources)
    document = SimpleNamespace(Uid="document-a")
    first = drawing_source_catalog_page(
        document,
        offset=0,
        page_size=48,
        structural_revision=7,
        require_cached=True,
    )
    third = drawing_source_catalog_page(
        document,
        offset=96,
        page_size=48,
        structural_revision=7,
        require_cached=True,
    )

    assert first["source_count"] == 100
    assert first["returned_count"] == 48
    assert first["next_offset"] == 48
    assert third["returned_count"] == 4
    assert third["next_offset"] is None
    assert discoveries == []
