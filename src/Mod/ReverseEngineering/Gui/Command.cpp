/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <QApplication>
#include <QMessageBox>
#include <algorithm>
#include <cmath>
#include <iterator>
#include <limits>
#include <memory>
#include <ranges>
#include <sstream>

#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRep_Builder.hxx>
#include <TopoDS_Compound.hxx>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObjectGroup.h>
#include <Base/CoordinateSystem.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Mesh/App/Core/Algorithm.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/App/Core/Elements.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/BackgroundMeshSegmentation.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Part/App/FaceMakerCheese.h>
#include <Mod/Part/App/FeaturePartSpline.h>
#include <Mod/Part/App/Geometry.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/App/PrimitiveFeature.h>
#include <Mod/Part/App/Tools.h>
#include <Mod/Points/App/Structured.h>
#include <Mod/ReverseEngineering/App/ApproxSurface.h>

#include "FitBSplineCurve.h"
#include "FitBSplineSurface.h"
#include "OperationSupport.h"
#include "Poisson.h"
#include "Segmentation.h"
#include "SegmentationManual.h"


using namespace std;

namespace
{

std::vector<App::DocumentObject*> asDocumentObjects(const std::vector<App::GeoFeature*>& objects)
{
    return {objects.begin(), objects.end()};
}

template<typename Object>
std::vector<App::DocumentObject*> asDocumentObjects(const std::vector<Object*>& objects)
{
    std::vector<App::DocumentObject*> result;
    result.reserve(objects.size());
    std::ranges::transform(objects, std::back_inserter(result), [](Object* object) {
        return static_cast<App::DocumentObject*>(object);
    });
    return result;
}

bool allMeshesNonEmpty(const std::vector<Mesh::Feature*>& meshes)
{
    return std::ranges::all_of(meshes, [](const Mesh::Feature* mesh) {
        return mesh && mesh->Mesh.getValue().countFacets() > 0
            && mesh->Mesh.getValue().countPoints() > 0;
    });
}

void showOperationError(const QString& title, const Base::Exception& error)
{
    QMessageBox::warning(Gui::getMainWindow(), title, QString::fromUtf8(error.what()));
}

void validatePartOutputs(const std::vector<Part::Feature*>& outputs)
{
    if (outputs.empty() || std::ranges::any_of(outputs, [](const Part::Feature* output) {
            return !output || output->isError() || output->Shape.getValue().IsNull();
        })) {
        throw Base::RuntimeError("The reconstruction did not produce valid shape geometry");
    }
}

bool isFinitePoint(const Base::Vector3f& point) noexcept
{
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}

double squaredDistance(const Base::Vector3f& first, const Base::Vector3f& second) noexcept
{
    const double x = static_cast<double>(first.x) - second.x;
    const double y = static_cast<double>(first.y) - second.y;
    const double z = static_cast<double>(first.z) - second.z;
    return x * x + y * y + z * z;
}

Mesh::MeshObject triangulateStructuredPoints(const Points::Structured& source)
{
    const auto width = source.Width.getValue();
    const auto height = source.Height.getValue();
    const auto& input = source.Points.getValue().getBasicPoints();
    const auto expected = static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
    if (width < 2 || height < 2 || input.size() != expected) {
        throw Base::ValueError(
            "Structured points require a complete grid of at least two rows and columns"
        );
    }

    constexpr auto missing = MeshCore::POINT_INDEX_MAX;
    std::vector<MeshCore::PointIndex> mapping(input.size(), missing);
    std::vector<Base::Vector3f> points;
    points.reserve(input.size());
    for (std::size_t index = 0; index < input.size(); ++index) {
        if (!isFinitePoint(input[index])) {
            continue;
        }
        mapping[index] = static_cast<MeshCore::PointIndex>(points.size());
        points.push_back(input[index]);
    }

    std::vector<MeshCore::MeshFacet> facets;
    facets.reserve(static_cast<std::size_t>(width - 1) * static_cast<std::size_t>(height - 1) * 2);
    const auto addFacet = [&facets,
                           &mapping](std::size_t first, std::size_t second, std::size_t third) {
        if (mapping[first] == missing || mapping[second] == missing || mapping[third] == missing) {
            return;
        }
        facets.emplace_back(mapping[first], mapping[second], mapping[third]);
    };

    for (int row = 0; row < height - 1; ++row) {
        for (int column = 0; column < width - 1; ++column) {
            const auto lowerLeft = static_cast<std::size_t>(row) * static_cast<std::size_t>(width)
                + static_cast<std::size_t>(column);
            const auto lowerRight = lowerLeft + 1;
            const auto upperLeft = lowerLeft + static_cast<std::size_t>(width);
            const auto upperRight = upperLeft + 1;
            const bool hasLowerLeft = mapping[lowerLeft] != missing;
            const bool hasLowerRight = mapping[lowerRight] != missing;
            const bool hasUpperLeft = mapping[upperLeft] != missing;
            const bool hasUpperRight = mapping[upperRight] != missing;
            const unsigned int validCount = static_cast<unsigned int>(hasLowerLeft)
                + static_cast<unsigned int>(hasLowerRight) + static_cast<unsigned int>(hasUpperLeft)
                + static_cast<unsigned int>(hasUpperRight);
            if (validCount < 3) {
                continue;
            }
            if (validCount == 3) {
                if (!hasLowerLeft) {
                    addFacet(lowerRight, upperRight, upperLeft);
                }
                else if (!hasLowerRight) {
                    addFacet(lowerLeft, upperRight, upperLeft);
                }
                else if (!hasUpperLeft) {
                    addFacet(lowerLeft, lowerRight, upperRight);
                }
                else {
                    addFacet(lowerLeft, lowerRight, upperLeft);
                }
                continue;
            }

            if (squaredDistance(input[lowerLeft], input[upperRight])
                <= squaredDistance(input[lowerRight], input[upperLeft])) {
                addFacet(lowerLeft, lowerRight, upperRight);
                addFacet(lowerLeft, upperRight, upperLeft);
            }
            else {
                addFacet(lowerLeft, lowerRight, upperLeft);
                addFacet(lowerRight, upperRight, upperLeft);
            }
        }
    }
    if (facets.empty()) {
        throw Base::RuntimeError("The structured point grid contains no triangulatable cells");
    }

    Mesh::MeshObject result;
    result.addFacets(facets, points, true);
    if (result.countFacets() == 0) {
        throw Base::RuntimeError("Structured-point triangulation produced an empty mesh");
    }
    return result;
}

}  // namespace

