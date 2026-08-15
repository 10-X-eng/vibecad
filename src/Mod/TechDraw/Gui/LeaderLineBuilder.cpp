/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 ***************************************************************************/

#include "LeaderLineBuilder.h"

#include <algorithm>
#include <cmath>
#include <utility>

#include <QCoreApplication>
#include <QString>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/ArrowPropEnum.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawProjGroup.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/LineGroup.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "PreferencesGui.h"
#include "ViewProviderLeader.h"


namespace
{

constexpr std::size_t MinimumPoints = 2;
constexpr std::size_t MaximumPoints = 64;
constexpr std::size_t MaximumLabelBytes = 512;
constexpr double MaximumCoordinateMm = 1'000'000.0;
constexpr double MaximumLineWidthMm = 100.0;
constexpr double PointToleranceMm = 1.0e-9;

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y)
        && std::isfinite(point.z)
        && std::abs(point.x) <= MaximumCoordinateMm
        && std::abs(point.y) <= MaximumCoordinateMm
        && std::abs(point.z) <= PointToleranceMm;
}

bool samePoint(const Base::Vector3d& first, const Base::Vector3d& second)
{
    return std::abs(first.x - second.x) <= PointToleranceMm
        && std::abs(first.y - second.y) <= PointToleranceMm
        && std::abs(first.z - second.z) <= PointToleranceMm;
}

void requireLiveTargets(TechDraw::DrawPage* page, TechDraw::DrawView* owner)
{
    auto* document = page ? page->getDocument() : nullptr;
    if (!document || !owner || owner->getDocument() != document
        || owner->findParentPage() != page) {
        throw Base::ValueError(
            "A leader line requires one live owner view on the exact Drawing page");
    }
    const auto parentPages = owner->findAllParentPages();
    if (parentPages.size() != 1 || parentPages.front() != page) {
        throw Base::ValueError(
            "The leader owner must belong to exactly one Drawing page");
    }
    if (!page->isValid() || !owner->isValid()) {
        throw Base::ValueError(
            "The leader page and owner view must both be valid");
    }
}

Base::Vector3d ownerPagePosition(TechDraw::DrawView* owner)
{
    if (auto* part = dynamic_cast<TechDraw::DrawViewPart*>(owner);
        part && TechDraw::DrawView::isProjGroupItem(part)) {
        TechDraw::DrawProjGroup* group = nullptr;
        for (auto* candidate : owner->getInList()) {
            auto* projectionGroup = dynamic_cast<TechDraw::DrawProjGroup*>(candidate);
            if (!projectionGroup) {
                continue;
            }
            if (group && group != projectionGroup) {
                throw Base::ValueError(
                    "The leader owner belongs to more than one projection group");
            }
            group = projectionGroup;
        }
        if (!group || group->getDocument() != owner->getDocument()) {
            throw Base::ValueError(
                "The leader owner has an invalid projection-group placement");
        }
        return Base::Vector3d(
            group->X.getValue() + owner->X.getValue(),
            group->Y.getValue() + owner->Y.getValue(),
            0.0);
    }
    return Base::Vector3d(owner->X.getValue(), owner->Y.getValue(), 0.0);
}

