// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2022 Abdullah Tahiri <abdullah.tahiri.yo@gmail.com>     *
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

#pragma once

#include <Gui/Notifications.h>
#include <Gui/Selection/SelectionFilter.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>

#include <Mod/Sketcher/App/SketchObject.h>

#include "DrawSketchHandler.h"
#include "Utils.h"
#include "ViewProviderSketch.h"
#include "SnapManager.h"


namespace SketcherGui
{

class ExtendSelection: public Gui::SelectionFilterGate
{
    App::DocumentObject* object;

public:
    explicit ExtendSelection(App::DocumentObject* obj)
        : Gui::SelectionFilterGate(nullPointer())
        , object(obj)
        , disabled(false)
    {}

    bool allow(App::Document* /*pDoc*/, App::DocumentObject* pObj, const char* sSubName) override
    {
        if (pObj != this->object) {
            return false;
        }
        if (Base::Tools::isNullOrEmpty(sSubName)) {
            return false;
        }
        if (disabled) {
            return true;
        }
        std::string element(sSubName);
        if (element.substr(0, 4) == "Edge") {
            int GeoId = std::atoi(element.substr(4, 4000).c_str()) - 1;
            Sketcher::SketchObject* Sketch = static_cast<Sketcher::SketchObject*>(object);
            const Part::Geometry* geom = Sketch->getGeometry(GeoId);
            if (geom->is<Part::GeomLineSegment>() || geom->is<Part::GeomArcOfCircle>()) {
                return true;
            }
        }
        return false;
    }

    void setDisabled(bool isDisabled)
    {
        disabled = isDisabled;
    }

protected:
    bool disabled;
};


class DrawSketchHandlerExtend: public DrawSketchHandler
{
    Q_DECLARE_TR_FUNCTIONS(SketcherGui::DrawSketchHandlerExtend)

public:
    DrawSketchHandlerExtend()
        : Mode(STATUS_SEEK_First)
        , EditCurve(2)
        , BaseGeoId(-1)
        , ExtendFromStart(false)
        , SavedExtendFromStart(false)
        , Increment(0)
    {}

    ~DrawSketchHandlerExtend() override
    {
        Gui::Selection().rmvSelectionGate();
    }
    enum SelectMode
    {
        STATUS_SEEK_First,
        STATUS_SEEK_Second,
    };

    void mouseMove(SnapManager::SnapHandle snapHandle) override
    {
        Base::Vector2d onSketchPos = snapHandle.compute();

        if (Mode == STATUS_SEEK_Second) {
            Sketcher::SketchObject* sketch = sketchgui->getSketchObject();
            const Part::Geometry* geom = sketch->getGeometry(BaseGeoId);
            const Base::Vector3d targetPoint(onSketchPos.x, onSketchPos.y, 0.0);
            if (geom->is<Part::GeomLineSegment>()) {
                const Part::GeomLineSegment* lineSeg = static_cast<const Part::GeomLineSegment*>(geom);
                Base::Vector3d start3d = lineSeg->getStartPoint();
                Base::Vector3d end3d = lineSeg->getEndPoint();
                Base::Vector2d startPoint = Base::Vector2d(start3d.x, start3d.y);
                Base::Vector2d endPoint = Base::Vector2d(end3d.x, end3d.y);
                Sketcher::PointPos effectiveEndpoint = SavedExtendFromStart
                    ? Sketcher::PointPos::start
                    : Sketcher::PointPos::end;
                if (!sketch->calculateExtensionParameters(
                        BaseGeoId,
                        targetPoint,
                        effectiveEndpoint,
                        Increment,
                        effectiveEndpoint
                    )) {
                    return;
                }
                ExtendFromStart = effectiveEndpoint == Sketcher::PointPos::start;
                Base::Vector2d direction = endPoint - startPoint;
                direction.Normalize();
                if (ExtendFromStart) {
                    EditCurve[0] = startPoint - direction * Increment;
                    EditCurve[1] = endPoint;
                }
                else {
                    EditCurve[0] = startPoint;
                    EditCurve[1] = endPoint + direction * Increment;
                }
                drawEdit(EditCurve);
            }
            else if (geom->is<Part::GeomArcOfCircle>()) {
                const Part::GeomArcOfCircle* arc = static_cast<const Part::GeomArcOfCircle*>(geom);
                Base::Vector3d center = arc->getCenter();
                double radius = arc->getRadius();
                double start, end;
                arc->getRange(start, end, true);
                Sketcher::PointPos effectiveEndpoint = ExtendFromStart
                    ? Sketcher::PointPos::start
                    : Sketcher::PointPos::end;
                if (!sketch->calculateExtensionParameters(
                        BaseGeoId,
                        targetPoint,
                        effectiveEndpoint,
                        Increment,
                        effectiveEndpoint
                    )) {
                    return;
                }
                ExtendFromStart = effectiveEndpoint == Sketcher::PointPos::start;
                const double modStartAngle = ExtendFromStart ? start - Increment : start;
                const double modArcAngle = end - start + Increment;
                for (int i = 0; i < 31; i++) {
                    double angle = modStartAngle + i * modArcAngle / 30.0;
                    EditCurve[i] = Base::Vector2d(
                        center.x + radius * cos(angle),
                        center.y + radius * sin(angle)
                    );
                }
                drawEdit(EditCurve);
            }
            int curveId = getPreselectCurve();
            if (BaseGeoId != curveId) {
                seekAndRenderAutoConstraint(SugConstr, onSketchPos, Base::Vector2d(0.f, 0.f));
            }
        }
    }

    bool pressButton(Base::Vector2d onSketchPos) override
    {
        Q_UNUSED(onSketchPos);
        return true;
    }