DEF_STD_CMD_A(CmdApproxCurve)

CmdApproxCurve::CmdApproxCurve()
    : Command("Reen_ApproxCurve")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Approximate B-Spline Curve…");
    sToolTipText = QT_TR_NOOP("Approximates a B-spline curve");
    sWhatsThis = "Reen_ApproxCurve";
    sStatusTip = sToolTipText;
    sPixmap = "Draft_BSpline";
}

void CmdApproxCurve::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Feature>();
    if (sources.size() != 1
        || !ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        || sources.front()->Points.getValue().size() < 2) {
        return;
    }

    Gui::Control().showDialog(new ReenGui::TaskFitBSplineCurve(App::DocumentObjectT(sources.front())));
}

bool CmdApproxCurve::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Feature>();
    return sources.size() == 1
        && ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && sources.front()->Points.getValue().size() >= 2;
}

DEF_STD_CMD_A(CmdApproxSurface)

CmdApproxSurface::CmdApproxSurface()
    : Command("Reen_ApproxSurface")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Approximate B-Spline Surface…");
    sToolTipText = QT_TR_NOOP("Approximates a B-spline surface");
    sWhatsThis = "Reen_ApproxSurface";
    sStatusTip = sToolTipText;
    sPixmap = "actions/FitSurface";
}

void CmdApproxSurface::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<App::GeoFeature>();
    if (sources.size() != 1
        || !(
            sources.front()->isDerivedFrom<Points::Feature>()
            || sources.front()->isDerivedFrom<Mesh::Feature>()
        )
        || !ReverseEngineeringGui::OperationSupport::areUsableSources(
            asDocumentObjects(sources),
            document
        )) {
        return;
    }
    const auto* geometry = sources.front()->getPropertyOfGeometry();
    if (!geometry || !geometry->getComplexData()) {
        return;
    }

    Gui::Control().showDialog(
        new ReenGui::TaskFitBSplineSurface(App::DocumentObjectT(sources.front()))
    );
}

