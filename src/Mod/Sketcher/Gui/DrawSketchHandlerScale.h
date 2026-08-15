// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2022 Boyer Pierre-Louis <pierrelouis.boyer@gmail.com>   *
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

#include <QApplication>

#include <Gui/BitmapFactory.h>
#include <Gui/Notifications.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>

#include <Mod/Sketcher/App/SketchObject.h>

#include "DrawSketchDefaultWidgetController.h"
#include "DrawSketchControllableHandler.h"
#include "Utils.h"

#include <memory>

using namespace Sketcher;

namespace SketcherGui
{

class DrawSketchHandlerScale;

using DSHScaleController = DrawSketchDefaultWidgetController<
    DrawSketchHandlerScale,
    StateMachines::ThreeSeekEnd,
    /*PAutoConstraintSize =*/0,
    /*OnViewParametersT =*/OnViewParameters<3>,
    /*WidgetParametersT =*/WidgetParameters<0>,
    /*WidgetCheckboxesT =*/WidgetCheckboxes<1>,
    /*WidgetComboboxesT =*/WidgetComboboxes<0>,
    /*WidgetLineEditsT =*/WidgetLineEdits<0>>;

using DSHScaleControllerBase = DSHScaleController::ControllerBase;

using DrawSketchHandlerScaleBase = DrawSketchControllableHandler<DSHScaleController>;

class DrawSketchHandlerScale: public DrawSketchHandlerScaleBase
{
    Q_DECLARE_TR_FUNCTIONS(SketcherGui::DrawSketchHandlerScale)

    friend DSHScaleController;
    friend DSHScaleControllerBase;

public:
    explicit DrawSketchHandlerScale(std::vector<int> listOfGeoIds)
        : listOfGeoIds(listOfGeoIds)
        , deleteOriginal(true)
        , abortOnFail(true)
        , allowOriginConstraint(false)
        , refLength(0.0)
        , length(0.0)
        , scaleFactor(0.0)
    {}

    DrawSketchHandlerScale(const DrawSketchHandlerScale&) = delete;
    DrawSketchHandlerScale(DrawSketchHandlerScale&&) = delete;
    DrawSketchHandlerScale& operator=(const DrawSketchHandlerScale&) = delete;
    DrawSketchHandlerScale& operator=(DrawSketchHandlerScale&&) = delete;

    ~DrawSketchHandlerScale() override = default;


    static std::unique_ptr<DrawSketchHandlerScale> make_centerScaleAll(
        SketcherGui::ViewProviderSketch* vp,
        double scaleFactor,
        bool abortOnFail
    )
    {
        std::vector<int> allGeoIds(vp->getSketchObject()->Geometry.getValues().size());
        std::iota(allGeoIds.begin(), allGeoIds.end(), 0);
        auto out = std::make_unique<DrawSketchHandlerScale>(std::move(allGeoIds));

        out->setSketchGui(vp);
        out->referencePoint = Base::Vector2d(0.0, 0.0);
        out->scaleFactor = scaleFactor;
        out->abortOnFail = abortOnFail;
        out->allowOriginConstraint = true;
        return out;
    }

public:
    void executeCommands() override
    {
        // validate scale factor to prevent geometric collapse and crashes
        if (scaleFactor <= Precision::Confusion() || !std::isfinite(scaleFactor)) {
            THROWM(
                Base::ValueError,
                QT_TRANSLATE_NOOP(
                    "Notifications",
                    "Invalid scale factor. Scale factor must be a positive number."
                )
            );
        }

        try {
            openCommand(QT_TRANSLATE_NOOP("Command", "Scale geometries"));

            std::stringstream geometryIds;
            for (std::size_t index = 0; index < listOfGeoIds.size(); ++index) {
                if (index != 0) {
                    geometryIds << ',';
                }
                geometryIds << listOfGeoIds[index];
            }
            Gui::cmdAppObjectArgs(
                sketchgui->getObject(),
                "scaleExact([%s],App.Vector(%.17g,%.17g,0.0),%.17g,%s,%s)",
                geometryIds.str().c_str(),
                referencePoint.x,
                referencePoint.y,
                scaleFactor,
                deleteOriginal ? "False" : "True",
                allowOriginConstraint ? "True" : "False"
            );

            commitCommand();
        }
        catch (const Base::Exception& e) {
            e.reportException();
            Gui::NotifyError(
                sketchgui,
                QT_TRANSLATE_NOOP("Notifications", "Error"),
                QT_TRANSLATE_NOOP("Notifications", "Failed to scale")
            );

            if (abortOnFail) {
                abortCommand();
            }
            THROWM(
                Base::RuntimeError,
                QT_TRANSLATE_NOOP(
                    "Notifications",
                    "Tool execution aborted"
                ) "\n"
            )  // This prevents constraints from being
               // applied on non existing geometry
        }
    }