    bool releaseButton(Base::Vector2d onSketchPos) override
    {
        Q_UNUSED(onSketchPos);
        if (Mode == STATUS_SEEK_First) {
            BaseGeoId = getPreselectCurve();
            if (BaseGeoId > -1) {
                const Part::Geometry* geom = sketchgui->getSketchObject()->getGeometry(BaseGeoId);
                if (geom->is<Part::GeomLineSegment>()) {
                    const Part::GeomLineSegment* seg = static_cast<const Part::GeomLineSegment*>(geom);
                    Base::Vector3d start3d = seg->getStartPoint();
                    Base::Vector3d end3d = seg->getEndPoint();
                    Base::Vector2d start = Base::Vector2d(start3d.x, start3d.y);
                    Base::Vector2d end = Base::Vector2d(end3d.x, end3d.y);
                    SavedExtendFromStart = (onSketchPos.Distance(start) < onSketchPos.Distance(end));
                    ExtendFromStart = SavedExtendFromStart;
                    Mode = STATUS_SEEK_Second;
                }
                else if (geom->is<Part::GeomArcOfCircle>()) {
                    const Part::GeomArcOfCircle* arc = static_cast<const Part::GeomArcOfCircle*>(geom);
                    double start, end;
                    arc->getRange(start, end, true);

                    Base::Vector3d center = arc->getCenter();
                    Base::Vector2d angle
                        = Base::Vector2d(onSketchPos.x - center.x, onSketchPos.y - center.y);
                    double angleToStart = angle.GetAngle(Base::Vector2d(cos(start), sin(start)));
                    double angleToEnd = angle.GetAngle(Base::Vector2d(cos(end), sin(end)));
                    ExtendFromStart = (angleToStart < angleToEnd);  // move start point if closer to
                                                                    // angle than end point
                    SavedExtendFromStart = ExtendFromStart;
                    EditCurve.resize(31);
                    Mode = STATUS_SEEK_Second;
                }
                filterGate->setDisabled(true);
            }
        }
        else if (Mode == STATUS_SEEK_Second) {
            try {
                openCommand(QT_TRANSLATE_NOOP("Command", "Extend edge"));
                Gui::cmdAppObjectArgs(
                    sketchgui->getObject(),
                    "extend(%d, %f, %d)\n",  // GeoId, increment, PointPos
                    BaseGeoId,
                    Increment,
                    ExtendFromStart ? static_cast<int>(Sketcher::PointPos::start)
                                    : static_cast<int>(Sketcher::PointPos::end)
                );
                commitCommand();

                ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
                    "User parameter:BaseApp/Preferences/Mod/Sketcher"
                );
                bool autoRecompute = hGrp->GetBool("AutoRecompute", false);
                if (autoRecompute) {
                    Gui::Command::updateActive();
                }

                // constrain chosen point
                if (!SugConstr.empty()) {
                    createAutoConstraints(
                        SugConstr,
                        BaseGeoId,
                        (ExtendFromStart) ? Sketcher::PointPos::start : Sketcher::PointPos::end
                    );
                    SugConstr.clear();
                }
                bool continuousMode = hGrp->GetBool("ContinuousCreationMode", true);

                if (continuousMode) {
                    // This code enables the continuous creation mode.
                    Mode = STATUS_SEEK_First;
                    filterGate->setDisabled(false);
                    EditCurve.clear();
                    drawEdit(EditCurve);
                    EditCurve.resize(2);
                    applyCursor();
                    /* this is ok not to call to purgeHandler
                     * in continuous creation mode because the
                     * handler is destroyed by the quit() method on pressing the
                     * right button of the mouse */
                }
                else {
                    sketchgui->purgeHandler();  // no code after this line, Handler get deleted in
                                                // ViewProvider
                }
            }
            catch (const Base::Exception&) {
                Gui::NotifyError(
                    sketchgui,
                    QT_TRANSLATE_NOOP("Notifications", "Error"),
                    QT_TRANSLATE_NOOP("Notifications", "Failed to extend edge")
                );
                abortCommand();
            }
        }
        else {  // exit extension tool if user clicked on empty space
            BaseGeoId = -1;
            sketchgui->purgeHandler();  // no code after this line, Handler get deleted in ViewProvider
        }

        updateHint();
        return true;
    }

private:
    void activated() override
    {
        Gui::Selection().clearSelection();
        Gui::Selection().rmvSelectionGate();
        filterGate = new ExtendSelection(sketchgui->getObject());
        Gui::Selection().addSelectionGate(filterGate);
    }

    QString getCrosshairCursorSVGName() const override
    {
        return QStringLiteral("Sketcher_Pointer_Extension");
    }

protected:
    SelectMode Mode;
    std::vector<Base::Vector2d> EditCurve;
    int BaseGeoId;
    ExtendSelection* filterGate = nullptr;
    bool ExtendFromStart;  // if true, extend from start, else extend from end (circle only)
    bool SavedExtendFromStart;
    double Increment;
    std::vector<AutoConstraint> SugConstr;

public:
    std::list<Gui::InputHint> getToolHints() const override
    {
        using enum Gui::InputHint::UserInput;

        return Gui::lookupHints<SelectMode>(
            Mode,
            {
                {.state = STATUS_SEEK_First,
                 .hints =
                     {
                         {tr("%1 pick edge to extend", "Sketcher Extend: hint"), {MouseLeft}},
                     }},
                {.state = STATUS_SEEK_Second,
                 .hints =
                     {
                         {tr("%1 set extension length", "Sketcher Extend: hint"), {MouseLeft}},
                     }},
            });
    }

};

}  // namespace SketcherGui
