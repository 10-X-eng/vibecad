# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real-document gate for separate and shared multipart analysis domains."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADAnalyzeGeometryGui import AnalyzeGeometryBrowser
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources
from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot


def _parts(document):
    left = document.addObject("Part::Box", "LeftColumn")
    left.Length = 20.0
    left.Width = 40.0
    left.Height = 100.0
    right = document.addObject("Part::Box", "RightColumn")
    right.Length = 20.0
    right.Width = 40.0
    right.Height = 100.0
    right.Placement.Base = App.Vector(180.0, 0.0, 0.0)
    deck = document.addObject("Part::Box", "Deck")
    deck.Length = 200.0
    deck.Width = 40.0
    deck.Height = 20.0
    deck.Placement.Base = App.Vector(0.0, 0.0, 100.0)
    assert document.recompute() is not False
    return left, right, deck


def _create(document, sources, mode: str, label: str):
    browser = AnalyzeGeometryBrowser()
    browser.refresh(document)
    assert browser.domain_button.isEnabled()
    result = browser.create_domain(
        [str(source.Name) for source in sources],
        mode,
        label,
    )
    assert not browser.domain_button.isEnabled(), tuple(browser._sources)
    domain = document.getObject(str(result["domain"]["object_name"]))
    assert domain is not None
    browser.deleteLater()
    return domain, result


def _verify_sources(document, names, expected_types):
    sources = tuple(document.getObject(name) for name in names)
    assert all(source is not None for source in sources)
    assert tuple(source.TypeId for source in sources) == expected_types
    assert all(len(source.Shape.Solids) == 1 for source in sources)
    return sources


def _run() -> None:
    document = None
    shared_document = None
    temporary = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-solid-domain-"
        )
        separate_path = Path(temporary.name) / "separate-domain.FCStd"
        shared_path = Path(temporary.name) / "shared-domain.FCStd"

        document = App.newDocument("SeparateSolidDomainGate")
        document.UndoMode = 1
        sources = _parts(document)
        source_names = tuple(str(source.Name) for source in sources)
        source_types = tuple(str(source.TypeId) for source in sources)
        source_volumes = tuple(float(source.Shape.Volume) for source in sources)
        domain, result = _create(document, sources, "separate", "Bridge domain")
        domain_name = str(domain.Name)

        assert domain.TypeId == "Part::Compound"
        assert tuple(domain.Links) == sources
        assert tuple(domain.AnalysisSources) == sources
        assert domain.AnalysisInterfaceMode == "separate"
        assert bool(domain.VibeCADAnalysisDomain)
        assert len(domain.Shape.Solids) == 3
        assert len(document.Objects) == 5
        timeline = document.getObject("VibeCADTimeline")
        assert timeline is not None and timeline.TypeId == "App::DocumentTimeline"
        assert tuple(float(source.Shape.Volume) for source in sources) == source_volumes
        assert result["source_count"] == 3
        assert result["solid_count"] == 3
        assert "next" not in result
        assert active_analyze_geometry_sources(document) == (domain,)

        sources[2].Height = 30.0
        assert document.recompute() is not False
        assert len(domain.Shape.Solids) == 3
        assert float(domain.Shape.Volume) > float(result["volume_mm3"])

        document.saveAs(str(separate_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(separate_path))
        sources = _verify_sources(document, source_names, source_types)
        domain = document.getObject(domain_name)
        assert domain is not None and domain.TypeId == "Part::Compound"
        assert tuple(domain.AnalysisSources) == sources
        assert len(domain.Shape.Solids) == 3
        assert active_analyze_geometry_sources(document) == (domain,)

        shared_document = App.newDocument("SharedSolidDomainGate")
        shared_document.UndoMode = 1
        shared_sources = _parts(shared_document)
        shared_names = tuple(str(source.Name) for source in shared_sources)
        shared_types = tuple(str(source.TypeId) for source in shared_sources)
        shared_domain, shared_result = _create(
            shared_document,
            shared_sources,
            "shared",
            "Bonded bridge domain",
        )
        shared_name = str(shared_domain.Name)
        assert tuple(shared_domain.AnalysisSources) == shared_sources
        assert shared_domain.AnalysisInterfaceMode == "shared"
        assert bool(shared_domain.VibeCADAnalysisDomain)
        assert len(shared_domain.Shape.Solids) == 3
        assert len(shared_domain.Shape.CompSolids) == 1
        assert shared_result["compsolid_count"] == 1
        assert active_analyze_geometry_sources(shared_document) == (shared_domain,)
        shared_source_state = build_analyze_snapshot(shared_document)[
            "geometry_sources"
        ][0]
        assert shared_source_state["interface_mode"] == "shared"
        assert shared_source_state["all_solids_conformal"] is True
        shared_document.saveAs(str(shared_path))
        App.closeDocument(shared_document.Name)
        shared_document = App.openDocument(str(shared_path))
        shared_sources = _verify_sources(
            shared_document,
            shared_names,
            shared_types,
        )
        shared_domain = shared_document.getObject(shared_name)
        assert shared_domain is not None
        assert tuple(shared_domain.AnalysisSources) == shared_sources
        assert len(shared_domain.Shape.CompSolids) == 1
        assert active_analyze_geometry_sources(shared_document) == (shared_domain,)
        reopened_source_state = build_analyze_snapshot(shared_document)[
            "geometry_sources"
        ][0]
        assert reopened_source_state["all_solids_conformal"] is True

        print(
            "VIBECAD_NATIVE_ANALYZE_SOLID_DOMAIN_GUI_OK "
            "separate_sources=3 separate_solids=3 shared_sources=3 "
            "shared_compsolids=1 conformal_state=true save_reopen=true recompute=true"
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        for candidate in (shared_document, document):
            if candidate is not None and App.getDocument(candidate.Name) is not None:
                App.closeDocument(candidate.Name)
        if temporary is not None:
            temporary.cleanup()
        QtCore.QTimer.singleShot(0, lambda: QtWidgets.QApplication.exit(exit_code))


QtCore.QTimer.singleShot(0, _run)
application = QtWidgets.QApplication.instance()
if application is None:
    raise RuntimeError("The solid-domain GUI gate requires a QApplication.")
if QtCore.QThread.currentThread().loopLevel() == 0:
    sys.exit(application.exec())
