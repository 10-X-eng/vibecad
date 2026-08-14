/***************************************************************************
 *   Copyright (c) 2016 WandererFan <wandererfan@gmail.com>                *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <iterator>
#include <limits>
#include <ranges>
#include <string>
#include <unordered_set>


#include <App/Document.h>
#include <App/DocumentPy.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentObjectPy.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ViewProvider.h>
#include <Gui/PythonWrapper.h>
#include <Mod/Part/App/OCCError.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawPagePy.h>
#include <Mod/TechDraw/App/DrawGeomHatch.h>
#include <Mod/TechDraw/App/DrawHatch.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawRichAnno.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>
#include <Mod/TechDraw/App/DrawViewBalloon.h>
#include <Mod/TechDraw/App/DrawViewSymbol.h>
#include <Mod/TechDraw/App/DrawWeldSymbol.h>
#include <Mod/TechDraw/App/DrawViewPy.h>  // generated from DrawViewPy.xml
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/DrawViewPartPy.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/LineFormat.h>
#include <Mod/TechDraw/App/LineGenerator.h>
#include <Mod/TechDraw/App/LineGroup.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "BalloonBuilder.h"
#include "CircleCenterLineBuilder.h"
#include "CommandExtensionDims.h"
#include "CosmeticCurveBuilder.h"
#include "CosmeticLineBuilder.h"
#include "CosmeticVertexBuilder.h"
#include "GeneralCenterLineBuilder.h"
#include "DimensionBuilder.h"
#include "DimensionTextBuilder.h"
#include "FormatBuilder.h"
#include "FrameVisibilityBuilder.h"
#include "HatchBuilder.h"
#include "LeaderLineBuilder.h"
#include "RichAnnotationBuilder.h"
#include "SurfaceFinishSymbolBuilder.h"
#include "WeldSymbolBuilder.h"
#include "LineAttributeBuilder.h"
#include "LineLengthBuilder.h"
#include "MDIViewPage.h"
#include "PageUpdateBuilder.h"
#include "PreferencesGui.h"
#include "ThreadRepresentationBuilder.h"
#include "QGIView.h"
#include "QGSPage.h"
#include "ViewProviderPage.h"
#include "ViewProviderDrawingView.h"
#include "ViewLockBuilder.h"
#include "PagePrinter.h"
#include "TaskSelectLineAttributes.h"


namespace TechDrawGui {

class Module : public Py::ExtensionModule<Module>
{
public:
    Module() : Py::ExtensionModule<Module>("TechDrawGui")
    {
       add_varargs_method("export", &Module::exporter,
            "TechDraw hook for FC Gui exporter."
       );
       add_varargs_method("exportPageAsPdf", &Module::exportPageAsPdf,
            "exportPageAsPdf(DrawPageObject, FilePath) -- print page as Pdf to file."
        );
       add_varargs_method("exportPageAsSvg", &Module::exportPageAsSvg,
            "exportPageAsSvg(DrawPageObject, FilePath) -- print page as Svg to file."
        );
        add_varargs_method(
            "printAllDrawingPages",
            &Module::printAllDrawingPages,
            "printAllDrawingPages(Document, validateBeforePrint) -- ask the human "
            "to authorize printing every current-History Drawing page, validating "
            "the exact source immediately before submission."
        );
        add_varargs_method(
            "drawingPagePresentation",
            &Module::drawingPagePresentation,
            "drawingPagePresentation(DrawPageObject) -- report whether one exact "
            "Drawing page tab is open and human-active without changing it."
        );
        add_varargs_method(
            "showDrawingPage",
            &Module::showDrawingPage,
            "showDrawingPage(DrawPageObject) -- open and activate one exact Drawing "
            "page through the same ViewProvider path as Show Drawing."
        );
        add_varargs_method("addQGIToView", &Module::addQGIToView,
            "addQGIToView(View, QGraphicsItem) -- insert graphics item into view's graphic."
        );
        add_varargs_method("addQGObjToView", &Module::addQGObjToView,
            "addQGObjToView(View, QGraphicsObject) -- insert graphics object into view's graphic. Use for QGraphicsItems that have QGraphicsObject as base class."
        );
        add_varargs_method("addQGIToScene", &Module::addQGIToScene,
            "addQGIToScene(Page, QGraphicsItem) -- insert graphics item into Page's scene."
        );
        add_varargs_method("addQGObjToScene", &Module::addQGObjToScene,
            "addQGObjToScene(Page, QGraphicsObject) -- insert graphics object into Page's scene. Use for QGraphicsItems that have QGraphicsObject as base class."
        );
        add_varargs_method("getSceneForPage", &Module::getSceneForPage,
            "QGSPage = getSceneForPage(page) -- get the scene for a DrawPage."
        );
        add_varargs_method("getViewStackState", &Module::getViewStackState,
            "getViewStackState(DrawViewObject) -- return the exact graphical stacking scope."
        );
       add_varargs_method("stackView", &Module::stackView,
            "stackView(DrawViewObject, operation) -- apply top, bottom, up, or down stacking."
        );
       add_varargs_method(
            "validateProjectedDimension",
            &Module::validateProjectedDimension,
            "validateProjectedDimension(view, type, subelements, allow_approximate) "
            "-- validate exact projected references without changing the document."
        );
       add_varargs_method(
            "createProjectedDimension",
            &Module::createProjectedDimension,
            "createProjectedDimension(view, type, subelements, allow_approximate, x_mm, y_mm) "
            "-- validate and create one projected dimension without owning a transaction."
        );
       add_varargs_method(
            "validateDrawingDimensionSeries",
            &Module::validateDrawingDimensionSeries,
            "validateDrawingDimensionSeries(view, kind, direction, vertices) -- "
            "validate a chain or coordinate series over 3 to 64 exact projected "
            "VertexN references without changing the document."
        );
       add_varargs_method(
            "createDrawingDimensionSeries",
            &Module::createDrawingDimensionSeries,
            "createDrawingDimensionSeries(view, kind, direction, vertices) -- "
            "create and timeline-group one validated horizontal, vertical, or "
            "oblique chain or coordinate dimension series without owning a transaction."
        );
       add_varargs_method(
            "removeDrawingDimensionSeriesCarriers",
            &Module::removeDrawingDimensionSeriesCarriers,
            "removeDrawingDimensionSeriesCarriers(view, edge_tags, vertex_tags) -- "
            "remove exact aborted dimension-series carriers and refresh projected geometry."
        );
       add_varargs_method(
            "validateProjectedExtent",
            &Module::validateProjectedExtent,
            "validateProjectedExtent(view, type, subelements) -- validate a whole-view "
            "or exact-edge projected extent without changing the document."
        );
       add_varargs_method(
            "createProjectedExtent",
            &Module::createProjectedExtent,
            "createProjectedExtent(view, type, subelements, x_mm, y_mm) -- create one "
            "whole-view or exact-edge projected extent without owning a transaction."
        );
       add_varargs_method(
            "validateProjectedChamfer",
            &Module::validateProjectedChamfer,
            "validateProjectedChamfer(view, type, vertices) -- validate exactly two "
            "projected chamfer vertices without changing the document."
        );
       add_varargs_method(
            "createProjectedChamfer",
            &Module::createProjectedChamfer,
            "createProjectedChamfer(view, type, vertices, x_mm, y_mm) -- create one "
            "size-and-angle chamfer dimension without owning a transaction."
        );
       add_varargs_method(
            "validateProjectedArcLength",
            &Module::validateProjectedArcLength,
            "validateProjectedArcLength(view, edge) -- validate one projected "
            "open circular arc without changing the document."
        );
       add_varargs_method(
            "createProjectedArcLength",
            &Module::createProjectedArcLength,
            "createProjectedArcLength(view, edge, x_mm, y_mm) -- create one "
            "arc-length dimension without owning a transaction."
        );
       add_varargs_method(
            "repairProjectedDimension",
            &Module::repairProjectedDimension,
            "repairProjectedDimension(dimension, view, subelements, allow_approximate) "
            "-- replace one standard projected dimension's references without owning "
            "a transaction."
        );
       add_varargs_method(
            "repairProjectedExtent",
            &Module::repairProjectedExtent,
            "repairProjectedExtent(dimension, view, subelements) -- replace one "
            "projected extent's whole-view or exact-edge target without owning a transaction."
        );
       add_varargs_method(
            "repairProjectedChamfer",
            &Module::repairProjectedChamfer,
            "repairProjectedChamfer(dimension, view, vertices) -- replace one "
            "projected chamfer's exact vertices without owning a transaction."
        );
       add_varargs_method(
            "repairProjectedArcLength",
            &Module::repairProjectedArcLength,
            "repairProjectedArcLength(dimension, view, edge) -- replace one "
            "projected arc-length source without owning a transaction."
        );
       add_varargs_method(
            "defaultDimensionFormatSpec",
            &Module::defaultDimensionFormatSpec,
            "defaultDimensionFormatSpec(dimension) -- return the dimension's host "
            "default value-format specification without changing the document."
        );
       add_varargs_method(
            "validateDrawingDimensionText",
            &Module::validateDrawingDimensionText,
            "validateDrawingDimensionText(dimensions, operation, repetition_text) "
            "-- plan exact prefix or decimal changes for 1 to 64 dimensions."
        );
       add_varargs_method(
            "changeDrawingDimensionText",
            &Module::changeDrawingDimensionText,
            "changeDrawingDimensionText(dimensions, operation, repetition_text) "
            "-- apply planned prefix or decimal changes without owning a transaction."
        );
       add_varargs_method(
            "drawingFrameVisibilityAvailable",
            &Module::drawingFrameVisibilityAvailable,
            "drawingFrameVisibilityAvailable() -- report whether explicit manual "
            "Drawing frame presentation is available."
        );
       add_varargs_method(
            "drawingFrameVisibility",
            &Module::drawingFrameVisibility,
            "drawingFrameVisibility(page) -- return exact transient frame visibility "
            "for the human-active Drawing page."
        );
       add_varargs_method(
            "validateDrawingFrameVisibility",
            &Module::validateDrawingFrameVisibility,
            "validateDrawingFrameVisibility(page, visible) -- plan explicit frame "
            "visibility without changing the page scene."
        );
       add_varargs_method(
            "changeDrawingFrameVisibility",
            &Module::changeDrawingFrameVisibility,
            "changeDrawingFrameVisibility(page, visible) -- set explicit transient "
            "frame visibility without changing the document."
        );
       add_varargs_method(
            "drawingGridVisibility",
            &Module::drawingGridVisibility,
            "drawingGridVisibility(page) -- return exact grid visibility for the "
            "human-active Drawing page."
        );
       add_varargs_method(
            "validateDrawingGridVisibility",
            &Module::validateDrawingGridVisibility,
            "validateDrawingGridVisibility(page, visible) -- plan explicit grid "
            "visibility without changing the page scene."
        );
       add_varargs_method(
            "changeDrawingGridVisibility",
            &Module::changeDrawingGridVisibility,
            "changeDrawingGridVisibility(page, visible) -- set explicit Drawing "
            "grid visibility."
        );
       add_varargs_method(
            "drawingHiddenEdgeVisibility",
            &Module::drawingHiddenEdgeVisibility,
            "drawingHiddenEdgeVisibility(view) -- return whether hidden projected "
            "edges are temporarily shown."
        );
       add_varargs_method(
            "validateDrawingHiddenEdgeVisibility",
            &Module::validateDrawingHiddenEdgeVisibility,
            "validateDrawingHiddenEdgeVisibility(view, visible) -- plan explicit "
            "hidden-edge presentation."
        );
       add_varargs_method(
            "changeDrawingHiddenEdgeVisibility",
            &Module::changeDrawingHiddenEdgeVisibility,
            "changeDrawingHiddenEdgeVisibility(view, visible) -- explicitly show "
            "or hide hidden projected edges."
        );
       add_varargs_method(
            "drawingKeepUpdated",
            &Module::drawingKeepUpdated,
            "drawingKeepUpdated(page) -- return one exact Drawing page update policy."
        );
       add_varargs_method(
            "validateDrawingKeepUpdated",
            &Module::validateDrawingKeepUpdated,
            "validateDrawingKeepUpdated(page, keep_updated) -- plan an explicit "
            "Drawing page update policy without changing the document."
        );
       add_varargs_method(
            "changeDrawingKeepUpdated",
            &Module::changeDrawingKeepUpdated,
            "changeDrawingKeepUpdated(page, keep_updated) -- apply an explicit "
            "Drawing page update policy without owning a transaction."
        );
       add_varargs_method(
            "drawingHatchDefaults",
            &Module::drawingHatchDefaults,
            "drawingHatchDefaults() -- return bounded configured image and PAT "
            "pattern defaults and the PAT pattern catalog."
        );
       add_varargs_method(
            "validateDrawingImageHatch",
            &Module::validateDrawingImageHatch,
            "validateDrawingImageHatch(view, faces, pattern_file, scale, rotation, "
            "offset_x, offset_y, red, green, blue) -- validate one exact image hatch."
        );
       add_varargs_method(
            "createDrawingImageHatch",
            &Module::createDrawingImageHatch,
            "createDrawingImageHatch(view, faces, pattern_file, scale, rotation, "
            "offset_x, offset_y, red, green, blue) -- create one exact image hatch."
        );
       add_varargs_method(
            "validateDrawingGeometricHatch",
            &Module::validateDrawingGeometricHatch,
            "validateDrawingGeometricHatch(view, faces, pattern_file, pattern_name, "
            "scale, rotation, offset_x, offset_y, width, red, green, blue) -- "
            "validate one exact geometric hatch."
        );
       add_varargs_method(
            "createDrawingGeometricHatch",
            &Module::createDrawingGeometricHatch,
            "createDrawingGeometricHatch(view, faces, pattern_file, pattern_name, "
            "scale, rotation, offset_x, offset_y, width, red, green, blue) -- "
            "create one exact geometric hatch."
        );
       add_varargs_method(
            "drawingRichAnnotationDefaults",
            &Module::drawingRichAnnotationDefaults,
            "drawingRichAnnotationDefaults() -- return the human command's semantic "
            "width and complete frame defaults."
        );
       add_varargs_method(
            "validateDrawingRichAnnotation",
            &Module::validateDrawingRichAnnotation,
            "validateDrawingRichAnnotation(page, owner_or_none, content_kind, content, "
            "label, x_mm, y_mm, maximum_width_mm, frame_visible, frame_width_mm, "
            "frame_style, red, green, blue) -- canonicalize and validate one exact "
            "resource-free rich annotation without changing the document."
        );
       add_varargs_method(
            "createDrawingRichAnnotation",
            &Module::createDrawingRichAnnotation,
            "createDrawingRichAnnotation(page, owner_or_none, content_kind, content, "
            "label, x_mm, y_mm, maximum_width_mm, frame_visible, frame_width_mm, "
            "frame_style, red, green, blue) -- create one exact rich annotation "
            "without owning a transaction."
        );
       add_varargs_method(
            "inspectDrawingRichAnnotationContent",
            &Module::inspectDrawingRichAnnotationContent,
            "inspectDrawingRichAnnotationContent(annotation) -- return bounded hashes, "
            "counts, and preview for one stored rich annotation."
        );
       add_varargs_method(
            "validateDrawingSurfaceFinishSymbol",
            &Module::validateDrawingSurfaceFinishSymbol,
            "validateDrawingSurfaceFinishSymbol(page, owner_or_none, x_mm, y_mm, "
            "standard, symbol_type, method, allowance, lay, iso_roughness, "
            "sampling_length, minimum_grade, maximum_grade, rotation, label) -- "
            "validate one canonical surface-finish symbol without mutation."
        );
       add_varargs_method(
            "createDrawingSurfaceFinishSymbol",
            &Module::createDrawingSurfaceFinishSymbol,
            "createDrawingSurfaceFinishSymbol(...) -- create one canonical "
            "surface-finish symbol without owning a transaction."
        );
       add_varargs_method(
            "drawingWeldSymbolCatalog",
            &Module::drawingWeldSymbolCatalog,
            "drawingWeldSymbolCatalog() -- return the bounded embedded weld SVG catalog."
        );
       add_varargs_method(
            "validateDrawingWeldSymbol",
            &Module::validateDrawingWeldSymbol,
            "validateDrawingWeldSymbol(target, create, all_around, field_weld, "
            "alternating, tail, arrow_left, arrow_center, arrow_right, arrow_key, "
            "other_left, other_center, other_right, other_key, label) -- validate "
            "one exact weld-symbol create or edit."
        );
       add_varargs_method(
            "changeDrawingWeldSymbol",
            &Module::changeDrawingWeldSymbol,
            "changeDrawingWeldSymbol(...) -- create or edit one exact weld symbol "
            "without owning a transaction."
        );
       add_varargs_method(
            "drawingLeaderDefaults",
            &Module::drawingLeaderDefaults,
            "drawingLeaderDefaults() -- return the human Leader Line command's "
            "symbols, behavior, and complete line-style defaults."
        );
       add_varargs_method(
            "validateDrawingLeaderLine",
            &Module::validateDrawingLeaderLine,
            "validateDrawingLeaderLine(page, owner, page_points, label, start_symbol, "
            "end_symbol, scalable, auto_horizontal, rotates_with_owner, line_width_mm, "
            "line_style, red, green, blue) -- plan one exact owner-linked Leader Line."
        );
       add_varargs_method(
            "createDrawingLeaderLine",
            &Module::createDrawingLeaderLine,
            "createDrawingLeaderLine(page, owner, page_points, label, start_symbol, "
            "end_symbol, scalable, auto_horizontal, rotates_with_owner, line_width_mm, "
            "line_style, red, green, blue) -- create one exact owner-linked Leader Line "
            "without owning a transaction."
        );
       add_varargs_method(
            "validateDrawingFormatCustomization",
            &Module::validateDrawingFormatCustomization,
            "validateDrawingFormatCustomization(target, value) -- validate a complete "
            "dimension format or literal balloon text and return the host preview "
            "without changing the document."
        );
       add_varargs_method(
            "applyDrawingFormatCustomization",
            &Module::applyDrawingFormatCustomization,
            "applyDrawingFormatCustomization(target, value) -- replace one live "
            "dimension format or balloon text without owning a transaction."
        );
       add_varargs_method(
            "validateDrawingCircleCenterLines",
            &Module::validateDrawingCircleCenterLines,
            "validateDrawingCircleCenterLines(view, edges) -- validate 1 to 32 "
            "exact projected circles or circular arcs without changing the document."
        );
       add_varargs_method(
            "createDrawingCircleCenterLines",
            &Module::createDrawingCircleCenterLines,
            "createDrawingCircleCenterLines(view, edges) -- create two persistent "
            "cosmetic centerline edges for each exact projected circle or arc "
            "without owning a transaction."
        );
       add_varargs_method(
            "validateDrawingBoltCircleCenterLines",
            &Module::validateDrawingBoltCircleCenterLines,
            "validateDrawingBoltCircleCenterLines(view, edges) -- derive one bolt "
            "circle from the first three of 3 to 32 exact projected circular "
            "edges and plan one radial center mark per edge without mutation."
        );
       add_varargs_method(
            "createDrawingBoltCircleCenterLines",
            &Module::createDrawingBoltCircleCenterLines,
            "createDrawingBoltCircleCenterLines(view, edges) -- create one "
            "persistent bolt-pattern circle and one radial cosmetic center mark "
            "per exact projected circular edge without owning a transaction."
        );
       add_varargs_method(
            "drawingPersistentCosmeticCircle",
            &Module::drawingPersistentCosmeticCircle,
            "drawingPersistentCosmeticCircle(view, tag) -- resolve one exact "
            "persistent cosmetic circle by stable tag."
        );
       add_varargs_method(
            "validateDrawingThreadSide",
            &Module::validateDrawingThreadSide,
            "validateDrawingThreadSide(view, kind, edges) -- plan one exact hole "
            "or bolt side thread representation from two projected parallel edges."
        );
       add_varargs_method(
            "createDrawingThreadSide",
            &Module::createDrawingThreadSide,
            "createDrawingThreadSide(view, kind, edges) -- create persistent "
            "host-styled cosmetic lines for one exact side thread representation."
        );
       add_varargs_method(
            "validateDrawingThreadBottom",
            &Module::validateDrawingThreadBottom,
            "validateDrawingThreadBottom(view, kind, circles) -- plan hole or bolt "
            "bottom thread arcs for 1 to 32 exact projected full circles."
        );
       add_varargs_method(
            "createDrawingThreadBottom",
            &Module::createDrawingThreadBottom,
            "createDrawingThreadBottom(view, kind, circles) -- create one persistent "
            "host-styled cosmetic thread arc per exact projected full circle."
        );
       add_varargs_method(
            "drawingPersistentCosmeticArc",
            &Module::drawingPersistentCosmeticArc,
            "drawingPersistentCosmeticArc(view, tag) -- resolve one exact "
            "persistent cosmetic circular arc by stable tag."
        );
       add_varargs_method(
            "validateDrawingCosmeticVertexPoint",
            &Module::validateDrawingCosmeticVertexPoint,
            "validateDrawingCosmeticVertexPoint(view, x, y) -- validate one "
            "explicit unscaled X/Y point in Drawing-view coordinates without mutation."
        );
       add_varargs_method(
            "createDrawingCosmeticVertexPoint",
            &Module::createDrawingCosmeticVertexPoint,
            "createDrawingCosmeticVertexPoint(view, x, y) -- create one durable "
            "cosmetic vertex at an explicit unscaled Drawing-view point."
        );
       add_varargs_method(
            "validateDrawingVertexIntersections",
            &Module::validateDrawingVertexIntersections,
            "validateDrawingVertexIntersections(view, edges) -- derive every "
            "cosmetic vertex from exactly two intersecting projected edges "
            "without changing the document."
        );
       add_varargs_method(
            "createDrawingVertexIntersections",
            &Module::createDrawingVertexIntersections,
            "createDrawingVertexIntersections(view, edges) -- create one durable "
            "cosmetic vertex at every intersection of two exact projected edges."
        );
       add_varargs_method(
            "validateDrawingMidpointVertices",
            &Module::validateDrawingMidpointVertices,
            "validateDrawingMidpointVertices(view, edges) -- derive one canonical "
            "cosmetic vertex at the midpoint of each exact projected edge."
        );
       add_varargs_method(
            "createDrawingMidpointVertices",
            &Module::createDrawingMidpointVertices,
            "createDrawingMidpointVertices(view, edges) -- create one durable "
            "cosmetic midpoint vertex for each exact projected edge."
        );
       add_varargs_method(
            "validateDrawingQuadrantVertices",
            &Module::validateDrawingQuadrantVertices,
            "validateDrawingQuadrantVertices(view, edges) -- derive three ordered "
            "canonical quarter-parameter vertices for each exact projected edge."
        );
       add_varargs_method(
            "createDrawingQuadrantVertices",
            &Module::createDrawingQuadrantVertices,
            "createDrawingQuadrantVertices(view, edges) -- create three durable "
            "quarter-parameter cosmetic vertices for each exact projected edge."
        );
       add_varargs_method(
            "validateDrawingOffsetVertex",
            &Module::validateDrawingOffsetVertex,
            "validateDrawingOffsetVertex(view, vertex, x, y) -- derive one "
            "cosmetic vertex at an explicit unscaled X/Y offset from an exact "
            "projected vertex without mutation."
        );
       add_varargs_method(
            "createDrawingOffsetVertex",
            &Module::createDrawingOffsetVertex,
            "createDrawingOffsetVertex(view, vertex, x, y) -- create one durable "
            "cosmetic vertex at the exact host-derived offset position."
        );
       add_varargs_method(
            "drawingPersistentCosmeticVertex",
            &Module::drawingPersistentCosmeticVertex,
            "drawingPersistentCosmeticVertex(view, tag) -- resolve one exact "
            "persistent cosmetic vertex by durable tag."
        );
       add_varargs_method(
            "drawingCosmeticVertices",
            &Module::drawingCosmeticVertices,
            "drawingCosmeticVertices(view) -- return every exact persistent "
            "cosmetic vertex in durable property-list order."
        );
       add_varargs_method(
            "validateDrawingGeneralCenterLine",
            &Module::validateDrawingGeneralCenterLine,
            "validateDrawingGeneralCenterLine(view, kind, sources) -- derive one "
            "face, two-edge, or two-vertex centerline using host defaults."
        );
       add_varargs_method(
            "createDrawingGeneralCenterLine",
            &Module::createDrawingGeneralCenterLine,
            "createDrawingGeneralCenterLine(view, kind, sources) -- create one "
            "persistent host-styled centerline from exact projected geometry."
        );
       add_varargs_method(
            "drawingPersistentGeneralCenterLine",
            &Module::drawingPersistentGeneralCenterLine,
            "drawingPersistentGeneralCenterLine(view, tag) -- resolve one exact "
            "persistent centerline by durable tag."
        );
       add_varargs_method(
            "drawingGeneralCenterLines",
            &Module::drawingGeneralCenterLines,
            "drawingGeneralCenterLines(view) -- return persistent face, two-edge, "
            "and two-vertex centerlines in durable property-list order."
        );
       add_varargs_method(
            "validateDrawingCosmeticCurve",
            &Module::validateDrawingCosmeticCurve,
            "validateDrawingCosmeticCurve(view, kind, vertices, radius) -- "
            "derive one exact host-styled cosmetic circle or arc without mutation."
        );
       add_varargs_method(
            "createDrawingCosmeticCurve",
            &Module::createDrawingCosmeticCurve,
            "createDrawingCosmeticCurve(view, kind, vertices, radius) -- create "
            "one durable cosmetic circle or arc from exact projected vertices."
        );
       add_varargs_method(
            "drawingPersistentCosmeticCurve",
            &Module::drawingPersistentCosmeticCurve,
            "drawingPersistentCosmeticCurve(view, tag) -- resolve one exact "
            "persistent cosmetic circle or arc by durable tag."
        );
       add_varargs_method(
            "drawingCosmeticCurves",
            &Module::drawingCosmeticCurves,
            "drawingCosmeticCurves(view) -- return every persistent cosmetic "
            "circle and circular arc in durable property-list order."
        );
       add_varargs_method(
            "validateDrawingCosmeticLine",
            &Module::validateDrawingCosmeticLine,
            "validateDrawingCosmeticLine(view, construction, edge, vertex) -- "
            "derive one exact host-styled parallel or perpendicular cosmetic line."
        );
       add_varargs_method(
            "createDrawingCosmeticLine",
            &Module::createDrawingCosmeticLine,
            "createDrawingCosmeticLine(view, construction, edge, vertex) -- "
            "create one durable parallel or perpendicular cosmetic line."
        );
       add_varargs_method(
            "validateDrawingTwoPointCosmeticLine",
            &Module::validateDrawingTwoPointCosmeticLine,
            "validateDrawingTwoPointCosmeticLine(view, vertices) -- derive one "
            "straight cosmetic line from two exact projected vertices."
        );
       add_varargs_method(
            "createDrawingTwoPointCosmeticLine",
            &Module::createDrawingTwoPointCosmeticLine,
            "createDrawingTwoPointCosmeticLine(view, vertices) -- create one "
            "persistent host-styled line through two exact projected vertices."
        );
       add_varargs_method(
            "drawingPersistentCosmeticLine",
            &Module::drawingPersistentCosmeticLine,
            "drawingPersistentCosmeticLine(view, tag) -- resolve one exact "
            "persistent straight cosmetic line by durable tag."
        );
       add_varargs_method(
            "drawingCosmeticLines",
            &Module::drawingCosmeticLines,
            "drawingCosmeticLines(view) -- return every persistent straight "
            "cosmetic line in durable property-list order."
        );
       add_varargs_method(
            "currentLineDefaults",
            &Module::currentLineDefaults,
            "currentLineDefaults() -- return the exact session defaults shown by "
            "Select Line Attributes without changing the document or GUI."
        );
       add_varargs_method(
            "drawingLineAttributes",
            &Module::drawingLineAttributes,
            "drawingLineAttributes(view) -- return stable cosmetic-edge and "
            "centerline targets and their exact persistent formats."
        );
       add_varargs_method(
            "changeDrawingLineAttributes",
            &Module::changeDrawingLineAttributes,
            "changeDrawingLineAttributes(view, targets, line_number, width_mm, "
            "red, green, blue, visible) -- apply one exact complete format to "
            "stable cosmetic-edge and centerline tags without owning a transaction."
        );
       add_varargs_method(
            "drawingLineLengths",
            &Module::drawingLineLengths,
            "drawingLineLengths(view) -- return stable straight cosmetic-line "
            "and centerline targets with exact current endpoints and lengths."
        );
       add_varargs_method(
            "changeDrawingLineLength",
            &Module::changeDrawingLineLength,
            "changeDrawingLineLength(view, kind, tag, operation, delta_distance_mm) "
            "-- extend or shorten one stable straight cosmetic line or centerline "
            "at both ends without owning a transaction."
        );
       add_varargs_method(
            "changeDrawingViewLocks",
            &Module::changeDrawingViewLocks,
            "changeDrawingViewLocks(page, changes) -- set 1 to 32 exact Drawing "
            "views to explicit lock states without owning a transaction. Each "
            "change is a (view, locked) pair."
        );
       add_varargs_method(
            "validateProjectedBalloonAnchor",
            &Module::validateProjectedBalloonAnchor,
            "validateProjectedBalloonAnchor(view, element) -- resolve one exact "
            "projected EdgeN midpoint or VertexN without changing the document."
        );
       add_varargs_method(
            "createProjectedBalloon",
            &Module::createProjectedBalloon,
            "createProjectedBalloon(view, element, text, label, offset_x_mm, "
            "offset_y_mm) -- create one exact projected balloon without owning "
            "a transaction; offsets use scaled view coordinates."
        );
       add_varargs_method(
            "validateProjectedMeasurementAnnotation",
            &Module::validateProjectedMeasurementAnnotation,
            "validateProjectedMeasurementAnnotation(view, kind, elements) -- "
            "measure 1 to 64 exact projected faces or edges without changing "
            "the document. Kind is area or arc_length."
        );
       add_varargs_method(
            "createProjectedMeasurementAnnotation",
            &Module::createProjectedMeasurementAnnotation,
            "createProjectedMeasurementAnnotation(view, kind, elements, label) "
            "-- create one exact host-measured annotation without owning a "
            "transaction. Kind is area or arc_length."
        );
        initialize("This is a module for displaying drawings"); // register with Python
    }
    ~Module() override {}

private:
    App::DocumentObject* drawingFormatTarget(PyObject* targetPy) const
    {
        if (!PyObject_TypeCheck(targetPy, &(App::DocumentObjectPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawViewDimension or DrawViewBalloon object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(targetPy)->getDocumentObjectPtr();
        if (!object
            || (!object->isDerivedFrom<TechDraw::DrawViewDimension>()
                && !object->isDerivedFrom<TechDraw::DrawViewBalloon>())) {
            throw Py::TypeError("expected a TechDraw DrawViewDimension or DrawViewBalloon object");
        }
        return object;
    }

    Py::Object drawingFormatResult(
        const TechDrawGui::DrawingFormatCustomization& value) const
    {
        Py::Dict result;
        result.setItem("target_kind", Py::String(value.targetKind));
        result.setItem("value", Py::String(value.value));
        result.setItem("preview", Py::String(value.preview));
        return result;
    }

    TechDraw::DrawViewDimension* drawingDimension(PyObject* dimensionPy) const
    {
        if (!PyObject_TypeCheck(dimensionPy, &(App::DocumentObjectPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawViewDimension object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(dimensionPy)->getDocumentObjectPtr();
        auto* dimension = dynamic_cast<TechDraw::DrawViewDimension*>(object);
        if (!dimension || !dimension->getDocument() || !dimension->findParentPage()) {
            throw Py::RuntimeError("the Drawing dimension is not attached to a live page");
        }
        return dimension;
    }

    TechDraw::DrawViewPart* drawingPart(PyObject* viewPy) const
    {
        if (!PyObject_TypeCheck(viewPy, &(TechDraw::DrawViewPartPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawViewPart object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(viewPy)->getDocumentObjectPtr();
        auto* view = dynamic_cast<TechDraw::DrawViewPart*>(object);
        if (!view || !view->getDocument() || !view->findParentPage()) {
            throw Py::RuntimeError("the Drawing view is not attached to a live page");
        }
        return view;
    }

    TechDraw::DrawPage* drawingPage(PyObject* pagePy) const
    {
        if (!PyObject_TypeCheck(pagePy, &(App::DocumentObjectPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawPage object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(pagePy)->getDocumentObjectPtr();
        auto* page = dynamic_cast<TechDraw::DrawPage*>(object);
        if (!page || !page->getDocument()) {
            throw Py::RuntimeError("the Drawing page is not attached to a live document");
        }
        return page;
    }

    TechDraw::DrawView* drawingView(PyObject* viewPy) const
    {
        if (!PyObject_TypeCheck(viewPy, &(App::DocumentObjectPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawView object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(viewPy)->getDocumentObjectPtr();
        auto* view = dynamic_cast<TechDraw::DrawView*>(object);
        if (!view || !view->getDocument() || !view->findParentPage()) {
            throw Py::RuntimeError(
                "the Drawing view is not attached to a live page");
        }
        return view;
    }

    TechDraw::DrawRichAnno* drawingRichAnnotation(PyObject* annotationPy) const
    {
        auto* annotation = dynamic_cast<TechDraw::DrawRichAnno*>(
            drawingView(annotationPy));
        if (!annotation) {
            throw Py::TypeError("expected a TechDraw DrawRichAnno object");
        }
        return annotation;
    }

    TechDraw::DrawLeaderLine* drawingLeader(PyObject* leaderPy) const
    {
        auto* leader = dynamic_cast<TechDraw::DrawLeaderLine*>(
            drawingView(leaderPy));
        if (!leader) {
            throw Py::TypeError("expected a TechDraw DrawLeaderLine object");
        }
        return leader;
    }

    TechDraw::DrawWeldSymbol* drawingWeldSymbol(PyObject* symbolPy) const
    {
        auto* symbol = dynamic_cast<TechDraw::DrawWeldSymbol*>(
            drawingView(symbolPy));
        if (!symbol) {
            throw Py::TypeError("expected a TechDraw DrawWeldSymbol object");
        }
        return symbol;
    }

    ViewProviderPage* drawingPageProvider(PyObject* pagePy) const
    {
        auto* page = drawingPage(pagePy);
        auto* guiDocument = Gui::Application::Instance->getDocument(
            page->getDocument());
        auto* provider = guiDocument
            ? guiDocument->getViewProvider(page)
            : nullptr;
        auto* pageProvider = freecad_cast<ViewProviderPage*>(provider);
        if (!pageProvider) {
            throw Py::RuntimeError(
                "the Drawing page has no active graphical provider");
        }
        return pageProvider;
    }

    Py::Dict drawingPagePresentationState(ViewProviderPage* provider) const
    {
        if (!provider || !provider->getDrawPage()) {
            throw Py::RuntimeError("the Drawing page has no live graphical provider");
        }
        auto* pageWindow = provider->getMDIViewPage();
        Py::Dict result;
        result.setItem(
            "page_name",
            Py::String(provider->getDrawPage()->getNameInDocument()));
        result.setItem("open", Py::Boolean(pageWindow != nullptr));
        result.setItem(
            "active",
            Py::Boolean(
                pageWindow != nullptr
                && Gui::getMainWindow()->activeWindow() == pageWindow));
        return result;
    }

    Py::Object drawingPagePresentation(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pagePy)) {
            throw Py::TypeError("expected (page)");
        }
        return drawingPagePresentationState(drawingPageProvider(pagePy));
    }

    Py::Object showDrawingPage(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pagePy)) {
            throw Py::TypeError("expected (page)");
        }
        auto* provider = drawingPageProvider(pagePy);
        const auto before = drawingPagePresentationState(provider);
        if (!provider->showMDIViewPage()) {
            throw Py::RuntimeError("the Drawing page could not be shown");
        }
        auto after = drawingPagePresentationState(provider);
        if (!PyObject_IsTrue(after.getItem("open").ptr())
            || !PyObject_IsTrue(after.getItem("active").ptr())) {
            throw Py::RuntimeError("the Drawing page did not become human-active");
        }
        after.setItem("previous_open", before.getItem("open"));
        after.setItem("previous_active", before.getItem("active"));
        after.setItem(
            "changed",
            Py::Boolean(!PyObject_IsTrue(before.getItem("active").ptr())));
        return after;
    }

    std::vector<std::string> projectedCircleElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "circle centerline sources must be a sequence of projected EdgeN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 1 || size > 32) {
            throw Py::ValueError(
                "circle centerlines require 1 to 32 projected edges");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every circle centerline source must be an EdgeN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError(
                    "a circle centerline source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedThreadSideElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "thread side sources must be a sequence of two projected EdgeN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        if (PySequence_Fast_GET_SIZE(sequence) != 2) {
            throw Py::ValueError(
                "thread side representation requires exactly two projected edges");
        }
        std::vector<std::string> elements;
        elements.reserve(2);
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < 2; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every thread side source must be an EdgeN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a thread side source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedIntersectionElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "intersection sources must be a sequence of two projected EdgeN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        if (PySequence_Fast_GET_SIZE(sequence) != 2) {
            throw Py::ValueError(
                "intersection vertices require exactly two projected edges");
        }
        std::vector<std::string> elements;
        elements.reserve(2);
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < 2; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every intersection source must be an EdgeN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError(
                    "an intersection source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedMidpointElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "midpoint sources must be a sequence of projected EdgeN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 64) {
            throw Py::ValueError(
                "midpoint vertices require between one and 64 projected edges");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(count));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < count; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every midpoint source must be an EdgeN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a midpoint source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedQuadrantElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "quadrant sources must be a sequence of projected EdgeN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 64) {
            throw Py::ValueError(
                "quadrant vertices require between one and 64 projected edges");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(count));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < count; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every quadrant source must be an EdgeN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a quadrant source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedGeneralCenterLineElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "centerline sources must be a sequence of projected subelement names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 64) {
            throw Py::ValueError(
                "centerlines require between one and 64 projected sources");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(count));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < count; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every centerline source must be a subelement string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a centerline source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedTwoPointCosmeticLineElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "two-point cosmetic-line sources must be two projected VertexN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        if (PySequence_Fast_GET_SIZE(sequence) != 2) {
            throw Py::ValueError(
                "a two-point cosmetic line requires exactly two projected vertices");
        }
        std::vector<std::string> elements;
        elements.reserve(2);
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < 2; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every two-point cosmetic-line source must be a VertexN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError(
                    "a two-point cosmetic-line source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedCosmeticCurveElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "cosmetic curve sources must be a sequence of projected VertexN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 1 || size > 3) {
            throw Py::ValueError(
                "a cosmetic curve requires one, two, or three projected vertices");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every cosmetic curve source must be a VertexN string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError(
                    "a cosmetic curve source name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    std::vector<std::string> projectedMeasurementElements(
        PyObject* elementsPy) const
    {
        auto* sequence = PySequence_Fast(
            elementsPy,
            "measurement elements must be a sequence of projected names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 1 || size > 64) {
            throw Py::ValueError(
                "a projected measurement requires 1 to 64 elements");
        }
        std::vector<std::string> elements;
        elements.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every projected measurement element must be a string");
            }
            const char* element = PyUnicode_AsUTF8(items[index]);
            if (!element) {
                throw Py::Exception();
            }
            const std::string name(element);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError(
                    "a projected measurement element name is invalid");
            }
            elements.push_back(name);
        }
        return elements;
    }

    TechDrawGui::MeasurementAnnotationKind measurementAnnotationKind(
        const char* rawKind) const
    {
        const std::string kind(rawKind ? rawKind : "");
        if (kind == "area") {
            return TechDrawGui::MeasurementAnnotationKind::Area;
        }
        if (kind == "arc_length") {
            return TechDrawGui::MeasurementAnnotationKind::ArcLength;
        }
        throw Py::ValueError(
            "measurement kind must be area or arc_length");
    }

    TechDraw::ReferenceVector projectedReferences(
        TechDraw::DrawViewPart* view,
        PyObject* subelementsPy) const
    {
        auto* sequence = PySequence_Fast(
            subelementsPy,
            "dimension subelements must be a sequence of strings");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 1 || size > 3) {
            throw Py::ValueError("a projected dimension requires 1 to 3 subelements");
        }
        TechDraw::ReferenceVector references;
        references.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError("every projected dimension subelement must be a string");
            }
            const char* subelement = PyUnicode_AsUTF8(items[index]);
            if (!subelement) {
                throw Py::Exception();
            }
            const std::string name(subelement);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a projected dimension subelement name is invalid");
            }
            references.emplace_back(view, name);
        }
        return references;
    }

    std::vector<std::string> projectedDimensionSeriesVertices(
        PyObject* verticesPy) const
    {
        auto* sequence = PySequence_Fast(
            verticesPy,
            "dimension series vertices must be a sequence of projected VertexN names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 3 || size > 64) {
            throw Py::ValueError(
                "a dimension series requires 3 to 64 projected vertices");
        }
        std::vector<std::string> vertices;
        vertices.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError(
                    "every dimension series source must be a VertexN string");
            }
            const char* vertex = PyUnicode_AsUTF8(items[index]);
            if (!vertex) {
                throw Py::Exception();
            }
            const std::string name(vertex);
            if (!name.starts_with("Vertex") || name.size() <= 6 || name.size() > 32
                || !std::ranges::all_of(name.substr(6), [](unsigned char character) {
                       return std::isdigit(character) != 0;
                   })) {
                throw Py::ValueError(
                    "every dimension series source must be a projected VertexN name");
            }
            vertices.push_back(name);
        }
        return vertices;
    }

    std::vector<std::string> drawingDimensionSeriesCarrierTags(
        PyObject* tagsPy,
        const char* noun) const
    {
        auto* sequence = PySequence_Fast(tagsPy, noun);
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 0 || size > 128) {
            throw Py::ValueError("a dimension series can remove at most 128 carrier tags");
        }
        std::vector<std::string> tags;
        std::unordered_set<std::string> distinct;
        tags.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError("every dimension-series carrier tag must be a string");
            }
            const char* raw = PyUnicode_AsUTF8(items[index]);
            if (!raw) {
                throw Py::Exception();
            }
            std::string tag(raw);
            if (tag.empty() || tag.size() > 80 || !distinct.insert(tag).second) {
                throw Py::ValueError("dimension-series carrier tags must be unique and valid");
            }
            tags.push_back(std::move(tag));
        }
        return tags;
    }

    Py::Dict drawingDimensionSeriesPlan(
        const DrawingDimensionSeriesPlan& plan) const
    {
        Py::List inputVertices;
        for (const auto& vertex : plan.inputVertices) {
            inputVertices.append(Py::String(vertex));
        }
        Py::List orderedVertices;
        for (const auto& vertex : plan.orderedVertices) {
            orderedVertices.append(Py::String(vertex));
        }
        Py::Dict result;
        result.setItem("kind", Py::String(plan.kind));
        result.setItem("direction", Py::String(plan.direction));
        result.setItem("input_vertices", inputVertices);
        result.setItem("ordered_vertices", orderedVertices);
        result.setItem(
            "dimension_count",
            Py::Long(static_cast<unsigned long>(plan.dimensionCount)));
        return result;
    }

    Py::Object validateDrawingDimensionSeries(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* verticesPy = nullptr;
        const char* kind = nullptr;
        const char* direction = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OssO", &viewPy, &kind, &direction, &verticesPy)) {
            throw Py::TypeError("expected (view, kind, direction, vertices)");
        }
        return drawingDimensionSeriesPlan(
            TechDrawGui::validateDrawingDimensionSeries(
                drawingPart(viewPy),
                kind ? kind : "",
                direction ? direction : "",
                projectedDimensionSeriesVertices(verticesPy)));
    }

    Py::Object createDrawingDimensionSeries(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* verticesPy = nullptr;
        const char* kind = nullptr;
        const char* direction = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OssO", &viewPy, &kind, &direction, &verticesPy)) {
            throw Py::TypeError("expected (view, kind, direction, vertices)");
        }
        const auto created = TechDrawGui::createDrawingDimensionSeries(
            drawingPart(viewPy),
            kind ? kind : "",
            direction ? direction : "",
            projectedDimensionSeriesVertices(verticesPy));
        Py::Dict result = drawingDimensionSeriesPlan(created.plan);
        result.setItem(
            "operation_group",
            Py::asObject(created.operationGroup->getPyObject()));
        Py::List dimensions;
        for (auto* dimension : created.dimensions) {
            dimensions.append(Py::asObject(dimension->getPyObject()));
        }
        result.setItem("dimensions", dimensions);
        return result;
    }

    Py::Object removeDrawingDimensionSeriesCarriers(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* edgesPy = nullptr;
        PyObject* verticesPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OOO", &viewPy, &edgesPy, &verticesPy)) {
            throw Py::TypeError("expected (view, edge_tags, vertex_tags)");
        }
        auto* view = drawingPart(viewPy);
        const auto edgeTags = drawingDimensionSeriesCarrierTags(
            edgesPy,
            "dimension-series edge tags must be a sequence");
        const auto vertexTags = drawingDimensionSeriesCarrierTags(
            verticesPy,
            "dimension-series vertex tags must be a sequence");
        std::unordered_set<std::string> liveEdges;
        for (auto* edge : view->CosmeticEdges.getValues()) {
            if (edge) {
                liveEdges.insert(edge->getTagAsString());
            }
        }
        std::unordered_set<std::string> liveVertices;
        for (auto* vertex : view->CosmeticVertexes.getValues()) {
            if (vertex) {
                liveVertices.insert(vertex->getTagAsString());
            }
        }
        if (std::ranges::any_of(edgeTags, [&liveEdges](const auto& tag) {
                return !liveEdges.contains(tag);
            })
            || std::ranges::any_of(vertexTags, [&liveVertices](const auto& tag) {
                   return !liveVertices.contains(tag);
               })) {
            throw Py::ValueError(
                "an exact dimension-series carrier is no longer present in the view");
        }
        view->removeCosmeticEdge(edgeTags);
        view->removeCosmeticVertex(vertexTags);
        view->refreshCEGeoms();
        view->refreshCVGeoms();
        view->requestPaint();
        return Py::None();
    }

    TechDraw::ReferenceVector projectedExtentReferences(
        TechDraw::DrawViewPart* view,
        PyObject* subelementsPy) const
    {
        auto* sequence = PySequence_Fast(
            subelementsPy,
            "extent subelements must be a sequence of edge names");
        if (!sequence) {
            throw Py::Exception();
        }
        Py::Object sequenceOwner(sequence, true);
        const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
        if (size < 0 || size > 64) {
            throw Py::ValueError("a projected extent accepts 0 to 64 edge names");
        }
        TechDraw::ReferenceVector references;
        references.reserve(static_cast<std::size_t>(size));
        PyObject** items = PySequence_Fast_ITEMS(sequence);
        for (Py_ssize_t index = 0; index < size; ++index) {
            if (!PyUnicode_Check(items[index])) {
                throw Py::TypeError("every projected extent subelement must be a string");
            }
            const char* subelement = PyUnicode_AsUTF8(items[index]);
            if (!subelement) {
                throw Py::Exception();
            }
            const std::string name(subelement);
            if (name.empty() || name.size() > 32) {
                throw Py::ValueError("a projected extent edge name is invalid");
            }
            references.emplace_back(view, name);
        }
        return references;
    }

    ViewProviderDrawingView* drawingViewProvider(PyObject* viewPy) const
    {
        if (!PyObject_TypeCheck(viewPy, &(TechDraw::DrawViewPy::Type))) {
            throw Py::TypeError("expected a TechDraw DrawView object");
        }
        auto* object =
            static_cast<App::DocumentObjectPy*>(viewPy)->getDocumentObjectPtr();
        auto* provider = Gui::Application::Instance->getViewProvider(object);
        auto* drawingProvider = freecad_cast<ViewProviderDrawingView*>(provider);
        if (!drawingProvider) {
            throw Py::RuntimeError("the Drawing view has no active graphical provider");
        }
        return drawingProvider;
    }

    Py::Dict viewStackState(ViewProviderDrawingView* provider) const
    {
        Py::Dict result;
        auto* qView = provider->getQView();
        auto* view = provider->getViewObject();
        auto* page = view ? view->findParentPage() : nullptr;
        result.setItem("available", Py::Boolean(qView && page));
        result.setItem("object_name",
                       Py::String(view && view->getNameInDocument()
                                      ? view->getNameInDocument()
                                      : ""));
        result.setItem("page_name",
                       Py::String(page && page->getNameInDocument()
                                      ? page->getNameInDocument()
                                      : ""));
        result.setItem("stack_order", Py::Long(provider->getZ()));
        result.setItem("z_value", Py::Float(qView ? qView->zValue() : 0.0));
        if (!qView || !page) {
            result.setItem("scope_kind", Py::String("unavailable"));
            result.setItem("scope_items", Py::List());
            return result;
        }

        Py::List scopeItems;
        int minimumOrder = std::numeric_limits<int>::max();
        int maximumOrder = std::numeric_limits<int>::min();
        bool targetInScope = false;
        const std::string targetName =
            view && view->getNameInDocument() ? view->getNameInDocument() : "";
        auto appendScopeItem = [&](const std::string& objectName, double zValue) {
            Py::Dict item;
            item.setItem("z_value", Py::Float(zValue));
            item.setItem("object_name", Py::String(objectName));
            scopeItems.append(item);
            if (zValue < minimumOrder) {
                minimumOrder = static_cast<int>(zValue);
            }
            if (zValue > maximumOrder) {
                maximumOrder = static_cast<int>(zValue);
            }
            targetInScope = targetInScope || (!targetName.empty() && objectName == targetName);
        };
        if (auto* parent = qView->parentItem()) {
            result.setItem("scope_kind", Py::String("owner"));
            for (auto* child : parent->childItems()) {
                auto* childView = dynamic_cast<QGIView*>(child);
                auto* childObject = childView ? childView->getViewObject() : nullptr;
                appendScopeItem(
                    childObject && childObject->getNameInDocument()
                        ? childObject->getNameInDocument()
                        : "",
                    child->zValue());
            }
        }
        else {
            result.setItem("scope_kind", Py::String("page"));
            auto* pageProvider = provider->getViewProviderPage();
            auto* guiDocument = provider->getDocument();
            if (!pageProvider || !guiDocument) {
                result.setItem("available", Py::Boolean(false));
                result.setItem("scope_kind", Py::String("unavailable"));
                result.setItem("scope_items", scopeItems);
                return result;
            }
            for (auto* peerObject : pageProvider->claimChildren()) {
                auto* peerProvider = freecad_cast<ViewProviderDrawingView*>(
                    guiDocument->getViewProvider(peerObject));
                if (!peerProvider) {
                    continue;
                }
                appendScopeItem(
                    peerObject->getNameInDocument()
                        ? peerObject->getNameInDocument()
                        : "",
                    peerProvider->getZ());
            }
        }
        if (scopeItems.length() == 0 || !targetInScope) {
            result.setItem("available", Py::Boolean(false));
        }
        else {
            result.setItem("scope_minimum_order", Py::Long(minimumOrder));
            result.setItem("scope_maximum_order", Py::Long(maximumOrder));
        }
        result.setItem("scope_items", scopeItems);
        return result;
    }

    Py::Object getViewStackState(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        return viewStackState(drawingViewProvider(viewPy));
    }

    Py::Object stackView(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* operation = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &operation)) {
            throw Py::TypeError("expected (view, operation)");
        }
        auto* provider = drawingViewProvider(viewPy);
        Py::Dict before = viewStackState(provider);
        if (!static_cast<bool>(Py::Boolean(before.getItem("available")))) {
            throw Py::RuntimeError(
                "the Drawing view is not available in an open page scene");
        }

        const std::string requested(operation ? operation : "");
        if (requested == "top") {
            provider->stackTop();
        }
        else if (requested == "bottom") {
            provider->stackBottom();
        }
        else if (requested == "up") {
            provider->stackUp();
        }
        else if (requested == "down") {
            provider->stackDown();
        }
        else {
            throw Py::ValueError("stack operation must be top, bottom, up, or down");
        }
        return viewStackState(provider);
    }

    Py::Object createProjectedDimension(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        int allowApproximate = 0;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOpdd",
                &viewPy,
                &dimensionType,
                &subelementsPy,
                &allowApproximate,
                &x,
                &y)) {
            throw Py::TypeError(
                "expected (view, type, subelements, allow_approximate, x_mm, y_mm)");
        }
        if (!std::isfinite(x) || !std::isfinite(y)
            || std::abs(x) > 1.0e6 || std::abs(y) > 1.0e6) {
            throw Py::ValueError("dimension label coordinates are outside the supported range");
        }
        auto* view = drawingPart(viewPy);
        TechDraw::ReferenceVector references =
            projectedReferences(view, subelementsPy);
        auto* dimension = TechDrawGui::createProjectedDimensionFeature(
            view,
            dimensionType ? dimensionType : "",
            references,
            allowApproximate != 0,
            Base::Vector3d(x, y, 0.0));
        // Match the human command's tree refresh without forcing the source
        // projection itself to recompute.
        view->touch(true);
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object validateProjectedDimension(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        int allowApproximate = 0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOp",
                &viewPy,
                &dimensionType,
                &subelementsPy,
                &allowApproximate)) {
            throw Py::TypeError(
                "expected (view, type, subelements, allow_approximate)");
        }
        auto* view = drawingPart(viewPy);
        const TechDraw::ReferenceVector references =
            projectedReferences(view, subelementsPy);
        const auto validation = TechDrawGui::validateProjectedDimension(
            view,
            dimensionType ? dimensionType : "",
            references,
            allowApproximate != 0);
        Py::Dict result;
        result.setItem(
            "geometry_configuration",
            Py::String(validation.geometryConfiguration));
        result.setItem("approximate", Py::Boolean(validation.approximate));
        return result;
    }

    Py::Object createProjectedExtent(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOdd",
                &viewPy,
                &dimensionType,
                &subelementsPy,
                &x,
                &y)) {
            throw Py::TypeError("expected (view, type, subelements, x_mm, y_mm)");
        }
        if (!std::isfinite(x) || !std::isfinite(y)
            || std::abs(x) > 1.0e6 || std::abs(y) > 1.0e6) {
            throw Py::ValueError("extent label coordinates are outside the supported range");
        }
        auto* view = drawingPart(viewPy);
        TechDraw::ReferenceVector references =
            projectedExtentReferences(view, subelementsPy);
        auto* dimension = TechDrawGui::createProjectedExtentFeature(
            view,
            dimensionType ? dimensionType : "",
            references,
            Base::Vector3d(x, y, 0.0));
        view->touch(true);
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object validateProjectedExtent(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsO",
                &viewPy,
                &dimensionType,
                &subelementsPy)) {
            throw Py::TypeError("expected (view, type, subelements)");
        }
        auto* view = drawingPart(viewPy);
        const TechDraw::ReferenceVector references =
            projectedExtentReferences(view, subelementsPy);
        const auto validation = TechDrawGui::validateProjectedExtent(
            view,
            dimensionType ? dimensionType : "",
            references);
        Py::Dict result;
        result.setItem(
            "geometry_configuration",
            Py::String(validation.geometryConfiguration));
        result.setItem("approximate", Py::Boolean(validation.approximate));
        return result;
    }

    Py::Object createProjectedChamfer(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOdd",
                &viewPy,
                &dimensionType,
                &subelementsPy,
                &x,
                &y)) {
            throw Py::TypeError("expected (view, type, vertices, x_mm, y_mm)");
        }
        if (!std::isfinite(x) || !std::isfinite(y)
            || std::abs(x) > 1.0e6 || std::abs(y) > 1.0e6) {
            throw Py::ValueError("chamfer label coordinates are outside the supported range");
        }
        auto* view = drawingPart(viewPy);
        const TechDraw::ReferenceVector references =
            projectedReferences(view, subelementsPy);
        auto* dimension = TechDrawGui::createProjectedChamferFeature(
            view,
            dimensionType ? dimensionType : "",
            references,
            Base::Vector3d(x, y, 0.0));
        view->touch(true);
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object validateProjectedChamfer(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        const char* dimensionType = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsO",
                &viewPy,
                &dimensionType,
                &subelementsPy)) {
            throw Py::TypeError("expected (view, type, vertices)");
        }
        auto* view = drawingPart(viewPy);
        const TechDraw::ReferenceVector references =
            projectedReferences(view, subelementsPy);
        const auto validation = TechDrawGui::validateProjectedChamfer(
            view,
            dimensionType ? dimensionType : "",
            references);
        Py::Dict result;
        result.setItem(
            "geometry_configuration",
            Py::String(validation.geometryConfiguration));
        result.setItem("approximate", Py::Boolean(validation.approximate));
        return result;
    }

    Py::Object createProjectedArcLength(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* edgeName = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "Osdd", &viewPy, &edgeName, &x, &y)) {
            throw Py::TypeError("expected (view, edge, x_mm, y_mm)");
        }
        if (!std::isfinite(x) || !std::isfinite(y)
            || std::abs(x) > 1.0e6 || std::abs(y) > 1.0e6) {
            throw Py::ValueError("arc-length label coordinates are outside the supported range");
        }
        auto* view = drawingPart(viewPy);
        auto* dimension = TechDrawGui::createProjectedArcLengthFeature(
            view,
            edgeName ? edgeName : "",
            Base::Vector3d(x, y, 0.0));
        view->touch(true);
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object validateProjectedArcLength(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* edgeName = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &edgeName)) {
            throw Py::TypeError("expected (view, edge)");
        }
        auto* view = drawingPart(viewPy);
        const auto validation = TechDrawGui::validateProjectedArcLength(
            view,
            edgeName ? edgeName : "");
        Py::Dict result;
        result.setItem(
            "geometry_configuration",
            Py::String(validation.geometryConfiguration));
        result.setItem("arc_length_mm", Py::Float(validation.arcLengthMm));
        return result;
    }

    Py::Object repairProjectedDimension(const Py::Tuple& args)
    {
        PyObject* dimensionPy = nullptr;
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        int allowApproximate = 0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOOp",
                &dimensionPy,
                &viewPy,
                &subelementsPy,
                &allowApproximate)) {
            throw Py::TypeError(
                "expected (dimension, view, subelements, allow_approximate)");
        }
        auto* view = drawingPart(viewPy);
        auto* dimension = TechDrawGui::repairProjectedDimensionFeature(
            drawingDimension(dimensionPy),
            view,
            projectedReferences(view, subelementsPy),
            allowApproximate != 0);
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object repairProjectedExtent(const Py::Tuple& args)
    {
        PyObject* dimensionPy = nullptr;
        PyObject* viewPy = nullptr;
        PyObject* subelementsPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OOO", &dimensionPy, &viewPy, &subelementsPy)) {
            throw Py::TypeError("expected (dimension, view, subelements)");
        }
        auto* view = drawingPart(viewPy);
        auto* dimension = TechDrawGui::repairProjectedExtentFeature(
            drawingDimension(dimensionPy),
            view,
            projectedExtentReferences(view, subelementsPy));
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object repairProjectedChamfer(const Py::Tuple& args)
    {
        PyObject* dimensionPy = nullptr;
        PyObject* viewPy = nullptr;
        PyObject* verticesPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OOO", &dimensionPy, &viewPy, &verticesPy)) {
            throw Py::TypeError("expected (dimension, view, vertices)");
        }
        auto* view = drawingPart(viewPy);
        auto* dimension = TechDrawGui::repairProjectedChamferFeature(
            drawingDimension(dimensionPy),
            view,
            projectedReferences(view, verticesPy));
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object repairProjectedArcLength(const Py::Tuple& args)
    {
        PyObject* dimensionPy = nullptr;
        PyObject* viewPy = nullptr;
        const char* edgeName = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OOs", &dimensionPy, &viewPy, &edgeName)) {
            throw Py::TypeError("expected (dimension, view, edge)");
        }
        auto* dimension = TechDrawGui::repairProjectedArcLengthFeature(
            drawingDimension(dimensionPy),
            drawingPart(viewPy),
            edgeName ? edgeName : "");
        return Py::asObject(dimension->getPyObject());
    }

    Py::Object defaultDimensionFormatSpec(const Py::Tuple& args)
    {
        PyObject* dimensionPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &dimensionPy)) {
            throw Py::TypeError("expected (dimension)");
        }
        return Py::String(TechDrawGui::defaultDimensionFormatSpec(
            drawingDimension(dimensionPy)));
    }

    DrawingDimensionTextOperation drawingDimensionTextOperation(
        const std::string& operation) const
    {
        if (operation == "insert_diameter_prefix") {
            return DrawingDimensionTextOperation::InsertDiameter;
        }
        if (operation == "insert_square_prefix") {
            return DrawingDimensionTextOperation::InsertSquare;
        }
        if (operation == "insert_repetition_prefix") {
            return DrawingDimensionTextOperation::InsertRepetition;
        }
        if (operation == "remove_prefix") {
            return DrawingDimensionTextOperation::RemovePrefix;
        }
        if (operation == "increase_decimals") {
            return DrawingDimensionTextOperation::IncreaseDecimals;
        }
        if (operation == "decrease_decimals") {
            return DrawingDimensionTextOperation::DecreaseDecimals;
        }
        throw Py::ValueError("unsupported Drawing dimension-text operation");
    }

    std::vector<TechDraw::DrawViewDimension*> drawingDimensions(
        PyObject* dimensionsPy) const
    {
        PyObject* sequence = PySequence_Fast(
            dimensionsPy,
            "dimensions must be a sequence of 1 to 64 Drawing dimensions");
        if (!sequence) {
            throw Py::TypeError(
                "dimensions must be a sequence of 1 to 64 Drawing dimensions");
        }
        [[maybe_unused]] Py::Object owner(sequence, true);
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
        if (count < 1 || count > 64) {
            throw Py::ValueError(
                "dimension text changes require 1 to 64 Drawing dimensions");
        }
        std::vector<TechDraw::DrawViewDimension*> result;
        result.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            result.push_back(drawingDimension(
                PySequence_Fast_GET_ITEM(sequence, index)));
        }
        return result;
    }

    Py::Dict drawingDimensionTextPlan(
        const DrawingDimensionTextPlan& plan) const
    {
        Py::Dict result;
        result.setItem("object_name", Py::String(plan.objectName));
        result.setItem("format_spec_before", Py::String(plan.formatSpecBefore));
        result.setItem("format_spec_after", Py::String(plan.formatSpecAfter));
        result.setItem("inserted_prefix", Py::String(plan.insertedPrefix));
        result.setItem(
            "decimal_places_before",
            plan.decimalPlacesBefore < 0
                ? Py::None()
                : Py::Object(Py::Long(plan.decimalPlacesBefore)));
        result.setItem(
            "decimal_places_after",
            plan.decimalPlacesAfter < 0
                ? Py::None()
                : Py::Object(Py::Long(plan.decimalPlacesAfter)));
        result.setItem("changed", Py::Boolean(plan.changed));
        result.setItem(
            "inapplicable_reason",
            Py::String(plan.inapplicableReason));
        return result;
    }

    Py::Object drawingDimensionTextPlans(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* dimensionsPy = nullptr;
        const char* operation = nullptr;
        const char* repetitionText = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "Oss", &dimensionsPy, &operation, &repetitionText)) {
            throw Py::TypeError(
                "expected (dimensions, operation, repetition_text)");
        }
        const auto dimensions = drawingDimensions(dimensionsPy);
        const auto parsedOperation = drawingDimensionTextOperation(
            operation ? operation : "");
        const std::string repetition(repetitionText ? repetitionText : "");
        const auto plans = apply
            ? TechDrawGui::changeDrawingDimensionText(
                dimensions,
                parsedOperation,
                repetition)
            : TechDrawGui::validateDrawingDimensionText(
                dimensions,
                parsedOperation,
                repetition);
        Py::List result;
        for (const auto& plan : plans) {
            result.append(drawingDimensionTextPlan(plan));
        }
        return result;
    }

    Py::Object validateDrawingDimensionText(const Py::Tuple& args)
    {
        return drawingDimensionTextPlans(args, false);
    }

    Py::Object changeDrawingDimensionText(const Py::Tuple& args)
    {
        return drawingDimensionTextPlans(args, true);
    }

    Py::Dict drawingFrameVisibilityPlan(
        const DrawingFrameVisibilityPlan& plan) const
    {
        Py::Dict result;
        result.setItem("page_name", Py::String(plan.pageName));
        result.setItem("previous_visible", Py::Boolean(plan.previousVisible));
        result.setItem("visible", Py::Boolean(plan.visible));
        result.setItem("changed", Py::Boolean(plan.changed));
        result.setItem(
            "graphical_view_count",
            Py::Long(static_cast<unsigned long>(plan.graphicalViewCount)));
        return result;
    }

    Py::Object drawingFrameVisibilityOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        int visible = 0;
        if (!PyArg_ParseTuple(args.ptr(), "Op", &pagePy, &visible)) {
            throw Py::TypeError("expected (page, visible)");
        }
        auto* provider = drawingPageProvider(pagePy);
        const auto plan = apply
            ? TechDrawGui::changeDrawingFrameVisibility(
                provider,
                visible != 0)
            : TechDrawGui::validateDrawingFrameVisibility(
                provider,
                visible != 0);
        return drawingFrameVisibilityPlan(plan);
    }

    Py::Object drawingFrameVisibility(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pagePy)) {
            throw Py::TypeError("expected (page)");
        }
        auto* provider = drawingPageProvider(pagePy);
        return drawingFrameVisibilityPlan(
            TechDrawGui::inspectDrawingFrameVisibility(provider));
    }

    Py::Object drawingFrameVisibilityAvailable(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::TypeError("expected no arguments");
        }
        return Py::Boolean(
            PreferencesGui::getViewFrameMode() == ViewFrameMode::Manual);
    }

    Py::Object validateDrawingFrameVisibility(const Py::Tuple& args)
    {
        return drawingFrameVisibilityOperation(args, false);
    }

    Py::Object changeDrawingFrameVisibility(const Py::Tuple& args)
    {
        return drawingFrameVisibilityOperation(args, true);
    }

    Py::Dict drawingGridVisibilityPlan(
        const DrawingGridVisibilityPlan& plan) const
    {
        Py::Dict result;
        result.setItem("page_name", Py::String(plan.pageName));
        result.setItem("previous_visible", Py::Boolean(plan.previousVisible));
        result.setItem("visible", Py::Boolean(plan.visible));
        result.setItem("changed", Py::Boolean(plan.changed));
        return result;
    }

    Py::Object drawingGridVisibilityOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        int visible = 0;
        if (!PyArg_ParseTuple(args.ptr(), "Op", &pagePy, &visible)) {
            throw Py::TypeError("expected (page, visible)");
        }
        auto* provider = drawingPageProvider(pagePy);
        const auto plan = apply
            ? TechDrawGui::changeDrawingGridVisibility(provider, visible != 0)
            : TechDrawGui::validateDrawingGridVisibility(provider, visible != 0);
        return drawingGridVisibilityPlan(plan);
    }

    Py::Object drawingGridVisibility(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pagePy)) {
            throw Py::TypeError("expected (page)");
        }
        return drawingGridVisibilityPlan(
            TechDrawGui::inspectDrawingGridVisibility(
                drawingPageProvider(pagePy)));
    }

    Py::Object validateDrawingGridVisibility(const Py::Tuple& args)
    {
        return drawingGridVisibilityOperation(args, false);
    }

    Py::Object changeDrawingGridVisibility(const Py::Tuple& args)
    {
        return drawingGridVisibilityOperation(args, true);
    }

    Py::Dict drawingHiddenEdgeVisibilityPlan(
        const DrawingHiddenEdgeVisibilityPlan& plan) const
    {
        Py::Dict result;
        result.setItem("page_name", Py::String(plan.pageName));
        result.setItem("view_name", Py::String(plan.viewName));
        result.setItem("previous_visible", Py::Boolean(plan.previousVisible));
        result.setItem("visible", Py::Boolean(plan.visible));
        result.setItem("changed", Py::Boolean(plan.changed));
        return result;
    }

    Py::Object drawingHiddenEdgeVisibilityOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* viewPy = nullptr;
        int visible = 0;
        if (!PyArg_ParseTuple(args.ptr(), "Op", &viewPy, &visible)) {
            throw Py::TypeError("expected (view, visible)");
        }
        auto* view = drawingPart(viewPy);
        const auto plan = apply
            ? TechDrawGui::changeDrawingHiddenEdgeVisibility(view, visible != 0)
            : TechDrawGui::validateDrawingHiddenEdgeVisibility(view, visible != 0);
        return drawingHiddenEdgeVisibilityPlan(plan);
    }

    Py::Object drawingHiddenEdgeVisibility(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        return drawingHiddenEdgeVisibilityPlan(
            TechDrawGui::inspectDrawingHiddenEdgeVisibility(
                drawingPart(viewPy)));
    }

    Py::Object validateDrawingHiddenEdgeVisibility(const Py::Tuple& args)
    {
        return drawingHiddenEdgeVisibilityOperation(args, false);
    }

    Py::Object changeDrawingHiddenEdgeVisibility(const Py::Tuple& args)
    {
        return drawingHiddenEdgeVisibilityOperation(args, true);
    }

    Py::Dict drawingKeepUpdatedPlan(const DrawingKeepUpdatedPlan& plan) const
    {
        Py::Dict result;
        result.setItem("page_name", Py::String(plan.pageName));
        result.setItem(
            "previous_keep_updated",
            Py::Boolean(plan.previousKeepUpdated));
        result.setItem("keep_updated", Py::Boolean(plan.keepUpdated));
        result.setItem("changed", Py::Boolean(plan.changed));
        return result;
    }

    Py::Object drawingKeepUpdatedOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        int keepUpdated = 0;
        if (!PyArg_ParseTuple(args.ptr(), "Op", &pagePy, &keepUpdated)) {
            throw Py::TypeError("expected (page, keep_updated)");
        }
        auto* page = drawingPage(pagePy);
        const auto plan = apply
            ? TechDrawGui::changeDrawingKeepUpdated(page, keepUpdated != 0)
            : TechDrawGui::validateDrawingKeepUpdated(page, keepUpdated != 0);
        return drawingKeepUpdatedPlan(plan);
    }

    Py::Object drawingKeepUpdated(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &pagePy)) {
            throw Py::TypeError("expected (page)");
        }
        return drawingKeepUpdatedPlan(
            TechDrawGui::inspectDrawingKeepUpdated(drawingPage(pagePy)));
    }

    Py::Object validateDrawingKeepUpdated(const Py::Tuple& args)
    {
        return drawingKeepUpdatedOperation(args, false);
    }

    Py::Object changeDrawingKeepUpdated(const Py::Tuple& args)
    {
        return drawingKeepUpdatedOperation(args, true);
    }

    Py::Dict drawingHatchColor(const Base::Color& value) const
    {
        Py::Dict result;
        result.setItem("red", Py::Float(value.r));
        result.setItem("green", Py::Float(value.g));
        result.setItem("blue", Py::Float(value.b));
        return result;
    }

    Py::Dict drawingHatchOffset(const Base::Vector3d& value) const
    {
        Py::Dict result;
        result.setItem("x_mm", Py::Float(value.x));
        result.setItem("y_mm", Py::Float(value.y));
        return result;
    }

    Py::List drawingHatchNames(const std::vector<std::string>& values) const
    {
        Py::List result;
        for (const auto& value : values) {
            result.append(Py::String(value));
        }
        return result;
    }

    Py::Dict drawingImageHatchPlan(const DrawingImageHatchPlan& plan) const
    {
        Py::Dict style;
        style.setItem("scale", Py::Float(plan.style.scale));
        style.setItem(
            "rotation_degrees",
            Py::Float(plan.style.rotationDegrees));
        style.setItem("offset_mm", drawingHatchOffset(plan.style.offsetMm));
        style.setItem("color_rgb", drawingHatchColor(plan.style.color));
        Py::Dict result;
        result.setItem("view_name", Py::String(plan.view->getNameInDocument()));
        result.setItem("page_name", Py::String(plan.page->getNameInDocument()));
        result.setItem("faces", drawingHatchNames(plan.faces));
        result.setItem(
            "pattern_file_name",
            Py::String(plan.patternFileName));
        result.setItem("pattern_kind", Py::String(plan.patternKind));
        result.setItem("style", style);
        return result;
    }

    Py::Dict drawingGeometricHatchPlan(
        const DrawingGeometricHatchPlan& plan) const
    {
        Py::Dict style;
        style.setItem("scale", Py::Float(plan.style.scale));
        style.setItem(
            "rotation_degrees",
            Py::Float(plan.style.rotationDegrees));
        style.setItem("offset_mm", drawingHatchOffset(plan.style.offsetMm));
        style.setItem(
            "line_width_mm",
            Py::Float(plan.style.lineWidthMm));
        style.setItem("color_rgb", drawingHatchColor(plan.style.color));
        Py::Dict result;
        result.setItem("view_name", Py::String(plan.view->getNameInDocument()));
        result.setItem("page_name", Py::String(plan.page->getNameInDocument()));
        result.setItem("faces", drawingHatchNames(plan.faces));
        result.setItem(
            "pattern_file_name",
            Py::String(plan.patternFileName));
        result.setItem("pattern_name", Py::String(plan.patternName));
        result.setItem("style", style);
        return result;
    }

    Py::Object drawingHatchDefaults(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::TypeError("expected no arguments");
        }
        const auto defaults = TechDrawGui::drawingHatchDefaults();
        Py::Dict image;
        image.setItem(
            "pattern_file",
            Py::String(defaults.imagePatternFile));
        image.setItem(
            "pattern_file_name",
            Py::String(defaults.imagePatternFileName));
        image.setItem("color_rgb", drawingHatchColor(defaults.imageColor));
        Py::Dict geometric;
        geometric.setItem(
            "pattern_file",
            Py::String(defaults.geometricPatternFile));
        geometric.setItem(
            "pattern_file_name",
            Py::String(defaults.geometricPatternFileName));
        geometric.setItem(
            "pattern_name",
            Py::String(defaults.geometricPatternName));
        geometric.setItem(
            "pattern_names",
            drawingHatchNames(defaults.geometricPatternNames));
        geometric.setItem(
            "color_rgb",
            drawingHatchColor(defaults.geometricColor));
        geometric.setItem(
            "line_width_mm",
            Py::Float(defaults.geometricLineWidthMm));
        Py::Dict result;
        result.setItem("image", image);
        result.setItem("geometric", geometric);
        return result;
    }

    Py::Object drawingImageHatchOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* viewPy = nullptr;
        PyObject* facesPy = nullptr;
        char* patternFile = nullptr;
        double scale = 0.0;
        double rotation = 0.0;
        double offsetX = 0.0;
        double offsetY = 0.0;
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOesddddddd",
                &viewPy,
                &facesPy,
                "utf-8",
                &patternFile,
                &scale,
                &rotation,
                &offsetX,
                &offsetY,
                &red,
                &green,
                &blue)) {
            throw Py::TypeError(
                "expected (view, faces, pattern_file, scale, rotation, "
                "offset_x, offset_y, red, green, blue)");
        }
        try {
            auto* view = drawingPart(viewPy);
            const auto faces = projectedMeasurementElements(facesPy);
            const DrawingImageHatchStyle style {
                scale,
                rotation,
                Base::Vector3d(offsetX, offsetY, 0.0),
                Base::Color(red, green, blue)};
            Py::Dict result = drawingImageHatchPlan(
                TechDrawGui::validateDrawingImageHatch(
                    view,
                    faces,
                    patternFile ? patternFile : "",
                    style));
            if (apply) {
                auto* hatch = TechDrawGui::createDrawingImageHatch(
                    view,
                    faces,
                    patternFile ? patternFile : "",
                    style);
                result.setItem("hatch", Py::asObject(hatch->getPyObject()));
                result.setItem(
                    "object_name",
                    Py::String(hatch->getNameInDocument()));
            }
            PyMem_Free(patternFile);
            return result;
        }
        catch (...) {
            PyMem_Free(patternFile);
            throw;
        }
    }

    Py::Object validateDrawingImageHatch(const Py::Tuple& args)
    {
        return drawingImageHatchOperation(args, false);
    }

    Py::Object createDrawingImageHatch(const Py::Tuple& args)
    {
        return drawingImageHatchOperation(args, true);
    }

    Py::Object drawingGeometricHatchOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* viewPy = nullptr;
        PyObject* facesPy = nullptr;
        char* patternFile = nullptr;
        char* patternName = nullptr;
        double scale = 0.0;
        double rotation = 0.0;
        double offsetX = 0.0;
        double offsetY = 0.0;
        double lineWidth = 0.0;
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOesesdddddddd",
                &viewPy,
                &facesPy,
                "utf-8",
                &patternFile,
                "utf-8",
                &patternName,
                &scale,
                &rotation,
                &offsetX,
                &offsetY,
                &lineWidth,
                &red,
                &green,
                &blue)) {
            throw Py::TypeError(
                "expected (view, faces, pattern_file, pattern_name, scale, "
                "rotation, offset_x, offset_y, width, red, green, blue)");
        }
        try {
            auto* view = drawingPart(viewPy);
            const auto faces = projectedMeasurementElements(facesPy);
            const DrawingGeometricHatchStyle style {
                scale,
                rotation,
                Base::Vector3d(offsetX, offsetY, 0.0),
                lineWidth,
                Base::Color(red, green, blue)};
            Py::Dict result = drawingGeometricHatchPlan(
                TechDrawGui::validateDrawingGeometricHatch(
                    view,
                    faces,
                    patternFile ? patternFile : "",
                    patternName ? patternName : "",
                    style));
            if (apply) {
                auto* hatch = TechDrawGui::createDrawingGeometricHatch(
                    view,
                    faces,
                    patternFile ? patternFile : "",
                    patternName ? patternName : "",
                    style);
                result.setItem("hatch", Py::asObject(hatch->getPyObject()));
                result.setItem(
                    "object_name",
                    Py::String(hatch->getNameInDocument()));
            }
            PyMem_Free(patternFile);
            PyMem_Free(patternName);
            return result;
        }
        catch (...) {
            PyMem_Free(patternFile);
            PyMem_Free(patternName);
            throw;
        }
    }

    Py::Object validateDrawingGeometricHatch(const Py::Tuple& args)
    {
        return drawingGeometricHatchOperation(args, false);
    }

    Py::Object createDrawingGeometricHatch(const Py::Tuple& args)
    {
        return drawingGeometricHatchOperation(args, true);
    }

    std::string drawingRichAnnotationString(
        PyObject* value,
        const char* noun) const
    {
        if (!PyUnicode_Check(value)) {
            throw Py::TypeError(std::string(noun) + " must be a Unicode string");
        }
        Py_ssize_t size = 0;
        const char* text = PyUnicode_AsUTF8AndSize(value, &size);
        if (!text) {
            throw Py::Exception();
        }
        std::string result(text, static_cast<std::size_t>(size));
        if (result.find('\0') != std::string::npos) {
            throw Py::ValueError(std::string(noun) + " may not contain NUL characters");
        }
        return result;
    }

    DrawingRichAnnotationContentKind drawingRichAnnotationContentKind(
        const std::string& value) const
    {
        if (value == "plain_text") {
            return DrawingRichAnnotationContentKind::PlainText;
        }
        if (value == "safe_html") {
            return DrawingRichAnnotationContentKind::SafeHtml;
        }
        throw Py::ValueError(
            "rich annotation content_kind must be plain_text or safe_html");
    }

    int drawingRichAnnotationLineStyle(const std::string& value) const
    {
        static constexpr std::array<const char*, 6> names {
            "no_line",
            "continuous",
            "dash",
            "dot",
            "dash_dot",
            "dash_dot_dot",
        };
        const auto found = std::ranges::find(names, value);
        if (found == names.end()) {
            throw Py::ValueError(
                "rich annotation frame_style must be no_line, continuous, dash, "
                "dot, dash_dot, or dash_dot_dot");
        }
        return static_cast<int>(std::distance(names.begin(), found));
    }

    const char* drawingRichAnnotationLineStyle(int value) const
    {
        static constexpr std::array<const char*, 6> names {
            "no_line",
            "continuous",
            "dash",
            "dot",
            "dash_dot",
            "dash_dot_dot",
        };
        if (value < 0 || value >= static_cast<int>(names.size())) {
            throw Py::ValueError("stored rich annotation frame style is invalid");
        }
        return names.at(static_cast<std::size_t>(value));
    }

    Py::Dict drawingRichAnnotationContent(
        const DrawingRichAnnotationContent& content) const
    {
        Py::Dict result;
        result.setItem("input_kind", Py::String(content.inputKind));
        result.setItem(
            "stored_html_sha256",
            Py::String(content.storedHtmlSha256));
        result.setItem(
            "plain_text_sha256",
            Py::String(content.plainTextSha256));
        result.setItem(
            "plain_text_preview",
            Py::String(content.plainTextPreview));
        result.setItem(
            "plain_text_characters",
            Py::Long(static_cast<unsigned long>(content.plainTextCharacters)));
        result.setItem(
            "block_count",
            Py::Long(static_cast<unsigned long>(content.blockCount)));
        result.setItem(
            "fragment_count",
            Py::Long(static_cast<unsigned long>(content.fragmentCount)));
        result.setItem(
            "link_count",
            Py::Long(static_cast<unsigned long>(content.linkCount)));
        result.setItem(
            "has_rich_formatting",
            Py::Boolean(content.hasRichFormatting));
        return result;
    }

    Py::Dict drawingRichAnnotationFrame(
        const DrawingRichAnnotationFrameStyle& frame) const
    {
        Py::Dict result;
        result.setItem("visible", Py::Boolean(frame.visible));
        result.setItem("line_width_mm", Py::Float(frame.lineWidthMm));
        result.setItem(
            "line_style",
            Py::String(drawingRichAnnotationLineStyle(frame.lineStyle)));
        result.setItem("color_rgb", drawingHatchColor(frame.lineColor));
        return result;
    }

    Py::Dict drawingRichAnnotationWidth(double maximumWidthMm) const
    {
        Py::Dict result;
        if (maximumWidthMm == -1.0) {
            result.setItem("mode", Py::String("automatic"));
        }
        else {
            result.setItem("mode", Py::String("fixed"));
            result.setItem("value_mm", Py::Float(maximumWidthMm));
        }
        return result;
    }

    Py::Dict drawingRichAnnotationPlan(
        const DrawingRichAnnotationPlan& plan) const
    {
        Py::Dict owner;
        owner.setItem(
            "kind",
            Py::String(plan.owner ? "view" : "page"));
        if (plan.owner) {
            owner.setItem(
                "object_name",
                Py::String(plan.owner->getNameInDocument()));
        }
        Py::Dict placement;
        placement.setItem("x_mm", Py::Float(plan.xMm));
        placement.setItem("y_mm", Py::Float(plan.yMm));
        Py::Dict result;
        result.setItem(
            "page_name",
            Py::String(plan.page->getNameInDocument()));
        result.setItem("owner", owner);
        result.setItem("object_name", Py::String(plan.objectName));
        result.setItem("label", Py::String(plan.label));
        result.setItem("content", drawingRichAnnotationContent(plan.content));
        result.setItem("placement_on_page_mm", placement);
        result.setItem(
            "width",
            drawingRichAnnotationWidth(plan.maximumWidthMm));
        result.setItem("frame", drawingRichAnnotationFrame(plan.frame));
        return result;
    }

    Py::Object drawingRichAnnotationDefaults(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::TypeError("expected no arguments");
        }
        const auto defaults = TechDrawGui::drawingRichAnnotationDefaults();
        Py::Dict result;
        result.setItem(
            "width",
            drawingRichAnnotationWidth(defaults.maximumWidthMm));
        result.setItem("frame", drawingRichAnnotationFrame(defaults.frame));
        return result;
    }

    Py::Object drawingRichAnnotationOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        PyObject* ownerPy = nullptr;
        PyObject* contentKindPy = nullptr;
        PyObject* contentPy = nullptr;
        PyObject* labelPy = nullptr;
        double xMm = 0.0;
        double yMm = 0.0;
        double maximumWidthMm = 0.0;
        int frameVisible = 0;
        double frameWidthMm = 0.0;
        PyObject* frameStylePy = nullptr;
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOOOOdddpdOddd",
                &pagePy,
                &ownerPy,
                &contentKindPy,
                &contentPy,
                &labelPy,
                &xMm,
                &yMm,
                &maximumWidthMm,
                &frameVisible,
                &frameWidthMm,
                &frameStylePy,
                &red,
                &green,
                &blue)) {
            throw Py::TypeError(
                "expected (page, owner_or_none, content_kind, content, label, x_mm, "
                "y_mm, maximum_width_mm, frame_visible, frame_width_mm, "
                "frame_style, red, green, blue)");
        }
        auto* page = drawingPage(pagePy);
        auto* owner = ownerPy == Py_None ? nullptr : drawingView(ownerPy);
        const auto contentKind = drawingRichAnnotationContentKind(
            drawingRichAnnotationString(contentKindPy, "content_kind"));
        const std::string content = drawingRichAnnotationString(
            contentPy,
            "content");
        const std::string label = drawingRichAnnotationString(labelPy, "label");
        const int frameStyle = drawingRichAnnotationLineStyle(
            drawingRichAnnotationString(frameStylePy, "frame_style"));
        const DrawingRichAnnotationFrameStyle frame {
            frameVisible != 0,
            frameWidthMm,
            frameStyle,
            Base::Color(red, green, blue),
        };
        DrawingRichAnnotationPlan plan =
            TechDrawGui::validateDrawingRichAnnotation(
                page,
                owner,
                contentKind,
                content,
                label,
                xMm,
                yMm,
                maximumWidthMm,
                frame);
        TechDraw::DrawRichAnno* annotation = nullptr;
        if (apply) {
            annotation = TechDrawGui::createDrawingRichAnnotation(
                page,
                owner,
                contentKind,
                content,
                label,
                xMm,
                yMm,
                maximumWidthMm,
                frame,
                &plan);
        }
        Py::Dict result = drawingRichAnnotationPlan(plan);
        if (annotation) {
            result.setItem(
                "annotation",
                Py::asObject(annotation->getPyObject()));
        }
        return result;
    }

    Py::Object validateDrawingRichAnnotation(const Py::Tuple& args)
    {
        return drawingRichAnnotationOperation(args, false);
    }

    Py::Object createDrawingRichAnnotation(const Py::Tuple& args)
    {
        return drawingRichAnnotationOperation(args, true);
    }

    Py::Object inspectDrawingRichAnnotationContent(const Py::Tuple& args)
    {
        PyObject* annotationPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &annotationPy)) {
            throw Py::TypeError("expected (annotation)");
        }
        auto* annotation = drawingRichAnnotation(annotationPy);
        return drawingRichAnnotationContent(
            TechDrawGui::inspectDrawingRichAnnotationContent(
                annotation->AnnoText.getValue()));
    }

    DrawingSurfaceFinishStandard drawingSurfaceFinishStandard(
        const std::string& value) const
    {
        if (value == "iso") {
            return DrawingSurfaceFinishStandard::ISO;
        }
        if (value == "asme") {
            return DrawingSurfaceFinishStandard::ASME;
        }
        throw Py::ValueError("surface-finish standard must be iso or asme");
    }

    DrawingSurfaceFinishType drawingSurfaceFinishType(
        const std::string& value) const
    {
        if (value == "any_method") {
            return DrawingSurfaceFinishType::AnyMethod;
        }
        if (value == "removal_prohibited") {
            return DrawingSurfaceFinishType::RemovalProhibited;
        }
        if (value == "removal_required") {
            return DrawingSurfaceFinishType::RemovalRequired;
        }
        if (value == "any_method_all_around") {
            return DrawingSurfaceFinishType::AnyMethodAllAround;
        }
        if (value == "removal_prohibited_all_around") {
            return DrawingSurfaceFinishType::RemovalProhibitedAllAround;
        }
        if (value == "removal_required_all_around") {
            return DrawingSurfaceFinishType::RemovalRequiredAllAround;
        }
        throw Py::ValueError("surface-finish symbol_type is unsupported");
    }

    Py::Dict drawingSurfaceFinishPlan(
        const DrawingSurfaceFinishPlan& plan) const
    {
        Py::Dict result;
        result.setItem("object_name", Py::String(plan.objectName));
        result.setItem("label", Py::String(plan.label));
        result.setItem("x_mm", Py::Float(plan.xMm));
        result.setItem("y_mm", Py::Float(plan.yMm));
        result.setItem("svg_sha256", Py::String(plan.svgSha256));
        return result;
    }

    Py::Object drawingSurfaceFinishOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        PyObject* ownerPy = nullptr;
        double xMm = 0.0;
        double yMm = 0.0;
        const char* standard = nullptr;
        const char* symbolType = nullptr;
        const char* method = nullptr;
        const char* allowance = nullptr;
        const char* lay = nullptr;
        const char* isoRoughness = nullptr;
        const char* samplingLength = nullptr;
        const char* minimumGrade = nullptr;
        const char* maximumGrade = nullptr;
        double rotation = 0.0;
        const char* label = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOdd" "sssssssss" "ds",
                &pagePy,
                &ownerPy,
                &xMm,
                &yMm,
                &standard,
                &symbolType,
                &method,
                &allowance,
                &lay,
                &isoRoughness,
                &samplingLength,
                &minimumGrade,
                &maximumGrade,
                &rotation,
                &label)) {
            throw Py::TypeError(
                "expected (page, owner_or_none, x_mm, y_mm, standard, symbol_type, "
                "method, allowance, lay, iso_roughness, sampling_length, "
                "minimum_grade, maximum_grade, rotation_degrees, label)");
        }
        auto* page = drawingPage(pagePy);
        auto* owner = ownerPy == Py_None ? nullptr : drawingView(ownerPy);
        const DrawingSurfaceFinishSpec spec {
            drawingSurfaceFinishStandard(standard ? standard : ""),
            drawingSurfaceFinishType(symbolType ? symbolType : ""),
            method ? method : "",
            allowance ? allowance : "",
            lay ? lay : "",
            isoRoughness ? isoRoughness : "",
            samplingLength ? samplingLength : "",
            minimumGrade ? minimumGrade : "",
            maximumGrade ? maximumGrade : "",
            rotation,
            label ? label : "",
        };
        DrawingSurfaceFinishPlan plan = TechDrawGui::validateDrawingSurfaceFinishSymbol(
            page, owner, xMm, yMm, spec);
        TechDraw::DrawViewSymbol* symbol = nullptr;
        if (apply) {
            symbol = TechDrawGui::createDrawingSurfaceFinishSymbol(
                page, owner, xMm, yMm, spec, &plan);
        }
        Py::Dict result = drawingSurfaceFinishPlan(plan);
        if (symbol) {
            result.setItem("symbol", Py::asObject(symbol->getPyObject()));
        }
        return result;
    }

    Py::Object validateDrawingSurfaceFinishSymbol(const Py::Tuple& args)
    {
        return drawingSurfaceFinishOperation(args, false);
    }

    Py::Object createDrawingSurfaceFinishSymbol(const Py::Tuple& args)
    {
        return drawingSurfaceFinishOperation(args, true);
    }

    Py::Object drawingWeldSymbolCatalog(const Py::Tuple& args)
    {
        if (args.size() != 0) {
            throw Py::TypeError("expected no arguments");
        }
        Py::List items;
        for (const auto& item : TechDrawGui::drawingWeldSymbolCatalog()) {
            Py::Dict entry;
            entry.setItem("key", Py::String(item.key));
            entry.setItem("svg_sha256", Py::String(item.svgSha256));
            items.append(entry);
        }
        Py::Dict result;
        result.setItem(
            "catalog_sha256",
            Py::String(TechDrawGui::drawingWeldSymbolCatalogHash()));
        result.setItem("items", items);
        return result;
    }

    Py::Dict drawingWeldSymbolPlan(
        const DrawingWeldSymbolPlan& plan,
        bool create) const
    {
        Py::Dict result;
        result.setItem("mode", Py::String(create ? "create" : "edit"));
        result.setItem("object_name", Py::String(plan.objectName));
        result.setItem("label", Py::String(plan.label));
        result.setItem(
            "catalog_sha256",
            Py::String(TechDrawGui::drawingWeldSymbolCatalogHash()));
        return result;
    }

    Py::Object drawingWeldSymbolOperation(const Py::Tuple& args, bool apply)
    {
        PyObject* targetPy = nullptr;
        int create = 0;
        int allAround = 0;
        int fieldWeld = 0;
        int alternating = 0;
        const char* tail = nullptr;
        const char* arrowLeft = nullptr;
        const char* arrowCenter = nullptr;
        const char* arrowRight = nullptr;
        const char* arrowKey = nullptr;
        const char* otherLeft = nullptr;
        const char* otherCenter = nullptr;
        const char* otherRight = nullptr;
        const char* otherKey = nullptr;
        const char* label = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "Opppp" "ssssssssss",
                &targetPy,
                &create,
                &allAround,
                &fieldWeld,
                &alternating,
                &tail,
                &arrowLeft,
                &arrowCenter,
                &arrowRight,
                &arrowKey,
                &otherLeft,
                &otherCenter,
                &otherRight,
                &otherKey,
                &label)) {
            throw Py::TypeError(
                "expected (target, create, all_around, field_weld, alternating, "
                "tail, arrow_left, arrow_center, arrow_right, arrow_key, "
                "other_left, other_center, other_right, other_key, label)");
        }
        const DrawingWeldSymbolSpec spec {
            allAround != 0,
            fieldWeld != 0,
            alternating != 0,
            tail ? tail : "",
            {
                arrowLeft ? arrowLeft : "",
                arrowCenter ? arrowCenter : "",
                arrowRight ? arrowRight : "",
                TechDrawGui::drawingWeldSymbolFileForCatalogKey(arrowKey ? arrowKey : ""),
            },
            {
                otherLeft ? otherLeft : "",
                otherCenter ? otherCenter : "",
                otherRight ? otherRight : "",
                TechDrawGui::drawingWeldSymbolFileForCatalogKey(otherKey ? otherKey : ""),
            },
            label ? label : "",
        };
        DrawingWeldSymbolPlan plan = create
            ? TechDrawGui::validateDrawingWeldSymbolCreation(drawingLeader(targetPy), spec)
            : TechDrawGui::validateDrawingWeldSymbolChange(drawingWeldSymbol(targetPy), spec);
        TechDraw::DrawWeldSymbol* symbol = nullptr;
        if (apply) {
            symbol = create
                ? TechDrawGui::createDrawingWeldSymbol(drawingLeader(targetPy), spec, &plan)
                : TechDrawGui::changeDrawingWeldSymbol(drawingWeldSymbol(targetPy), spec, &plan);
        }
        Py::Dict result = drawingWeldSymbolPlan(plan, create != 0);
        if (symbol) {
            result.setItem("symbol", Py::asObject(symbol->getPyObject()));
        }
        return result;
    }

    Py::Object validateDrawingWeldSymbol(const Py::Tuple& args)
    {
        return drawingWeldSymbolOperation(args, false);
    }

    Py::Object changeDrawingWeldSymbol(const Py::Tuple& args)
    {
        return drawingWeldSymbolOperation(args, true);
    }

    int drawingLeaderArrow(const std::string& value) const
    {
        static constexpr std::array<const char*, 8> names {
            "filled_arrow",
            "open_arrow",
            "tick",
            "dot",
            "open_circle",
            "fork",
            "filled_triangle",
            "none",
        };
        const auto found = std::ranges::find(names, value);
        if (found == names.end()) {
            throw Py::ValueError(
                "leader symbol must be filled_arrow, open_arrow, tick, dot, "
                "open_circle, fork, filled_triangle, or none");
        }
        return static_cast<int>(std::distance(names.begin(), found));
    }

    const char* drawingLeaderArrow(int value) const
    {
        static constexpr std::array<const char*, 8> names {
            "filled_arrow",
            "open_arrow",
            "tick",
            "dot",
            "open_circle",
            "fork",
            "filled_triangle",
            "none",
        };
        if (value < 0 || value >= static_cast<int>(names.size())) {
            throw Py::ValueError("stored leader symbol is invalid");
        }
        return names.at(static_cast<std::size_t>(value));
    }

    int drawingLeaderLineStyle(const std::string& value) const
    {
        static constexpr std::array<const char*, 6> names {
            "no_line",
            "continuous",
            "dash",
            "dot",
            "dash_dot",
            "dash_dot_dot",
        };
        const auto found = std::ranges::find(names, value);
        if (found == names.end()) {
            throw Py::ValueError(
                "leader line_style must be no_line, continuous, dash, dot, "
                "dash_dot, or dash_dot_dot");
        }
        return static_cast<int>(std::distance(names.begin(), found));
    }

    const char* drawingLeaderLineStyle(int value) const
    {
        static constexpr std::array<const char*, 6> names {
            "no_line",
            "continuous",
            "dash",
            "dot",
            "dash_dot",
            "dash_dot_dot",
        };
        if (value < 0 || value >= static_cast<int>(names.size())) {
            throw Py::ValueError("stored leader line style is invalid");
        }
        return names.at(static_cast<std::size_t>(value));
    }

    Py::Dict drawingLeaderPoint(const Base::Vector3d& point) const
    {
        Py::Dict result;
        result.setItem("x_mm", Py::Float(point.x));
        result.setItem("y_mm", Py::Float(point.y));
        return result;
    }

    Py::List drawingLeaderPoints(
        const std::vector<Base::Vector3d>& points) const
    {
        Py::List result;
        for (const auto& point : points) {
            result.append(drawingLeaderPoint(point));
        }
        return result;
    }

    std::vector<Base::Vector3d> drawingLeaderPoints(PyObject* value) const
    {
        Py::Sequence points(value);
        std::vector<Base::Vector3d> result;
        result.reserve(points.length());
        for (const auto& item : points) {
            Py::Sequence pair(item);
            if (pair.length() != 2) {
                throw Py::TypeError(
                    "each leader page point must be a two-number sequence");
            }
            const double x = PyFloat_AsDouble(pair[0].ptr());
            if (PyErr_Occurred()) {
                throw Py::TypeError("leader point X must be numeric");
            }
            const double y = PyFloat_AsDouble(pair[1].ptr());
            if (PyErr_Occurred()) {
                throw Py::TypeError("leader point Y must be numeric");
            }
            result.emplace_back(x, y, 0.0);
        }
        return result;
    }

    Py::Dict drawingLeaderStyle(const DrawingLeaderStyle& style) const
    {
        Py::Dict symbols;
        symbols.setItem(
            "start",
            Py::String(drawingLeaderArrow(style.startSymbol)));
        symbols.setItem(
            "end",
            Py::String(drawingLeaderArrow(style.endSymbol)));

        Py::Dict behavior;
        behavior.setItem("scalable", Py::Boolean(style.scalable));
        behavior.setItem(
            "auto_horizontal",
            Py::Boolean(style.autoHorizontal));
        behavior.setItem(
            "rotates_with_owner",
            Py::Boolean(style.rotatesWithParent));

        Py::Dict line;
        line.setItem("line_width_mm", Py::Float(style.lineWidthMm));
        line.setItem(
            "line_style",
            Py::String(drawingLeaderLineStyle(style.lineStyle)));
        line.setItem("color_rgb", drawingHatchColor(style.lineColor));

        Py::Dict result;
        result.setItem("symbols", symbols);
        result.setItem("behavior", behavior);
        result.setItem("line", line);
        return result;
    }

    Py::Dict drawingLeaderPlan(const DrawingLeaderPlan& plan) const
    {
        Py::Dict ownerTransform;
        ownerTransform.setItem(
            "position_on_page_mm",
            drawingLeaderPoint(plan.ownerPositionOnPageMm));
        ownerTransform.setItem("scale", Py::Float(plan.ownerScale));
        ownerTransform.setItem(
            "rotation_degrees",
            Py::Float(plan.ownerRotationDegrees));

        Py::Dict stored;
        stored.setItem(
            "anchor_in_owner_mm",
            drawingLeaderPoint(plan.anchorInOwnerMm));
        stored.setItem(
            "waypoints_in_owner_mm",
            drawingLeaderPoints(plan.storedWayPoints));

        Py::Dict result;
        result.setItem(
            "page_name",
            Py::String(plan.page->getNameInDocument()));
        result.setItem(
            "owner_name",
            Py::String(plan.owner->getNameInDocument()));
        result.setItem("object_name", Py::String(plan.objectName));
        result.setItem("label", Py::String(plan.label));
        result.setItem(
            "requested_points_on_page_mm",
            drawingLeaderPoints(plan.requestedPointsOnPageMm));
        result.setItem("owner_transform", ownerTransform);
        result.setItem("stored", stored);
        result.setItem(
            "rendered_points_on_page_mm",
            drawingLeaderPoints(plan.renderedPointsOnPageMm));
        const Py::Dict style = drawingLeaderStyle(plan.style);
        result.setItem("symbols", style.getItem("symbols"));
        result.setItem("behavior", style.getItem("behavior"));
        result.setItem("line", style.getItem("line"));
        return result;
    }

    Py::Object drawingLeaderDefaults(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::TypeError("expected no arguments");
        }
        return drawingLeaderStyle(
            TechDrawGui::drawingLeaderDefaults().style);
    }

    Py::Object drawingLeaderOperation(
        const Py::Tuple& args,
        bool apply)
    {
        PyObject* pagePy = nullptr;
        PyObject* ownerPy = nullptr;
        PyObject* pointsPy = nullptr;
        const char* label = nullptr;
        const char* startSymbol = nullptr;
        const char* endSymbol = nullptr;
        int scalable = 0;
        int autoHorizontal = 0;
        int rotatesWithOwner = 0;
        double lineWidthMm = 0.0;
        const char* lineStyle = nullptr;
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOOssspppdsddd",
                &pagePy,
                &ownerPy,
                &pointsPy,
                &label,
                &startSymbol,
                &endSymbol,
                &scalable,
                &autoHorizontal,
                &rotatesWithOwner,
                &lineWidthMm,
                &lineStyle,
                &red,
                &green,
                &blue)) {
            throw Py::TypeError(
                "expected (page, owner, page_points, label, start_symbol, "
                "end_symbol, scalable, auto_horizontal, rotates_with_owner, "
                "line_width_mm, line_style, red, green, blue)");
        }
        const DrawingLeaderStyle style {
            drawingLeaderArrow(startSymbol ? startSymbol : ""),
            drawingLeaderArrow(endSymbol ? endSymbol : ""),
            scalable != 0,
            autoHorizontal != 0,
            rotatesWithOwner != 0,
            lineWidthMm,
            drawingLeaderLineStyle(lineStyle ? lineStyle : ""),
            Base::Color(red, green, blue),
        };
        auto* page = drawingPage(pagePy);
        auto* owner = drawingView(ownerPy);
        const auto points = drawingLeaderPoints(pointsPy);
        DrawingLeaderPlan plan = TechDrawGui::validateDrawingLeaderLine(
            page,
            owner,
            points,
            label ? label : "",
            style);
        TechDraw::DrawLeaderLine* leader = nullptr;
        if (apply) {
            leader = TechDrawGui::createDrawingLeaderLine(
                page,
                owner,
                points,
                label ? label : "",
                style,
                &plan);
        }
        Py::Dict result = drawingLeaderPlan(plan);
        if (leader) {
            result.setItem("leader", Py::asObject(leader->getPyObject()));
        }
        return result;
    }

    Py::Object validateDrawingLeaderLine(const Py::Tuple& args)
    {
        return drawingLeaderOperation(args, false);
    }

    Py::Object createDrawingLeaderLine(const Py::Tuple& args)
    {
        return drawingLeaderOperation(args, true);
    }

    Py::Object validateDrawingFormatCustomization(const Py::Tuple& args)
    {
        PyObject* targetPy = nullptr;
        char* value = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Oes", &targetPy, "utf-8", &value)) {
            throw Py::TypeError("expected (target, value)");
        }
        try {
            const auto result =
                TechDrawGui::validateDrawingFormatCustomization(
                    drawingFormatTarget(targetPy),
                    value ? value : "");
            PyMem_Free(value);
            return drawingFormatResult(result);
        }
        catch (...) {
            PyMem_Free(value);
            throw;
        }
    }

    Py::Object applyDrawingFormatCustomization(const Py::Tuple& args)
    {
        PyObject* targetPy = nullptr;
        char* value = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Oes", &targetPy, "utf-8", &value)) {
            throw Py::TypeError("expected (target, value)");
        }
        try {
            const auto result = TechDrawGui::applyDrawingFormatCustomization(
                drawingFormatTarget(targetPy),
                value ? value : "");
            PyMem_Free(value);
            return drawingFormatResult(result);
        }
        catch (...) {
            PyMem_Free(value);
            throw;
        }
    }

    Py::Dict drawingCircleCenterLinePoint(
        const Base::Vector3d& value) const
    {
        Py::Dict result;
        result.setItem("x_mm", Py::Float(value.x));
        result.setItem("y_mm", Py::Float(value.y));
        return result;
    }

    Py::Dict drawingCircleCenterLineSegment(
        const Base::Vector3d& start,
        const Base::Vector3d& end,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem("start_in_view_mm", drawingCircleCenterLinePoint(start));
        result.setItem("end_in_view_mm", drawingCircleCenterLinePoint(end));
        if (!tag.empty()) {
            result.setItem("tag", Py::String(tag));
        }
        return result;
    }

    Py::Dict drawingCircleCenterLineFormat(
        const TechDraw::LineFormat& format) const
    {
        const Base::Color colorValue = format.getColor();
        Py::Dict color;
        color.setItem("red", Py::Float(colorValue.r));
        color.setItem("green", Py::Float(colorValue.g));
        color.setItem("blue", Py::Float(colorValue.b));
        Py::Dict result;
        result.setItem("line_number", Py::Long(format.getLineNumber()));
        result.setItem("style_code", Py::Long(format.getStyle()));
        result.setItem("width_mm", Py::Float(format.getWidth()));
        result.setItem("color_rgb", color);
        result.setItem("visible", Py::Boolean(format.getVisible()));
        return result;
    }

    Py::Dict drawingCircleCenterLinePlan(
        const DrawingCircleCenterLinePlan& plan,
        const std::string& horizontalTag = {},
        const std::string& verticalTag = {}) const
    {
        Py::Dict result;
        result.setItem(
            "source_subelement",
            Py::String(plan.sourceSelectionName));
        result.setItem(
            "geometry_configuration",
            Py::String(plan.geometryConfiguration));
        result.setItem(
            "center_in_view_mm",
            drawingCircleCenterLinePoint(plan.centerInViewMm));
        result.setItem("radius_mm", Py::Float(plan.radiusMm));
        result.setItem(
            "outside_extension_mm",
            Py::Float(plan.outsideExtensionMm));
        result.setItem(
            "horizontal",
            drawingCircleCenterLineSegment(
                plan.horizontalStartInViewMm,
                plan.horizontalEndInViewMm,
                horizontalTag));
        result.setItem(
            "vertical",
            drawingCircleCenterLineSegment(
                plan.verticalStartInViewMm,
                plan.verticalEndInViewMm,
                verticalTag));
        result.setItem("line_format", drawingCircleCenterLineFormat(plan.format));
        return result;
    }

    Py::Object validateDrawingCircleCenterLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        Py::List pairs;
        for (const auto& plan : TechDrawGui::validateDrawingCircleCenterLines(
                 drawingPart(viewPy),
                 projectedCircleElements(elementsPy))) {
            pairs.append(drawingCircleCenterLinePlan(plan));
        }
        return pairs;
    }

    Py::Object createDrawingCircleCenterLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        Py::List pairs;
        for (const auto& created : TechDrawGui::createDrawingCircleCenterLines(
                 drawingPart(viewPy),
                 projectedCircleElements(elementsPy))) {
            pairs.append(drawingCircleCenterLinePlan(
                created.plan,
                created.horizontalTag,
                created.verticalTag));
        }
        return pairs;
    }

    Py::Dict drawingBoltCircleCenterLinePlan(
        const DrawingBoltCircleCenterLinePlan& plan,
        const std::string& patternCircleTag = {},
        const std::vector<std::string>& holeCenterLineTags = {}) const
    {
        if (!holeCenterLineTags.empty()
            && holeCenterLineTags.size() != plan.holes.size()) {
            throw Py::RuntimeError(
                "the bolt-circle host returned an incomplete persistent result");
        }
        Py::Dict result;
        result.setItem(
            "pattern_center_in_view_mm",
            drawingCircleCenterLinePoint(plan.patternCenterInViewMm));
        result.setItem("pattern_radius_mm", Py::Float(plan.patternRadiusMm));
        result.setItem(
            "maximum_pattern_radius_deviation_mm",
            Py::Float(plan.maximumPatternRadiusDeviationMm));
        result.setItem(
            "pattern_radius_tolerance_mm",
            Py::Float(plan.patternRadiusToleranceMm));
        result.setItem(
            "all_centers_on_pattern",
            Py::Boolean(plan.allCentersOnPattern));
        result.setItem(
            "hole_center_line_extension_factor",
            Py::Float(plan.holeCenterLineExtensionFactor));
        result.setItem("line_format", drawingCircleCenterLineFormat(plan.format));
        if (!patternCircleTag.empty()) {
            result.setItem(
                "pattern_circle_tag",
                Py::String(patternCircleTag));
        }
        Py::List holes;
        for (std::size_t index = 0; index < plan.holes.size(); ++index) {
            const auto& hole = plan.holes.at(index);
            Py::Dict item;
            item.setItem(
                "source_subelement",
                Py::String(hole.sourceSelectionName));
            item.setItem(
                "geometry_configuration",
                Py::String(hole.geometryConfiguration));
            item.setItem(
                "center_in_view_mm",
                drawingCircleCenterLinePoint(hole.centerInViewMm));
            item.setItem("radius_mm", Py::Float(hole.radiusMm));
            item.setItem(
                "pattern_radius_at_center_mm",
                Py::Float(hole.patternRadiusAtCenterMm));
            item.setItem(
                "pattern_radius_deviation_mm",
                Py::Float(hole.patternRadiusDeviationMm));
            item.setItem(
                "center_line",
                drawingCircleCenterLineSegment(
                    hole.centerLineStartInViewMm,
                    hole.centerLineEndInViewMm,
                    holeCenterLineTags.empty()
                        ? std::string()
                        : holeCenterLineTags.at(index)));
            holes.append(item);
        }
        result.setItem("holes", holes);
        return result;
    }

    Py::Object validateDrawingBoltCircleCenterLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        return drawingBoltCircleCenterLinePlan(
            TechDrawGui::validateDrawingBoltCircleCenterLines(
                drawingPart(viewPy),
                projectedCircleElements(elementsPy)));
    }

    Py::Object createDrawingBoltCircleCenterLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        const auto created = TechDrawGui::createDrawingBoltCircleCenterLines(
            drawingPart(viewPy),
            projectedCircleElements(elementsPy));
        return drawingBoltCircleCenterLinePlan(
            created.plan,
            created.patternCircleTag,
            created.holeCenterLineTags);
    }

    Py::Object drawingPersistentCosmeticCircle(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        const auto state = TechDrawGui::drawingPersistentCosmeticCircleState(
            drawingPart(viewPy),
            tag ? tag : "");
        Py::Dict result;
        result.setItem("tag", Py::String(state.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem(
            "center_in_view_mm",
            drawingCircleCenterLinePoint(state.centerInViewMm));
        result.setItem("radius_mm", Py::Float(state.radiusMm));
        result.setItem("line_format", drawingCircleCenterLineFormat(state.format));
        return result;
    }

    DrawingThreadRepresentationKind drawingThreadKind(
        const std::string& name) const
    {
        if (name == "hole_side") {
            return DrawingThreadRepresentationKind::HoleSide;
        }
        if (name == "hole_bottom") {
            return DrawingThreadRepresentationKind::HoleBottom;
        }
        if (name == "bolt_side") {
            return DrawingThreadRepresentationKind::BoltSide;
        }
        if (name == "bolt_bottom") {
            return DrawingThreadRepresentationKind::BoltBottom;
        }
        throw Py::ValueError(
            "thread kind must be hole_side, hole_bottom, bolt_side, or bolt_bottom");
    }

    std::string drawingThreadKindName(
        DrawingThreadRepresentationKind kind) const
    {
        switch (kind) {
            case DrawingThreadRepresentationKind::HoleSide:
                return "hole_side";
            case DrawingThreadRepresentationKind::HoleBottom:
                return "hole_bottom";
            case DrawingThreadRepresentationKind::BoltSide:
                return "bolt_side";
            case DrawingThreadRepresentationKind::BoltBottom:
                return "bolt_bottom";
        }
        throw Py::RuntimeError("the thread representation kind is invalid");
    }

    Py::Dict drawingThreadLinePlan(
        const DrawingThreadLinePlan& line,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem("role", Py::String(line.role));
        result.setItem(
            "segment",
            drawingCircleCenterLineSegment(
                line.startInViewMm,
                line.endInViewMm,
                tag));
        result.setItem("line_format", drawingCircleCenterLineFormat(line.format));
        return result;
    }

    Py::Dict drawingThreadSidePlan(
        const DrawingThreadSidePlan& plan,
        const std::vector<std::string>& tags = {}) const
    {
        if (!tags.empty() && tags.size() != plan.lines.size()) {
            throw Py::RuntimeError(
                "the thread side host returned incomplete persistent tags");
        }
        Py::List sources;
        for (const auto& source : plan.sourceSelectionNames) {
            sources.append(Py::String(source));
        }
        Py::List lines;
        for (std::size_t index = 0; index < plan.lines.size(); ++index) {
            lines.append(drawingThreadLinePlan(
                plan.lines.at(index),
                tags.empty() ? std::string() : tags.at(index)));
        }
        Py::Dict sourceLines;
        sourceLines.setItem(
            "first",
            drawingCircleCenterLineSegment(
                plan.firstStartInViewMm,
                plan.firstEndInViewMm));
        sourceLines.setItem(
            "second",
            drawingCircleCenterLineSegment(
                plan.secondStartInViewMm,
                plan.secondEndInViewMm));
        Py::Dict result;
        result.setItem("kind", Py::String(drawingThreadKindName(plan.kind)));
        result.setItem("thread_factor", Py::Float(plan.threadFactor));
        result.setItem("source_diameter_mm", Py::Float(plan.sourceDiameterMm));
        result.setItem("source_subelements", sources);
        result.setItem("source_lines", sourceLines);
        result.setItem("lines", lines);
        return result;
    }

    Py::Dict drawingThreadBottomPlan(
        const DrawingThreadBottomPlan& plan,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem("kind", Py::String(drawingThreadKindName(plan.kind)));
        result.setItem(
            "source_subelement",
            Py::String(plan.sourceSelectionName));
        result.setItem(
            "center_in_view_mm",
            drawingCircleCenterLinePoint(plan.centerInViewMm));
        result.setItem("source_radius_mm", Py::Float(plan.sourceRadiusMm));
        result.setItem("thread_factor", Py::Float(plan.threadFactor));
        result.setItem("thread_radius_mm", Py::Float(plan.threadRadiusMm));
        result.setItem(
            "start_angle_degrees",
            Py::Float(plan.startAngleDegrees));
        result.setItem("end_angle_degrees", Py::Float(plan.endAngleDegrees));
        result.setItem("line_format", drawingCircleCenterLineFormat(plan.format));
        if (!tag.empty()) {
            result.setItem("arc_tag", Py::String(tag));
        }
        return result;
    }

    Py::Object validateDrawingThreadSide(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, edges)");
        }
        return drawingThreadSidePlan(TechDrawGui::validateDrawingThreadSide(
            drawingPart(viewPy),
            drawingThreadKind(kind ? kind : ""),
            projectedThreadSideElements(elementsPy)));
    }

    Py::Object createDrawingThreadSide(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, edges)");
        }
        const auto created = TechDrawGui::createDrawingThreadSide(
            drawingPart(viewPy),
            drawingThreadKind(kind ? kind : ""),
            projectedThreadSideElements(elementsPy));
        return drawingThreadSidePlan(created.plan, created.lineTags);
    }

    Py::Object validateDrawingThreadBottom(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, circles)");
        }
        Py::List result;
        for (const auto& plan : TechDrawGui::validateDrawingThreadBottom(
                 drawingPart(viewPy),
                 drawingThreadKind(kind ? kind : ""),
                 projectedCircleElements(elementsPy))) {
            result.append(drawingThreadBottomPlan(plan));
        }
        return result;
    }

    Py::Object createDrawingThreadBottom(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, circles)");
        }
        Py::List result;
        for (const auto& created : TechDrawGui::createDrawingThreadBottom(
                 drawingPart(viewPy),
                 drawingThreadKind(kind ? kind : ""),
                 projectedCircleElements(elementsPy))) {
            result.append(drawingThreadBottomPlan(
                created.plan,
                created.arcTag));
        }
        return result;
    }

    Py::Object drawingPersistentCosmeticArc(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        const auto state = TechDrawGui::drawingPersistentCosmeticArcState(
            drawingPart(viewPy),
            tag ? tag : "");
        Py::Dict result;
        result.setItem("tag", Py::String(state.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem(
            "center_in_view_mm",
            drawingCircleCenterLinePoint(state.centerInViewMm));
        result.setItem("radius_mm", Py::Float(state.radiusMm));
        result.setItem(
            "start_angle_degrees",
            Py::Float(state.startAngleDegrees));
        result.setItem("end_angle_degrees", Py::Float(state.endAngleDegrees));
        result.setItem("clockwise", Py::Boolean(state.clockwise));
        result.setItem("line_format", drawingCircleCenterLineFormat(state.format));
        return result;
    }

    Py::Dict drawingCosmeticVertexFormat(
        const DrawingCosmeticVertexFormat& format) const
    {
        Py::Dict color;
        color.setItem("red", Py::Float(format.color.r));
        color.setItem("green", Py::Float(format.color.g));
        color.setItem("blue", Py::Float(format.color.b));
        Py::Dict result;
        result.setItem("color_rgb", color);
        result.setItem("size_mm", Py::Float(format.size));
        result.setItem("style_code", Py::Long(format.style));
        result.setItem("visible", Py::Boolean(format.visible));
        return result;
    }

    Py::Dict drawingCosmeticVertexPointPlan(
        const DrawingCosmeticVertexPointPlan& plan,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem(
            "point_in_view_mm",
            drawingCircleCenterLinePoint(plan.pointInViewMm));
        result.setItem(
            "vertex_format",
            drawingCosmeticVertexFormat(plan.format));
        if (!tag.empty()) {
            result.setItem("tag", Py::String(tag));
        }
        return result;
    }

    Py::Object validateDrawingCosmeticVertexPoint(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "Odd", &viewPy, &x, &y)) {
            throw Py::TypeError("expected (view, x, y)");
        }
        return drawingCosmeticVertexPointPlan(
            TechDrawGui::validateDrawingCosmeticVertexPoint(
                drawingPart(viewPy),
                Base::Vector3d(x, y, 0.0)));
    }

    Py::Object createDrawingCosmeticVertexPoint(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "Odd", &viewPy, &x, &y)) {
            throw Py::TypeError("expected (view, x, y)");
        }
        const auto created = TechDrawGui::createDrawingCosmeticVertexPoint(
            drawingPart(viewPy),
            Base::Vector3d(x, y, 0.0));
        return drawingCosmeticVertexPointPlan(created.plan, created.vertexTag);
    }

    Py::Dict drawingVertexIntersectionPlan(
        const DrawingVertexIntersectionPlan& plan,
        const std::vector<std::string>& tags = {}) const
    {
        if (!tags.empty() && tags.size() != plan.vertices.size()) {
            throw Py::RuntimeError(
                "the intersection host returned incomplete persistent tags");
        }
        Py::List sources;
        for (const auto& source : plan.sourceSelectionNames) {
            sources.append(Py::String(source));
        }
        Py::List vertices;
        for (std::size_t index = 0; index < plan.vertices.size(); ++index) {
            vertices.append(drawingCosmeticVertexPointPlan(
                plan.vertices.at(index),
                tags.empty() ? std::string() : tags.at(index)));
        }
        Py::Dict result;
        result.setItem("source_subelements", sources);
        result.setItem("vertices", vertices);
        return result;
    }

    Py::Object validateDrawingVertexIntersections(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        return drawingVertexIntersectionPlan(
            TechDrawGui::validateDrawingVertexIntersections(
                drawingPart(viewPy),
                projectedIntersectionElements(elementsPy)));
    }

    Py::Object createDrawingVertexIntersections(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        const auto created = TechDrawGui::createDrawingVertexIntersections(
            drawingPart(viewPy),
            projectedIntersectionElements(elementsPy));
        return drawingVertexIntersectionPlan(created.plan, created.vertexTags);
    }

    Py::Dict drawingMidpointVerticesPlan(
        const DrawingMidpointVerticesPlan& plan,
        const std::vector<std::string>& tags = {}) const
    {
        if (!tags.empty() && tags.size() != plan.midpoints.size()) {
            throw Py::RuntimeError(
                "the midpoint host returned incomplete persistent tags");
        }
        Py::List midpoints;
        for (std::size_t index = 0; index < plan.midpoints.size(); ++index) {
            const auto& midpoint = plan.midpoints.at(index);
            Py::Dict item;
            item.setItem(
                "source_subelement",
                Py::String(midpoint.sourceSelectionName));
            item.setItem(
                "vertex",
                drawingCosmeticVertexPointPlan(
                    midpoint.vertex,
                    tags.empty() ? std::string() : tags.at(index)));
            midpoints.append(item);
        }
        Py::Dict result;
        result.setItem("midpoints", midpoints);
        return result;
    }

    Py::Object validateDrawingMidpointVertices(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        return drawingMidpointVerticesPlan(
            TechDrawGui::validateDrawingMidpointVertices(
                drawingPart(viewPy),
                projectedMidpointElements(elementsPy)));
    }

    Py::Object createDrawingMidpointVertices(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        const auto created = TechDrawGui::createDrawingMidpointVertices(
            drawingPart(viewPy),
            projectedMidpointElements(elementsPy));
        return drawingMidpointVerticesPlan(created.plan, created.vertexTags);
    }

    Py::Dict drawingQuadrantVerticesPlan(
        const DrawingQuadrantVerticesPlan& plan,
        const std::vector<std::string>& tags = {}) const
    {
        std::size_t expectedTagCount = 0;
        for (const auto& source : plan.sources) {
            expectedTagCount += source.vertices.size();
        }
        if (!tags.empty() && tags.size() != expectedTagCount) {
            throw Py::RuntimeError(
                "the quadrant host returned incomplete persistent tags");
        }
        std::size_t tagIndex = 0;
        Py::List sources;
        for (const auto& source : plan.sources) {
            Py::List vertices;
            for (const auto& vertex : source.vertices) {
                vertices.append(drawingCosmeticVertexPointPlan(
                    vertex,
                    tags.empty() ? std::string() : tags.at(tagIndex++)));
            }
            Py::Dict item;
            item.setItem(
                "source_subelement",
                Py::String(source.sourceSelectionName));
            item.setItem("vertices", vertices);
            sources.append(item);
        }
        Py::Dict result;
        result.setItem("sources", sources);
        return result;
    }

    Py::Object validateDrawingQuadrantVertices(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        return drawingQuadrantVerticesPlan(
            TechDrawGui::validateDrawingQuadrantVertices(
                drawingPart(viewPy),
                projectedQuadrantElements(elementsPy)));
    }

    Py::Object createDrawingQuadrantVertices(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, edges)");
        }
        const auto created = TechDrawGui::createDrawingQuadrantVertices(
            drawingPart(viewPy),
            projectedQuadrantElements(elementsPy));
        return drawingQuadrantVerticesPlan(created.plan, created.vertexTags);
    }

    Py::Dict drawingOffsetVertexPlan(
        const DrawingOffsetVertexPlan& plan,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem(
            "source_subelement",
            Py::String(plan.sourceSelectionName));
        result.setItem(
            "source_point_in_view_mm",
            drawingCircleCenterLinePoint(plan.sourcePointInViewMm));
        result.setItem(
            "offset_mm",
            drawingCircleCenterLinePoint(plan.offsetInViewMm));
        result.setItem(
            "vertex",
            drawingCosmeticVertexPointPlan(plan.vertex, tag));
        return result;
    }

    Py::Object validateDrawingOffsetVertex(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* source = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "Osdd", &viewPy, &source, &x, &y)) {
            throw Py::TypeError("expected (view, vertex, x, y)");
        }
        return drawingOffsetVertexPlan(
            TechDrawGui::validateDrawingOffsetVertex(
                drawingPart(viewPy),
                source ? source : "",
                Base::Vector3d(x, y, 0.0)));
    }

    Py::Object createDrawingOffsetVertex(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* source = nullptr;
        double x = 0.0;
        double y = 0.0;
        if (!PyArg_ParseTuple(args.ptr(), "Osdd", &viewPy, &source, &x, &y)) {
            throw Py::TypeError("expected (view, vertex, x, y)");
        }
        const auto created = TechDrawGui::createDrawingOffsetVertex(
            drawingPart(viewPy),
            source ? source : "",
            Base::Vector3d(x, y, 0.0));
        return drawingOffsetVertexPlan(created.plan, created.vertexTag);
    }

    Py::Dict drawingPersistentCosmeticVertexResult(
        const DrawingPersistentCosmeticVertexState& state) const
    {
        Py::Dict result;
        result.setItem("tag", Py::String(state.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem(
            "point_in_view_mm",
            drawingCircleCenterLinePoint(state.pointInViewMm));
        result.setItem(
            "vertex_format",
            drawingCosmeticVertexFormat(state.format));
        return result;
    }

    Py::Object drawingPersistentCosmeticVertex(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        return drawingPersistentCosmeticVertexResult(
            TechDrawGui::drawingPersistentCosmeticVertexState(
                drawingPart(viewPy),
                tag ? tag : ""));
    }

    Py::Object drawingCosmeticVertices(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : TechDrawGui::drawingCosmeticVertexStates(
                 drawingPart(viewPy))) {
            result.append(drawingPersistentCosmeticVertexResult(state));
        }
        return result;
    }

    DrawingGeneralCenterLineKind drawingGeneralCenterLineKind(
        const std::string& value) const
    {
        if (value == "face") {
            return DrawingGeneralCenterLineKind::Face;
        }
        if (value == "between_edges") {
            return DrawingGeneralCenterLineKind::BetweenEdges;
        }
        if (value == "between_vertices") {
            return DrawingGeneralCenterLineKind::BetweenVertices;
        }
        throw Py::ValueError(
            "kind must be face, between_edges, or between_vertices");
    }

    std::string drawingGeneralCenterLineKindName(
        DrawingGeneralCenterLineKind kind) const
    {
        if (kind == DrawingGeneralCenterLineKind::Face) {
            return "face";
        }
        if (kind == DrawingGeneralCenterLineKind::BetweenEdges) {
            return "between_edges";
        }
        return "between_vertices";
    }

    std::string drawingGeneralCenterLineModeName(
        TechDraw::CenterLine::Mode mode) const
    {
        if (mode == TechDraw::CenterLine::Mode::HORIZONTAL) {
            return "horizontal";
        }
        if (mode == TechDraw::CenterLine::Mode::ALIGNED) {
            return "aligned";
        }
        return "vertical";
    }

    Py::Dict drawingGeneralCenterLinePlan(
        const DrawingGeneralCenterLinePlan& plan,
        const std::string& tag = {},
        const std::string& selectionName = {}) const
    {
        Py::List sources;
        for (const auto& source : plan.sourceSelectionNames) {
            sources.append(Py::String(source));
        }
        Py::Dict settings;
        settings.setItem(
            "mode",
            Py::String(drawingGeneralCenterLineModeName(plan.settings.mode)));
        settings.setItem(
            "horizontal_shift_mm",
            Py::Float(plan.settings.horizontalShiftMm));
        settings.setItem(
            "vertical_shift_mm",
            Py::Float(plan.settings.verticalShiftMm));
        settings.setItem(
            "rotation_degrees",
            Py::Float(plan.settings.rotationDegrees));
        settings.setItem(
            "extension_mm",
            Py::Float(plan.settings.extensionMm));
        settings.setItem("flip", Py::Boolean(plan.settings.flip));
        settings.setItem(
            "line_format",
            drawingCircleCenterLineFormat(plan.settings.format));
        Py::Dict line;
        line.setItem(
            "start_in_view_mm",
            drawingCircleCenterLinePoint(plan.startInViewMm));
        line.setItem(
            "end_in_view_mm",
            drawingCircleCenterLinePoint(plan.endInViewMm));
        line.setItem("length_mm", Py::Float(plan.lengthMm));
        Py::Dict result;
        result.setItem(
            "kind",
            Py::String(drawingGeneralCenterLineKindName(plan.kind)));
        result.setItem("source_subelements", sources);
        result.setItem("settings", settings);
        result.setItem("line", line);
        if (!tag.empty()) {
            result.setItem("centerline_tag", Py::String(tag));
        }
        if (!selectionName.empty()) {
            result.setItem("subelement", Py::String(selectionName));
        }
        return result;
    }

    Py::Object validateDrawingGeneralCenterLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, sources)");
        }
        const auto elements = projectedGeneralCenterLineElements(elementsPy);
        return drawingGeneralCenterLinePlan(
            TechDrawGui::validateDrawingGeneralCenterLineWithDefaults(
                drawingPart(viewPy),
                drawingGeneralCenterLineKind(kind ? kind : ""),
                elements));
    }

    Py::Object createDrawingGeneralCenterLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OsO", &viewPy, &kind, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, sources)");
        }
        const auto elements = projectedGeneralCenterLineElements(elementsPy);
        const auto created =
            TechDrawGui::createDrawingGeneralCenterLineWithDefaults(
                drawingPart(viewPy),
                drawingGeneralCenterLineKind(kind ? kind : ""),
                elements);
        const auto persistent =
            TechDrawGui::drawingPersistentGeneralCenterLineState(
                drawingPart(viewPy), created.centerLineTag);
        return drawingGeneralCenterLinePlan(
            created.plan,
            created.centerLineTag,
            persistent.selectionName);
    }

    Py::Object drawingPersistentGeneralCenterLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        const auto state =
            TechDrawGui::drawingPersistentGeneralCenterLineState(
                drawingPart(viewPy), tag ? tag : "");
        return drawingGeneralCenterLinePlan(
            state.plan, state.tag, state.selectionName);
    }

    Py::Object drawingGeneralCenterLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : TechDrawGui::drawingGeneralCenterLineStates(
                 drawingPart(viewPy))) {
            result.append(drawingGeneralCenterLinePlan(
                state.plan, state.tag, state.selectionName));
        }
        return result;
    }

    DrawingCosmeticCurveKind drawingCosmeticCurveKind(
        const std::string& name) const
    {
        if (name == "one_point_circle") {
            return DrawingCosmeticCurveKind::OnePointCircle;
        }
        if (name == "two_point_circle") {
            return DrawingCosmeticCurveKind::TwoPointCircle;
        }
        if (name == "three_point_circle") {
            return DrawingCosmeticCurveKind::ThreePointCircle;
        }
        if (name == "center_start_end_arc") {
            return DrawingCosmeticCurveKind::CenterStartEndArc;
        }
        throw Py::ValueError(
            "cosmetic curve kind must be one_point_circle, two_point_circle, "
            "three_point_circle, or center_start_end_arc");
    }

    std::string drawingCosmeticCurveKindName(
        DrawingCosmeticCurveKind kind) const
    {
        switch (kind) {
            case DrawingCosmeticCurveKind::OnePointCircle:
                return "one_point_circle";
            case DrawingCosmeticCurveKind::TwoPointCircle:
                return "two_point_circle";
            case DrawingCosmeticCurveKind::ThreePointCircle:
                return "three_point_circle";
            case DrawingCosmeticCurveKind::CenterStartEndArc:
                return "center_start_end_arc";
        }
        throw Py::RuntimeError("the cosmetic curve kind is invalid");
    }

    Py::Dict drawingCosmeticCurveGeometry(
        const Base::Vector3d& center,
        double radius,
        bool circularArc,
        double startAngle,
        double endAngle,
        bool clockwise) const
    {
        Py::Dict result;
        result.setItem(
            "geometry_configuration",
            Py::String(circularArc ? "circular_arc" : "circle"));
        result.setItem(
            "center_in_view_mm",
            drawingCircleCenterLinePoint(center));
        result.setItem("radius_mm", Py::Float(radius));
        if (circularArc) {
            result.setItem(
                "start_angle_degrees",
                Py::Float(startAngle));
            result.setItem(
                "end_angle_degrees",
                Py::Float(endAngle));
        }
        else {
            result.setItem("start_angle_degrees", Py::None());
            result.setItem("end_angle_degrees", Py::None());
        }
        result.setItem("clockwise", Py::Boolean(circularArc && clockwise));
        return result;
    }

    Py::Dict drawingCosmeticCurvePlan(
        const DrawingCosmeticCurvePlan& plan,
        const std::string& tag = {}) const
    {
        Py::List sources;
        for (const auto& source : plan.sourceSelectionNames) {
            sources.append(Py::String(source));
        }
        Py::List sourcePoints;
        for (const auto& point : plan.sourcePointsInViewMm) {
            sourcePoints.append(drawingCircleCenterLinePoint(point));
        }
        Py::Dict result;
        result.setItem(
            "kind",
            Py::String(drawingCosmeticCurveKindName(plan.kind)));
        result.setItem("source_subelements", sources);
        result.setItem("source_points_in_view_mm", sourcePoints);
        result.setItem(
            "geometry",
            drawingCosmeticCurveGeometry(
                plan.centerInViewMm,
                plan.radiusMm,
                plan.kind == DrawingCosmeticCurveKind::CenterStartEndArc,
                plan.startAngleDegrees,
                plan.endAngleDegrees,
                plan.clockwise));
        result.setItem("line_format", drawingCircleCenterLineFormat(plan.format));
        if (!tag.empty()) {
            result.setItem("curve_tag", Py::String(tag));
        }
        return result;
    }

    Py::Object validateDrawingCosmeticCurve(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        double radius = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOd",
                &viewPy,
                &kind,
                &elementsPy,
                &radius)) {
            throw Py::TypeError("expected (view, kind, vertices, radius)");
        }
        return drawingCosmeticCurvePlan(
            TechDrawGui::validateDrawingCosmeticCurve(
                drawingPart(viewPy),
                drawingCosmeticCurveKind(kind ? kind : ""),
                projectedCosmeticCurveElements(elementsPy),
                radius));
    }

    Py::Object createDrawingCosmeticCurve(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        PyObject* elementsPy = nullptr;
        double radius = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OsOd",
                &viewPy,
                &kind,
                &elementsPy,
                &radius)) {
            throw Py::TypeError("expected (view, kind, vertices, radius)");
        }
        const auto created = TechDrawGui::createDrawingCosmeticCurve(
            drawingPart(viewPy),
            drawingCosmeticCurveKind(kind ? kind : ""),
            projectedCosmeticCurveElements(elementsPy),
            radius);
        return drawingCosmeticCurvePlan(created.plan, created.curveTag);
    }

    Py::Dict drawingPersistentCosmeticCurveResult(
        const DrawingPersistentCosmeticCurveState& state) const
    {
        Py::Dict result;
        result.setItem("tag", Py::String(state.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem(
            "geometry",
            drawingCosmeticCurveGeometry(
                state.centerInViewMm,
                state.radiusMm,
                state.circularArc,
                state.startAngleDegrees,
                state.endAngleDegrees,
                state.clockwise));
        result.setItem("line_format", drawingCircleCenterLineFormat(state.format));
        return result;
    }

    Py::Object drawingPersistentCosmeticCurve(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        return drawingPersistentCosmeticCurveResult(
            TechDrawGui::drawingPersistentCosmeticCurveState(
                drawingPart(viewPy),
                tag ? tag : ""));
    }

    Py::Object drawingCosmeticCurves(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : TechDrawGui::drawingCosmeticCurveStates(
                 drawingPart(viewPy))) {
            result.append(drawingPersistentCosmeticCurveResult(state));
        }
        return result;
    }

    DrawingCosmeticLineConstruction drawingCosmeticLineConstruction(
        const std::string& name) const
    {
        if (name == "parallel") {
            return DrawingCosmeticLineConstruction::Parallel;
        }
        if (name == "perpendicular") {
            return DrawingCosmeticLineConstruction::Perpendicular;
        }
        throw Py::ValueError(
            "cosmetic line construction must be parallel or perpendicular");
    }

    std::string drawingCosmeticLineConstructionName(
        DrawingCosmeticLineConstruction construction) const
    {
        return construction == DrawingCosmeticLineConstruction::Parallel
            ? "parallel"
            : "perpendicular";
    }

    Py::Dict drawingCosmeticLineGeometry(
        const Base::Vector3d& start,
        const Base::Vector3d& end,
        double length) const
    {
        Py::Dict result;
        result.setItem(
            "start_in_view_mm",
            drawingCircleCenterLinePoint(start));
        result.setItem(
            "end_in_view_mm",
            drawingCircleCenterLinePoint(end));
        result.setItem("length_mm", Py::Float(length));
        return result;
    }

    Py::Dict drawingCosmeticLinePlan(
        const DrawingCosmeticLinePlan& plan,
        const std::string& tag = {}) const
    {
        Py::Dict result;
        result.setItem(
            "construction",
            Py::String(drawingCosmeticLineConstructionName(plan.construction)));
        result.setItem(
            "reference_edge_subelement",
            Py::String(plan.referenceEdgeName));
        result.setItem(
            "through_vertex_subelement",
            Py::String(plan.throughVertexName));
        result.setItem(
            "reference_start_in_view_mm",
            drawingCircleCenterLinePoint(plan.referenceStartInViewMm));
        result.setItem(
            "reference_end_in_view_mm",
            drawingCircleCenterLinePoint(plan.referenceEndInViewMm));
        result.setItem(
            "through_point_in_view_mm",
            drawingCircleCenterLinePoint(plan.throughPointInViewMm));
        result.setItem(
            "line",
            drawingCosmeticLineGeometry(
                plan.startInViewMm,
                plan.endInViewMm,
                plan.lengthMm));
        result.setItem("line_format", drawingCircleCenterLineFormat(plan.format));
        if (!tag.empty()) {
            result.setItem("line_tag", Py::String(tag));
        }
        return result;
    }

    Py::Object validateDrawingCosmeticLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* construction = nullptr;
        const char* edge = nullptr;
        const char* vertex = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "Osss",
                &viewPy,
                &construction,
                &edge,
                &vertex)) {
            throw Py::TypeError(
                "expected (view, construction, edge, vertex)");
        }
        return drawingCosmeticLinePlan(
            TechDrawGui::validateDrawingCosmeticLine(
                drawingPart(viewPy),
                drawingCosmeticLineConstruction(
                    construction ? construction : ""),
                {edge ? edge : "", vertex ? vertex : ""}));
    }

    Py::Object createDrawingCosmeticLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* construction = nullptr;
        const char* edge = nullptr;
        const char* vertex = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "Osss",
                &viewPy,
                &construction,
                &edge,
                &vertex)) {
            throw Py::TypeError(
                "expected (view, construction, edge, vertex)");
        }
        const auto created = TechDrawGui::createDrawingCosmeticLine(
            drawingPart(viewPy),
            drawingCosmeticLineConstruction(construction ? construction : ""),
            {edge ? edge : "", vertex ? vertex : ""});
        return drawingCosmeticLinePlan(created.plan, created.lineTag);
    }

    Py::Dict drawingTwoPointCosmeticLinePlan(
        const DrawingTwoPointCosmeticLinePlan& plan,
        const std::string& tag = {}) const
    {
        Py::List sources;
        for (const auto& source : plan.sourceVertexNames) {
            sources.append(Py::String(source));
        }
        Py::Dict result;
        result.setItem("construction", Py::String("between_vertices"));
        result.setItem("source_vertex_subelements", sources);
        result.setItem(
            "line",
            drawingCosmeticLineGeometry(
                plan.segment.startInViewMm,
                plan.segment.endInViewMm,
                plan.segment.lengthMm));
        result.setItem(
            "line_format",
            drawingCircleCenterLineFormat(plan.segment.format));
        if (!tag.empty()) {
            result.setItem("line_tag", Py::String(tag));
        }
        return result;
    }

    Py::Object validateDrawingTwoPointCosmeticLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, vertices)");
        }
        return drawingTwoPointCosmeticLinePlan(
            TechDrawGui::validateDrawingTwoPointCosmeticLine(
                drawingPart(viewPy),
                projectedTwoPointCosmeticLineElements(elementsPy)));
    }

    Py::Object createDrawingTwoPointCosmeticLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &viewPy, &elementsPy)) {
            throw Py::TypeError("expected (view, vertices)");
        }
        const auto created = TechDrawGui::createDrawingTwoPointCosmeticLine(
            drawingPart(viewPy),
            projectedTwoPointCosmeticLineElements(elementsPy));
        return drawingTwoPointCosmeticLinePlan(
            created.plan, created.lineTag);
    }

    Py::Dict drawingPersistentCosmeticLineResult(
        const DrawingPersistentCosmeticLineState& state) const
    {
        Py::Dict result;
        result.setItem("tag", Py::String(state.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem(
            "line",
            drawingCosmeticLineGeometry(
                state.startInViewMm,
                state.endInViewMm,
                state.lengthMm));
        result.setItem("line_format", drawingCircleCenterLineFormat(state.format));
        return result;
    }

    Py::Object drawingPersistentCosmeticLine(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* tag = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &tag)) {
            throw Py::TypeError("expected (view, tag)");
        }
        return drawingPersistentCosmeticLineResult(
            TechDrawGui::drawingPersistentCosmeticLineState(
                drawingPart(viewPy),
                tag ? tag : ""));
    }

    Py::Object drawingCosmeticLines(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : TechDrawGui::drawingCosmeticLineStates(
                 drawingPart(viewPy))) {
            result.append(drawingPersistentCosmeticLineResult(state));
        }
        return result;
    }

    Py::Object currentLineDefaults(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::TypeError("expected no arguments");
        }
        const TechDraw::LineFormat& format =
            TechDraw::LineFormat::getCurrentLineFormat();
        const std::vector<std::string> standards =
            TechDraw::LineGenerator::getAvailableLineStandards();
        const int standardIndex = TechDraw::Preferences::lineStandard();
        if (standardIndex < 0
            || static_cast<std::size_t>(standardIndex) >= standards.size()) {
            throw Py::RuntimeError("the active Drawing line standard is unavailable");
        }
        TechDraw::LineGenerator generator;
        const std::vector<std::string> descriptions =
            generator.getLoadedDescriptions();
        const int lineNumber = format.getLineNumber();
        const std::string styleName =
            lineNumber > 0
                && static_cast<std::size_t>(lineNumber) <= descriptions.size()
            ? descriptions.at(static_cast<std::size_t>(lineNumber - 1))
            : std::string();

        const double thinWidth = TechDraw::LineGroup::getDefaultWidth("Thin");
        const double middleWidth = TechDraw::LineGroup::getDefaultWidth("Graphic");
        const double thickWidth = TechDraw::LineGroup::getDefaultWidth("Thick");
        const double width = format.getWidth();
        const char* widthChoice = width <= thinWidth ? "thin"
            : width <= middleWidth                  ? "middle"
            : width <= thickWidth                   ? "thick"
                                                    : "middle";

        Py::Dict color;
        color.setItem("red", Py::Float(format.getColor().r));
        color.setItem("green", Py::Float(format.getColor().g));
        color.setItem("blue", Py::Float(format.getColor().b));

        Py::Dict availableWidths;
        availableWidths.setItem("thin_mm", Py::Float(thinWidth));
        availableWidths.setItem("middle_mm", Py::Float(middleWidth));
        availableWidths.setItem("thick_mm", Py::Float(thickWidth));

        Py::List styles;
        for (std::size_t index = 0; index < descriptions.size(); ++index) {
            Py::Dict style;
            style.setItem("line_number", Py::Long(index + 1));
            style.setItem("name", Py::String(descriptions.at(index)));
            styles.append(style);
        }

        Py::Dict result;
        result.setItem("line_standard", Py::String(standards.at(standardIndex)));
        result.setItem(
            "standards_body",
            Py::String(TechDraw::LineGenerator::getLineStandardsBody()));
        result.setItem("line_number", Py::Long(lineNumber));
        result.setItem("style_code", Py::Long(format.getStyle()));
        result.setItem("style_name", Py::String(styleName));
        result.setItem("width_mm", Py::Float(width));
        result.setItem("width_choice", Py::String(widthChoice));
        result.setItem("available_widths", availableWidths);
        result.setItem("color_rgb", color);
        result.setItem("visible", Py::Boolean(format.getVisible()));
        result.setItem(
            "cascade_spacing_mm",
            Py::Float(activeDimAttributes.getCascadeSpacing()));
        result.setItem(
            "delta_distance_mm",
            Py::Float(activeDimAttributes.getLineStretch()));
        result.setItem("available_styles", styles);
        return result;
    }

    Py::Dict drawingLineAttributeState(
        const DrawingLineAttributeState& state) const
    {
        const TechDraw::LineFormat& format = state.format;
        const Base::Color colorValue = format.getColor();
        Py::Dict color;
        color.setItem("red", Py::Float(colorValue.r));
        color.setItem("green", Py::Float(colorValue.g));
        color.setItem("blue", Py::Float(colorValue.b));

        Py::Dict result;
        const char* kind = nullptr;
        switch (state.target.kind) {
            case DrawingLineKind::ProjectedEdge:
                kind = "projected_edge";
                break;
            case DrawingLineKind::CosmeticEdge:
                kind = "cosmetic_edge";
                break;
            case DrawingLineKind::CenterLine:
                kind = "centerline";
                break;
        }
        result.setItem(
            "kind",
            Py::String(kind ? kind : ""));
        if (state.target.kind != DrawingLineKind::ProjectedEdge) {
            result.setItem("tag", Py::String(state.target.tag));
        }
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem("line_number", Py::Long(format.getLineNumber()));
        result.setItem("style_code", Py::Long(format.getStyle()));
        result.setItem("width_mm", Py::Float(format.getWidth()));
        result.setItem("color_rgb", color);
        result.setItem("visible", Py::Boolean(format.getVisible()));
        return result;
    }

    DrawingLineKind drawingLineKind(const std::string& name) const
    {
        if (name == "cosmetic_edge") {
            return DrawingLineKind::CosmeticEdge;
        }
        if (name == "centerline") {
            return DrawingLineKind::CenterLine;
        }
        throw Py::ValueError("target kind must be cosmetic_edge or centerline");
    }

    DrawingLineKind drawingLineAttributeKind(const std::string& name) const
    {
        if (name == "projected_edge") {
            return DrawingLineKind::ProjectedEdge;
        }
        return drawingLineKind(name);
    }

    Py::Dict drawingLineLengthState(const DrawingLineLengthState& state) const
    {
        Py::Dict start;
        start.setItem("x_mm", Py::Float(state.startInViewMm.x));
        start.setItem("y_mm", Py::Float(state.startInViewMm.y));
        Py::Dict end;
        end.setItem("x_mm", Py::Float(state.endInViewMm.x));
        end.setItem("y_mm", Py::Float(state.endInViewMm.y));

        Py::Dict result;
        result.setItem(
            "kind",
            Py::String(
                state.target.kind == DrawingLineKind::CosmeticEdge
                ? "cosmetic_edge"
                : "centerline"));
        result.setItem("tag", Py::String(state.target.tag));
        result.setItem("subelement", Py::String(state.selectionName));
        result.setItem("start_in_view_mm", start);
        result.setItem("end_in_view_mm", end);
        result.setItem("length_mm", Py::Float(state.lengthMm));
        if (state.hasCenterLineExtension) {
            result.setItem(
                "centerline_extension_mm",
                Py::Float(state.centerLineExtensionMm));
        }
        else {
            result.setItem("centerline_extension_mm", Py::None());
        }
        return result;
    }

    Py::Object drawingLineAttributes(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : drawingLineAttributeStates(drawingPart(viewPy))) {
            result.append(drawingLineAttributeState(state));
        }
        return result;
    }

    Py::Object changeDrawingLineAttributes(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        PyObject* targetsPy = nullptr;
        int lineNumber = 0;
        double widthMm = 0.0;
        double red = 0.0;
        double green = 0.0;
        double blue = 0.0;
        int visible = 0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "OOiddddp",
                &viewPy,
                &targetsPy,
                &lineNumber,
                &widthMm,
                &red,
                &green,
                &blue,
                &visible)) {
            throw Py::TypeError(
                "expected (view, targets, line_number, width_mm, red, green, "
                "blue, visible)");
        }

        PyObject* fastSequence = PySequence_Fast(
            targetsPy,
            "targets must be a sequence of (kind, tag) pairs");
        if (!fastSequence) {
            throw Py::TypeError("targets must be a sequence of (kind, tag) pairs");
        }
        [[maybe_unused]] Py::Object ownedSequence(fastSequence, true);
        std::vector<DrawingLineTarget> targets;
        const Py_ssize_t targetCount = PySequence_Fast_GET_SIZE(fastSequence);
        targets.reserve(static_cast<std::size_t>(targetCount));
        for (Py_ssize_t index = 0; index < targetCount; ++index) {
            PyObject* item = PySequence_Fast_GET_ITEM(fastSequence, index);
            const char* kind = nullptr;
            const char* tag = nullptr;
            if (!PyTuple_Check(item) || !PyArg_ParseTuple(item, "ss", &kind, &tag)) {
                throw Py::TypeError("each target must be a (kind, tag) string pair");
            }
            const std::string kindName = kind ? kind : "";
            targets.push_back({drawingLineAttributeKind(kindName), tag ? tag : ""});
        }

        Base::Color color;
        color.r = static_cast<float>(red);
        color.g = static_cast<float>(green);
        color.b = static_cast<float>(blue);
        TechDraw::LineFormat format;
        format.setStyle(lineNumber);
        format.setLineNumber(lineNumber);
        format.setWidth(widthMm);
        format.setColor(color);
        format.setVisible(visible != 0);
        Py::List result;
        for (const auto& state : TechDrawGui::changeDrawingLineAttributes(
                 drawingPart(viewPy),
                 targets,
                 format)) {
            result.append(drawingLineAttributeState(state));
        }
        return result;
    }

    Py::Object drawingLineLengths(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O", &viewPy)) {
            throw Py::TypeError("expected (view)");
        }
        Py::List result;
        for (const auto& state : drawingLineLengthStates(drawingPart(viewPy))) {
            result.append(drawingLineLengthState(state));
        }
        return result;
    }

    Py::Object changeDrawingLineLength(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kind = nullptr;
        const char* tag = nullptr;
        const char* operation = nullptr;
        double deltaDistanceMm = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "Osssd",
                &viewPy,
                &kind,
                &tag,
                &operation,
                &deltaDistanceMm)) {
            throw Py::TypeError(
                "expected (view, kind, tag, operation, delta_distance_mm)");
        }
        const std::string operationName = operation ? operation : "";
        DrawingLineLengthOperation lineOperation;
        if (operationName == "extend") {
            lineOperation = DrawingLineLengthOperation::Extend;
        }
        else if (operationName == "shorten") {
            lineOperation = DrawingLineLengthOperation::Shorten;
        }
        else {
            throw Py::ValueError("operation must be extend or shorten");
        }
        return drawingLineLengthState(TechDrawGui::changeDrawingLineLength(
            drawingPart(viewPy),
            {drawingLineKind(kind ? kind : ""), tag ? tag : ""},
            lineOperation,
            deltaDistanceMm));
    }

    Py::Object changeDrawingViewLocks(const Py::Tuple& args)
    {
        PyObject* pagePy = nullptr;
        PyObject* changesPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OO", &pagePy, &changesPy)) {
            throw Py::TypeError("expected (page, changes)");
        }
        PyObject* fastSequence = PySequence_Fast(
            changesPy,
            "changes must be a sequence of (view, locked) pairs");
        if (!fastSequence) {
            throw Py::TypeError(
                "changes must be a sequence of (view, locked) pairs");
        }
        [[maybe_unused]] Py::Object ownedSequence(fastSequence, true);
        std::vector<DrawingViewLockRequest> requests;
        const Py_ssize_t count = PySequence_Fast_GET_SIZE(fastSequence);
        requests.reserve(static_cast<std::size_t>(count));
        for (Py_ssize_t index = 0; index < count; ++index) {
            PyObject* item = PySequence_Fast_GET_ITEM(fastSequence, index);
            if (!PyTuple_Check(item) || PyTuple_GET_SIZE(item) != 2
                || !PyBool_Check(PyTuple_GET_ITEM(item, 1))) {
                throw Py::TypeError(
                    "each change must be a (view, boolean locked) pair");
            }
            requests.push_back(
                {drawingPart(PyTuple_GET_ITEM(item, 0)),
                 PyTuple_GET_ITEM(item, 1) == Py_True});
        }
        Py::List result;
        for (const auto& state : TechDrawGui::changeDrawingViewLocks(
                 drawingPage(pagePy),
                 requests)) {
            Py::Dict item;
            item.setItem(
                "object_name",
                Py::String(
                    state.view->getNameInDocument()
                    ? state.view->getNameInDocument()
                    : ""));
            item.setItem("locked", Py::Boolean(state.locked));
            result.append(item);
        }
        return result;
    }

    Py::Object validateProjectedBalloonAnchor(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* elementName = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "Os", &viewPy, &elementName)) {
            throw Py::TypeError("expected (view, element)");
        }
        const auto validation = TechDrawGui::validateProjectedBalloonAnchor(
            drawingPart(viewPy),
            elementName ? elementName : "");
        Py::Dict pointInView;
        pointInView.setItem("x_mm", Py::Float(validation.pointInViewMm.x));
        pointInView.setItem("y_mm", Py::Float(validation.pointInViewMm.y));
        Py::Dict pointInSource;
        pointInSource.setItem("x_mm", Py::Float(validation.pointInSourceMm.x));
        pointInSource.setItem("y_mm", Py::Float(validation.pointInSourceMm.y));
        Py::Dict result;
        result.setItem("element_type", Py::String(validation.elementType));
        result.setItem("point_in_view_mm", pointInView);
        result.setItem("point_in_source_mm", pointInSource);
        return result;
    }

    Py::Object createProjectedBalloon(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* elementName = nullptr;
        const char* text = nullptr;
        const char* label = nullptr;
        double offsetX = 0.0;
        double offsetY = 0.0;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "Osssdd",
                &viewPy,
                &elementName,
                &text,
                &label,
                &offsetX,
                &offsetY)) {
            throw Py::TypeError(
                "expected (view, element, text, label, offset_x_mm, offset_y_mm)");
        }
        const std::string textValue = text ? text : "";
        const std::string labelValue = label ? label : "";
        if (textValue.empty() || textValue.size() > 2048) {
            throw Py::ValueError("balloon text must contain 1 to 2048 UTF-8 bytes");
        }
        if (labelValue.empty() || labelValue.size() > 640) {
            throw Py::ValueError("balloon label must contain 1 to 640 UTF-8 bytes");
        }
        if (!std::isfinite(offsetX) || !std::isfinite(offsetY)
            || std::abs(offsetX) > 1000.0 || std::abs(offsetY) > 1000.0) {
            throw Py::ValueError("balloon bubble offset is outside the supported range");
        }
        auto* view = drawingPart(viewPy);
        auto* balloon = TechDrawGui::createProjectedBalloonFeature(
            view,
            elementName ? elementName : "",
            textValue,
            labelValue,
            Base::Vector3d(offsetX, offsetY, 0.0));
        view->touch(true);
        return Py::asObject(balloon->getPyObject());
    }

    Py::Object validateProjectedMeasurementAnnotation(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kindRaw = nullptr;
        PyObject* elementsPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "OsO", &viewPy, &kindRaw, &elementsPy)) {
            throw Py::TypeError("expected (view, kind, elements)");
        }
        auto* view = drawingPart(viewPy);
        const auto kind = measurementAnnotationKind(kindRaw);
        const auto elements = projectedMeasurementElements(elementsPy);
        const auto measurement =
            TechDrawGui::validateProjectedMeasurementAnnotation(
                view, kind, elements);

        Py::List elementNames;
        for (const std::string& name : measurement.elements) {
            elementNames.append(Py::String(name));
        }
        Py::Dict anchorInView;
        anchorInView.setItem("x_mm", Py::Float(measurement.anchorInViewMm.x));
        anchorInView.setItem("y_mm", Py::Float(measurement.anchorInViewMm.y));
        Py::Dict anchorInSource;
        anchorInSource.setItem(
            "x_mm", Py::Float(measurement.anchorInSourceMm.x));
        anchorInSource.setItem(
            "y_mm", Py::Float(measurement.anchorInSourceMm.y));
        Py::Dict result;
        result.setItem(
            "kind",
            Py::String(
                kind == TechDrawGui::MeasurementAnnotationKind::Area
                    ? "area"
                    : "arc_length"));
        result.setItem("elements", elementNames);
        result.setItem("value", Py::Float(measurement.value));
        result.setItem(
            "unit",
            Py::String(
                kind == TechDrawGui::MeasurementAnnotationKind::Area
                    ? "mm^2"
                    : "mm"));
        result.setItem("anchor_in_view_mm", anchorInView);
        result.setItem("anchor_in_source_mm", anchorInSource);
        result.setItem("text", Py::String(measurement.text));
        return result;
    }

    Py::Object createProjectedMeasurementAnnotation(const Py::Tuple& args)
    {
        PyObject* viewPy = nullptr;
        const char* kindRaw = nullptr;
        PyObject* elementsPy = nullptr;
        const char* label = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(), "OsOs", &viewPy, &kindRaw, &elementsPy, &label)) {
            throw Py::TypeError("expected (view, kind, elements, label)");
        }
        const std::string labelValue(label ? label : "");
        if (labelValue.empty() || labelValue.size() > 640) {
            throw Py::ValueError(
                "measurement annotation label must contain 1 to 640 UTF-8 bytes");
        }
        auto* view = drawingPart(viewPy);
        auto* balloon =
            TechDrawGui::createProjectedMeasurementAnnotationFeature(
                view,
                measurementAnnotationKind(kindRaw),
                projectedMeasurementElements(elementsPy),
                labelValue);
        view->touch(true);
        return Py::asObject(balloon->getPyObject());
    }

    Py::Object invoke_method_varargs(void *method_def, const Py::Tuple &args) override
    {
        try {
            return Py::ExtensionModule<Module>::invoke_method_varargs(method_def, args);
        }
        catch (const Standard_Failure &e) {
            std::string str;
            Standard_CString msg = e.GetMessageString();
            str += typeid(e).name();
            str += " ";
            if (msg) {str += msg;}
            else     {str += "No OCCT Exception Message";}
            Base::Console().error("%s\n", str.c_str());
            throw Py::Exception(Part::PartExceptionOCCError, str);
        }
        catch (const Base::Exception &e) {
            std::string str;
            str += "FreeCAD exception thrown (";
            str += e.what();
            str += ")";
            e.reportException();
            throw Py::RuntimeError(str);
        }
        catch (const std::exception &e) {
            std::string str;
            str += "C++ exception thrown (";
            str += e.what();
            str += ")";
            Base::Console().error("%s\n", str.c_str());
            throw Py::RuntimeError(str);
        }
        return Py::None(); //only here to prevent warning re no return value
    }

//! hook for FC Gui export function
    Py::Object exporter(const Py::Tuple& args)
    {
        PyObject* object;
        char* Name;
        if (!PyArg_ParseTuple(args.ptr(), "Oet", &object, "utf-8", &Name))
            throw Py::Exception();

        std::string EncodedName = std::string(Name);
        PyMem_Free(Name);

        TechDraw::DrawPage* page = nullptr;
        Py::Sequence list(object);
        for (Py::Sequence::iterator it = list.begin(); it != list.end(); ++it) {
            PyObject* item = (*it).ptr();
            if (PyObject_TypeCheck(item, &(App::DocumentObjectPy::Type))) {
                App::DocumentObject* obj =
                    static_cast<App::DocumentObjectPy*>(item)->getDocumentObjectPtr();
                if (obj->isDerivedFrom<TechDraw::DrawPage>()) {
                    page = static_cast<TechDraw::DrawPage*>(obj);
                    Gui::Document* activeGui =
                        Gui::Application::Instance->getDocument(page->getDocument());
                    Gui::ViewProvider* vp = activeGui->getViewProvider(obj);
                    ViewProviderPage* vpPage = freecad_cast<ViewProviderPage*>(vp);
                    if (!vpPage) {
                        throw Py::TypeError("TechDraw can not find Page");
                    }

                    Base::FileInfo fi_out(EncodedName.c_str());

                    if (fi_out.hasExtension("svg")) {
                        PagePrinter::saveSVG(vpPage, EncodedName);
                    }
                    else if (fi_out.hasExtension("dxf")) {
                        PagePrinter::saveDXF(vpPage, EncodedName);
                    }
                    else if (fi_out.hasExtension("pdf")) {
                        PagePrinter::savePDF(vpPage, EncodedName);
                    }
                    else {
                        throw Py::TypeError("TechDraw can not export this file format");
                    }
                }
                else {
                    throw Py::TypeError("No Technical Drawing Page found in selection.");
                }
            }
        }

        return Py::None();
    }

//!exportPageAsPdf(PageObject, FullPath)
    Py::Object printAllDrawingPages(const Py::Tuple& args)
    {
        PyObject* documentPy = nullptr;
        PyObject* validatorPy = nullptr;
        if (!PyArg_ParseTuple(
                args.ptr(),
                "O!O",
                &(App::DocumentPy::Type),
                &documentPy,
                &validatorPy)) {
            throw Py::TypeError("expected (Document, validateBeforePrint)");
        }
        if (!PyCallable_Check(validatorPy)) {
            throw Py::TypeError("validateBeforePrint must be callable");
        }
        auto* document =
            static_cast<App::DocumentPy*>(documentPy)->getDocumentPtr();
        const auto outcome = MDIViewPage::requestPrintAllPages(
            document,
            [validatorPy]() {
                PyObject* value = PyObject_CallNoArgs(validatorPy);
                if (!value) {
                    throw Py::Exception();
                }
                Py_DECREF(value);
            });
        Py::Dict result;
        result.setItem("authorized", Py::Boolean(outcome.authorized));
        result.setItem("submitted", Py::Boolean(outcome.submitted));
        result.setItem(
            "output_mode",
            Py::String(
                !outcome.submitted ? "none" : outcome.fileOutput ? "file" : "printer"));
        result.setItem("page_count", Py::Long(outcome.pageCount));
        return result;
    }

//!exportPageAsPdf(PageObject, FullPath)
    Py::Object exportPageAsPdf(const Py::Tuple& args)
    {
        PyObject *pageObj;
        char* name;
        if (!PyArg_ParseTuple(args.ptr(), "Oet", &pageObj, "utf-8", &name)) {
            throw Py::TypeError("expected (Page, path");
        }

        std::string filePath = std::string(name);
        PyMem_Free(name);

        try {
            App::DocumentObject* obj = nullptr;
            Gui::ViewProvider* vp = nullptr;
            if (PyObject_TypeCheck(pageObj, &(App::DocumentObjectPy::Type))) {
                obj = static_cast<App::DocumentObjectPy*>(pageObj)->getDocumentObjectPtr();
                vp = Gui::Application::Instance->getViewProvider(obj);
                if (vp) {
                    TechDrawGui::ViewProviderPage* vpPage =
                        dynamic_cast<TechDrawGui::ViewProviderPage*>(vp);
                    if (vpPage) {
                        PagePrinter::savePDF(vpPage, filePath);
                    }
                    else {
                        throw Py::TypeError("Page not available! Is it Hidden?");
                    }
                }
            }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }

//!exportPageAsSvg(PageObject, FullPath)
    Py::Object exportPageAsSvg(const Py::Tuple& args)
    {
        PyObject *pageObj;
        char* name;
        if (!PyArg_ParseTuple(args.ptr(), "Oet", &pageObj, "utf-8", &name)) {
            throw Py::TypeError("expected (Page, path");
        }

        std::string filePath = std::string(name);
        PyMem_Free(name);

        try {
            App::DocumentObject* obj = nullptr;
            Gui::ViewProvider* vp = nullptr;
            if (PyObject_TypeCheck(pageObj, &(App::DocumentObjectPy::Type))) {
                obj = static_cast<App::DocumentObjectPy*>(pageObj)->getDocumentObjectPtr();
                vp = Gui::Application::Instance->getViewProvider(obj);
                if (vp) {
                    TechDrawGui::ViewProviderPage* vpPage =
                        dynamic_cast<TechDrawGui::ViewProviderPage*>(vp);
                    if (vpPage) {
                        PagePrinter::saveSVG(vpPage, filePath);
                    }
                    else {
                        throw Py::TypeError("Page not available! Is it Hidden?");
                    }
                }
            }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }

        Py::Object addQGIToView(const Py::Tuple& args)
    {
        PyObject *viewPy = nullptr;
        PyObject *qgiPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O!O", &(TechDraw::DrawViewPy::Type), &viewPy, &qgiPy)) {
            throw Py::TypeError("expected (view, item)");
        }

        try {
           App::DocumentObject* obj = nullptr;
           Gui::ViewProvider* vp = nullptr;
           QGIView* qgiv = nullptr;
           obj = static_cast<App::DocumentObjectPy*>(viewPy)->getDocumentObjectPtr();
           vp = Gui::Application::Instance->getViewProvider(obj);
           if (vp) {
               TechDrawGui::ViewProviderDrawingView* vpdv =
                            dynamic_cast<TechDrawGui::ViewProviderDrawingView*>(vp);
               if (vpdv) {
                   qgiv = vpdv->getQView();
                   if (qgiv) {
                       Gui::PythonWrapper wrap;
                       if (!wrap.loadGuiModule()) {
                           throw Py::RuntimeError("Failed to load Python wrapper for Qt::Gui");
                        }
                        QGraphicsItem* item = wrap.toQGraphicsItem(args[1]);
                        if (item) {
                            qgiv->addArbitraryItem(item);
                        }
                    }
               }
           }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }

    Py::Object addQGObjToView(const Py::Tuple& args)
    {
        PyObject *viewPy = nullptr;
        PyObject *qgiPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O!O", &(TechDraw::DrawViewPy::Type), &viewPy, &qgiPy)) {
            throw Py::TypeError("expected (view, item)");
        }

        try {
           App::DocumentObject* obj = nullptr;
           Gui::ViewProvider* vp = nullptr;
           QGIView* qgiv = nullptr;
           obj = static_cast<App::DocumentObjectPy*>(viewPy)->getDocumentObjectPtr();
           vp = Gui::Application::Instance->getViewProvider(obj);
           if (vp) {
               TechDrawGui::ViewProviderDrawingView* vpdv =
                            dynamic_cast<TechDrawGui::ViewProviderDrawingView*>(vp);
               if (vpdv) {
                   qgiv = vpdv->getQView();
                   if (qgiv) {
                       Gui::PythonWrapper wrap;
                       if (!wrap.loadGuiModule()) {
                           throw Py::RuntimeError("Failed to load Python wrapper for Qt::Gui");
                        }
                        QGraphicsObject* item = wrap.toQGraphicsObject(args[1]);
                        if (item) {
                            qgiv->addArbitraryItem(item);
                        }
                    }
               }
           }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }


    //adds a free graphics item to a Page's scene
    Py::Object addQGIToScene(const Py::Tuple& args)
    {
        PyObject *pagePy = nullptr;
        PyObject *qgiPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O!O", &(TechDraw::DrawPagePy::Type), &pagePy, &qgiPy)) {
            throw Py::TypeError("expected (view, item)");
        }

        try {
           App::DocumentObject* obj = nullptr;
           Gui::ViewProvider* vp = nullptr;
           QGSPage* qgsp = nullptr;
           obj = static_cast<App::DocumentObjectPy*>(pagePy)->getDocumentObjectPtr();
           vp = Gui::Application::Instance->getViewProvider(obj);
           if (vp) {
               TechDrawGui::ViewProviderPage* vpp =
                            dynamic_cast<TechDrawGui::ViewProviderPage*>(vp);
               if (vpp) {
                   qgsp = vpp->getQGSPage();
                   if (qgsp) {
                       Gui::PythonWrapper wrap;
                       if (!wrap.loadGuiModule()) {
                           throw Py::RuntimeError("Failed to load Python wrapper for Qt::Gui");
                       }
                        QGraphicsItem* item = wrap.toQGraphicsItem(args[1]);
                        if (item) {
                            qgsp->addItem(item);
                        }
                    }
               }
           }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }


    //adds a free graphics object to a Page's scene
//!use addQGObjToScene for QGraphics items like QGraphicsSvgItem or QGraphicsTextItem that are
//! derived from QGraphicsObject
    Py::Object addQGObjToScene(const Py::Tuple& args)
    {
        PyObject *pagePy = nullptr;
        PyObject *qgiPy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O!O", &(TechDraw::DrawPagePy::Type), &pagePy, &qgiPy)) {
            throw Py::TypeError("expected (view, item)");
        }

        try {
           App::DocumentObject* obj = nullptr;
           Gui::ViewProvider* vp = nullptr;
           QGSPage* qgsp = nullptr;
           obj = static_cast<App::DocumentObjectPy*>(pagePy)->getDocumentObjectPtr();
           vp = Gui::Application::Instance->getViewProvider(obj);
           if (vp) {
               TechDrawGui::ViewProviderPage* vpp =
                            dynamic_cast<TechDrawGui::ViewProviderPage*>(vp);
               if (vpp) {
                   qgsp = vpp->getQGSPage();
                   if (qgsp) {
                       Gui::PythonWrapper wrap;
                       if (!wrap.loadGuiModule()) {
                           throw Py::RuntimeError("Failed to load Python wrapper for Qt::Gui");
                       }
                        QGraphicsObject* item = wrap.toQGraphicsObject(args[1]);
                        if (item) {
                            qgsp->addItem(item);
                        }
                    }
               }
           }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }

    Py::Object getSceneForPage(const Py::Tuple& args)
    {
        PyObject *pagePy = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "O!", &(TechDraw::DrawPagePy::Type), &pagePy)) {
            throw Py::TypeError("expected (page)");
        }

        try {
           App::DocumentObject* obj = nullptr;
           Gui::ViewProvider* vp = nullptr;
           QGSPage* qgsp = nullptr;
           obj = static_cast<App::DocumentObjectPy*>(pagePy)->getDocumentObjectPtr();
           vp = Gui::Application::Instance->getViewProvider(obj);
           if (vp) {
               TechDrawGui::ViewProviderPage* vpp =
                            dynamic_cast<TechDrawGui::ViewProviderPage*>(vp);
               if (vpp) {
                   qgsp = vpp->getQGSPage();
                   if (qgsp) {
                       Gui::PythonWrapper wrap;
                       if (!wrap.loadGuiModule()) {
                           throw Py::RuntimeError("Failed to load Python wrapper for Qt::Gui");
                       }
                       return wrap.fromQObject(qgsp, "TechDrawGui::QGSPage");
                    }
               }
           }
        }
        catch (Base::Exception &e) {
            e.setPyException();
            throw Py::Exception();
        }

        return Py::None();
    }
};

PyObject* initModule()
{
    return Base::Interpreter().addModule(new Module);
}

} // namespace TechDrawGui
