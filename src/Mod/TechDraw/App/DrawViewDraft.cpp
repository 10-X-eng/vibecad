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


# include <algorithm>
# include <cctype>
# include <iomanip>
# include <sstream>

# include <QSet>
# include <QXmlStreamReader>


#include <Base/Console.h>
#include <Base/Interpreter.h>

#include <App/Document.h>

#include "DrawViewDraft.h"
#include "DrawViewDraftPy.h"


using namespace TechDraw;

namespace
{
constexpr std::size_t maxPrecomputedDraftBytes = 32U * 1024U * 1024U;
constexpr std::size_t maxPrecomputedDraftElements = 200000U;

bool validSourceState(const std::string& value)
{
    return value.size() == 64
        && std::all_of(value.begin(), value.end(), [](unsigned char character) {
               return std::isxdigit(character) != 0;
           });
}

bool validDraftSymbol(const std::string& symbol)
{
    if (symbol.size() < 32 || symbol.size() > maxPrecomputedDraftBytes) {
        return false;
    }
    static const QSet<QString> forbidden {
        QStringLiteral("embed"),
        QStringLiteral("foreignobject"),
        QStringLiteral("iframe"),
        QStringLiteral("object"),
        QStringLiteral("script"),
    };
    static const QSet<QString> drawables {
        QStringLiteral("circle"),
        QStringLiteral("ellipse"),
        QStringLiteral("image"),
        QStringLiteral("line"),
        QStringLiteral("path"),
        QStringLiteral("polygon"),
        QStringLiteral("polyline"),
        QStringLiteral("rect"),
        QStringLiteral("text"),
    };
    QXmlStreamReader reader(
        QByteArray::fromRawData(symbol.data(), static_cast<int>(symbol.size())));
    std::size_t elementCount = 0;
    std::size_t drawableCount = 0;
    bool foundRoot = false;
    while (!reader.atEnd()) {
        const auto token = reader.readNext();
        if (token == QXmlStreamReader::DTD || token == QXmlStreamReader::EntityReference) {
            return false;
        }
        if (token != QXmlStreamReader::StartElement) {
            continue;
        }
        if (++elementCount > maxPrecomputedDraftElements) {
            return false;
        }
        const QString name = reader.name().toString().toLower();
        if (!foundRoot) {
            if (name != QStringLiteral("svg")) {
                return false;
            }
            foundRoot = true;
        }
        if (forbidden.contains(name)) {
            return false;
        }
        drawableCount += drawables.contains(name) ? 1U : 0U;
        for (const auto& attribute : reader.attributes()) {
            const QString attributeName = attribute.name().toString().toLower();
            const QString value = attribute.value().toString().trimmed();
            if (attributeName == QStringLiteral("href") && !value.isEmpty()
                && !value.startsWith(QLatin1Char('#'))
                && !value.startsWith(QStringLiteral("data:image/"))) {
                return false;
            }
            QString compact = value.toLower();
            compact.remove(QLatin1Char(' '));
            if (compact.contains(QStringLiteral("url("))
                && !compact.contains(QStringLiteral("url(#"))) {
                return false;
            }
        }
    }
    return !reader.hasError() && foundRoot && drawableCount > 0;
}
}  // namespace

//===========================================================================
// DrawViewDraft
//===========================================================================

PROPERTY_SOURCE(TechDraw::DrawViewDraft, TechDraw::DrawViewSymbol)