bool CmdApproxSurface::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<App::GeoFeature>();
    if (sources.size() != 1
        || !(
            sources.front()->isDerivedFrom<Points::Feature>()
            || sources.front()->isDerivedFrom<Mesh::Feature>()
        )
        || !ReverseEngineeringGui::OperationSupport::areUsableSources(
            asDocumentObjects(sources),
            document
        )) {
        return false;
    }
    const auto* geometry = sources.front()->getPropertyOfGeometry();
    return geometry && geometry->getComplexData();
}

DEF_STD_CMD_A(CmdApproxPlane)

CmdApproxPlane::CmdApproxPlane()
    : Command("Reen_ApproxPlane")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Plane");
    sToolTipText = QT_TR_NOOP("Approximates a plane");
    sWhatsThis = "Reen_ApproxPlane";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Plane";
}

void CmdApproxPlane::activated(int)
{
    struct PlaneResult
    {
        App::GeoFeature* source;
        float length;
        float width;
        Base::Placement placement;
    };

    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<App::GeoFeature>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)) {
        return;
    }

    try {
        std::vector<PlaneResult> fits;
        fits.reserve(sources.size());
        for (auto* source : sources) {
            std::vector<Base::Vector3d> points;
            std::vector<Base::Vector3d> normals;
            const auto* geometry = source->getPropertyOfGeometry();
            const auto* data = geometry ? geometry->getComplexData() : nullptr;
            if (!data) {
                throw Base::ValueError("Every selected object must provide point geometry");
            }
            data->getPoints(points, normals, 0.01f);
            if (points.size() < 3) {
                throw Base::ValueError("Plane fitting requires at least three source points");
            }

            Base::Vector3f referenceNormal;
            if (!normals.empty()) {
                referenceNormal = Base::convertTo<Base::Vector3f>(normals.front());
            }
            std::vector<Base::Vector3f> dataPoints;
            dataPoints.reserve(points.size());
            std::ranges::transform(
                points,
                std::back_inserter(dataPoints),
                [](const Base::Vector3d& point) { return Base::toVector<float>(point); }
            );

            MeshCore::PlaneFit fit;
            fit.AddPoints(dataPoints);
            const float sigma = fit.Fit();
            if (sigma >= std::numeric_limits<float>::max()) {
                throw Base::RuntimeError("The selected points could not be fit to a plane");
            }
            Base::Vector3f base = fit.GetBase();
            Base::Vector3f directionU = fit.GetDirU();
            Base::Vector3f directionV = fit.GetDirV();
            Base::Vector3f normal = fit.GetNormal();
            if (referenceNormal * normal < 0) {
                normal = -normal;
                directionU = -directionU;
            }

            float length = 0;
            float width = 0;
            fit.Dimension(length, width);
            if (length <= 0 || width <= 0) {
                throw Base::RuntimeError("The selected points do not span a usable plane");
            }
            base -= 0.5f * length * directionU + 0.5f * width * directionV;

            Base::CoordinateSystem coordinateSystem;
            coordinateSystem.setPosition(Base::convertTo<Base::Vector3d>(base));
            coordinateSystem.setAxes(
                Base::convertTo<Base::Vector3d>(normal),
                Base::convertTo<Base::Vector3d>(directionU)
            );
            fits.push_back({
                source,
                length,
                width,
                Base::CoordinateSystem().displacement(coordinateSystem),
            });
            Base::Console()
                .log("RMS value for plane fit with %lu points: %.4f\n", dataPoints.size(), sigma);
        }

        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Fit plane"));
        std::vector<Part::Feature*> outputs;
        outputs.reserve(fits.size());
        for (const auto& fit : fits) {
            auto* output = document->addObject<Part::Plane>("Plane_fit");
            output->Length.setValue(fit.length);
            output->Width.setValue(fit.width);
            output->Placement.setValue(fit.placement);
            ReverseEngineeringGui::OperationSupport::setSource(*output, *fit.source);
            outputs.push_back(output);
        }
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            sourceObjects,
            asDocumentObjects(outputs),
            "PlaneFits",
            "Fitted Planes",
            "Fit planes"
        );
        document->recompute();
        validatePartOutputs(outputs);
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Fit Plane"), error);
    }
}

bool CmdApproxPlane::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(
        asDocumentObjects(getSelection().getObjectsOfType<App::GeoFeature>()),
        document
    );
}