void requireGeometry(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    const std::vector<Base::Vector3d>& points,
    const TechDrawGui::DrawingLeaderStyle& style)
{
    if (points.size() < MinimumPoints || points.size() > MaximumPoints) {
        throw Base::ValueError("A leader line requires 2 through 64 page points");
    }
    const double pageWidth = page->getPageWidth();
    const double pageHeight = page->getPageHeight();
    if (!std::isfinite(pageWidth) || !std::isfinite(pageHeight)
        || pageWidth <= 0.0 || pageHeight <= 0.0) {
        throw Base::ValueError("The Drawing page has no valid paper bounds");
    }
    for (std::size_t index = 0; index < points.size(); ++index) {
        const auto& point = points[index];
        if (!finitePoint(point)) {
            throw Base::ValueError(
                "Leader page points must be finite two-dimensional millimetre coordinates");
        }
        if (point.x < 0.0 || point.x > pageWidth
            || point.y < 0.0 || point.y > pageHeight) {
            throw Base::ValueError(
                "Every leader point must lie within the exact Drawing page bounds");
        }
        if (index > 0 && samePoint(points[index - 1], point)) {
            throw Base::ValueError(
                "A leader line may not contain consecutive duplicate points");
        }
    }
    const double ownerScale = owner->getScale();
    const double ownerRotation = owner->Rotation.getValue();
    const Base::Vector3d ownerPosition = ownerPagePosition(owner);
    if (!std::isfinite(ownerScale) || ownerScale <= 0.0
        || ownerScale > MaximumCoordinateMm
        || !std::isfinite(ownerRotation)
        || std::abs(ownerRotation) > MaximumCoordinateMm
        || !finitePoint(ownerPosition)) {
        throw Base::ValueError(
            "The leader owner has an invalid page placement, scale, or rotation");
    }
    if (style.startSymbol < 0
        || style.startSymbol >= TechDraw::ArrowPropEnum::ArrowCount
        || style.endSymbol < 0
        || style.endSymbol >= TechDraw::ArrowPropEnum::ArrowCount) {
        throw Base::ValueError("A leader symbol must be one of the eight TechDraw arrow types");
    }
    if (!std::isfinite(style.lineWidthMm) || style.lineWidthMm < 0.0
        || style.lineWidthMm > MaximumLineWidthMm) {
        throw Base::ValueError("Leader line width must be from 0 through 100 mm");
    }
    if (style.lineStyle < 0 || style.lineStyle > 5) {
        throw Base::ValueError(
            "Leader line style must be NoLine, Continuous, Dash, Dot, DashDot, or DashDotDot");
    }
    if (!std::isfinite(style.lineColor.r) || !std::isfinite(style.lineColor.g)
        || !std::isfinite(style.lineColor.b)
        || style.lineColor.r < 0.0F || style.lineColor.r > 1.0F
        || style.lineColor.g < 0.0F || style.lineColor.g > 1.0F
        || style.lineColor.b < 0.0F || style.lineColor.b > 1.0F) {
        throw Base::ValueError("Leader color channels must be from 0 through 1");
    }
}

TechDrawGui::ViewProviderLeader* leaderProvider(TechDraw::DrawLeaderLine* leader)
{
    auto* provider = Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(leader)
        : nullptr;
    auto* result = dynamic_cast<TechDrawGui::ViewProviderLeader*>(provider);
    if (!result) {
        throw Base::RuntimeError("The leader line has no compatible graphical provider");
    }
    return result;
}

std::vector<Base::Vector3d> storedWayPoints(
    const std::vector<Base::Vector3d>& pagePoints,
    double ownerScale,
    double ownerRotationDegrees,
    const TechDrawGui::DrawingLeaderStyle& style)
{
    std::vector<Base::Vector3d> result;
    result.reserve(pagePoints.size());
    for (const auto& point : pagePoints) {
        Base::Vector3d canonical = point - pagePoints.front();
        if (style.rotatesWithParent) {
            canonical.RotateZ(Base::toRadians(-ownerRotationDegrees));
        }
        if (style.scalable) {
            canonical /= ownerScale;
        }
        result.push_back(TechDraw::DrawUtil::invertY(canonical));
    }
    return result;
}

std::vector<Base::Vector3d> renderedPagePoints(
    const std::vector<Base::Vector3d>& pagePoints,
    const std::vector<Base::Vector3d>& stored,
    double ownerScale,
    double ownerRotationDegrees,
    const TechDrawGui::DrawingLeaderStyle& style)
{
    std::vector<Base::Vector3d> transformed;
    transformed.reserve(stored.size());
    for (const auto& point : stored) {
        Base::Vector3d conventional = TechDraw::DrawUtil::invertY(
            point * (style.scalable ? ownerScale : 1.0));
        if (style.rotatesWithParent) {
            conventional.RotateZ(Base::toRadians(ownerRotationDegrees));
        }
        transformed.push_back(TechDraw::DrawUtil::invertY(conventional));
    }
    if (style.autoHorizontal) {
        transformed = TechDraw::DrawLeaderLine::horizLastSegment(
            transformed,
            ownerRotationDegrees);
    }
    std::vector<Base::Vector3d> result;
    result.reserve(transformed.size());
    for (const auto& point : transformed) {
        result.push_back(
            pagePoints.front() + TechDraw::DrawUtil::invertY(point));
    }
    return result;
}

}  // namespace

TechDrawGui::DrawingLeaderDefaults TechDrawGui::drawingLeaderDefaults()
{
    return {{
        static_cast<int>(PreferencesGui::dimArrowStyle()),
        static_cast<int>(TechDraw::ArrowType::NONE),
        false,
        TechDraw::Preferences::getPreferenceGroup("LeaderLine")
            ->GetBool("AutoHorizontal", true),
        true,
        TechDraw::LineGroup::getDefaultWidth("Graphic"),
        1,
        PreferencesGui::leaderColor(),
    }};
}