DrawViewDraft::DrawViewDraft()
{
    static const char *group = "Draft view";

    ADD_PROPERTY_TYPE(Source ,(nullptr), group, App::Prop_None, "Draft object for this view");
    Source.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(LineWidth, (0.35), group, App::Prop_None, "Line width of this view. If Override Style is false, this value multiplies the object line width");
    ADD_PROPERTY_TYPE(FontSize, (12.0), group, App::Prop_None, "Text size for this view");
    ADD_PROPERTY_TYPE(Direction ,(0, 0,1.0), group, App::Prop_None, "Projection direction. The direction you are looking from.");
    ADD_PROPERTY_TYPE(Color, (0.0f, 0.0f, 0.0f), group, App::Prop_None, "The default color of text and lines");
    ADD_PROPERTY_TYPE(LineStyle, ("Solid") ,group, App::Prop_None, "A line style to use for this view. Can be Solid, Dashed, Dashdot, Dot or a SVG pattern like 0.20, 0.20");
    ADD_PROPERTY_TYPE(LineSpacing, (1.0f), group, App::Prop_None, "The spacing between lines to use for multiline texts");
    ADD_PROPERTY_TYPE(OverrideStyle, (false), group, App::Prop_None, "If True, line color, width and style of this view will override those of rendered objects");

    static const char* cacheGroup = "Precomputed Draft";
    const auto cacheFlags = static_cast<App::PropertyType>(
        App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden | App::Prop_NoRecompute);
    ADD_PROPERTY_TYPE(PrecomputedDraftSymbol,
                      (""),
                      cacheGroup,
                      cacheFlags,
                      "Persisted SVG generated by an isolated Draft worker.");
    ADD_PROPERTY_TYPE(PrecomputedDraftSourceState,
                      (""),
                      cacheGroup,
                      cacheFlags,
                      "Exact Draft source state used to generate the persisted SVG.");
    ScaleType.setValue("Custom");
}

DrawViewDraft::PrecomputedDraftState DrawViewDraft::getPrecomputedDraft() const
{
    const std::string symbol = PrecomputedDraftSymbol.getValue();
    const std::string sourceState = PrecomputedDraftSourceState.getValue();
    if (!validDraftSymbol(symbol) || !validSourceState(sourceState)) {
        throw Base::RuntimeError("The TechDraw Draft view has no valid precomputed SVG");
    }
    return {symbol, sourceState};
}

void DrawViewDraft::setPrecomputedDraft(const std::string& symbol,
                                        const std::string& sourceState)
{
    auto* source = Source.getValue();
    if (!source || source->getDocument() != getDocument() || !isActiveInDocumentTimeline()) {
        throw Base::RuntimeError(
            "A precomputed Draft view requires a current same-document source");
    }
    if (!validDraftSymbol(symbol)) {
        throw Base::ValueError("Precomputed Draft SVG is empty, malformed, or exceeds 32 MiB");
    }
    if (!validSourceState(sourceState)) {
        throw Base::ValueError("Precomputed Draft source state must be one SHA-256 value");
    }

    PrecomputedDraftSymbol.setValue(symbol);
    PrecomputedDraftSourceState.setValue(sourceState);
    m_adoptingPrecomputedDraft = true;
    try {
        Symbol.setValue(symbol);
    }
    catch (...) {
        m_adoptingPrecomputedDraft = false;
        throw;
    }
    m_adoptingPrecomputedDraft = false;
    Symbol.purgeTouched();
    m_restoreCacheActive = false;
    overrideKeepUpdated(false);
    connectSourceChanges();
    requestPaint();
}

void DrawViewDraft::invalidatePrecomputedDraft()
{
    m_restoreCacheActive = false;
    m_documentRecomputedConnection.disconnect();
    if (!PrecomputedDraftSymbol.isEmpty()) {
        PrecomputedDraftSymbol.setValue("");
    }
    if (!PrecomputedDraftSourceState.isEmpty()) {
        PrecomputedDraftSourceState.setValue("");
    }
}