DEF_STD_CMD_A(CmdApproxCylinder)

CmdApproxCylinder::CmdApproxCylinder()
    : Command("Reen_ApproxCylinder")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Cylinder");
    sToolTipText = QT_TR_NOOP("Approximates a cylinder");
    sWhatsThis = "Reen_ApproxCylinder";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Cylinder_Parametric";
}

void CmdApproxCylinder::activated(int)
{
    struct CylinderResult
    {
        Mesh::Feature* source;
        double radius;
        double height;
        Base::Placement placement;
    };

    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)
        || !allMeshesNonEmpty(sources)) {
        return;
    }

    try {
        std::vector<CylinderResult> fits;
        fits.reserve(sources.size());
        for (auto* source : sources) {
            const Mesh::MeshObject& mesh = source->Mesh.getValue();
            const MeshCore::MeshKernel& kernel = mesh.getKernel();
            MeshCore::CylinderFit fit;
            fit.AddPoints(kernel.GetPoints());

            std::vector<MeshCore::FacetIndex> facets(kernel.CountFacets());
            std::generate(facets.begin(), facets.end(), Base::iotaGen<MeshCore::FacetIndex>(0));
            std::vector<Base::Vector3f> normals = kernel.GetFacetNormals(facets);
            Base::Vector3f initialBase = fit.GetGravity();
            Base::Vector3f axis = fit.GetInitialAxisFromNormals(normals);
            fit.SetInitialValues(initialBase, axis);
            if (fit.Fit() >= std::numeric_limits<float>::max()) {
                throw Base::RuntimeError("The selected mesh could not be fit to a cylinder");
            }

            Base::Vector3f base;
            Base::Vector3f top;
            fit.GetBounding(base, top);
            const double height = Base::Distance(base, top);
            const double radius = fit.GetRadius();
            if (height <= 0 || radius <= 0) {
                throw Base::RuntimeError("The cylinder fit did not produce usable dimensions");
            }

            Base::Rotation rot;
            rot.setValue(Base::Vector3d(0, 0, 1), Base::convertTo<Base::Vector3d>(fit.GetAxis()));
            fits.push_back({
                source,
                radius,
                height,
                Base::Placement(Base::convertTo<Base::Vector3d>(base), rot),
            });
        }

        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Fit cylinder"));
        std::vector<Part::Feature*> outputs;
        outputs.reserve(fits.size());
        for (const auto& fit : fits) {
            auto* output = document->addObject<Part::Cylinder>("Cylinder_fit");
            output->Radius.setValue(fit.radius);
            output->Height.setValue(fit.height);
            output->Placement.setValue(fit.placement);
            ReverseEngineeringGui::OperationSupport::setSource(*output, *fit.source);
            outputs.push_back(output);
        }
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            sourceObjects,
            asDocumentObjects(outputs),
            "CylinderFits",
            "Fitted Cylinders",
            "Fit cylinders"
        );
        document->recompute();
        validatePartOutputs(outputs);
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Fit Cylinder"), error);
    }
}

bool CmdApproxCylinder::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && allMeshesNonEmpty(sources);
}

DEF_STD_CMD_A(CmdApproxSphere)

CmdApproxSphere::CmdApproxSphere()
    : Command("Reen_ApproxSphere")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Sphere");
    sToolTipText = QT_TR_NOOP("Approximates a sphere");
    sWhatsThis = "Reen_ApproxSphere";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Sphere_Parametric";
}

void CmdApproxSphere::activated(int)
{
    struct SphereResult
    {
        Mesh::Feature* source;
        double radius;
        Base::Vector3d center;
    };

    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)
        || !allMeshesNonEmpty(sources)) {
        return;
    }

    try {
        std::vector<SphereResult> fits;
        fits.reserve(sources.size());
        for (auto* source : sources) {
            MeshCore::SphereFit fit;
            fit.AddPoints(source->Mesh.getValue().getKernel().GetPoints());
            if (fit.Fit() >= std::numeric_limits<float>::max() || fit.GetRadius() <= 0) {
                throw Base::RuntimeError("The selected mesh could not be fit to a sphere");
            }
            fits.push_back({
                source,
                fit.GetRadius(),
                Base::convertTo<Base::Vector3d>(fit.GetCenter()),
            });
        }

        Gui::ExactTransaction mutation(*document, QT_TRANSLATE_NOOP("Command", "Fit sphere"));
        std::vector<Part::Feature*> outputs;
        outputs.reserve(fits.size());
        for (const auto& fit : fits) {
            auto* output = document->addObject<Part::Sphere>("Sphere_fit");
            output->Radius.setValue(fit.radius);
            output->Placement.setValue(Base::Placement(fit.center, Base::Rotation()));
            ReverseEngineeringGui::OperationSupport::setSource(*output, *fit.source);
            outputs.push_back(output);
        }
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            sourceObjects,
            asDocumentObjects(outputs),
            "SphereFits",
            "Fitted Spheres",
            "Fit spheres"
        );
        document->recompute();
        validatePartOutputs(outputs);
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Fit Sphere"), error);
    }
}