    std::list<Gui::InputHint> getToolHints() const override
    {
        using enum Gui::InputHint::UserInput;

        return Gui::lookupHints<SelectMode>(
            state(),
            {
                {.state = SelectMode::SeekFirst,
                 .hints =
                     {
                         {tr("%1 pick reference point"), {MouseLeft}},
                     }},
                {.state = SelectMode::SeekSecond,
                 .hints =
                     {
                         {tr("%1 set scale factor"), {MouseLeft}},
                     }},
            });
    }

private:
    void updateDataAndDrawToPosition(Base::Vector2d onSketchPos) override
    {
        switch (state()) {
            case SelectMode::SeekFirst: {
                referencePoint = onSketchPos;
            } break;
            case SelectMode::SeekSecond: {
                refLength = (onSketchPos - referencePoint).Length();

                startPoint = onSketchPos;

                CreateAndDrawShapeGeometry();
            } break;
            case SelectMode::SeekThird: {
                length = (onSketchPos - referencePoint).Length();
                endPoint = onSketchPos;
                scaleFactor = length / refLength;

                CreateAndDrawShapeGeometry();
            } break;
            default:
                break;
        }
    }

    void createAutoConstraints() override
    {
        // none
    }

    std::string getToolName() const override
    {
        return "DSH_Scale";
    }

    QString getCrosshairCursorSVGName() const override
    {
        return QStringLiteral("Sketcher_Pointer_Create_Scale");
    }

    std::unique_ptr<QWidget> createWidget() const override
    {
        return std::make_unique<SketcherToolDefaultWidget>();
    }

    bool isWidgetVisible() const override
    {
        return true;
    };

    QPixmap getToolIcon() const override
    {
        return Gui::BitmapFactory().pixmap("Sketcher_Scale");
    }

    QString getToolWidgetText() const override
    {
        return QString(tr("Scale Parameters"));
    }

    void activated() override
    {
        DrawSketchDefaultHandler::activated();
        continuousMode = false;
    }

    bool canGoToNextMode() override
    {
        if (state() == SelectMode::SeekThird && scaleFactor < Precision::Confusion()) {
            // Prevent validation zero scale.
            return false;
        }
        return true;
    }

    void angleSnappingControl() override
    {
        if (state() == SelectMode::SeekSecond || state() == SelectMode::SeekThird) {
            setAngleSnapping(true, referencePoint);
        }

        else {
            setAngleSnapping(false);
        }
    }

private:
    std::vector<int> listOfGeoIds;
    Base::Vector2d referencePoint, startPoint, endPoint;
    bool deleteOriginal;
    bool abortOnFail;  // When the scale operation is part of a larger transaction, one might want
                       // to continue even if the scaling failed
    bool allowOriginConstraint;  // Conserve constraints with origin
    double refLength, length, scaleFactor;