TechDrawGui::DrawingLeaderPlan TechDrawGui::validateDrawingLeaderLine(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    const std::vector<Base::Vector3d>& pointsOnPageMm,
    const std::string& preferredLabel,
    const DrawingLeaderStyle& style)
{
    requireLiveTargets(page, owner);
    requireGeometry(page, owner, pointsOnPageMm, style);
    if (preferredLabel.empty() || preferredLabel.size() > MaximumLabelBytes) {
        throw Base::ValueError("A leader label requires 1 to 512 UTF-8 bytes");
    }
    const std::string objectBaseName{"LeaderLine"};
    const std::string objectName =
        page->getDocument()->getUniqueObjectName(objectBaseName.c_str());
    const std::string suffix = objectName.rfind(objectBaseName, 0) == 0
        ? objectName.substr(objectBaseName.size())
        : objectName;
    const Base::Vector3d position = ownerPagePosition(owner);
    const double scale = owner->getScale();
    const double rotation = owner->Rotation.getValue();
    Base::Vector3d anchor = pointsOnPageMm.front() - position;
    anchor.RotateZ(Base::toRadians(-rotation));
    anchor /= scale;
    const auto stored = storedWayPoints(
        pointsOnPageMm,
        scale,
        rotation,
        style);
    return {
        page,
        owner,
        objectName,
        preferredLabel + suffix,
        pointsOnPageMm,
        position,
        scale,
        rotation,
        anchor,
        stored,
        renderedPagePoints(pointsOnPageMm, stored, scale, rotation, style),
        style,
    };
}

TechDraw::DrawLeaderLine* TechDrawGui::createDrawingLeaderLine(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    const std::vector<Base::Vector3d>& pointsOnPageMm,
    const std::string& preferredLabel,
    const DrawingLeaderStyle& style,
    DrawingLeaderPlan* appliedPlan)
{
    DrawingLeaderPlan plan = validateDrawingLeaderLine(
        page,
        owner,
        pointsOnPageMm,
        preferredLabel,
        style);
    auto* document = page->getDocument();
    if (document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Leader creation requires an owning document transaction");
    }
    const std::string documentName =
        Base::InterpreterSingleton::strToPython(document->getName());
    const QString factory =
        QStringLiteral("App.getDocument('%1').addObject('%2', '%3')")
            .arg(
                QString::fromStdString(documentName),
                QStringLiteral("TechDraw::DrawLeaderLine"),
                QString::fromStdString(plan.objectName));
    auto* object = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        factory.toUtf8(),
        TechDraw::DrawLeaderLine::getClassTypeId());
    auto* leader = dynamic_cast<TechDraw::DrawLeaderLine*>(object);
    if (!leader || plan.objectName != leader->getNameInDocument()) {
        throw Base::RuntimeError(
            "The leader factory returned an incompatible or unexpected object");
    }

    const std::string pageCommand = Gui::Command::getObjectCmd(page);
    const std::string ownerCommand = Gui::Command::getObjectCmd(owner);
    const std::string leaderCommand = Gui::Command::getObjectCmd(leader);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.addView(%s)",
        pageCommand.c_str(),
        leaderCommand.c_str());
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.LeaderParent = %s",
        leaderCommand.c_str(),
        ownerCommand.c_str());

    leader->Label.setValue(plan.label);
    leader->Scalable.setValue(plan.style.scalable);
    leader->AutoHorizontal.setValue(plan.style.autoHorizontal);
    leader->RotatesWithParent.setValue(plan.style.rotatesWithParent);
    leader->StartSymbol.setValue(plan.style.startSymbol);
    leader->EndSymbol.setValue(plan.style.endSymbol);
    leader->setPosition(plan.anchorInOwnerMm.x, plan.anchorInOwnerMm.y, true);
    leader->WayPoints.setValues(plan.storedWayPoints);

    auto* provider = leaderProvider(leader);
    provider->LineWidth.setValue(plan.style.lineWidthMm);
    provider->LineStyle.setValue(plan.style.lineStyle);
    provider->Color.setValue(plan.style.lineColor);
    provider->UseOldCoords.setValue(false);

    document->publishProvisionalTimelineOperationBlock(leader, {}, {});
    owner->touch();
    page->touch();
    leader->recomputeFeature();
    if (leader->isError() || !leader->isValid()) {
        throw Base::RuntimeError(
            "The leader line could not produce a valid Drawing result");
    }
    leader->requestPaint();

    if (appliedPlan) {
        *appliedPlan = std::move(plan);
    }
    return leader;
}