void DrawViewDraft::connectSourceChanges()
{
    m_sourceConnections.clear();
    auto* source = Source.getValue();
    if (!source || source->getDocument() != getDocument()) {
        return;
    }
    std::vector<App::DocumentObject*> dependencies {source};
    const auto outward = source->getOutListRecursive();
    dependencies.insert(dependencies.end(), outward.begin(), outward.end());
    if (dependencies.size() > 256) {
        invalidatePrecomputedDraft();
        return;
    }
    for (auto* dependency : dependencies) {
        if (!dependency || dependency->getDocument() != getDocument()) {
            continue;
        }
        m_sourceConnections.emplace_back(dependency->signalChanged.connect(
            [this](const App::DocumentObject&, const App::Property&) {
                auto* document = getDocument();
                const bool restoringCache = m_restoreCacheActive && document
                    && (document->testStatus(App::Document::Restoring)
                        || document->testStatus(App::Document::Recomputing));
                if (!isRestoring() && !restoringCache
                    && (!document || !document->isPerformingTransaction())) {
                    invalidatePrecomputedDraft();
                }
            }));
    }
}

bool DrawViewDraft::restorePrecomputedDraft(bool protectRestoreRecompute)
{
    auto* source = Source.getValue();
    const std::string symbol = PrecomputedDraftSymbol.getValue();
    const std::string sourceState = PrecomputedDraftSourceState.getValue();
    if (!source || source->getDocument() != getDocument() || !validDraftSymbol(symbol)
        || !validSourceState(sourceState)) {
        return false;
    }
    m_adoptingPrecomputedDraft = true;
    try {
        Symbol.setValue(symbol);
    }
    catch (...) {
        m_adoptingPrecomputedDraft = false;
        throw;
    }
    m_adoptingPrecomputedDraft = false;
    Symbol.purgeTouched();
    m_restoreCacheActive = protectRestoreRecompute;
    overrideKeepUpdated(false);
    connectSourceChanges();
    return true;
}

short DrawViewDraft::mustExecute() const
{
    if (!isRestoring()) {
        if(Source.isTouched() ||
            LineWidth.isTouched() ||
            FontSize.isTouched() ||
            Direction.isTouched() ||
            Color.isTouched() ||
            LineStyle.isTouched() ||
            LineSpacing.isTouched() ||
            OverrideStyle.isTouched()) {
            return true;
        }
    }
    return DrawViewSymbol::mustExecute();
}

void DrawViewDraft::onChanged(const App::Property* prop)
{
    auto* document = getDocument();
    const bool restoringCache = m_restoreCacheActive && document
        && (document->testStatus(App::Document::Restoring)
            || document->testStatus(App::Document::Recomputing));
    if (!isRestoring() && !m_adoptingPrecomputedDraft && !restoringCache
        && (!document || !document->isPerformingTransaction())
        && (prop == &Source || prop == &Symbol || prop == &LineWidth || prop == &FontSize
            || prop == &Direction || prop == &Color || prop == &LineStyle
            || prop == &LineSpacing || prop == &OverrideStyle || prop == &Scale
            || prop == &ScaleType)) {
        invalidatePrecomputedDraft();
    }
    if (prop == &Source && !isRestoring()) {
        connectSourceChanges();
    }
    DrawViewSymbol::onChanged(prop);
}

void DrawViewDraft::onDocumentRestored()
{
    DrawViewSymbol::onDocumentRestored();
    try {
        const bool restored = restorePrecomputedDraft(true);
        m_documentRecomputedConnection.disconnect();
        if (restored && getDocument()) {
            m_documentRecomputedConnection = getDocument()->signalRecomputed.connect(
                [this](const App::Document& document,
                       const std::vector<App::DocumentObject*>&) {
                    if (&document == getDocument()) {
                        m_restoreCacheActive = false;
                        m_documentRecomputedConnection.disconnect();
                    }
                });
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not restore precomputed TechDraw Draft view for %s: %s\n",
            getNameInDocument(),
            error.what());
        m_restoreCacheActive = false;
    }
}

void DrawViewDraft::onUndoRedoFinished()
{
    DrawViewSymbol::onUndoRedoFinished();
    try {
        if (restorePrecomputedDraft(false)) {
            requestPaint();
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not restore precomputed TechDraw Draft view after undo/redo for %s: %s\n",
            getNameInDocument(),
            error.what());
        m_restoreCacheActive = false;
    }
}