bool CmdApproxSphere::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && allMeshesNonEmpty(sources);
}

DEF_STD_CMD_A(CmdApproxPolynomial)

CmdApproxPolynomial::CmdApproxPolynomial()
    : Command("Reen_ApproxPolynomial")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Polynomial Surface");
    sToolTipText = QT_TR_NOOP("Approximates a polynomial surface");
    sWhatsThis = "Reen_ApproxPolynomial";
    sStatusTip = sToolTipText;
    sPixmap = "actions/FitSurface";
}

void CmdApproxPolynomial::activated(int)
{
    struct SurfaceResult
    {
        Mesh::Feature* source;
        TopoDS_Shape shape;
    };

    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)
        || !allMeshesNonEmpty(sources)) {
        return;
    }

    try {
        std::vector<SurfaceResult> fits;
        fits.reserve(sources.size());
        for (auto* source : sources) {
            MeshCore::SurfaceFit fit;
            fit.AddPoints(source->Mesh.getValue().getKernel().GetPoints());
            if (fit.Fit() >= std::numeric_limits<float>::max()) {
                throw Base::RuntimeError("The selected mesh could not be fit to a polynomial surface");
            }
            Base::BoundBox3f bbox = fit.GetBoundings();
            std::vector<Base::Vector3d> poles
                = fit.toBezier(bbox.MinX, bbox.MaxX, bbox.MinY, bbox.MaxY);
            if (poles.size() != 9) {
                throw Base::RuntimeError("The polynomial fit did not produce a complete control grid");
            }
            fit.Transform(poles);

            TColgp_Array2OfPnt grid(1, 3, 1, 3);
            for (Standard_Integer column = 1; column <= 3; ++column) {
                for (Standard_Integer row = 1; row <= 3; ++row) {
                    const auto index = static_cast<std::size_t>((column - 1) * 3 + (row - 1));
                    grid.SetValue(row, column, Base::convertTo<gp_Pnt>(poles.at(index)));
                }
            }

            Handle(Geom_BezierSurface) bezier(new Geom_BezierSurface(grid));
            TopoDS_Shape shape = Part::GeomBezierSurface(bezier).toShape();
            if (shape.IsNull()) {
                throw Base::RuntimeError("The polynomial fit produced an empty surface");
            }
            fits.push_back({source, std::move(shape)});
        }

        Gui::ExactTransaction mutation(
            *document,
            QT_TRANSLATE_NOOP("Command", "Fit polynomial surface")
        );
        std::vector<Part::Feature*> outputs;
        outputs.reserve(fits.size());
        for (const auto& fit : fits) {
            auto* output = document->addObject<Part::Spline>("Bezier");
            output->Shape.setValue(fit.shape);
            ReverseEngineeringGui::OperationSupport::setSource(*output, *fit.source);
            outputs.push_back(output);
        }
        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            sourceObjects,
            asDocumentObjects(outputs),
            "PolynomialFits",
            "Polynomial Surfaces",
            "Fit polynomial surfaces"
        );
        document->recompute();
        validatePartOutputs(outputs);
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Fit Polynomial Surface"), error);
    }
}

bool CmdApproxPolynomial::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = getSelection().getObjectsOfType<Mesh::Feature>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && allMeshesNonEmpty(sources);
}

DEF_STD_CMD_A(CmdSegmentation)

