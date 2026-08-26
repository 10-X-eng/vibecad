# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace
import sys

import VibeCADNativeDrawingSourceCatalog as drawing_catalog
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources
from VibeCADNativeDrawingSourceCatalog import drawing_source_catalog_page
from VibeCADNativeGeometrySources import active_design_geometry_sources


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
        self.Shape = _Shape()
        self.VibeCADAnalysisDomain = analysis_domain
        self.AnalysisSources = ()

    @staticmethod
    def getParentGeoFeatureGroup():
        return None

    @staticmethod
    def isDerivedFrom(_type_id: str) -> bool:
        return False


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


def test_drawing_source_catalog_pages_one_hundred_bodies_without_guessing(
    monkeypatch,
) -> None:
    sources = tuple(SimpleNamespace(Name=f"Body{index:03d}") for index in range(100))
    monkeypatch.setattr(
        drawing_catalog,
        "active_design_geometry_sources",
        lambda _document: sources,
    )
    monkeypatch.setattr(
        drawing_catalog,
        "drawing_source_state",
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