bool DrawViewDraft::timelineDependenciesActive(
    TimelineDependencyStack& stack) const
{
    if (!DrawView::timelineDependenciesActive(stack)) {
        return false;
    }
    auto* source = Source.getValue();
    return !source || timelineDependencyIsActive(source, stack);
}



App::DocumentObjectExecReturn *DrawViewDraft::execute()
{
//    Base::Console().message("DVDr::execute() \n");
    if (m_restoreCacheActive && !PrecomputedDraftSymbol.isEmpty()) {
        overrideKeepUpdated(false);
        return DrawViewSymbol::execute();
    }
    m_restoreCacheActive = false;
    if (!keepUpdated()) {
        return App::DocumentObject::StdReturn;
    }

    App::DocumentObject* sourceObj = Source.getValue();
    if (sourceObj) {
        std::string svgFrag;
        std::string svgHead = getSVGHead();
        std::string svgTail = getSVGTail();
        std::string FeatName = getNameInDocument();
        std::string SourceName = sourceObj->getNameInDocument();
        // Draft.get_svg(obj, scale=1, linewidth=0.35, fontsize=12, fillstyle="shape color", direction=None, linestyle=None, color=None, linespacing=None, techdraw=False)

        std::stringstream paramStr;
        Base::Color col = Color.getValue();
        paramStr << ", scale=" << getScale()
                 << ", linewidth=" << LineWidth.getValue()
                 << ", fontsize=" << FontSize.getValue()
                 // TODO treat fillstyle here
                 << ", direction=FreeCAD.Vector(" << Direction.getValue().x << ", " << Direction.getValue().y << ", " << Direction.getValue().z << ")"
                 << ", linestyle=\"" << LineStyle.getValue() << "\""
                 << ", color=\"" << col.asHexString() << "\""
                 << ", linespacing=" << LineSpacing.getValue()
                 // We must set techdraw to "true" becausea couple of things behave differently than in Drawing
                 << ", techdraw=True"
                 << ", override=" << (OverrideStyle.getValue() ? "True" : "False");

// this is ok for a starting point, but should eventually make dedicated Draft functions that build the svg for all the special cases
// (Arch section, etc)
// like Draft.makeDrawingView, but we don't need to create the actual document objects in Draft, just the svg.
        Base::Interpreter().runString("import Draft");
        Base::Interpreter().runStringArg("svgBody = Draft.get_svg(App.activeDocument().%s %s)",
                                         SourceName.c_str(), paramStr.str().c_str());
//        Base::Interpreter().runString("print svgBody");
        Base::Interpreter().runStringArg("App.activeDocument().%s.Symbol = '%s' + svgBody + '%s'",
                                          FeatName.c_str(), svgHead.c_str(), svgTail.c_str());
        }

    overrideKeepUpdated(false);
    return DrawView::execute();
}

std::string DrawViewDraft::getSVGHead()
{
    return std::string("<svg\\n") +
           std::string("	xmlns=\"http://www.w3.org/2000/svg\" version=\"1.1\"\\n") +
           std::string("	xmlns:freecad=\"https://www.freecad.org/wiki/index.php?title=Svg_Namespace\">\\n");
}

std::string DrawViewDraft::getSVGTail()
{
    return "\\n</svg>";
}

PyObject* DrawViewDraft::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        PythonObject = Py::Object(new DrawViewDraftPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}

// Python Drawing feature ---------------------------------------------------------

namespace App {
/// @cond DOXERR
PROPERTY_SOURCE_TEMPLATE(TechDraw::DrawViewDraftPython, TechDraw::DrawViewDraft)
template<> const char* TechDraw::DrawViewDraftPython::getViewProviderName() const {
    return "TechDrawGui::ViewProviderDraft";
}
/// @endcond

// explicit template instantiation
template class TechDrawExport FeaturePythonT<TechDraw::DrawViewDraft>;
}