CmdSegmentation::CmdSegmentation()
    : Command("Reen_Segmentation")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Mesh Segmentation…");
    sToolTipText = QT_TR_NOOP("Creates separate mesh segments based on surface types");
    sWhatsThis = "Reen_Segmentation";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_Segmentation";
}

void CmdSegmentation::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    if (sources.size() != 1
        || !ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        || !allMeshesNonEmpty(sources)) {
        return;
    }
    Gui::Control().showDialog(new ReverseEngineeringGui::TaskSegmentation(sources.front()));
}

bool CmdSegmentation::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    return sources.size() == 1
        && ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && allMeshesNonEmpty(sources);
}

DEF_STD_CMD_A(CmdSegmentationManual)

CmdSegmentationManual::CmdSegmentationManual()
    : Command("Reen_SegmentationManual")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Manual Segmentation…");
    sToolTipText = QT_TR_NOOP("Creates mesh segments manually");
    sWhatsThis = "Reen_SegmentationManual";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_RemoveCompByHand";
}

void CmdSegmentationManual::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    if (!document
        || !std::ranges::any_of(
            document->getObjectsOfType<Mesh::Feature>(),
            [document](const Mesh::Feature* source) {
                return ReverseEngineeringGui::OperationSupport::isUsableSource(source, document)
                    && source->Mesh.getValue().countFacets() > 0;
            }
        )) {
        return;
    }
    Gui::Control().showDialog(new ReverseEngineeringGui::TaskSegmentationManual(document));
}

bool CmdSegmentationManual::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    return document
        && std::ranges::any_of(
               document->getObjectsOfType<Mesh::Feature>(),
               [document](const Mesh::Feature* source) {
                   return ReverseEngineeringGui::OperationSupport::isUsableSource(source, document)
                       && source->Mesh.getValue().countFacets() > 0;
               }
        );
}

DEF_STD_CMD_A(CmdSegmentationFromComponents)

CmdSegmentationFromComponents::CmdSegmentationFromComponents()
    : Command("Reen_SegmentationFromComponents")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("From Components");
    sToolTipText = QT_TR_NOOP("Creates mesh segments from components");
    sWhatsThis = "Reen_SegmentationFromComponents";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_SplitComponents";
}

void CmdSegmentationFromComponents::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto selected = getSelection().getObjectsOfType<Mesh::Feature>();
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(selected), document)
        || !allMeshesNonEmpty(selected)) {
        return;
    }

    try {
        MeshGui::startBackgroundMeshSegmentation(
            selected,
            "segmentation_from_components",
            R"({"result_label_prefix":"Component"})"
        );
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Segment Mesh Components"), error);
    }
}

bool CmdSegmentationFromComponents::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto selected = getSelection().getObjectsOfType<Mesh::Feature>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(selected), document)
        && allMeshesNonEmpty(selected);
}

DEF_STD_CMD_A(CmdMeshBoundary)

CmdMeshBoundary::CmdMeshBoundary()
    : Command("Reen_MeshBoundary")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Wire From Mesh Boundary…");
    sToolTipText = QT_TR_NOOP("Creates a wire from mesh boundaries");
    sWhatsThis = "Reen_MeshBoundary";
    sStatusTip = sToolTipText;
    sPixmap = "Mesh_SectionByPlane";
}

void CmdMeshBoundary::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)
        || !allMeshesNonEmpty(sources)) {
        return;
    }

    try {
        MeshGui::startBackgroundMeshSegmentation(
            sources,
            "mesh_boundary",
            R"({"make_faces_when_closed":true})"
        );
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Create Mesh Boundary"), error);
    }
}

bool CmdMeshBoundary::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && allMeshesNonEmpty(sources);
}

DEF_STD_CMD_A(CmdPoissonReconstruction)

CmdPoissonReconstruction::CmdPoissonReconstruction()
    : Command("Reen_PoissonReconstruction")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Poisson…");
    sToolTipText = QT_TR_NOOP("Performs Poisson surface reconstruction");
    sWhatsThis = "Reen_PoissonReconstruction";
    sStatusTip = sToolTipText;
    sPixmap = "Surface_Surface";
}

void CmdPoissonReconstruction::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Feature>();
    if (sources.size() != 1
        || !ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        || sources.front()->Points.getValue().size() == 0) {
        return;
    }

    Gui::Control().showDialog(new ReenGui::TaskPoisson(App::DocumentObjectT(sources.front())));
}

