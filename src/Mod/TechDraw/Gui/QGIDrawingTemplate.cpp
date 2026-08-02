/***************************************************************************
 *   Copyright (c) 2012-2014 Luke Parry <l.parry@warwick.ac.uk>            *
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
#include <cmath>
#include <iterator>

#include <QFontMetricsF>
#include <QGraphicsPathItem>
#include <QGraphicsRectItem>
#include <QGraphicsSimpleTextItem>
#include <QPainterPath>
#include <QPen>


#include <Mod/TechDraw/App/DrawParametricTemplate.h>
#include <Mod/TechDraw/App/Geometry.h>

#include "PreferencesGui.h"
#include "QGIDrawingTemplate.h"
#include "Rez.h"
#include "ZVALUE.h"


using namespace TechDrawGui;

QGIDrawingTemplate::QGIDrawingTemplate(QGSPage* scene)
    : QGITemplate(scene)
    , pageItem(new QGraphicsRectItem)
    , pathItem(new QGraphicsPathItem)
    , plainPathItem(new QGraphicsPathItem)
{
    pageItem->setZValue(ZVALUE::BACKGROUND);
    pageItem->setPen(Qt::NoPen);
    addToGroup(pageItem);

    // Invert the Y for the QGraphicsPathItem with Y pointing upwards
    QTransform qtrans;
    qtrans.scale(1., -1.);
    pathItem->setTransform(qtrans);
    addToGroup(pathItem);
    addToGroup(plainPathItem);
}

QGIDrawingTemplate::~QGIDrawingTemplate()
{
    plainTextItems.clear();
    pageItem = nullptr;
    pathItem = nullptr;
    plainPathItem = nullptr;
}

void QGIDrawingTemplate::clearContents()
{
    pathItem->setPath({});
    plainPathItem->setPath({});
    for (auto* item : plainTextItems) {
        removeFromGroup(item);
        delete item;
    }
    plainTextItems.clear();
}

TechDraw::DrawParametricTemplate* QGIDrawingTemplate::getParametricTemplate()
{
    if (pageTemplate
        && pageTemplate->isDerivedFrom<TechDraw::DrawParametricTemplate>()) {
        return static_cast<TechDraw::DrawParametricTemplate*>(pageTemplate);
    }
    return nullptr;
}

void QGIDrawingTemplate::drawPlainTemplate()
{
    constexpr double marginMm = 10.0;
    constexpr double rowHeightMm = 7.0;
    constexpr double preferredColumnWidthMm = 90.0;
    constexpr double titleBlockHeightFraction = 0.35;

    const double widthMm = pageTemplate->Width.getValue();
    const double heightMm = pageTemplate->Height.getValue();
    if (!std::isfinite(widthMm) || !std::isfinite(heightMm) || widthMm <= 0.0
        || heightMm <= 0.0) {
        throw Base::RuntimeError("A drawing template requires a positive finite sheet size");
    }

    const double width = Rez::guiX(widthMm);
    const double height = Rez::guiX(heightMm);
    const double margin = Rez::guiX(marginMm);
    const double innerWidth = width - 2.0 * margin;
    const double innerHeight = height - 2.0 * margin;
    if (innerWidth <= 0.0 || innerHeight <= 0.0) {
        throw Base::RuntimeError("A drawing template sheet is too small for its border");
    }

    pageItem->setRect(0.0, -height, width, height);
    pageItem->setBrush(PreferencesGui::pageQColor());

    QPainterPath path;
    path.addRect(margin, -height + margin, innerWidth, innerHeight);

    const auto fields = pageTemplate->EditableTexts.getValues();
    if (!fields.empty()) {
        const double rowHeight = Rez::guiX(rowHeightMm);
        const auto rowsPerColumn = static_cast<std::size_t>(std::max(
            1.0,
            std::floor(
                innerHeight * titleBlockHeightFraction / rowHeight
            )
        ));
        const auto columnCount =
            (fields.size() + rowsPerColumn - 1) / rowsPerColumn;
        const double columnWidth = std::min(
            Rez::guiX(preferredColumnWidthMm),
            innerWidth / static_cast<double>(columnCount)
        );
        const double tableWidth = columnWidth * static_cast<double>(columnCount);
        const auto rowCount = std::min(rowsPerColumn, fields.size());
        const double tableHeight = rowHeight * static_cast<double>(rowCount);
        const double tableLeft = width - margin - tableWidth;
        const double tableTop = -margin - tableHeight;

        QFont font = PreferencesGui::labelFontQFont();
        font.setPointSizeF(8.0);
        QFontMetricsF metrics(font);
        const double horizontalPadding = Rez::guiX(2.0);
        const double textWidth = std::max(1.0, columnWidth - 2.0 * horizontalPadding);

        std::size_t index = 0;
        for (const auto& [name, value] : fields) {
            const auto column = index / rowsPerColumn;
            const auto row = index % rowsPerColumn;
            const double left = tableLeft + static_cast<double>(column) * columnWidth;
            const double top = tableTop + static_cast<double>(row) * rowHeight;
            path.addRect(left, top, columnWidth, rowHeight);

            const QString content = QString::fromUtf8(name.c_str())
                + QStringLiteral(": ") + QString::fromUtf8(value.c_str());
            auto* textItem = new QGraphicsSimpleTextItem(
                metrics.elidedText(content, Qt::ElideRight, textWidth)
            );
            textItem->setFont(font);
            textItem->setBrush(PreferencesGui::normalQColor());
            const double textTop = top + (rowHeight - textItem->boundingRect().height()) / 2.0;
            textItem->setPos(left + horizontalPadding, textTop);
            addToGroup(textItem);
            plainTextItems.push_back(textItem);
            ++index;
        }
    }

    QPen pen(PreferencesGui::normalQColor());
    pen.setWidthF(Rez::guiX(0.35));
    plainPathItem->setPen(pen);
    plainPathItem->setPath(path);
}

void QGIDrawingTemplate::draw()
{
    if (!pageTemplate) {
        throw Base::RuntimeError("Template feature not set for QGIDrawingTemplate");
    }
    clearContents();

    TechDraw::DrawParametricTemplate* tmplte = getParametricTemplate();
    if (!tmplte) {
        drawPlainTemplate();
        return;
    }

    const double width = Rez::guiX(pageTemplate->Width.getValue());
    const double height = Rez::guiX(pageTemplate->Height.getValue());
    pageItem->setRect(0.0, -height, width, height);
    pageItem->setBrush(PreferencesGui::pageQColor());

    // Get a list of geometry and iterate
    const TechDraw::BaseGeomPtrVector& geoms = tmplte->getGeometry();

    QPainterPath path;
    for (const auto& item : geoms) {
        if (item->getGeomType() == TechDraw::GeomType::GENERIC) {
            TechDraw::GenericPtr geom = std::static_pointer_cast<TechDraw::Generic>(item);
            if (geom->points.empty()) {
                continue;
            }

            path.moveTo(geom->points[0].x, geom->points[0].y);
            for (auto point = std::next(geom->points.begin());
                 point != geom->points.end();
                 ++point) {
                path.lineTo(point->x, point->y);
            }
        }
    }

    pathItem->setPath(path);
}

void QGIDrawingTemplate::updateView(bool update)
{
    Q_UNUSED(update);
    draw();
}

#include <Mod/TechDraw/Gui/moc_QGIDrawingTemplate.cpp>