    void createShape(bool onlyeditoutline) override
    {
        (void)onlyeditoutline;
        SketchObject* Obj = sketchgui->getSketchObject();

        ShapeGeometry.clear();

        if (state() == SelectMode::SeekSecond) {
            if (refLength > Precision::Confusion()) {
                addLineToShapeGeometry(toVector3d(referencePoint), toVector3d(startPoint), true);
            }
            return;
        }

        if (fabs(scaleFactor) < Precision::Confusion()) {
            return;
        }

        for (auto& geoId : listOfGeoIds) {
            const Part::Geometry* pGeo = Obj->getGeometry(geoId);
            auto geoUniquePtr = std::unique_ptr<Part::Geometry>(pGeo->clone());
            Part::Geometry* geo = geoUniquePtr.get();

            if (isCircle(*geo)) {
                auto* circle = static_cast<Part::GeomCircle*>(geo);  // NOLINT
                circle->setRadius(circle->getRadius() * scaleFactor);
                circle->setCenter(getScaledPoint(circle->getCenter(), referencePoint, scaleFactor));
            }
            else if (isArcOfCircle(*geo)) {
                auto* arcOfCircle = static_cast<Part::GeomArcOfCircle*>(geo);  // NOLINT
                arcOfCircle->setRadius(arcOfCircle->getRadius() * scaleFactor);
                arcOfCircle->setCenter(
                    getScaledPoint(arcOfCircle->getCenter(), referencePoint, scaleFactor)
                );
            }
            else if (isLineSegment(*geo)) {
                auto* line = static_cast<Part::GeomLineSegment*>(geo);  // NOLINT
                line->setPoints(
                    getScaledPoint(line->getStartPoint(), referencePoint, scaleFactor),
                    getScaledPoint(line->getEndPoint(), referencePoint, scaleFactor)
                );
            }
            else if (isEllipse(*geo)) {
                auto* ellipse = static_cast<Part::GeomEllipse*>(geo);  // NOLINT

                // OpenCascade throws if we try to set a major radius smaller than
                // the minor radius or conversely, so we reorder the operations
                // depending on if we scale up or down
                if (scaleFactor < 1.0) {
                    ellipse->setMinorRadius(ellipse->getMinorRadius() * scaleFactor);
                    ellipse->setMajorRadius(ellipse->getMajorRadius() * scaleFactor);
                }
                else {
                    ellipse->setMajorRadius(ellipse->getMajorRadius() * scaleFactor);
                    ellipse->setMinorRadius(ellipse->getMinorRadius() * scaleFactor);
                }
                ellipse->setCenter(getScaledPoint(ellipse->getCenter(), referencePoint, scaleFactor));
            }
            else if (isArcOfEllipse(*geo)) {
                auto* arcOfEllipse = static_cast<Part::GeomArcOfEllipse*>(geo);  // NOLINT

                // Same reasoning as Part::GeomEllipse
                if (scaleFactor < 1.0) {
                    arcOfEllipse->setMinorRadius(arcOfEllipse->getMinorRadius() * scaleFactor);
                    arcOfEllipse->setMajorRadius(arcOfEllipse->getMajorRadius() * scaleFactor);
                }
                else {
                    arcOfEllipse->setMajorRadius(arcOfEllipse->getMajorRadius() * scaleFactor);
                    arcOfEllipse->setMinorRadius(arcOfEllipse->getMinorRadius() * scaleFactor);
                }
                arcOfEllipse->setCenter(
                    getScaledPoint(arcOfEllipse->getCenter(), referencePoint, scaleFactor)
                );
            }
            else if (isArcOfHyperbola(*geo)) {
                auto* arcOfHyperbola = static_cast<Part::GeomArcOfHyperbola*>(geo);  // NOLINT
                arcOfHyperbola->setMajorRadius(arcOfHyperbola->getMajorRadius() * scaleFactor);
                arcOfHyperbola->setMinorRadius(arcOfHyperbola->getMinorRadius() * scaleFactor);
                arcOfHyperbola->setCenter(
                    getScaledPoint(arcOfHyperbola->getCenter(), referencePoint, scaleFactor)
                );
            }
            else if (isArcOfParabola(*geo)) {
                auto* arcOfParabola = static_cast<Part::GeomArcOfParabola*>(geo);  // NOLINT
                arcOfParabola->setFocal(arcOfParabola->getFocal() * scaleFactor);
                double start, end;
                arcOfParabola->getRange(start, end, true);
                arcOfParabola->setRange(start * scaleFactor, end * scaleFactor, true);
                arcOfParabola->setCenter(
                    getScaledPoint(arcOfParabola->getCenter(), referencePoint, scaleFactor)
                );
            }
            else if (isBSplineCurve(*geo)) {
                auto* bSpline = static_cast<Part::GeomBSplineCurve*>(geo);  // NOLINT
                std::vector<Base::Vector3d> poles = bSpline->getPoles();
                for (size_t p = 0; p < poles.size(); p++) {
                    poles[p] = getScaledPoint(std::move(poles[p]), referencePoint, scaleFactor);
                }
                bSpline->setPoles(poles);
            }
            else if (isPoint(*geo)) {
                auto* point = static_cast<Part::GeomPoint*>(geo);
                point->setPoint(getScaledPoint(point->getPoint(), referencePoint, scaleFactor));
            }

            ShapeGeometry.emplace_back(std::move(geoUniquePtr));
        }

        // Add the lines that communicate the original and scaled lengths.
        addLineToShapeGeometry(toVector3d(referencePoint), toVector3d(startPoint), true);
        addLineToShapeGeometry(toVector3d(referencePoint), toVector3d(endPoint), true);
    }
    Base::Vector3d getScaledPoint(
        Base::Vector3d&& pointToScale,
        const Base::Vector2d& referencePoint,
        double scaleFactor
    )
    {
        Base::Vector2d pointToScale2D;
        pointToScale2D.x = pointToScale.x;
        pointToScale2D.y = pointToScale.y;
        pointToScale2D = (pointToScale2D - referencePoint) * scaleFactor + referencePoint;

        pointToScale.x = pointToScale2D.x;
        pointToScale.y = pointToScale2D.y;

        return pointToScale;
    }
};

template<>
auto DSHScaleControllerBase::getState(int labelindex) const
{
    switch (labelindex) {
        case OnViewParameter::First:
        case OnViewParameter::Second:
            return SelectMode::SeekFirst;
            break;
        case OnViewParameter::Third:
            return SelectMode::SeekThird;
            break;
        default:
            THROWM(Base::ValueError, "OnViewParameter index without an associated machine state")
    }
}

template<>
void DSHScaleController::configureToolWidget()
{
    if (!init) {  // Code to be executed only upon initialisation
        toolWidget->setCheckboxLabel(
            WCheckbox::FirstBox,
            QApplication::translate("TaskSketcherTool_c1_scale", "Keep original geometries (U)")
        );
    }

    onViewParameters[OnViewParameter::First]->setLabelType(Gui::SoDatumLabel::DISTANCEX);
    onViewParameters[OnViewParameter::Second]->setLabelType(Gui::SoDatumLabel::DISTANCEY);
    onViewParameters[OnViewParameter::Third]->setLabelType(
        Gui::SoDatumLabel::DISTANCE,
        Gui::EditableDatumLabel::Function::Forced
    );
}

template<>
void DSHScaleController::adaptDrawingToCheckboxChange(int checkboxindex, bool value)
{
    switch (checkboxindex) {
        case WCheckbox::FirstBox: {
            handler->deleteOriginal = !value;
        } break;
    }
}

template<>
void DSHScaleControllerBase::doEnforceControlParameters(Base::Vector2d& onSketchPos)
{
    switch (handler->state()) {
        case SelectMode::SeekFirst: {
            if (onViewParameters[OnViewParameter::First]->isSet) {
                onSketchPos.x = onViewParameters[OnViewParameter::First]->getValue();
            }

            if (onViewParameters[OnViewParameter::Second]->isSet) {
                onSketchPos.y = onViewParameters[OnViewParameter::Second]->getValue();
            }
        } break;
        case SelectMode::SeekThird: {
            if (onViewParameters[OnViewParameter::Third]->isSet) {
                double scaleFactor = onViewParameters[OnViewParameter::Third]->getValue();
                handler->refLength = 1.0;
                handler->startPoint = handler->referencePoint + Base::Vector2d(1.0, 0.0);
                handler->endPoint = handler->referencePoint + Base::Vector2d(scaleFactor, 0.0);

                onSketchPos = handler->endPoint;
            }
        } break;
        default:
            break;
    }
}

template<>
void DSHScaleController::adaptParameters(Base::Vector2d onSketchPos)
{
    switch (handler->state()) {
        case SelectMode::SeekFirst: {
            if (!onViewParameters[OnViewParameter::First]->isSet) {
                setOnViewParameterValue(OnViewParameter::First, onSketchPos.x);
            }

            if (!onViewParameters[OnViewParameter::Second]->isSet) {
                setOnViewParameterValue(OnViewParameter::Second, onSketchPos.y);
            }

            bool sameSign = onSketchPos.x * onSketchPos.y > 0.;
            onViewParameters[OnViewParameter::First]->setLabelAutoDistanceReverse(!sameSign);
            onViewParameters[OnViewParameter::Second]->setLabelAutoDistanceReverse(sameSign);
            onViewParameters[OnViewParameter::First]->setPoints(
                Base::Vector3d(),
                toVector3d(onSketchPos)
            );
            onViewParameters[OnViewParameter::Second]->setPoints(
                Base::Vector3d(),
                toVector3d(onSketchPos)
            );
        } break;
        case SelectMode::SeekThird: {
            if (!onViewParameters[OnViewParameter::Third]->isSet) {
                setOnViewParameterValue(OnViewParameter::Third, handler->scaleFactor, Base::Unit());
            }

            Base::Vector3d start = toVector3d(handler->referencePoint);
            Base::Vector3d end = toVector3d(handler->endPoint);
            onViewParameters[OnViewParameter::Third]->setPoints(start, end);
        } break;
        default:
            break;
    }
}

template<>
void DSHScaleController::computeNextDrawSketchHandlerMode()
{
    switch (handler->state()) {
        case SelectMode::SeekFirst: {
            if (onViewParameters[OnViewParameter::First]->isSet
                && onViewParameters[OnViewParameter::Second]->isSet) {

                handler->setNextState(SelectMode::SeekSecond);
            }
        } break;
        case SelectMode::SeekThird: {
            if (onViewParameters[OnViewParameter::Third]->hasFinishedEditing) {

                handler->setNextState(SelectMode::End);
            }
        } break;
            break;
        default:
            break;
    }
}


}  // namespace SketcherGui
