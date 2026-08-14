// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <cstddef>

#include <Base/Placement.h>
#include <Base/Vector3D.h>
#include <TopoDS_Shape.hxx>

#include <Mod/Mesh/App/Mesh.h>
#include <Mod/ReverseEngineering/ReverseEngineeringGlobal.h>


namespace Data
{
class ComplexGeoData;
}

namespace Points
{
class PointKernel;
}

namespace Reen
{

struct NativePlaneFit
{
    double length {};
    double width {};
    double rmsDeviation {};
    Base::Placement placement;
};

struct NativeCylinderFit
{
    double radius {};
    double height {};
    double rmsDeviation {};
    Base::Placement placement;
};

struct NativeSphereFit
{
    double radius {};
    double rmsDeviation {};
    Base::Vector3d center;
};

struct NativePolynomialFit
{
    TopoDS_Shape shape;
    double rmsDeviation {};
};

ReenExport NativePlaneFit fitNativePlane(const Data::ComplexGeoData& geometry);
ReenExport NativeCylinderFit fitNativeCylinder(const Mesh::MeshObject& mesh);
ReenExport NativeSphereFit fitNativeSphere(const Mesh::MeshObject& mesh);
ReenExport NativePolynomialFit fitNativePolynomial(const Mesh::MeshObject& mesh);

ReenExport Mesh::MeshObject triangulateNativeStructuredPoints(
    const Points::PointKernel& points,
    std::size_t width,
    std::size_t height
);

}  // namespace Reen
