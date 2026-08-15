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

#pragma once

#include <string>

#include <Base/Vector3D.h>
#include <Mod/TechDraw/TechDrawGlobal.h>
#include <Mod/TechDraw/App/DimensionReferences.h>


namespace TechDraw
{
class DrawViewDimension;
class DrawViewPart;
}

namespace TechDrawGui
{

struct TechDrawGuiExport ProjectedDimensionValidation
{
    std::string geometryConfiguration;
    bool approximate{false};
};

struct TechDrawGuiExport ProjectedArcLengthValidation
{
    std::string geometryConfiguration;
    double arcLengthMm{0.0};
};

TechDrawGuiExport ProjectedDimensionValidation validateProjectedDimension(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references,
    bool allowApproximate);

TechDrawGuiExport TechDraw::DrawViewDimension* createDimensionFeature(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references2d,
    const TechDraw::ReferenceVector& references3d);

TechDrawGuiExport TechDraw::DrawViewDimension* createProjectedDimensionFeature(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references,
    bool allowApproximate,
    const Base::Vector3d& labelPosition);

TechDrawGuiExport ProjectedDimensionValidation validateProjectedExtent(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references);

TechDrawGuiExport TechDraw::DrawViewDimension* createProjectedExtentFeature(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references,
    const Base::Vector3d& labelPosition);

TechDrawGuiExport ProjectedDimensionValidation validateProjectedChamfer(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references);

TechDrawGuiExport TechDraw::DrawViewDimension* createProjectedChamferFeature(
    TechDraw::DrawViewPart* view,
    const std::string& dimensionType,
    const TechDraw::ReferenceVector& references,
    const Base::Vector3d& labelPosition);

TechDrawGuiExport ProjectedArcLengthValidation validateProjectedArcLength(
    TechDraw::DrawViewPart* view,
    const std::string& edgeName);

TechDrawGuiExport TechDraw::DrawViewDimension* createProjectedArcLengthFeature(
    TechDraw::DrawViewPart* view,
    const std::string& edgeName);

TechDrawGuiExport TechDraw::DrawViewDimension* createProjectedArcLengthFeature(
    TechDraw::DrawViewPart* view,
    const std::string& edgeName,
    const Base::Vector3d& labelPosition);

TechDrawGuiExport TechDraw::DrawViewDimension* repairProjectedDimensionFeature(
    TechDraw::DrawViewDimension* dimension,
    TechDraw::DrawViewPart* view,
    const TechDraw::ReferenceVector& references,
    bool allowApproximate);

TechDrawGuiExport TechDraw::DrawViewDimension* repairProjectedExtentFeature(
    TechDraw::DrawViewDimension* dimension,
    TechDraw::DrawViewPart* view,
    const TechDraw::ReferenceVector& references);

TechDrawGuiExport TechDraw::DrawViewDimension* repairProjectedChamferFeature(
    TechDraw::DrawViewDimension* dimension,
    TechDraw::DrawViewPart* view,
    const TechDraw::ReferenceVector& references);

TechDrawGuiExport TechDraw::DrawViewDimension* repairProjectedArcLengthFeature(
    TechDraw::DrawViewDimension* dimension,
    TechDraw::DrawViewPart* view,
    const std::string& edgeName);

TechDrawGuiExport std::string defaultDimensionFormatSpec(
    TechDraw::DrawViewDimension* dimension);

}  // namespace TechDrawGui