bool CmdPoissonReconstruction::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Feature>();
    return sources.size() == 1
        && ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && sources.front()->Points.getValue().size() > 0;
}

DEF_STD_CMD_A(CmdViewTriangulation)

CmdViewTriangulation::CmdViewTriangulation()
    : Command("Reen_ViewTriangulation")
{
    sAppModule = "Reen";
    sGroup = QT_TR_NOOP("Reverse Engineering");
    sMenuText = QT_TR_NOOP("Structured Point Clouds");
    sToolTipText = QT_TR_NOOP("Triangulates structured point clouds");
    sStatusTip = QT_TR_NOOP("Triangulation of structured point clouds");
    sWhatsThis = "Reen_ViewTriangulation";
    sPixmap = "Mesh_Tree";
}

void CmdViewTriangulation::activated(int)
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Structured>();
    auto sourceObjects = asDocumentObjects(sources);
    if (!ReverseEngineeringGui::OperationSupport::areUsableSources(sourceObjects, document)
        || std::ranges::any_of(sources, [](const Points::Structured* source) {
               const auto width = source->Width.getValue();
               const auto height = source->Height.getValue();
               return width < 2 || height < 2
                   || source->Points.getValue().size() != static_cast<std::size_t>(width * height);
           })) {
        return;
    }

    try {
        std::vector<Mesh::MeshObject> triangulations;
        triangulations.reserve(sources.size());
        for (const auto* source : sources) {
            triangulations.push_back(triangulateStructuredPoints(*source));
        }

        Gui::ExactTransaction mutation(
            *document,
            QT_TRANSLATE_NOOP("Command", "Triangulate structured points")
        );
        std::vector<Mesh::Feature*> outputs;
        outputs.reserve(sources.size());
        for (std::size_t index = 0; index < sources.size(); ++index) {
            auto* source = sources[index];
            auto* output = document->addObject<Mesh::Feature>("ViewMesh");
            output->Mesh.setValue(triangulations[index]);
            output->Label.setValue(source->Label.getStrValue() + " Triangulation");
            ReverseEngineeringGui::OperationSupport::setSource(*output, *source);
            outputs.push_back(output);
        }

        ReverseEngineeringGui::OperationSupport::publishSourcePreserving(
            *document,
            sourceObjects,
            asDocumentObjects(outputs),
            "PointTriangulations",
            "Point Triangulations",
            "Triangulate structured points"
        );
        document->recompute();
        if (std::ranges::any_of(outputs, [](const Mesh::Feature* output) {
                return !output || output->isError() || output->Mesh.getValue().countFacets() == 0;
            })) {
            throw Base::RuntimeError("Structured-point triangulation produced invalid geometry");
        }
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        updateActive();
    }
    catch (const Base::Exception& error) {
        showOperationError(QObject::tr("Triangulate Structured Points"), error);
    }
}

bool CmdViewTriangulation::isActive()
{
    auto* document = ReverseEngineeringGui::OperationSupport::cleanActiveDocument();
    auto sources = Gui::Selection().getObjectsOfType<Points::Structured>();
    return ReverseEngineeringGui::OperationSupport::areUsableSources(asDocumentObjects(sources), document)
        && std::ranges::all_of(sources, [](const Points::Structured* source) {
               const auto width = source->Width.getValue();
               const auto height = source->Height.getValue();
               return width >= 2 && height >= 2
                   && source->Points.getValue().size() == static_cast<std::size_t>(width * height);
           });
}

void CreateReverseEngineeringCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdApproxCurve());
    rcCmdMgr.addCommand(new CmdApproxSurface());
    rcCmdMgr.addCommand(new CmdApproxPlane());
    rcCmdMgr.addCommand(new CmdApproxCylinder());
    rcCmdMgr.addCommand(new CmdApproxSphere());
    rcCmdMgr.addCommand(new CmdApproxPolynomial());
    rcCmdMgr.addCommand(new CmdSegmentation());
    rcCmdMgr.addCommand(new CmdSegmentationManual());
    rcCmdMgr.addCommand(new CmdSegmentationFromComponents());
    rcCmdMgr.addCommand(new CmdMeshBoundary());
    rcCmdMgr.addCommand(new CmdPoissonReconstruction());
    rcCmdMgr.addCommand(new CmdViewTriangulation());
}
